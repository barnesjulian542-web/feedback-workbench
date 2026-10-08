"""读这个文件理解 State、Node、Edge、interrupt 与 checkpoint。"""
import hashlib
import json
import os
import time
from contextlib import contextmanager
from typing import TypedDict

from dotenv import load_dotenv
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from models import (Analysis, ReviewDecision, ClarificationInput, validate_evidence,
                    requires_clarification, clarification_questions, validate_review_action)
from storage import Store
from analysis_prompt import PROMPT_REVISION, build_analysis_prompt
from analysis_quality import GUARD_REVISION, protect_reported_facts

load_dotenv()


class FeedbackState(TypedDict, total=False):
    feedback_id: str
    text: str
    llm_mode: str
    analysis: dict
    candidates: list[dict]
    draft: dict
    review: dict
    outcome: dict
    clarification_round: int


def mock_analysis(text):
    """不调用模型的规则演示，仅用于学习流程。"""
    focus = text.rsplit("\n\n", 1)[-1]
    if any(word in focus for word in ("没想好", "更智能", "不太对劲")):
        category = "unclear"
    elif any(word in focus for word in ("报错", "失败", "崩溃", "闪退")):
        category = "bug"
    elif any(word in focus for word in ("希望", "增加", "能不能", "支持")):
        category = "feature"
    elif any(word in focus for word in ("难用", "麻烦", "每次", "太慢", "找不到")):
        category = "ux"
    else:
        category = "unclear"
    return Analysis(
        category=category, title=focus[:40], problem=text[:1000],
        evidence=[text[:100]], keywords=[w for w in ("上传", "团队", "导出", "登录", "搜索") if w in text] or ["反馈"],
        acceptance_criteria=["由产品负责人根据原始反馈补充可验证的验收条件"],
        needs_clarification=category == "unclear",
    ), {"mode": "mock", "prompt_tokens": 0, "completion_tokens": 0}


def real_analysis(text):
    from openai import OpenAI
    key = os.getenv("LLM_API_KEY", "").strip()
    base_url = os.getenv("LLM_BASE_URL", "").strip()
    model = os.getenv("LLM_MODEL", "").strip()
    if not all((key, base_url, model)):
        raise ValueError("请在 .env 中填写 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL")
    # QUALITY_REVIEW_V2_BEGIN: consolidated v1/v2 rules live in analysis_prompt.py.
    system_prompt = build_analysis_prompt()
    # QUALITY_REVIEW_V2_END
    extra_options = {}
    if os.getenv("LLM_DISABLE_THINKING", "false").lower() == "true":
        extra_options["extra_body"] = {"enable_thinking": False}
    # Keep the model's route independent from desktop proxy settings.
    trust_env = os.getenv("LLM_TRUST_ENV", "true").strip().lower() in ("true", "1", "yes")
    from openai import DefaultHttpxClient
    http_attempts = 0

    def count_attempt(request):
        nonlocal http_attempts
        http_attempts += 1

    try:
        with OpenAI(api_key=key, base_url=base_url, timeout=45.0, max_retries=1,
                    http_client=DefaultHttpxClient(trust_env=trust_env,
                                                  event_hooks={"request": [count_attempt]})) as client:
            response = client.chat.completions.create(
                model=model, temperature=0.1,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system_prompt},
                          {"role": "user", "content": json.dumps({"feedback": text}, ensure_ascii=False)}],
                **extra_options,
            )
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        error = RuntimeError(f"模型调用失败（{type(exc).__name__}，HTTP {status or '无'}）。检查地域、Key、模型权限和网络。")
        error.analysis_metadata = {"mode": "real", "model": model, "sdk_http_attempts": http_attempts,
                                   "prompt_revision": PROMPT_REVISION,
                                   "prompt_sha256": hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()}
        raise error from None
    content = response.choices[0].message.content or ""
    usage = response.usage
    metadata = {"mode": "real", "model": model,
                "sdk_http_attempts": http_attempts,
                "prompt_revision": PROMPT_REVISION,
                "prompt_sha256": hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None)}
    metadata["raw_model_response"] = content
    try:
        analysis = Analysis.model_validate_json(content)
    except Exception:
        error = ValueError("模型没有返回符合结构的 JSON；本次调用可能已计费。请检查模型的 JSON 输出能力。")
        error.analysis_metadata = metadata
        raise error from None
    metadata["raw_analysis"] = analysis.model_dump()
    analysis, notes = protect_reported_facts(analysis, text)
    metadata.update(quality_notes=notes, guard_revision=GUARD_REVISION)
    return analysis, metadata


def build_graph(store, checkpointer):
    def analyze(state):
        start = time.perf_counter()
        fn = mock_analysis if state["llm_mode"] == "mock" else real_analysis
        analysis, usage = fn(state["text"])
        validate_evidence(analysis, state["text"])
        store.save_analysis(state["feedback_id"], analysis)
        store.log(state["feedback_id"], "analyze", {**usage, "seconds": round(time.perf_counter() - start, 3)})
        return {"analysis": analysis.model_dump()}

    def retrieve(state):
        # 这里是流程显式调用工具；尚未实现模型自主选择工具的 Function Calling。
        a = state["analysis"]
        unclear = requires_clarification(Analysis.model_validate(a))
        candidates = [] if unclear else store.search_requirements(a["title"] + a["problem"])
        store.log(state["feedback_id"], "retrieve", {"candidate_ids": [c["id"] for c in candidates],
                  "method": "needs_clarification_skip" if unclear else "character_bigram"})
        return {"candidates": candidates}

    def draft(state):
        return {"draft": state["analysis"]}

    def review(state):
        # 恢复时该节点会重新开始，因此这里不能提前写入需求池。
        decision_data = interrupt({"draft": state["draft"], "candidates": state["candidates"],
                                   "text": state["text"], "instruction": "核对证据；信息不足先待补充，信息充分再创建、关联或驳回。"})
        decision = ReviewDecision.model_validate(decision_data)
        edited = decision.edited_analysis or Analysis.model_validate(state["draft"])
        validate_evidence(edited, state["text"])
        validate_review_action(decision.action, edited, Analysis.model_validate(state["analysis"]))
        if decision.action == "link" and not store.has_requirement(decision.requirement_id):
            raise ValueError("所选需求不存在")
        return {"review": decision.model_dump(), "draft": edited.model_dump()}

    def clarify(state):
        # interrupt 恢复会重跑节点；等待标记和补充写入均按轮次幂等。
        round_no = state.get("clarification_round", 0) + 1
        decision = ReviewDecision.model_validate(state["review"])
        store.wait_for_clarification(state["feedback_id"], round_no, decision.note)
        answer = interrupt({"kind": "clarification", "text": state["text"], "round_no": round_no,
                            "questions": clarification_questions(Analysis.model_validate(state["draft"])),
                            "note": decision.note, "instruction": "补充具体使用场景、问题和目标后重新分析。"})
        supplement = ClarificationInput.model_validate(answer).supplement
        context = store.add_clarification(state["feedback_id"], round_no, supplement)
        return {"text": context, "clarification_round": round_no, "review": {}, "outcome": {}}

    def save(state):
        decision = ReviewDecision.model_validate(state["review"])
        analysis = Analysis.model_validate(state["draft"])
        outcome = store.persist(state["feedback_id"], decision, analysis)
        return {"outcome": outcome}

    graph = StateGraph(FeedbackState)
    graph.add_node("analyze", analyze)
    graph.add_node("retrieve", retrieve)
    graph.add_node("draft", draft)
    graph.add_node("review", review)
    graph.add_node("save", save)
    graph.add_node("archive_rejected", save)
    graph.add_node("clarify", clarify)
    graph.add_edge(START, "analyze")
    graph.add_edge("analyze", "retrieve")
    graph.add_edge("retrieve", "draft")
    graph.add_edge("draft", "review")
    graph.add_conditional_edges("review", lambda s: s["review"]["action"],
                                {"create": "save", "link": "save", "reject": "archive_rejected", "clarify": "clarify"})
    graph.add_edge("clarify", "analyze")
    graph.add_edge("save", END)
    graph.add_edge("archive_rejected", END)
    return graph.compile(checkpointer=checkpointer)


@contextmanager
def open_graph(data_dir="data"):
    store = Store(data_dir)
    with SqliteSaver.from_conn_string(str(store.data_dir / "checkpoints.sqlite")) as saver:
        yield store, build_graph(store, saver)


def config_for(feedback_id):
    return {"configurable": {"thread_id": feedback_id}}


def start_feedback(store, graph, text, source_id="", mode=None):
    mode = mode or os.getenv("LLM_MODE", "mock")
    if mode not in ("mock", "real"):
        raise ValueError("LLM_MODE 只能是 mock 或 real")
    record = store.register(text, source_id, mode)
    feedback_id = record["id"]
    config = config_for(feedback_id)
    snapshot = graph.get_state(config)
    if record["status"] == "done":
        # 若写库后、检查点落盘前崩溃，先续跑幂等保存节点，保持两库状态一致。
        if snapshot.next:
            graph.invoke(None, config)
        return feedback_id, graph.get_state(config)
    if not snapshot.values:
        graph.invoke({"feedback_id": feedback_id, "text": record["text"], "llm_mode": record["llm_mode"]}, config)
    elif any(task.name == "clarify" and task.interrupts for task in snapshot.tasks):
        # 业务补充已写入、检查点还未完成时，用同轮已保存内容修复中断。
        round_no = snapshot.values.get("clarification_round", 0) + 1
        saved = next((part for part in store.list_clarifications(feedback_id) if part["round_no"] == round_no), None)
        if saved:
            graph.invoke(Command(resume={"supplement": saved["text"]}), config)
    elif snapshot.next and not any(task.interrupts for task in snapshot.tasks):
        graph.invoke(None, config)  # 显式重试未完成节点
    return feedback_id, graph.get_state(config)


def resume_feedback(store, graph, feedback_id, decision_data):
    record = store.get_feedback(feedback_id)
    if record["status"] == "done":
        return json.loads(record["outcome"])
    snapshot = graph.get_state(config_for(feedback_id))
    if not any(task.name == "review" and task.interrupts for task in snapshot.tasks):
        raise ValueError("流程当前不在等待人工审核状态")
    decision = ReviewDecision.model_validate(decision_data)
    edited = decision.edited_analysis or Analysis.model_validate(snapshot.values["draft"])
    validate_evidence(edited, snapshot.values["text"])
    validate_review_action(decision.action, edited, Analysis.model_validate(snapshot.values["analysis"]))
    if decision.action == "link" and not store.has_requirement(decision.requirement_id):
        raise ValueError("所选需求不存在")
    graph.invoke(Command(resume=decision.model_dump()), config_for(feedback_id))
    if decision.action == "clarify":
        return {"action": "clarify", "status": "waiting_clarification", "requirement_id": None, "note": decision.note}
    return graph.get_state(config_for(feedback_id)).values["outcome"]


def resume_clarification(store, graph, feedback_id, supplement):
    """补充沿用同一反馈 ID；继续分析后再次停在人工审核。"""
    record = store.get_feedback(feedback_id)
    if record["status"] == "done":
        raise ValueError("反馈已完成，无法继续补充")
    config = config_for(feedback_id)
    snapshot = graph.get_state(config)
    if not any(task.name == "clarify" and task.interrupts for task in snapshot.tasks):
        raise ValueError("流程当前不在等待补充信息状态")
    answer = ClarificationInput(supplement=supplement)
    store.preview_clarification(feedback_id, snapshot.values.get("clarification_round", 0) + 1, answer.supplement)
    graph.invoke(Command(resume=answer.model_dump()), config)
    return graph.get_state(config)
