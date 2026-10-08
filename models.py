"""模型输出与人工决策的数据约束。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Analysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["bug", "ux", "feature", "unclear"]
    title: str = Field(min_length=1, max_length=100)
    problem: str = Field(
        min_length=1, max_length=1000,
        description="只复述主要诉求的反馈现象。列表没有新记录就写列表没有新记录，不得推断数据未写入，不描述次要功能。",
    )
    evidence: list[str] = Field(min_length=1, max_length=5)
    keywords: list[str] = Field(min_length=1, max_length=8)
    acceptance_criteria: list[str] = Field(
        min_length=1, max_length=5,
        description="仅主要诉求的验收结果，不能混入次要诉求；通常1至2条。上传故障只写上传可成功完成，不延伸到解析入库或后续处理。未知参数写待确认，不编造数值、标准、功能、方案或排查项。",
    )
    needs_clarification: bool = Field(
        description="仅诉求信息不足或当前多个独立诉求尚未确认拆分时为true；缺少根因、复现参数或验收阈值不阻断明确诉求。",
    )
    clarification_questions: list[str] = Field(
        default_factory=list, max_length=3,
        description="只问阻断分流的场景、问题、目标或拆分范围；非阻断参数不追问；无需澄清时必须为空。",
    )


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["create", "link", "reject", "clarify"]
    requirement_id: str | None = None
    edited_analysis: Analysis | None = None
    priority: Literal["未定", "P0", "P1", "P2", "P3"] = "未定"
    note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def valid_link(self):
        if self.action == "link" and not self.requirement_id:
            raise ValueError("关联已有需求时必须选择需求 ID")
        return self


class ClarificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplement: str = Field(min_length=1, max_length=2000)

    @field_validator("supplement", mode="before")
    @classmethod
    def strip_supplement(cls, value):
        return value.strip() if isinstance(value, str) else value


def requires_clarification(analysis: Analysis) -> bool:
    return analysis.needs_clarification or analysis.category == "unclear"


def clarification_questions(analysis: Analysis) -> list[str]:
    questions = [q.strip() for q in analysis.clarification_questions if q.strip()]
    return questions or [
        "你希望在哪个使用场景中改进？",
        "现在遇到了什么具体问题？",
        "希望改进后能够完成什么操作？",
    ]


def validate_review_action(action: str, analysis: Analysis, original_analysis: Analysis | None = None) -> None:
    """本轮模型草稿和人工草稿都需信息充分；只改 JSON 标记不能绕过。"""
    if action in ("create", "link") and (
        requires_clarification(analysis)
        or (original_analysis is not None and requires_clarification(original_analysis))
    ):
        raise ValueError("反馈信息不足，请选择待补充信息，补充并重新分析后再创建或关联需求")


def validate_evidence(analysis: Analysis, text: str) -> None:
    """引用必须能在反馈原文中逐字找到，防止模型伪造原文。"""
    if any(not quote.strip() or quote not in text for quote in analysis.evidence):
        raise ValueError("证据引用不是反馈原文的连续片段，请重新分析或修改引用")
