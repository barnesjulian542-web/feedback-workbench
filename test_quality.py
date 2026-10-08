import unittest

from analysis_quality import protect_reported_facts
from models import Analysis


def draft(problem, quote):
    return Analysis(category="bug", title="导入失败", problem=problem, evidence=[quote],
                    keywords=["导入"], acceptance_criteria=["恢复导入"], needs_clarification=False)


class ReportedFactsTests(unittest.TestCase):
    def test_unverified_write_cause_falls_back_to_observed_quote(self):
        text = "导入失败，列表里没有新记录。"
        original = draft("导入失败，数据未写入系统，列表没有新记录。", text)
        protected, notes = protect_reported_facts(original, text)
        self.assertEqual(protected.problem, text)
        self.assertEqual(original.problem, "导入失败，数据未写入系统，列表没有新记录。")
        self.assertEqual(notes[0]["original"], original.problem)

    def test_reported_write_failure_is_retained(self):
        text = "后台日志确认数据库写入失败，导入失败。"
        original = draft("后台日志确认数据库写入失败。", text)
        self.assertEqual(protect_reported_facts(original, text), (original, []))

    def test_uncertain_hypothesis_is_not_treated_as_asserted_fact(self):
        text = "导入失败，列表里没有新记录。"
        original = draft("导入失败，可能数据未写入，原因待排查。", text)
        self.assertEqual(protect_reported_facts(original, text), (original, []))

    def test_invalid_evidence_is_not_used_for_rewriting(self):
        original = draft("数据未写入系统。", "数据库已确认损坏")
        self.assertEqual(protect_reported_facts(original, "列表没有新记录。"), (original, []))


if __name__ == "__main__":
    unittest.main()
