"""读取本地记录，导出首轮模型评测；本脚本不调用模型、不修改业务数据库。"""
import argparse
import csv
import hashlib
import json
import math
import os
import sqlite3
import statistics
from pathlib import Path

from dotenv import load_dotenv

CATEGORIES = ("bug", "ux", "feature", "unclear")
FIELDS = ["case_id", "source_id", "text", "expected_category", "expected_needs_clarification",
          "rubric", "data_type", "analysis_success", "model_category", "category_correct",
          "model_needs_clarification", "clarification_correct", "quote_exact_match",
          "evidence_support_manual", "acceptance_scope_manual", "review_notes",
          "model", "seconds", "prompt_tokens", "completion_tokens", "business_status",
          "analysis_event_count", "first_analysis_event_id", "case_fingerprint", "analysis_json", "system_note"]
MANUAL_FIELDS = ("evidence_support_manual", "acceptance_scope_manual", "review_notes")


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_cases(path):
    cases = read_csv(path)
    if not cases:
        raise ValueError("评测样例为空")
    seen = set()
    for case in cases:
        for name in ("case_id", "source_id", "text", "expected_category", "expected_needs_clarification"):
            if not case.get(name):
                raise ValueError(f"样例缺少 {name}")
        if case["source_id"] in seen:
            raise ValueError("评测样例的来源 ID 重复")
        seen.add(case["source_id"])
        if case["expected_category"] not in CATEGORIES or case["expected_needs_clarification"] not in ("true", "false"):
            raise ValueError("预期分类或澄清标记不合法")
    return cases


def fingerprint(case):
    fields = {k: case[k] for k in ("source_id", "text", "expected_category", "expected_needs_clarification")}
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def decode_analysis(raw):
    value = json.loads(raw)
    if value.get("category") not in CATEGORIES or type(value.get("needs_clarification")) is not bool:
        raise ValueError("分析缺少分类或布尔澄清标记")
    quotes = value.get("evidence")
    if not isinstance(quotes, list) or not quotes or not all(isinstance(q, str) for q in quotes):
        raise ValueError("分析缺少证据引用")
    return value


def build_results(cases, database, previous=None):
    database = Path(database).resolve()
    if not database.is_file():
        raise ValueError(f"找不到业务数据库：{database}。请先在网页导入本批样例，并核对 DATA_DIR。")
    previous = {r["source_id"]: r for r in (previous or [])}
    rows = []
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN")
        columns = {r["name"] for r in con.execute("PRAGMA table_info(feedback)")}
        if "latest_analysis_json" not in columns:
            raise ValueError("当前数据库尚未应用待补充信息更新；请先启动更新后的 app.py。")
        for case in cases:
            row = {field: "" for field in FIELDS}
            row.update({key: case.get(key, "") for key in FIELDS[:7]})
            row["case_fingerprint"] = fingerprint(case)
            old = previous.get(case["source_id"], {})
            if old.get("case_fingerprint") == row["case_fingerprint"]:
                row.update({key: old.get(key, "") for key in MANUAL_FIELDS})
            record = con.execute("SELECT * FROM feedback WHERE source_id=?", (case["source_id"],)).fetchone()
            if record is None:
                row.update(analysis_success="not_imported", system_note="未找到此来源 ID 的记录；先导入样例。")
                rows.append(row)
                continue
            row["business_status"] = record["status"]
            if record["text"] != case["text"]:
                row.update(analysis_success="text_mismatch", system_note="同一来源 ID 的原文与评测样例不同，无法评分。")
                rows.append(row)
                continue
            events = list(con.execute("SELECT * FROM events WHERE feedback_id=? AND node='analyze' ORDER BY id", (record["id"],)))
            row["analysis_event_count"] = len(events)
            if not events:
                row.update(analysis_success="not_successful", system_note="尚无可用分析；查看网页中的失败信息并重试。")
                rows.append(row)
                continue
            first = events[0]
            row["first_analysis_event_id"] = first["id"]
            if len(events) == 1 and record["latest_analysis_json"]:
                raw = record["latest_analysis_json"]
            elif (old.get("analysis_success") == "yes"
                  and old.get("case_fingerprint") == row["case_fingerprint"]
                  and str(old.get("first_analysis_event_id")) == str(first["id"])
                  and old.get("analysis_json")):
                raw = old["analysis_json"]
                row["system_note"] = "采用此前导出的首轮分析，后续补充/重新分析未替换基线。"
            else:
                row.update(analysis_success="first_output_unavailable",
                           system_note="首轮输出已不可用且此前未导出，不能把最新结果当首轮。请另用新的来源 ID 评测。")
                rows.append(row)
                continue
            try:
                analysis = decode_analysis(raw)
                metadata = json.loads(first["detail"])
                flag = analysis["needs_clarification"]
                row.update(analysis_success="yes", model_category=analysis["category"],
                           category_correct="yes" if analysis["category"] == case["expected_category"] else "no",
                           model_needs_clarification=str(flag).lower(),
                           clarification_correct="yes" if str(flag).lower() == case["expected_needs_clarification"] else "no",
                           quote_exact_match="yes" if all(q.strip() and q in case["text"] for q in analysis["evidence"]) else "no",
                           analysis_json=json.dumps(analysis, ensure_ascii=False))
                for key in ("model", "seconds", "prompt_tokens", "completion_tokens"):
                    row[key] = metadata.get(key) if metadata.get(key) is not None else ""
            except (ValueError, TypeError, KeyError) as exc:
                row.update(analysis_success="invalid_output", system_note=f"本地分析记录无法评分：{type(exc).__name__}")
            rows.append(row)
    return rows


def number(value):
    if value in ("", None):
        return None
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (ValueError, TypeError):
        return None


def ratio(count, total):
    return round(count / total, 4) if total else None


def summarize(rows):
    successful = [r for r in rows if r["analysis_success"] == "yes"]
    correct = sum(r["category_correct"] == "yes" for r in successful)
    flags_correct = sum(r["clarification_correct"] == "yes" for r in successful)
    requires = [r for r in rows if r["expected_needs_clarification"] == "true"]
    needs_found = sum(r["analysis_success"] == "yes" and r["model_needs_clarification"] == "true" for r in requires)
    times = [n for r in successful if (n := number(r["seconds"])) is not None]
    evidence_scored = [r for r in successful if r["evidence_support_manual"] in ("支持", "部分支持", "不支持")]
    invalid_ratings = [r["case_id"] for r in successful if r["evidence_support_manual"]
                       and r["evidence_support_manual"] not in ("支持", "部分支持", "不支持")]
    by_category = {}
    for category in CATEGORIES:
        group = [r for r in rows if r["expected_category"] == category]
        count = sum(r["analysis_success"] == "yes" and r["category_correct"] == "yes" for r in group)
        by_category[category] = {"expected_cases": len(group), "correct": count, "accuracy_all_cases": ratio(count, len(group))}
    return {
        "expected_cases": len(rows), "successful_first_analyses": len(successful),
        "analysis_success_rate_all_cases": ratio(len(successful), len(rows)),
        "correct_categories": correct, "category_accuracy_all_cases": ratio(correct, len(rows)),
        "category_accuracy_successful_cases": ratio(correct, len(successful)),
        "clarification_correct": flags_correct, "clarification_accuracy_all_cases": ratio(flags_correct, len(rows)),
        "expected_clarification_cases": len(requires), "clarification_detected": needs_found,
        "clarification_recall_all_expected_cases": ratio(needs_found, len(requires)),
        "semantic_evidence_scored_cases": len(evidence_scored),
        "semantic_evidence_fully_supported_rate_scored_cases": ratio(sum(r["evidence_support_manual"] == "支持" for r in evidence_scored), len(evidence_scored)),
        "invalid_manual_rating_cases": invalid_ratings,
        "latency_recorded_cases": len(times), "mean_seconds_successful_recorded": round(statistics.mean(times), 3) if times else None,
        "median_seconds_successful_recorded": round(statistics.median(times), 3) if times else None,
        "recorded_prompt_tokens": int(sum(number(r["prompt_tokens"]) or 0 for r in successful)),
        "recorded_completion_tokens": int(sum(number(r["completion_tokens"]) or 0 for r in successful)),
        "per_category": by_category,
        "cases_needing_attention": [{"case_id":r["case_id"], "status":r["analysis_success"], "note":r["system_note"]} for r in rows if r["analysis_success"] != "yes"],
        "limits": ["模拟开发样例，不能代表真实用户分布或独立测试集。", "准确率同时展示全样例和成功样例口径。",
                   "逐字引用检查不等于语义证据支持。", "耗时为成功记录的 analyze 节点耗时，不含人工等待和完整端到端耗时。",
                   "Token 只统计已记录的首轮成功调用；未记录的失败/重试费用不在此汇总。"]
    }


def pct(value):
    return f"{value:.1%}" if value is not None else "待评分"


def write_summary(out, summary):
    (out / "evaluation_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 首轮反馈分析评测", "", f"样例数：{summary['expected_cases']}；成功首轮分析：{summary['successful_first_analyses']}。", "",
             "| 指标 | 结果 |", "| --- | --- |",
             f"| 分析成功率（全部样例） | {pct(summary['analysis_success_rate_all_cases'])} |",
             f"| 分类正确率（全部样例） | {pct(summary['category_accuracy_all_cases'])} |",
             f"| 分类正确率（成功样例） | {pct(summary['category_accuracy_successful_cases'])} |",
             f"| 澄清标记正确率（全部样例） | {pct(summary['clarification_accuracy_all_cases'])} |",
             f"| 需澄清样例识别率（全部需澄清样例） | {pct(summary['clarification_recall_all_expected_cases'])} |",
             f"| 证据完整支持率（已人工评分样例） | {pct(summary['semantic_evidence_fully_supported_rate_scored_cases'])} |",
             f"| 人工证据评分覆盖数 | {summary['semantic_evidence_scored_cases']} |",
             f"| 成功 analyze 平均耗时（秒） | {summary['mean_seconds_successful_recorded']} |",
             f"| 已记录首轮输入 / 输出 Token | {summary['recorded_prompt_tokens']} / {summary['recorded_completion_tokens']} |",
             "", "## 按预期分类查看", "", "| 分类 | 样例数 | 分类正确数 | 全样例正确率 |", "| --- | --- | --- | --- |"]
    for category, result in summary["per_category"].items():
        lines.append(f"| {category} | {result['expected_cases']} | {result['correct']} | {pct(result['accuracy_all_cases'])} |")
    lines += ["", "## 口径", ""] + ["- " + limit for limit in summary["limits"]]
    if summary["cases_needing_attention"]:
        lines += ["", "## 需要处理的记录", ""]
        lines += [f"- {r['case_id']}：{r['status']}，{r['note']}" for r in summary["cases_needing_attention"]]
    if summary["invalid_manual_rating_cases"]:
        lines += ["", "人工证据评分值无法识别：" + "、".join(summary["invalid_manual_rating_cases"])]
    (out / "evaluation_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_review(out, rows):
    lines = ["# 逐条核对首轮分析", "", "与 evaluation_results.csv 对照，填写人工证据评分、验收范围判断和备注。", ""]
    for row in rows:
        lines += [f"## {row['case_id']} · {row['source_id']}", "", "原始反馈：", "", row["text"], "",
                  f"预期分类：{row['expected_category']}；预期需澄清：{row['expected_needs_clarification']}。",
                  f"模型分类：{row['model_category'] or '暂无'}；模型需澄清：{row['model_needs_clarification'] or '暂无'}。", ""]
        if row["analysis_success"] != "yes":
            lines += [row["system_note"], ""]
            continue
        analysis = json.loads(row["analysis_json"])
        lines += ["模型问题描述：", "", analysis.get("problem", ""), "", "引用证据：", ""]
        lines += ["- " + quote for quote in analysis["evidence"]]
        lines += ["", "验收建议：", ""]
        lines += ["- " + str(item) for item in analysis.get("acceptance_criteria", [])]
        lines += ["", "澄清问题：", ""]
        lines += ["- " + str(item) for item in analysis.get("clarification_questions", [])] or ["本轮未提供。"]
        lines += ["", "人工核对：问题描述是否有原文未支持的事实？验收建议是否增加了需要确认的范围？", ""]
    (out / "evaluation_review.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    load_dotenv(Path(__file__).resolve().with_name(".env"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="evaluation_cases.csv")
    parser.add_argument("--data-dir", default=os.getenv("DATA_DIR", "data"))
    parser.add_argument("--out", default="evaluation_v2_results")
    parser.add_argument("--summarize-only", action="store_true", help="只汇总已填写的结果 CSV，不读取数据库")
    args = parser.parse_args()
    try:
        out = Path(args.out)
        csv_path = out / "evaluation_results.csv"
        if args.summarize_only:
            rows = read_csv(csv_path)
        else:
            cases = load_cases(args.cases)
            prior = read_csv(csv_path) if csv_path.exists() else []
            rows = build_results(cases, Path(args.data_dir) / "business.sqlite", prior)
            out.mkdir(parents=True, exist_ok=True)
            with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(rows)
        summary = summarize(rows)
        write_summary(out, summary)
        write_review(out, rows)
        print(f"首轮成功分析：{summary['successful_first_analyses']}/{summary['expected_cases']}")
        print(f"分类正确率（全部样例）：{pct(summary['category_accuracy_all_cases'])}")
        print(f"人工证据评分覆盖数：{summary['semantic_evidence_scored_cases']}")
        print(f"结果文件：{csv_path.resolve()}")
        print(f"汇总说明：{(out / 'evaluation_summary.md').resolve()}")
    except (ValueError, OSError, sqlite3.Error, KeyError, csv.Error) as exc:
        parser.exit(1, f"导出失败：{exc}\n")


if __name__ == "__main__":
    main()
