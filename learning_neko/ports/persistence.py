# 记忆、会话、学习状态与可观测性四个存储边界的抽象契约
from __future__ import annotations

from typing import Protocol, runtime_checkable

from learning_neko.domain.models.enums import ArtifactKind, LearningPhase
from learning_neko.domain.models.memory import (
    LlmCallRecord,
    MaterialRecord,
    SessionRecord,
    TopicMemory,
    VerificationLogRecord
)


# 主题记忆的读写入口
@runtime_checkable
class MemoryRepositoryPort(Protocol):
    # 按主题取记忆
    async def get(self, topic: str) -> TopicMemory | None: ...

    # 按资料正文哈希反查记忆
    async def find_by_doc_hash(self, source_doc_hash: str) -> TopicMemory | None: ...

    # 写入或更新主题记忆
    async def upsert(self, memory: TopicMemory) -> None: ...


# 会话与产物的读写入口
@runtime_checkable
class SessionRepositoryPort(Protocol):
    # 新建会话
    async def create(self, record: SessionRecord) -> None: ...

    # 按会话id取会话
    async def get(self, session_id: str) -> SessionRecord | None: ...

    # 按最近变更时间倒序列出会话，供前端发现既有会话
    async def list_recent(self, limit: int) -> list[SessionRecord]: ...

    # 保存会话快照
    async def save(self, record: SessionRecord) -> None: ...

    # 保存一份产物
    async def save_material(self, record: MaterialRecord) -> None: ...

    # 按分节与产物种类取产物
    async def get_material(
        self,
        session_id: str,
        section_index: int,
        artifact_kind: ArtifactKind
    ) -> MaterialRecord | None: ...

    # 列出会话的全部产物
    async def list_materials(self, session_id: str) -> list[MaterialRecord]: ...


# 会话阶段的读写入口
@runtime_checkable
class LearningStateStorePort(Protocol):
    # 读取会话当前阶段
    async def read(self, session_id: str) -> LearningPhase | None: ...

    # 按比较并交换的方式迁移阶段
    async def transition(
        self,
        session_id: str,
        *,
        expected: LearningPhase,
        to: LearningPhase
    ) -> LearningPhase: ...

    # 记录当前学习到的分节序号
    async def set_section_index(self, session_id: str, index: int) -> None: ...


# 智能体运行记录的写入入口
@runtime_checkable
class AgentObserverPort(Protocol):
    # 落一条模型调用记录
    async def record_llm_call(self, record: LlmCallRecord) -> None: ...

    # 落一条校验记录
    async def record_verification(self, record: VerificationLogRecord) -> None: ...
