"""用当前真实模型做8条定向回归；保存输出，不写入业务数据库。"""
import argparse
import json
from pathlib import Path
import time
from datetime import datetime, timezone
from uuid import uuid4


B05 = "最急的问题是上传 CSV 出现 500 错误而失败。另外也希望新增批量审核功能，这条反馈涉及两个问题。"
CASES = [
    {"case_id": "R01", "text": B05, "expected_category": "bug", "expected_needs_clarification": True,
     "manual_check": "主诉求为上传故障；问题应确认批量审核是否拆单，不能重复询问哪个更急。"},
    {"case_id": "R02", "text": "上传 CSV 出现 500 错误，导入失败，列表里没有新记录。",
     "expected_category": "bug", "expected_needs_clarification": False,
     "manual_check": "异常、失败及记录缺失属于同一故障；不能过度澄清或擅自确定接口成功状态码。"},
    {"case_id": "R03", "text": "审核能够正常完成，但原文的字号太小，我得不断放大页面才能看清。",
     "expected_category": "ux", "expected_needs_clarification": False,
     "manual_check": "不能编造WCAG最低16px要求；字号等参数应待确认，不强制增加独立缩放控件。"},
    {"case_id": "R04", "text": "搜索能够找到正确的需求，但每次需要等十多秒，频繁查询很拖沓。",
     "expected_category": "ux", "expected_needs_clarification": False,
     "manual_check": "十多秒是模拟反馈中的主张；不能擅自增加3秒、弱网、连续3次等验收阈值。"},
    {"case_id": "R05", "text": "目前不能给需求添加负责人，希望新增负责人字段，便于分派处理。",
     "expected_category": "feature", "expected_needs_clarification": False,
     "manual_check": "验收聚焦设置和保存负责人；不能直接增加按负责人筛选或统计这一独立能力。"},
    {"case_id": "R06", "text": "希望增加智能化能力，具体想解决什么问题还没确定。",
     "expected_category": "unclear", "expected_needs_clarification": True,
     "manual_check": "问题聚焦具体场景和目标；不擅自给出95%准确率、2秒等数字或绑定具体业务。"},
    {"case_id": "R07", "text": "主要想解决审核页面字号太小、阅读费力的问题。也希望增加截图上传功能，这两个诉求都要处理。",
     "expected_category": "ux", "expected_needs_clarification": True,
     "manual_check": "多诉求规则也应适用于ux；验收聚焦可读性，确认截图上传是否单独处理。"},
    {"case_id": "R08", "text": B05 + "\n\n本条只处理上传 CSV 的 500 错误，批量审核另开一条需求，当前先不处理。",
     "expected_category": "bug", "expected_needs_clarification": False,
     "manual_check": "补充已确认拆分范围，应停止重复澄清；不能重新把批量审核写进验收条件。"},
]


def evaluate(case, analyze, validate):
    started = time.perf_counter()
    result = {**case, "status": "error", "acceptance_scope_manual": "未评分"}
    try:
        analysis, usage = analyze(case["text"])
        data = analysis.model_dump()
        result.update(analysis=data, usage=usage)
        validate(analysis, case["text"])
        category_ok = data["category"] == case["expected_category"]
        clarify_ok = data["needs_clarification"] == case["expected_needs_clarification"]
        questions = data["clarification_questions"]
        questions_ok = (1 <= len(questions) <= 3 and all(q.strip() for q in questions)) if data["needs_clarification"] else not questions
        result.update(category_correct=category_ok, clarification_correct=clarify_ok,
                      quote_exact_match=True, questions_consistent=questions_ok,
                      status="pass" if category_ok and clarify_ok and questions_ok else "fail")
    except Exception as exc:
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}
    result["seconds"] = round(time.perf_counter() - started, 3)
    return result


def write_results(json_path, report_path, run_id, rows):
    counts = {name: sum(r["status"] == name for r in rows) for name in ("pass", "fail", "error", "not_run")}
    payload = {"run_id": run_id, "evaluation_type": "synthetic_development_regression",
               "scope_review": "验收范围仍需逐条人工评分；自动通过不代表全部质量通过。",
               "counts": counts, "cases": rows}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 提示词定向回归", "", "这是8条模拟开发回归，不是独立测试集；未写入业务数据库。", "",
             f"自动通过：{counts['pass']}/8；不符合预期：{counts['fail']}；错误：{counts['error']}；未运行：{counts['not_run']}。", "",
             "自动检查分类、澄清标记、逐字引用和问题列表；验收范围需要对照下表和JSON人工核对。", "",
             "| 案例 | 预期分类 | 预期需澄清 | 实际分类 | 实际需澄清 | 自动检查 |", "| --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        analysis = row.get("analysis", {})
        lines.append(f"| {row['case_id']} | {row['expected_category']} | {row['expected_needs_clarification']} | {analysis.get('category', '无输出')} | {analysis.get('needs_clarification', '无输出')} | {row['status']} |")
    lines += ["", "## 人工核对验收范围", ""]
    lines += [f"- {r['case_id']}：{r['manual_check']}" for r in rows]
    lines += ["", f"完整输出：{json_path.name}", "", "模型调用沿用项目的真实模型设置。Token只包含成功返回的usage，失败或重试可能未完整记录。"]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "quality_smoke_results")
    args = parser.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent / ".env")
        from workflow import real_analysis
        from models import validate_evidence
    except ImportError as exc:
        print("项目依赖未就绪，请使用项目的 .venv Python 运行；缺失模块：", exc.name)
        return 1
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    args.out.mkdir(parents=True, exist_ok=True)
    json_path = args.out / (run_id + ".json")
    report_path = args.out / (run_id + ".md")
    rows = [{**case, "status": "not_run", "acceptance_scope_manual": "未评分"} for case in CASES]
    write_results(json_path, report_path, run_id, rows)
    print("开始真实模型回归：8条。每条输出即时保存，不写入业务数据库。", flush=True)
    try:
        for index, case in enumerate(CASES):
            print(f"[{index+1}/8] {case['case_id']}", flush=True)
            rows[index] = evaluate(case, real_analysis, validate_evidence)
            write_results(json_path, report_path, run_id, rows)
            print("结果：", rows[index]["status"], flush=True)
    except KeyboardInterrupt:
        print("已停止，完成的记录已保存。")
    print("完整结果：", json_path.resolve())
    print("回归说明：", report_path.resolve())
    print("请上传这次生成的JSON；验收范围需继续人工核对。")
    return 0 if all(r["status"] == "pass" for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
