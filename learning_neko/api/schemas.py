# 线上请求与响应契约
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from learning_neko.application.study_service import MemoryContext
from learning_neko.domain.models.enums import ArtifactKind, ArtifactStatus, LearningPhase
from learning_neko.domain.models.learning import (
    AnswerSubmission,
    CheckResult,
    ExerciseSet,
    GradeResult,
    MaterialBundle,
    Outline
)
from learning_neko.domain.models.memory import Doubt, MaterialRecord, Mistake, SessionRecord
from learning_neko.domain.state import allowed_actions


# 开始学习的请求体
class StartSessionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    source_doc: str = Field(min_length=1)
    user_request: str = Field(min_length=1)


# 提问的请求体
class QuestionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    question: str = Field(min_length=1)


# 提交作答的请求体
class AnswersRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    answers: list[AnswerSubmission] = Field(min_length=1)


# 会话快照的响应体
class SessionView(BaseModel):
    model_config = ConfigDict(extra='forbid')

    session_id: str
    topic: str
    phase: LearningPhase
    current_section_index: int
    section_count: int
    outline: Outline
    doubts: list[Doubt]
    mistakes: list[Mistake]
    allowed_actions: list[str]
    memory_context: MemoryContext | None = None
    generated_sections: list[int] = []
    created_at: datetime
    updated_at: datetime


# 会话列表里的一条，只带定位与状态，不带大纲等重内容
class SessionSummaryView(BaseModel):
    model_config = ConfigDict(extra='forbid')

    session_id: str
    topic: str
    phase: LearningPhase
    current_section_index: int
    section_count: int
    allowed_actions: list[str]
    created_at: datetime
    updated_at: datetime


# 产物读写的响应体
class MaterialView(BaseModel):
    model_config = ConfigDict(extra='forbid')

    session_id: str
    section_index: int
    artifact_kind: ArtifactKind
    status: ArtifactStatus
    attempts: int
    bundle: MaterialBundle | None = None
    exercise_set: ExerciseSet | None = None


# 答疑的响应体
class QaView(BaseModel):
    model_config = ConfigDict(extra='forbid')

    session_id: str
    answer: str
    point: str
    explained: bool


# 单题判定结果
class VerdictItemView(BaseModel):
    model_config = ConfigDict(extra='forbid')

    q_id: str
    correct: bool
    reason: str
    advice: str | None = None


# 逐题判定的响应体
class VerdictView(BaseModel):
    model_config = ConfigDict(extra='forbid')

    session_id: str
    results: list[VerdictItemView]
    correct_count: int
    total: int


# 学习报告与归档的响应体
class SummaryView(BaseModel):
    model_config = ConfigDict(extra='forbid')

    session_id: str
    report: str
    score: str
    comment: str
    mastery: dict[str, float]
    next_focus: list[str]


# 由会话生成会话列表项
def session_summary_view(record: SessionRecord) -> SessionSummaryView:
    return SessionSummaryView(
        session_id=record.session_id,
        topic=record.topic,
        phase=record.phase,
        current_section_index=record.current_section_index,
        section_count=len(record.outline.outline),
        allowed_actions=list(allowed_actions(record.phase)),
        created_at=record.created_at,
        updated_at=record.updated_at
    )


# 由会话生成会话快照响应
def session_view(
        record: SessionRecord,
        memory_context: MemoryContext | None = None,
        generated_sections: list[int] | None = None
) -> SessionView:
    return SessionView(
        session_id=record.session_id,
        topic=record.topic,
        phase=record.phase,
        current_section_index=record.current_section_index,
        section_count=len(record.outline.outline),
        outline=record.outline,
        doubts=list(record.doubts),
        mistakes=list(record.mistakes),
        allowed_actions=list(allowed_actions(record.phase)),
        memory_context=memory_context,
        generated_sections=generated_sections or [],
        created_at=record.created_at,
        updated_at=record.updated_at
    )


# 由产物生成产物响应
def material_view(record: MaterialRecord) -> MaterialView:
    is_bundle = record.artifact_kind is ArtifactKind.MATERIAL
    return MaterialView(
        session_id=record.session_id,
        section_index=record.section_index,
        artifact_kind=record.artifact_kind,
        status=record.status,
        attempts=record.attempts,
        bundle=MaterialBundle.model_validate_json(record.content) if is_bundle else None,
        exercise_set=None if is_bundle else ExerciseSet.model_validate_json(record.content)
    )


# 由例题判定生成逐题判定响应
def check_view(session_id: str, verdicts: CheckResult, total: int) -> VerdictView:
    items = [VerdictItemView(q_id=item.q_id, correct=item.correct, reason=item.reason) for item in verdicts.results]
    return _verdict_view(session_id, items, total)


# 由课后批阅生成逐题判定响应
def grade_view(session_id: str, verdicts: GradeResult, total: int) -> VerdictView:
    items = [
        VerdictItemView(q_id=item.q_id, correct=item.correct, reason=item.reason, advice=item.advice)
        for item in verdicts.results
    ]

    return _verdict_view(session_id, items, total)


# 拼装逐题判定响应并统计答对数
def _verdict_view(session_id: str, items: list[VerdictItemView], total: int) -> VerdictView:
    return VerdictView(
        session_id=session_id,
        results=items,
        correct_count=sum(1 for item in items if item.correct),
        total=total
    )
