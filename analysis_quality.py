"""针对已复现的写入原因推断做保守保护，不代替完整语义审核。"""
import re

from models import Analysis


GUARD_REVISION = "reported_facts_v1"
WRITE_CLAIM = re.compile(
    r"(?:数据(?:库)?|记录).{0,4}(?:未|没有|没能)(?:被|成功)?(?:写入|入库)"
    r"|(?:未|没有)(?:成功)?(?:写入|入库)(?:数据(?:库)?|系统)"
    r"|(?:数据库|数据)写入失败"
)


def protect_reported_facts(analysis: Analysis, text: str):
    """未报告的确定写入断言退回有效引用；原输出由调用层另存。"""
    problem = re.sub(r"\s+", "", analysis.problem)
    source = re.sub(r"\s+", "", text)
    claims = list(WRITE_CLAIM.finditer(problem))
    if not claims or WRITE_CLAIM.search(source):
        return analysis, []
    certain = any(not re.search(r"可能|或许|疑似|待排查|待确认|不确定|无法确定|是否",
                                problem[max(0, m.start() - 10):m.end() + 5]) for m in claims)
    if not certain:
        return analysis, []
    quotes = list(dict.fromkeys(q for q in analysis.evidence if q.strip() and q in text))
    if not quotes or len(quotes) != len(set(analysis.evidence)):
        # 无效引用继续交给原引用校验拒绝，不据此改写内容。
        return analysis, []
    observed = "；".join(quotes)[:1000]
    updated = analysis.model_copy(update={"problem": observed})
    return updated, [{"code": "unsupported_write_cause", "field": "problem",
                      "reason": "原文未报告确定的写入失败，问题描述已保守退回原文引用。",
                      "original": analysis.problem, "replacement": observed}]
