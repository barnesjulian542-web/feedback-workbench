"""单用户本地原型：Streamlit 页面。"""
import csv
import io
import json
import os

import streamlit as st

from models import Analysis, ReviewDecision, requires_clarification, clarification_questions
from workflow import open_graph, start_feedback, resume_feedback, resume_clarification, config_for


st.set_page_config(page_title="反馈 → 需求工作台", page_icon="💡", layout="wide")
st.title("用户反馈分析与需求分流工作台")
mode = os.getenv("LLM_MODE", "mock")
st.caption("导入反馈、核对证据、查询相似需求，由你决定是否进入需求池。")
if mode == "mock":
    st.info("当前为规则演示模式，不调用大模型。接入模型后才能评测 AI 分析质量。")
else:
    st.info("当前为真实模型模式。开始分析会调用你在 .env 中配置的模型服务。")

last_review = st.session_state.get("last_review")
if last_review:
    st.success("最近一次审核结果已保存：" + last_review["label"])
    st.json(last_review["outcome"])

with open_graph(os.getenv("DATA_DIR", "data")) as (store, graph):
    tab_input, tab_review, tab_pool, tab_logs = st.tabs(["导入反馈", "人工审核", "需求池", "处理记录"])

    with tab_input:
        with st.form("single_feedback"):
            text = st.text_area("反馈原文", value="每次上传申报材料都要重新填团队信息，能不能记住上次的数据？", height=100)
            source_id = st.text_input("来源 ID（选填）", help="同一个来源 ID 不会重复处理；无 ID 时按原文去重。")
            submit = st.form_submit_button("分析反馈", type="primary")
        if submit:
            try:
                with st.spinner("分析中…"):
                    fid, snapshot = start_feedback(store, graph, text, source_id)
                st.success("已保存，前往人工审核页继续。" if snapshot.next else "该反馈已处理，查看处理记录。")
                st.code(fid)
            except Exception as exc:
                st.error(str(exc))

        st.divider()
        st.write("批量导入 CSV：至少包含 source_id、text 两列。入门阶段建议每次最多 20 条。")
        upload = st.file_uploader("选择 CSV 文件", type="csv")
        if upload is not None:
            try:
                rows = list(csv.DictReader(io.StringIO(upload.getvalue().decode("utf-8-sig"))))
                if not rows or not {"source_id", "text"}.issubset(rows[0]):
                    raise ValueError("CSV 必须包含 source_id、text 两列且至少有一行数据")
                if len(rows) > 20:
                    raise ValueError("入门版每批最多 20 条，请分批上传")
                st.dataframe(rows, width="stretch")
                if st.button(f"开始分析 {len(rows)} 条"):
                    good = 0
                    for row in rows:
                        try:
                            start_feedback(store, graph, row["text"], row["source_id"])
                            good += 1
                        except Exception as exc:
                            st.error(f"来源 {row.get('source_id', '')}：{exc}")
                    st.success(f"完成 {good}/{len(rows)} 条，进入人工审核页查看。")
            except Exception as exc:
                st.error(str(exc))

    with tab_review:
        pending = [r for r in store.list_feedback() if r["status"] in ("pending", "waiting_clarification")]
        if not pending:
            st.write("暂无待审核反馈。先在导入反馈页开始分析。")
        else:
            labels = {r["id"]: ("[待补充] " if r["status"] == "waiting_clarification" else "[待审核] ")
                      + (r["source_id"] + " · " if r["source_id"] else "")
                      + r["text"][:55] + " · " + r["id"][:6] for r in pending}
            fid = st.selectbox("选择待审核反馈", list(labels), format_func=lambda x: labels[x])
            record = store.get_feedback(fid)
            st.caption("当前状态：" + ("待补充信息" if record["status"] == "waiting_clarification" else "待人工审核"))
            snapshot = graph.get_state(config_for(fid))
            waiting = any(task.interrupts for task in snapshot.tasks)
            if not waiting:
                st.warning("上次分析未完成，可重试未完成节点。真实模型模式下重试可能产生调用费用。")
                if st.button("重试该反馈"):
                    try:
                        start_feedback(store, graph, record["text"], record["source_id"], record["llm_mode"])
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
            elif any(task.name == "clarify" and task.interrupts for task in snapshot.tasks):
                st.warning("这条反馈正在等待补充信息，尚未进入需求池。")
                st.write("原始反馈")
                st.code(record["text"], language=None)
                supplements = store.list_clarifications(fid)
                for item in supplements:
                    st.write(f"第 {item['round_no']} 次补充")
                    st.code(item["text"], language=None)
                task = next(task for task in snapshot.tasks if task.name == "clarify" and task.interrupts)
                payload = task.interrupts[0].value
                if payload.get("note"):
                    st.write("审核备注：" + payload["note"])
                st.write("建议补充的问题")
                for question in payload["questions"]:
                    st.write("• " + question)
                with st.form(f"clarification_{fid}_{payload['round_no']}"):
                    supplement = st.text_area("补充说明", height=160,
                                              help="填写用户补充的使用场景、具体问题或预期结果。")
                    submitted = st.form_submit_button("提交补充并重新分析", type="primary")
                if submitted:
                    try:
                        with st.spinner("结合原文和补充说明重新分析中…"):
                            resume_clarification(store, graph, fid, supplement)
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
            else:
                values = snapshot.values
                st.caption("这条反馈的分析方式：" + values["llm_mode"])
                st.write("原始反馈")
                st.code(record["text"], language=None)
                for item in store.list_clarifications(fid):
                    st.write(f"第 {item['round_no']} 次补充")
                    st.code(item["text"], language=None)
                original = Analysis.model_validate(values["analysis"])
                unclear = requires_clarification(original)
                if not unclear:
                    st.caption("下一步：核对原文、证据与验收建议，再选择创建、关联或驳回。")
                notes = [json.loads(e["detail"]).get("quality_notes", []) for e in store.list_events(fid)
                         if e["node"] == "analyze"]
                if notes and notes[-1]:
                    st.warning("问题描述已退回原文支持的现象，请核对原始模型输出和处理记录。")
                if unclear:
                    st.warning("当前信息不足。请先选择待补充信息，补充并重新分析后再创建或关联需求。")
                    st.write("建议补充的问题")
                    for question in clarification_questions(original):
                        st.write("• " + question)
                left, right = st.columns(2)
                with left:
                    st.write("需求草稿（可编辑）")
                    with st.form("review_" + fid):
                        edited = st.text_area("JSON 字段", value=json.dumps(values["draft"], ensure_ascii=False, indent=2), height=330)
                        actions = ["待补充信息", "驳回并归档"] if unclear else ["创建新需求", "关联已有需求", "待补充信息", "驳回并归档"]
                        action_label = st.radio("处理方式", actions)
                        requirements = store.list_requirements()
                        options = [""] + [r["id"] for r in requirements]
                        req_labels = {r["id"]: r["title"] for r in requirements}
                        selected_req = st.selectbox("关联目标（仅关联时需要）", options,
                                                   format_func=lambda x: req_labels.get(x, "请选择"), disabled=unclear)
                        priority = st.selectbox("人工确认的优先级（创建新需求时使用）", ["未定", "P0", "P1", "P2", "P3"])
                        note = st.text_input("审核备注")
                        approved = st.form_submit_button("提交审核结果", type="primary")
                    if approved:
                        try:
                            analysis = Analysis.model_validate_json(edited)
                            action = {"创建新需求": "create", "关联已有需求": "link", "待补充信息": "clarify", "驳回并归档": "reject"}[action_label]
                            decision = ReviewDecision(action=action, requirement_id=selected_req or None,
                                                      edited_analysis=analysis, priority=priority, note=note)
                            outcome = resume_feedback(store, graph, fid, decision.model_dump())
                            st.session_state["last_review"] = {"label": action_label, "outcome": outcome}
                            st.success("已进入待补充信息状态。" if action == "clarify" else "审核结果已保存。")
                            st.json(outcome)
                            st.rerun()
                        except Exception as exc:
                            st.error(str(exc))
                with right:
                    st.write("相似需求候选")
                    st.caption("候选采用字符重合度排序；分数不是重复概率，最终由人核对。")
                    if values["candidates"]:
                        st.dataframe(values["candidates"], width="stretch")
                    else:
                        st.write("暂无候选。")
                    st.write("检查原文证据、分类、问题描述和验收建议。未知的影响人数与开发成本需要另行调查。")

    with tab_pool:
        reqs = store.list_requirements()
        if reqs:
            st.dataframe([{k: r[k] for k in ("id", "title", "category", "priority", "feedback_count")} for r in reqs], width="stretch")
            st.download_button("导出需求 JSON", json.dumps(reqs, ensure_ascii=False, indent=2), "requirements.json", "application/json")
        else:
            st.write("需求池为空。人工审核批准后才会出现需求。")

    with tab_logs:
        feedback = store.list_feedback()
        if feedback:
            st.dataframe([{k: r[k] for k in ("id", "source_id", "text", "llm_mode", "status")} for r in feedback], width="stretch")
            selected = st.selectbox("查看反馈的调用记录", [r["id"] for r in feedback])
            st.dataframe(store.list_events(selected), width="stretch")
            selected_record = store.get_feedback(selected)
            if selected_record["latest_analysis_json"]:
                st.write("本轮模型分析（审核前）")
                st.json(json.loads(selected_record["latest_analysis_json"]))
            for item in store.list_clarifications(selected):
                st.write(f"第 {item['round_no']} 次补充")
                st.code(item["text"], language=None)
            outcome = selected_record["outcome"]
            if outcome:
                st.json(json.loads(outcome))
