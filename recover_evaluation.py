"""从只读 LangGraph SQLite 历史恢复首轮评测；不调用模型。"""
import argparse
import csv
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from export_evaluation import (
    FIELDS, build_results, decode_analysis, load_cases,
    load_dotenv, summarize, write_review, write_summary,
)

OUTPUT_FIELDS = FIELDS + ["feedback_id", "analysis_source", "first_checkpoint_id"]
REPORT_FIELDS = ["case_id", "source_id", "feedback_id", "status", "analysis_source",
                 "first_checkpoint_id", "reason"]


def read_csv_compatible(path):
    """兼容 WPS 保存的 GBK/GB18030；解码失败不忽略字符。"""
    data = Path(path).read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("gb18030")
    import io
    rows = list(csv.DictReader(io.StringIO(text, newline="")))
    for row in rows:
        for key in ("expected_needs_clarification", "model_needs_clarification"):
            if row.get(key):
                row[key] = row[key].strip().lower()
    return rows


def parse_time(value):
    if not isinstance(value, str) or not value:
        raise ValueError("缺少时间戳")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("时间戳缺少时区")
    return result.astimezone(timezone.utc)


def decode_checkpoint(kind, payload):
    # 本项目状态由 JSON 可表示的基础类型组成，不启用 pickle 反序列化。
    if kind not in ("json", "msgpack"):
        raise ValueError(f"不支持的检查点格式：{kind}")
    if kind == "json":
        return json.loads(payload)
    try:
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    except ImportError:
        try:
            import msgpack
        except ImportError as exc:
            raise ValueError("请使用运行工作台的 .venv Python；缺少检查点解码依赖") from exc
        return msgpack.unpackb(payload, raw=False, strict_map_key=False)
    return JsonPlusSerializer().loads_typed((kind, bytes(payload)))


def recover_first_output(connection, feedback_id, case, events):
    """只接受首次 analyze 之后、第二次 analyze 之前、原文一致的状态。"""
    try:
        start = parse_time(events[0]["created_at"])
        end = parse_time(events[1]["created_at"]) if len(events) > 1 else None
        if end is not None and end <= start:
            return None, "分析时间顺序无法区分，不能确认首轮"
    except (ValueError, KeyError, IndexError):
        return None, "缺少可核对的分析时间，不能确认首轮"
    candidates = []
    errors = []
    # 空命名空间是当前工作台主图，子图同名字段不作为本流程证据。
    records = connection.execute(
        "SELECT checkpoint_id,type,checkpoint FROM checkpoints "
        "WHERE thread_id=? AND checkpoint_ns='' ORDER BY checkpoint_id",
        (feedback_id,),
    )
    for item in records:
        try:
            state = decode_checkpoint(item["type"], item["checkpoint"])
            timestamp = parse_time(state.get("ts"))
            if timestamp < start or (end is not None and timestamp >= end):
                continue
            values = state.get("channel_values", {})
            if values.get("feedback_id") != feedback_id or values.get("text") != case["text"]:
                continue
            if values.get("clarification_round", 0) != 0:
                continue
            analysis = values.get("analysis")
            if not isinstance(analysis, dict):
                continue
            raw = json.dumps(analysis, ensure_ascii=False, allow_nan=False)
            analysis = decode_analysis(raw)
            candidates.append((timestamp, item["checkpoint_id"], analysis))
        except Exception as exc:
            errors.append(type(exc).__name__)
    if not candidates:
        suffix = "；部分历史解码失败：" + ",".join(sorted(set(errors))) if errors else ""
        return None, "没有找到首次分析时间范围内、原文一致的检查点" + suffix
    timestamp, checkpoint_id, analysis = min(candidates, key=lambda item: (item[0], item[1]))
    return {"analysis": analysis, "checkpoint_id": checkpoint_id}, "按首次分析时间范围及原文核对恢复"


def populate_analysis(row, analysis, metadata):
    flag = str(analysis["needs_clarification"]).lower()
    row.update(
        analysis_success="yes", model_category=analysis["category"],
        category_correct="yes" if analysis["category"] == row["expected_category"] else "no",
        model_needs_clarification=flag,
        clarification_correct="yes" if flag == row["expected_needs_clarification"] else "no",
        quote_exact_match="yes" if all(q.strip() and q in row["text"] for q in analysis["evidence"]) else "no",
        analysis_json=json.dumps(analysis, ensure_ascii=False),
    )
    for key in ("model", "seconds", "prompt_tokens", "completion_tokens"):
        row[key] = metadata.get(key) if metadata.get(key) is not None else ""


def recover_results(cases, data_dir, previous=None):
    data_dir = Path(data_dir).resolve()
    database = data_dir / "business.sqlite"
    rows = build_results(cases, database, previous)
    previous_by_id = {r["source_id"]: r for r in (previous or [])}
    case_by_id = {r["source_id"]: r for r in cases}
    report = []
    checkpoint_path = data_dir / "checkpoints.sqlite"
    checkpoint_connection = None
    checkpoint_error = "找不到 checkpoints.sqlite，保留原有缺失标记"
    try:
        if checkpoint_path.is_file():
            checkpoint_connection = sqlite3.connect(checkpoint_path.as_uri() + "?mode=ro", uri=True)
            checkpoint_connection.row_factory = sqlite3.Row
            checkpoint_connection.execute("BEGIN")
            columns = {r["name"] for r in checkpoint_connection.execute("PRAGMA table_info(checkpoints)")}
            if not {"thread_id", "checkpoint_ns", "checkpoint_id", "type", "checkpoint"}.issubset(columns):
                checkpoint_connection.close()
                checkpoint_connection = None
                checkpoint_error = "检查点表结构不兼容，保留原有缺失标记"
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as con:
            con.row_factory = sqlite3.Row
            con.execute("BEGIN")
            for row in rows:
                record = con.execute("SELECT id FROM feedback WHERE source_id=?", (row["source_id"],)).fetchone()
                row["feedback_id"] = record["id"] if record else ""
                row["analysis_source"] = "existing_export_or_single_analysis" if row["analysis_success"] == "yes" else "unavailable"
                row["first_checkpoint_id"] = ""
                old = previous_by_id.get(row["source_id"], {})
                if (row["analysis_success"] == "yes" and old.get("case_fingerprint") == row["case_fingerprint"]
                        and old.get("analysis_json") == row["analysis_json"]):
                    row["analysis_source"] = old.get("analysis_source") or row["analysis_source"]
                    row["first_checkpoint_id"] = old.get("first_checkpoint_id", "")
                reason = "已有可评分首轮结果" if row["analysis_success"] == "yes" else row["system_note"]
                if row["analysis_success"] == "first_output_unavailable" and record:
                    events = list(con.execute(
                        "SELECT id,detail,created_at FROM events WHERE feedback_id=? AND node='analyze' ORDER BY id",
                        (record["id"],),
                    ))
                    if checkpoint_connection is not None:
                        recovered, reason = recover_first_output(
                            checkpoint_connection, record["id"], case_by_id[row["source_id"]], events,
                        )
                    else:
                        recovered, reason = None, checkpoint_error
                    if recovered is not None:
                        try:
                            metadata = json.loads(events[0]["detail"])
                            if not isinstance(metadata, dict):
                                raise ValueError("调用元数据不是对象")
                            populate_analysis(row, recovered["analysis"], metadata)
                            row.update(analysis_source="checkpoint_history", first_checkpoint_id=recovered["checkpoint_id"],
                                       system_note="已恢复首轮分析；" + reason)
                        except (ValueError, TypeError, KeyError) as exc:
                            reason = "首轮元数据不可读取：" + type(exc).__name__
                    if row["analysis_success"] != "yes":
                        row["system_note"] = reason + "。此条首轮暂不可评分。"
                report.append({
                    "case_id": row["case_id"], "source_id": row["source_id"], "feedback_id": row["feedback_id"],
                    "status": row["analysis_success"], "analysis_source": row["analysis_source"],
                    "first_checkpoint_id": row["first_checkpoint_id"], "reason": reason,
                })
    finally:
        if checkpoint_connection is not None:
            checkpoint_connection.close()
    return rows, report


def write_csv(path, rows, fields):
    # 先写临时文件再替换，写入失败时已有结果仍保留。
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main():
    load_dotenv(Path(__file__).resolve().with_name(".env"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="evaluation_cases.csv")
    parser.add_argument("--data-dir", default=os.getenv("DATA_DIR", "data"))
    parser.add_argument("--original-out", default="evaluation_v2_results")
    parser.add_argument("--out", default="evaluation_v2_recovered")
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()
    try:
        out = Path(args.out).resolve()
        old_out = Path(args.original_out).resolve()
        if out == old_out:
            raise ValueError("请使用独立输出目录，保留原始评测表")
        result_path = out / "evaluation_results.csv"
        if args.summarize_only:
            rows = read_csv_compatible(result_path)
        else:
            cases = load_cases(args.cases)
            previous = []
            for path in (old_out / "evaluation_results.csv", result_path):
                if path.is_file():
                    previous.extend(read_csv_compatible(path))
            rows, report = recover_results(cases, args.data_dir, previous)
            out.mkdir(parents=True, exist_ok=True)
            write_csv(result_path, rows, OUTPUT_FIELDS)
            write_csv(out / "recovery_report.csv", report, REPORT_FIELDS)
        summary = summarize(rows)
        write_summary(out, summary)
        write_review(out, rows)
        summary_path = out / "evaluation_summary.md"
        summary_path.write_text(summary_path.read_text(encoding="utf-8") +
            "\n## 历史恢复说明\n\n"
            "首轮可评分结果来自单次分析记录、已有首轮导出快照或按时间核对的历史检查点。"
            "仍缺失的首轮输出不等于模型没有成功分析；全样例正确率同时受记录完整性影响。"
            "请结合可评分覆盖数和成功样例正确率解读。恢复工具只读业务及检查点数据库，未调用模型。\n",
            encoding="utf-8")
        if not args.summarize_only:
            print(f"读取的数据目录：{Path(args.data_dir).resolve()}")
        print(f"可评分首轮结果：{summary['successful_first_analyses']}/{summary['expected_cases']}")
        print(f"来自历史检查点的结果：{sum(r.get('analysis_source') == 'checkpoint_history' for r in rows)}")
        print(f"人工证据评分覆盖数：{summary['semantic_evidence_scored_cases']}")
        print(f"结果文件：{result_path}")
        unavailable = [r["case_id"] for r in rows if r["analysis_success"] != "yes"]
        if unavailable:
            print("仍缺少可评分首轮输出：" + ", ".join(unavailable))
            print("查看 recovery_report.csv；不要将当前最新输出手动改成首轮输出。")
    except (ValueError, OSError, sqlite3.Error, KeyError, csv.Error) as exc:
        parser.exit(1, f"恢复导出失败：{exc}\n")


if __name__ == "__main__":
    main()
