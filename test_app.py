import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class AppSmokeTest(unittest.TestCase):
    def test_multiple_requests_clarify_then_review_same_feedback(self):
        from models import Analysis
        from quality_smoke_test import B05
        from storage import Store

        supplement = "本条只处理上传 CSV 的 500 错误，批量审核另开一条需求，当前先不处理。"
        original = Analysis(
            category="bug", title="CSV 上传失败", problem="上传 CSV 出现 500 错误而失败。",
            evidence=["上传 CSV 出现 500 错误而失败"], keywords=["CSV"],
            acceptance_criteria=["上传 CSV 不再出现反馈中的 500 错误，上传可完成。"],
            needs_clarification=True,
            clarification_questions=["批量审核是否另建一条需求？"],
        )
        clarified = original.model_copy(update={"needs_clarification": False, "clarification_questions": []})
        responses = [(original, {"mode": "test"}), (clarified, {"mode": "test"})]
        with tempfile.TemporaryDirectory() as data_dir, \
                patch.dict(os.environ, {"DATA_DIR": data_dir, "LLM_MODE": "real"}), \
                patch("workflow.real_analysis", side_effect=responses) as analyze:
            app = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=20).run()
            app.text_area[0].set_value(B05)
            app.text_input[0].set_value("test-B05-clarify")
            next(b for b in app.button if b.label == "分析反馈").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            actions = next(r for r in app.radio if r.label == "处理方式")
            self.assertNotIn("创建新需求", actions.options)
            self.assertNotIn("关联已有需求", actions.options)
            store = Store(data_dir)
            fid = store.list_feedback()[0]["id"]
            self.assertEqual(store.list_requirements(), [])
            next(b for b in app.button if b.label == "提交审核结果").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertEqual(store.get_feedback(fid)["status"], "waiting_clarification")
            next(t for t in app.text_area if t.label == "补充说明").set_value(supplement)
            next(b for b in app.button if b.label == "提交补充并重新分析").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertIn("创建新需求", next(r for r in app.radio if r.label == "处理方式").options)
            self.assertEqual(len(store.list_feedback()), 1)
            self.assertEqual(store.list_requirements(), [])
            self.assertEqual(store.list_clarifications(fid)[0]["text"], supplement)
            next(b for b in app.button if b.label == "提交审核结果").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertEqual(store.get_feedback(fid)["status"], "done")
            self.assertEqual(len(store.list_requirements()), 1)
            self.assertEqual(analyze.call_count, 2)
            self.assertEqual(analyze.call_args_list[1].args[0], B05 + "\n\n" + supplement)

    def test_input_and_human_approval(self):
        with tempfile.TemporaryDirectory() as data_dir, patch.dict(os.environ, {"DATA_DIR": data_dir, "LLM_MODE": "mock"}):
            app = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=20).run()
            self.assertFalse(app.exception)
            app.button[0].click().run()
            self.assertFalse(app.exception)
            buttons = [b for b in app.button if b.label == "提交审核结果"]
            self.assertEqual(len(buttons), 1)
            buttons[0].click().run()
            self.assertFalse(app.exception)
            from storage import Store
            self.assertEqual(len(Store(data_dir).list_requirements()), 1)
            self.assertTrue(any("最近一次审核结果已保存" in s.value for s in app.success))


if __name__ == "__main__":
    unittest.main()
