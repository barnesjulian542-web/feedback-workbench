"""有意义的验证：审核阻断、进程恢复、幂等写库、失败重试。"""
import tempfile
import unittest
from unittest.mock import patch

from workflow import open_graph, start_feedback, resume_feedback, resume_clarification, config_for


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = self.temp.name

    def tearDown(self):
        self.temp.cleanup()

    def test_pending_survives_restart_and_writes_only_after_approval(self):
        with open_graph(self.path) as (store, graph):
            fid, state = start_feedback(store, graph, "希望支持导出 Excel", mode="mock")
            self.assertTrue(any(t.interrupts for t in state.tasks))
            self.assertEqual(store.list_requirements(), [])
        with open_graph(self.path) as (store, graph):
            state = graph.get_state(config_for(fid))
            self.assertTrue(any(t.interrupts for t in state.tasks))
            outcome = resume_feedback(store, graph, fid, {"action": "create"})
            self.assertEqual(outcome["action"], "create")
            self.assertEqual(len(store.list_requirements()), 1)
            self.assertEqual(graph.get_state(config_for(fid)).next, ())

    def test_duplicate_submission_and_resume_do_not_create_duplicate(self):
        with open_graph(self.path) as (store, graph):
            fid, _ = start_feedback(store, graph, "导出失败", mode="mock")
            one = resume_feedback(store, graph, fid, {"action": "create"})
            again, _ = start_feedback(store, graph, "导出失败", mode="mock")
            two = resume_feedback(store, graph, fid, {"action": "create"})
            self.assertEqual(fid, again)
            self.assertEqual(one, two)
            self.assertEqual(len(store.list_requirements()), 1)

    def test_link_updates_feedback_count_without_creating_new_requirement(self):
        with open_graph(self.path) as (store, graph):
            first, _ = start_feedback(store, graph, "希望支持导出 Excel", "user-a", mode="mock")
            req = resume_feedback(store, graph, first, {"action": "create"})["requirement_id"]
            second, state = start_feedback(store, graph, "希望支持导出表格", "user-b", mode="mock")
            self.assertTrue(state.values["candidates"])
            resume_feedback(store, graph, second, {"action": "link", "requirement_id": req})
            self.assertEqual(len(store.list_requirements()), 1)
            self.assertEqual(store.list_requirements()[0]["feedback_count"], 2)

    def test_reject_archives_without_requirement(self):
        with open_graph(self.path) as (store, graph):
            fid, _ = start_feedback(store, graph, "不好用", mode="mock")
            out = resume_feedback(store, graph, fid, {"action": "reject", "note": "需补充使用场景"})
            self.assertEqual(out["action"], "reject")
            self.assertFalse(store.list_requirements())
            self.assertEqual(store.get_feedback(fid)["status"], "done")

    def test_invalid_review_preserves_pending_state(self):
        with open_graph(self.path) as (store, graph):
            fid, state = start_feedback(store, graph, "上传时出现报错", mode="mock")
            edited = {**state.values["draft"], "evidence": ["这是不存在的原文"]}
            with self.assertRaises(ValueError):
                resume_feedback(store, graph, fid, {"action": "create", "edited_analysis": edited})
            self.assertTrue(any(t.interrupts for t in graph.get_state(config_for(fid)).tasks))
            self.assertFalse(store.list_requirements())

    def test_failed_analysis_can_retry_from_checkpoint(self):
        with open_graph(self.path) as (store, graph):
            with patch("workflow.mock_analysis", side_effect=RuntimeError("temporary failure")):
                with self.assertRaises(RuntimeError):
                    start_feedback(store, graph, "登录失败", "retry-1", mode="mock")
            fid, state = start_feedback(store, graph, "登录失败", "retry-1", mode="mock")
            self.assertTrue(any(t.interrupts for t in state.tasks))
            self.assertEqual(len(store.list_feedback()), 1)

    def test_clarification_blocks_flag_override_and_survives_restart(self):
        with open_graph(self.path) as (store, graph):
            fid, state = start_feedback(store, graph, "希望更智能，没想好场景", "clarify-restart", mode="mock")
            edited = {**state.values["draft"], "category": "feature", "needs_clarification": False}
            with self.assertRaises(ValueError):
                resume_feedback(store, graph, fid, {"action": "create", "edited_analysis": edited})
            self.assertFalse(store.list_requirements())
            resume_feedback(store, graph, fid, {"action": "clarify"})
            self.assertEqual(store.get_feedback(fid)["status"], "waiting_clarification")
        with open_graph(self.path) as (store, graph):
            state = resume_clarification(store, graph, fid, "希望支持导出 Excel")
            self.assertFalse(state.values["analysis"]["needs_clarification"])
            self.assertTrue(any(t.name == "review" and t.interrupts for t in state.tasks))
            self.assertEqual(len(store.list_clarifications(fid)), 1)
            self.assertFalse(store.list_requirements())
            resume_feedback(store, graph, fid, {"action": "create"})
            self.assertEqual(len(store.list_feedback()), 1)
            self.assertEqual(len(store.list_requirements()), 1)


if __name__ == "__main__":
    unittest.main()
