# 跨会话记忆、会话运行期记录与校验结论的领域模型
from __future__ import annotations

import re
from datetime import (
    date as DateValue,
    datetime as DateTimeValue
)

from pydantic import BaseModel, ConfigDict, Field

from learning_neko.domain.errors import SectionNotFound
from learning_neko.domain.models.enums import (
    ArtifactKind,
    ArtifactStatus,
    ExerciseStage,
    LearningPhase,
    StructuredMode,
    VerificationSource
)
from learning_neko.domain.models.learning import Outline, OutlineSection

_WHITESPACE = re.compile(r'\s+')


# 把主题名归一成检索键，吸收大小写与空白差异
def topic_key_of(topic: str) -> str:
    return _WHITESPACE.sub('', topic).strip().lower()


# 一条疑点记录
class Doubt(BaseModel):
    model_config = ConfigDict(extra='forbid')

    point: str
    question: str
    resolved: bool


# 一道错题记录，含所属知识点与错误原因
class Mistake(BaseModel):
    model_config = ConfigDict(extra='forbid')

    q_id: str
    knowledge_point: str
    reason: str
    stage: ExerciseStage


# 一次学习的归档记录，sessions数组按时间累积保存在它之上
class SessionOutcome(BaseModel):
    model_config = ConfigDict(extra='forbid')

    session_id: str
    date: DateValue
    doubts: list[Doubt] = Field(default_factory=list)
    mistakes: list[Mistake] = Field(default_factory=list)
    score: str = ''
    comment: str = ''
    report: str = ''


# 一个主题下的全部记忆，是跨会话闭环的载体
class TopicMemory(BaseModel):
    model_config = ConfigDict(extra='forbid')

    topic: str
    last_study: DateValue | None = None
    sessions: list[SessionOutcome] = Field(default_factory=list)
    mastery: dict[str, float] = Field(default_factory=dict)
    next_focus: list[str] = Field(default_factory=list)
    source_doc_hash: str = ''


# 总结归档智能体的结构化输出
class SummaryOutput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    score: str = ''
    comment: str
    mastery: dict[str, float]
    next_focus: list[str]
    report: str


# 产物里的一个问题，含位置、原因与可执行的修正动作
class VerificationError(BaseModel):
    model_config = ConfigDict(extra='forbid')

    content: str
    reason: str
    fix: str


# 一轮校验的结论，passed字段对外序列化为pass
class VerificationResult(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)

    passed: bool = Field(alias='pass')
    errors: list[VerificationError] = Field(default_factory=list)


# 课后测验的统计结果，用于计算得分
class QuizOutcome(BaseModel):
    model_config = ConfigDict(extra='forbid')

    total: int
    correct: int


# 一次学习的完整记录，含大纲、累积的疑点与错题
class SessionRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')

    session_id: str
    topic: str
    phase: LearningPhase
    current_section_index: int
    source_doc: str
    source_doc_hash: str
    user_request: str
    outline: Outline
    doubts: list[Doubt] = Field(default_factory=list)
    mistakes: list[Mistake] = Field(default_factory=list)
    quiz: QuizOutcome | None = None
    created_at: DateTimeValue
    updated_at: DateTimeValue

    # 按下标取分节，越界时抛SectionNotFound
    def section_at(self, index: int) -> OutlineSection:
        if index < 0 or index >= len(self.outline.outline):
            raise SectionNotFound(
                '分节下标超出大纲范围',
                session_id=self.session_id,
                section_index=index,
                section_count=len(self.outline.outline)
            )
        return self.outline.outline[index]


# 已落盘的生成产物
class MaterialRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')

    session_id: str
    section_index: int
    artifact_kind: ArtifactKind
    status: ArtifactStatus
    content: str
    attempts: int
    updated_at: DateTimeValue


# 一次模型调用的观测记录
class LlmCallRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')

    session_id: str | None = None
    agent: str
    provider: str
    model: str
    structured_mode: StructuredMode | None = None
    degraded_from: str | None = None
    latency_ms: float
    prompt_chars: int
    completion_chars: int
    prompt_fingerprint: str | None = None
    request_json: str | None = None
    ok: bool
    error: str | None = None
    created_at: DateTimeValue


# 一轮校验的观测记录
class VerificationLogRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')

    session_id: str | None = None
    artifact_ref: str
    artifact_kind: ArtifactKind
    attempt: int
    source: VerificationSource
    passed: bool
    errors: list[VerificationError] = Field(default_factory=list)
    created_at: DateTimeValue
