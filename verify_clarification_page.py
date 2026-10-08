"""用 AppTest 驱动现有页面和真实模型，新增一条验证反馈，停在待人工审核。"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from uuid import uuid4
from unittest.mock import patch

from dotenv import load_dotenv
from streamlit.testing.v1 import AppTest

from quality_smoke_test import B05
from workflow import config_for, open_graph


SUPPLEMENT = "本条只处理上传 CSV 的 500 错误，批量审核另开一条需求，当前先不处理。"


def main():
    root = Path(__file__).resolve().parent
    load_dotenv(root / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", default="iter2-B05-01")
    parser.add_argument("--data-dir", type=Path, default=Path(os.getenv("DATA_DIR", "data")))
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()
    with open_graph(data_dir) as (store, graph):
        if any(r["source_id"] == args.source_id for r in store.list_feedback()):
            raise ValueError("来源 ID 已存在；不要重复分析，查看既有页面验证记录或使用新的来源 ID。")
        initial_counts = {"feedback": len(store.list_feedback()), "requirements": len(store.list_requirements())}

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    out = root / "quality_smoke_results" / (run_id + "_page.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    result = {"run_id": run_id, "verification_type": "real_model_streamlit_apptest",
              "source_id": args.source_id, "data_dir": str(data_dir), "initial_counts": initial_counts,
              "stages": [], "status": "running", "human_approval": "未提交；停在待人工审核"}

    def save():
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    def check_page(app):
        if app.exception or app.error:
            raise AssertionError([str(e.value) for e in [*app.exception, *app.error]])

    def button(app, label):
        return next(b for b in app.button if b.label == label)

    def snapshot_stage(name, fid):
        with open_graph(data_dir) as (store, graph):
            snapshot = graph.get_state(config_for(fid))
            result["stages"].append({"name": name, "feedback_id": fid,
                                     "status": store.get_feedback(fid)["status"],
                                     "analysis": snapshot.values["analysis"],
                                     "next": list(snapshot.next),
                                     "clarifications": store.list_clarifications(fid),
                                     "events": store.list_events(fid)})
            save()
            return snapshot

    save()
    started = time.perf_counter()
    try:
        # 仅指定已有环境和数据目录；不替换模型函数或模型配置。
        with patch.dict(os.environ, {"DATA_DIR": str(data_dir), "LLM_MODE": "real"}):
            app = AppTest.from_file(str(root / "app.py"), default_timeout=120).run()
            check_page(app)
            next(t for t in app.text_area if t.label == "反馈原文").set_value(B05)
            next(t for t in app.text_input if t.label == "来源 ID（选填）").set_value(args.source_id)
            button(app, "分析反馈").click().run()
            check_page(app)
            with open_graph(data_dir) as (store, graph):
                fid = next(r["id"] for r in store.list_feedback() if r["source_id"] == args.source_id)
            result["feedback_id"] = fid
            next(s for s in app.selectbox if s.label == "选择待审核反馈").select(fid).run()
            check_page(app)
            first = snapshot_stage("first_analysis", fid).values["analysis"]
            assert first["category"] == "bug" and first["needs_clarification"]
            actions = next(r for r in app.radio if r.label == "处理方式")
            assert "创建新需求" not in actions.options and "关联已有需求" not in actions.options
            actions.set_value("待补充信息")
            next(t for t in app.text_input if t.label == "审核备注").set_value("流程验证：先确认批量审核是否另建。")
            button(app, "提交审核结果").click().run()
            check_page(app)
            waiting = snapshot_stage("waiting_for_supplement", fid)
            assert waiting.next == ("clarify",)
            next(t for t in app.text_area if t.label == "补充说明").set_value(SUPPLEMENT)
            button(app, "提交补充并重新分析").click().run()
            check_page(app)
            second = snapshot_stage("reanalyzed_pending_human_review", fid).values["analysis"]
            assert second["category"] == "bug" and not second["needs_clarification"]
            assert second["clarification_questions"] == []
            actions = next(r for r in app.radio if r.label == "处理方式")
            result["final_review_options"] = list(actions.options)
            assert "创建新需求" in actions.options and "关联已有需求" in actions.options
            with open_graph(data_dir) as (store, graph):
                result["final_counts"] = {"feedback": len(store.list_feedback()),
                                           "requirements": len(store.list_requirements())}
                assert result["final_counts"]["feedback"] == initial_counts["feedback"] + 1
                assert result["final_counts"]["requirements"] == initial_counts["requirements"]
                assert len(store.list_clarifications(fid)) == 1
                assert store.get_feedback(fid)["status"] == "pending"
            result["status"] = "pass"
    except Exception as exc:
        result["status"] = "error"
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        result["seconds"] = round(time.perf_counter() - started, 3)
        save()
        print("页面验证结果：", out)
        print("状态：", result["status"], "；最终需求审批留待用户确认。")
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
