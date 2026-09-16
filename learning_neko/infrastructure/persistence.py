# sqlite持久化实现：行模型、领域模型互转、引擎与各仓储
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import (
    date as DateValue,
    datetime as DateTimeValue
)
from pathlib import Path
from typing import Protocol, TypeVar

from anyio import to_thread
from loguru import logger
from sqlalchemy import delete, event, UniqueConstraint, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry
from sqlmodel import create_engine, Field, select, Session, SQLModel

from learning_neko.config import DatabaseSettings
from learning_neko.domain.errors import AlreadyCompleted, DomainError, IllegalTransition, SessionNotFound
from learning_neko.domain.models import dump_models
from learning_neko.domain.models.enums import ArtifactKind, ArtifactStatus, LearningPhase
from learning_neko.domain.models.learning import Outline
from learning_neko.domain.models.memory import (
    Doubt,
    LlmCallRecord,
    MaterialRecord,
    Mistake,
    QuizOutcome,
    SessionOutcome,
    SessionRecord,
    topic_key_of,
    TopicMemory,
    VerificationLogRecord
)
from learning_neko.domain.state import assert_transition

REQUEST_JSON_LIMIT = 20000

T = TypeVar('T')


# 生成一条冲突即更新的插入语句，用于并发下的幂等写入
def _upsert(
    table: type[SQLModel],
    values: dict[str, object],
    key_columns: tuple[str, ...],
    update_columns: tuple[str, ...]
) -> object:
    statement = sqlite_insert(table).values(**values)
    return statement.on_conflict_do_update(
        index_elements=list(key_columns),
        set_={name: values[name] for name in update_columns}
    )


# 主题记忆的头部行
class TopicMemoryRow(SQLModel, table=True):
    __tablename__ = 'topic_memory'

    topic_key: str = Field(primary_key=True)
    topic: str
    last_study: DateValue | None = Field(default=None)
    source_doc_hash: str = Field(default='')
    created_at: DateTimeValue


# 学习会话行
class StudySessionRow(SQLModel, table=True):
    __tablename__ = 'study_session'

    session_id: str = Field(primary_key=True)
    topic_key: str = Field(index=True)
    phase: str
    current_section_index: int = Field(default=0)
    source_doc: str
    source_doc_hash: str
    user_request: str
    outline_json: str
    doubts_json: str = Field(default='[]')
    mistakes_json: str = Field(default='[]')
    quiz_json: str | None = Field(default=None)
    created_at: DateTimeValue
    updated_at: DateTimeValue


# 单次学习归档行
class SessionOutcomeRow(SQLModel, table=True):
    __tablename__ = 'session_outcome'

    session_id: str = Field(primary_key=True)
    topic_key: str = Field(index=True)
    date: DateValue
    score: str = Field(default='')
    comment: str = Field(default='')
    report: str = Field(default='')
    doubts_json: str = Field(default='[]')
    mistakes_json: str = Field(default='[]')


# 知识点掌握度行
class MasteryRow(SQLModel, table=True):
    __tablename__ = 'mastery'

    topic_key: str = Field(primary_key=True)
    knowledge_point: str = Field(primary_key=True)
    score: float


# 后续复习重点行
class NextFocusRow(SQLModel, table=True):
    __tablename__ = 'next_focus'
    __table_args__ = (UniqueConstraint('topic_key', 'ordinal', name='uq_next_focus_slot'),)

    id: int | None = Field(default=None, primary_key=True)
    topic_key: str = Field(index=True)
    ordinal: int
    point: str


# 会话产物行
class StudyMaterialRow(SQLModel, table=True):
    __tablename__ = 'study_materials'
    __table_args__ = (
        UniqueConstraint('session_id', 'section_index', 'artifact_kind', name='uq_material_slot'),
    )

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    section_index: int
    artifact_kind: str
    status: str
    content_json: str
    attempts: int = Field(default=1)
    updated_at: DateTimeValue


# 校验记录行
class VerificationLogRow(SQLModel, table=True):
    __tablename__ = 'verification_logs'

    id: int | None = Field(default=None, primary_key=True)
    session_id: str | None = Field(default=None, index=True)
    artifact_ref: str = Field(index=True)
    artifact_kind: str
    attempt: int
    source: str
    passed: bool
    errors_json: str = Field(default='[]')
    created_at: DateTimeValue


# 模型调用记录行
class LlmCallRow(SQLModel, table=True):
    __tablename__ = 'llm_calls'

    id: int | None = Field(default=None, primary_key=True)
    session_id: str | None = Field(default=None, index=True)
    agent: str
    provider: str
    model: str
    structured_mode: str | None = Field(default=None)
    degraded_from: str | None = Field(default=None)
    latency_ms: float
    prompt_chars: int
    completion_chars: int
    prompt_fingerprint: str | None = Field(default=None)
    request_json: str | None = Field(default=None)
    ok: bool
    error: str | None = Field(default=None)
    created_at: DateTimeValue


# 把json文本反序列化成字典列表
def load_records(payload: str) -> list[dict[str, object]]:
    return [item for item in json.loads(payload) if isinstance(item, dict)]


# 由头部行与三张明细表拼出主题记忆
def assemble_memory(
    head: TopicMemoryRow,
    outcomes: list[SessionOutcomeRow],
    mastery: list[MasteryRow],
    focus: list[NextFocusRow]
) -> TopicMemory:
    return TopicMemory(
        topic=head.topic,
        last_study=head.last_study,
        sessions=[outcome_row_to_domain(row) for row in outcomes],
        mastery={row.knowledge_point: row.score for row in mastery},
        next_focus=[row.point for row in focus],
        source_doc_hash=head.source_doc_hash
    )


# 由主题记忆生成头部行
def memory_to_head(memory: TopicMemory, created_at: DateTimeValue) -> TopicMemoryRow:
    return TopicMemoryRow(
        topic_key=topic_key_of(memory.topic),
        topic=memory.topic,
        last_study=memory.last_study,
        source_doc_hash=memory.source_doc_hash,
        created_at=created_at
    )


# 由学习归档生成归档行
def outcome_to_row(topic_key: str, outcome: SessionOutcome) -> SessionOutcomeRow:
    return SessionOutcomeRow(
        session_id=outcome.session_id,
        topic_key=topic_key,
        date=outcome.date,
        score=outcome.score,
        comment=outcome.comment,
        report=outcome.report,
        doubts_json=dump_models(list(outcome.doubts)),
        mistakes_json=dump_models(list(outcome.mistakes))
    )


# 由归档行还原学习归档
def outcome_row_to_domain(row: SessionOutcomeRow) -> SessionOutcome:
    return SessionOutcome(
        session_id=row.session_id,
        date=row.date,
        score=row.score,
        comment=row.comment,
        report=row.report,
        doubts=[Doubt.model_validate(item) for item in load_records(row.doubts_json)],
        mistakes=[Mistake.model_validate(item) for item in load_records(row.mistakes_json)]
    )


# 由会话生成会话行
def session_to_row(record: SessionRecord) -> StudySessionRow:
    return StudySessionRow(
        session_id=record.session_id,
        topic_key=topic_key_of(record.topic),
        phase=str(record.phase),
        current_section_index=record.current_section_index,
        source_doc=record.source_doc,
        source_doc_hash=record.source_doc_hash,
        user_request=record.user_request,
        outline_json=record.outline.model_dump_json(),
        doubts_json=dump_models(list(record.doubts)),
        mistakes_json=dump_models(list(record.mistakes)),
        quiz_json=None if record.quiz is None else record.quiz.model_dump_json(),
        created_at=record.created_at,
        updated_at=record.updated_at
    )


# 由会话行还原会话
def session_row_to_domain(row: StudySessionRow) -> SessionRecord:
    outline = Outline.model_validate_json(row.outline_json)
    return SessionRecord(
        session_id=row.session_id,
        topic=outline.topic,
        phase=LearningPhase(row.phase),
        current_section_index=row.current_section_index,
        source_doc=row.source_doc,
        source_doc_hash=row.source_doc_hash,
        user_request=row.user_request,
        outline=outline,
        doubts=[Doubt.model_validate(item) for item in load_records(row.doubts_json)],
        mistakes=[Mistake.model_validate(item) for item in load_records(row.mistakes_json)],
        quiz=None if row.quiz_json is None else QuizOutcome.model_validate_json(row.quiz_json),
        created_at=row.created_at,
        updated_at=row.updated_at
    )


# 由产物生成产物行
def material_to_row(record: MaterialRecord) -> StudyMaterialRow:
    return StudyMaterialRow(
        session_id=record.session_id,
        section_index=record.section_index,
        artifact_kind=str(record.artifact_kind),
        status=str(record.status),
        content_json=record.content,
        attempts=record.attempts,
        updated_at=record.updated_at
    )


# 由产物行还原产物
def material_row_to_domain(row: StudyMaterialRow) -> MaterialRecord:
    return MaterialRecord(
        session_id=row.session_id,
        section_index=row.section_index,
        artifact_kind=ArtifactKind(row.artifact_kind),
        status=ArtifactStatus(row.status),
        content=row.content_json,
        attempts=row.attempts,
        updated_at=row.updated_at
    )


# 由调用记录生成调用行，超长请求正文会被截断
def llm_call_to_row(record: LlmCallRecord) -> LlmCallRow:
    return LlmCallRow(
        session_id=record.session_id,
        agent=record.agent,
        provider=record.provider,
        model=record.model,
        structured_mode=None if record.structured_mode is None else str(record.structured_mode),
        degraded_from=record.degraded_from,
        latency_ms=record.latency_ms,
        prompt_chars=record.prompt_chars,
        completion_chars=record.completion_chars,
        prompt_fingerprint=record.prompt_fingerprint,
        request_json=None if record.request_json is None else record.request_json[:REQUEST_JSON_LIMIT],
        ok=record.ok,
        error=record.error,
        created_at=record.created_at
    )


# 由校验记录生成校验行
def verification_log_to_row(record: VerificationLogRecord) -> VerificationLogRow:
    return VerificationLogRow(
        session_id=record.session_id,
        artifact_ref=record.artifact_ref,
        artifact_kind=str(record.artifact_kind),
        attempt=record.attempt,
        source=str(record.source),
        passed=record.passed,
        errors_json=dump_models(list(record.errors)),
        created_at=record.created_at
    )


# 可用于执行 pragma 的游标
class Cursor(Protocol):
    # 执行一条语句
    def execute(self, statement: str) -> object: ...

    # 关闭游标
    def close(self) -> None: ...


# 可用于配置的底层连接
class DbapiConnection(Protocol):
    # 打开一个游标
    def cursor(self) -> Cursor: ...


# 建立连接后开启外键、wal与折中的同步策略
def configure_connection(dbapi_connection: DbapiConnection, connection_record: ConnectionPoolEntry) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute('PRAGMA foreign_keys=ON')
    cursor.execute('PRAGMA journal_mode=WAL')
    cursor.execute('PRAGMA synchronous=NORMAL')
    cursor.close()


# 持有引擎并在工作线程中执行事务
class Database:
    # 建立引擎、连接钩子与会话工厂
    def __init__(self, *, settings: DatabaseSettings, path: Path) -> None:
        self._settings = settings
        path.parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_engine(
            f'sqlite:///{path.as_posix()}',
            echo=settings.echo,
            future=True,
            connect_args={
                'check_same_thread': False,
                'timeout': settings.connect_timeout_seconds
            }
        )
        event.listen(self._engine, 'connect', configure_connection)
        self._session_factory = sessionmaker(bind=self._engine, class_=Session, expire_on_commit=False)

    # sqlalchemy引擎
    @property
    def engine(self) -> Engine:
        return self._engine

    # 按行模型建表
    def create_all(self) -> None:
        SQLModel.metadata.create_all(self._engine)

    # 把同步读写交给工作线程执行并提交
    async def run(self, operation: Callable[[Session], T]) -> T:
        return await to_thread.run_sync(self._run_sync, operation)

    # 在工作线程中用独立会话执行操作并提交
    def _run_sync(self, operation: Callable[[Session], T]) -> T:
        with self._session_factory() as session:
            result = operation(session)
            session.commit()
            return result

    # 关闭引擎并释放连接
    def dispose(self) -> None:
        self._engine.dispose()


# 读写主题记忆及其明细表
class SqlMemoryRepository:
    # 保存数据库
    def __init__(self, database: Database) -> None:
        self._database = database

    # 按主题取记忆
    async def get(self, topic: str) -> TopicMemory | None:
        return await self._database.run(lambda session: self._load(session, topic))

    # 按资料指纹反查记忆
    async def find_by_doc_hash(self, source_doc_hash: str) -> TopicMemory | None:
        return await self._database.run(lambda session: self._find_by_hash(session, source_doc_hash))

    # 写入记忆并同步全部明细表
    async def upsert(self, memory: TopicMemory) -> None:
        await self._database.run(lambda session: self._store(session, memory))

    # 在事务内按资料指纹反查记忆
    def _find_by_hash(self, session: Session, source_doc_hash: str) -> TopicMemory | None:
        if not source_doc_hash:
            return None
        head = session.exec(
            select(TopicMemoryRow)
            .where(TopicMemoryRow.source_doc_hash == source_doc_hash)
            .order_by(TopicMemoryRow.last_study.desc())
        ).first()
        if head is None:
            return None
        return self._assemble(session, head)

    # 在事务内按主题加载记忆
    def _load(self, session: Session, topic: str) -> TopicMemory | None:
        head = session.get(TopicMemoryRow, topic_key_of(topic))
        if head is None:
            return None
        return self._assemble(session, head)

    # 由头部行与明细表拼出记忆
    def _assemble(self, session: Session, head: TopicMemoryRow) -> TopicMemory:
        key = head.topic_key
        outcomes = session.exec(
            select(SessionOutcomeRow)
            .where(SessionOutcomeRow.topic_key == key)
            .order_by(SessionOutcomeRow.date, SessionOutcomeRow.session_id)
        ).all()
        mastery = session.exec(
            select(MasteryRow).where(MasteryRow.topic_key == key).order_by(MasteryRow.knowledge_point)
        ).all()
        focus = session.exec(
            select(NextFocusRow).where(NextFocusRow.topic_key == key).order_by(NextFocusRow.ordinal)
        ).all()
        return assemble_memory(head, list(outcomes), list(mastery), list(focus))

    # 在事务内写入头部与全部明细
    def _store(self, session: Session, memory: TopicMemory) -> None:
        key = topic_key_of(memory.topic)
        logger.debug(
            '写入记忆 | topic={!r} key={} | 归档 {} 次 掌握度 {} 项 重点 {} 项',
            memory.topic,
            key,
            len(memory.sessions),
            len(memory.mastery),
            len(memory.next_focus)
        )
        head = memory_to_head(memory, DateTimeValue.now())
        session.execute(
            _upsert(
                TopicMemoryRow,
                head.model_dump(),
                ('topic_key',),
                ('topic', 'last_study', 'source_doc_hash')
            )
        )
        self._store_outcomes(session, key, memory.sessions)
        self._store_mastery(session, key, memory.mastery)
        self._store_focus(session, key, memory.next_focus)

    # 同步归档表并删去其中已不存在的归档
    def _store_outcomes(self, session: Session, key: str, outcomes: list[SessionOutcome]) -> None:
        existing = session.exec(select(SessionOutcomeRow).where(SessionOutcomeRow.topic_key == key)).all()
        incoming = {outcome.session_id for outcome in outcomes}
        for row in existing:
            if row.session_id not in incoming:
                session.delete(row)
        for outcome in outcomes:
            row = outcome_to_row(key, outcome)
            session.execute(
                _upsert(
                    SessionOutcomeRow,
                    row.model_dump(),
                    ('session_id',),
                    ('topic_key', 'date', 'score', 'comment', 'report', 'doubts_json', 'mistakes_json')
                )
            )

    # 同步掌握度表并删去其中已不存在的知识点
    def _store_mastery(self, session: Session, key: str, mastery: dict[str, float]) -> None:
        existing = session.exec(select(MasteryRow).where(MasteryRow.topic_key == key)).all()
        for row in existing:
            if row.knowledge_point not in mastery:
                session.delete(row)
        for point, score in mastery.items():
            session.execute(
                _upsert(
                    MasteryRow,
                    {'topic_key': key, 'knowledge_point': point, 'score': score},
                    ('topic_key', 'knowledge_point'),
                    ('score',)
                )
            )

    # 重写复习重点表
    def _store_focus(self, session: Session, key: str, points: list[str]) -> None:
        session.execute(
            delete(NextFocusRow)
            .where(NextFocusRow.topic_key == key)
            .where(NextFocusRow.ordinal >= len(points))
        )
        for ordinal, point in enumerate(points):
            session.execute(
                _upsert(
                    NextFocusRow,
                    {'topic_key': key, 'ordinal': ordinal, 'point': point},
                    ('topic_key', 'ordinal'),
                    ('point',)
                )
            )


# 读写会话及其产物
class SqlSessionRepository:
    # 保存数据库
    def __init__(self, database: Database) -> None:
        self._database = database

    # 新建会话
    async def create(self, record: SessionRecord) -> None:
        logger.debug(
            '新建会话 | session={} 主题={!r} 阶段={}',
            record.session_id,
            record.topic,
            record.phase
        )
        await self._database.run(lambda session: session.add(session_to_row(record)))

    # 按会话id取会话
    async def get(self, session_id: str) -> SessionRecord | None:
        return await self._database.run(lambda session: self._load(session, session_id))

    # 覆盖保存会话
    async def save(self, record: SessionRecord) -> None:
        logger.debug(
            '更新会话 | session={} 阶段={} 分节={} 疑点={} 错题={}',
            record.session_id,
            record.phase,
            record.current_section_index,
            len(record.doubts),
            len(record.mistakes)
        )
        await self._database.run(lambda session: session.merge(session_to_row(record)))

    # 按会话、分节与类型覆盖保存产物
    async def save_material(self, record: MaterialRecord) -> None:
        logger.debug(
            '写入产物 | session={} 分节={} 类型={} 状态={} 轮次={} 内容 {} 字',
            record.session_id,
            record.section_index,
            record.artifact_kind,
            record.status,
            record.attempts,
            len(record.content)
        )
        await self._database.run(lambda session: self._store_material(session, record))

    # 取单份产物
    async def get_material(
        self,
        session_id: str,
        section_index: int,
        artifact_kind: ArtifactKind
    ) -> MaterialRecord | None:
        return await self._database.run(
            lambda session: self._load_material(session, session_id, section_index, artifact_kind)
        )

    # 列出会话的全部产物
    async def list_materials(self, session_id: str) -> list[MaterialRecord]:
        return await self._database.run(lambda session: self._load_materials(session, session_id))

    # 在事务内加载会话
    def _load(self, session: Session, session_id: str) -> SessionRecord | None:
        row = session.get(StudySessionRow, session_id)
        return None if row is None else session_row_to_domain(row)

    # 在事务内写入或更新产物
    def _store_material(self, session: Session, record: MaterialRecord) -> None:
        row = material_to_row(record)
        session.execute(
            _upsert(
                StudyMaterialRow,
                row.model_dump(),
                ('session_id', 'section_index', 'artifact_kind'),
                ('status', 'content_json', 'attempts', 'updated_at')
            )
        )

    # 在事务内取单份产物
    def _load_material(
        self,
        session: Session,
        session_id: str,
        section_index: int,
        artifact_kind: ArtifactKind
    ) -> MaterialRecord | None:
        row = session.exec(
            select(StudyMaterialRow)
            .where(StudyMaterialRow.session_id == session_id)
            .where(StudyMaterialRow.section_index == section_index)
            .where(StudyMaterialRow.artifact_kind == str(artifact_kind))
        ).first()
        return None if row is None else material_row_to_domain(row)

    # 在事务内列出全部产物
    def _load_materials(self, session: Session, session_id: str) -> list[MaterialRecord]:
        rows = session.exec(
            select(StudyMaterialRow)
            .where(StudyMaterialRow.session_id == session_id)
            .order_by(StudyMaterialRow.section_index, StudyMaterialRow.artifact_kind)
        ).all()
        return [material_row_to_domain(row) for row in rows]


# 写入模型调用与校验记录
class SqlObservabilityRepository:
    # 保存数据库
    def __init__(self, database: Database) -> None:
        self._database = database

    # 落一条模型调用记录
    async def record_llm_call(self, record: LlmCallRecord) -> None:
        await self._database.run(lambda session: session.add(llm_call_to_row(record)))

    # 落一条校验记录
    async def record_verification(self, record: VerificationLogRecord) -> None:
        await self._database.run(lambda session: session.add(verification_log_to_row(record)))


# 以比较并交换的方式读写会话阶段
class SqlLearningStateStore:
    # 保存数据库
    def __init__(self, database: Database) -> None:
        self._database = database

    # 读取会话当前阶段
    async def read(self, session_id: str) -> LearningPhase | None:
        return await self._database.run(lambda session: self._read(session, session_id))

    # 按预期阶段原子迁移
    async def transition(
        self,
        session_id: str,
        *,
        expected: LearningPhase,
        to: LearningPhase
    ) -> LearningPhase:
        return await self._database.run(
            lambda session: self._transition(session, session_id, expected, to)
        )

    # 写入当前分节下标
    async def set_section_index(self, session_id: str, index: int) -> None:
        await self._database.run(lambda session: self._write_index(session, session_id, index))

    # 在事务内读取阶段
    def _read(self, session: Session, session_id: str) -> LearningPhase | None:
        row = session.get(StudySessionRow, session_id)
        return None if row is None else LearningPhase(row.phase)

    # 在事务内写入分节下标
    def _write_index(self, session: Session, session_id: str, index: int) -> None:
        row = session.get(StudySessionRow, session_id)
        if row is None:
            raise SessionNotFound('会话不存在，无法写入分节下标', session_id=session_id)
        row.current_section_index = index
        row.updated_at = DateTimeValue.now()
        session.add(row)

    # 在事务内以带条件的更新完成迁移
    def _transition(
        self,
        session: Session,
        session_id: str,
        expected: LearningPhase,
        to: LearningPhase
    ) -> LearningPhase:
        if expected is to:
            row = session.get(StudySessionRow, session_id)
            if row is None:
                raise SessionNotFound('会话不存在', session_id=session_id)
            raise AlreadyCompleted(
                '目标状态与调用方预期相同，无需迁移',
                session_id=session_id,
                current_phase=row.phase
            )
        assert_transition(expected, to)
        changed = session.exec(
            update(StudySessionRow)
            .where(StudySessionRow.session_id == session_id)
            .where(StudySessionRow.phase == str(expected))
            .values(phase=str(to), updated_at=DateTimeValue.now())
        )
        if changed.rowcount == 1:
            logger.debug('状态迁移成功 | session={} {} -> {}', session_id, expected, to)
            return to
        logger.warning(
            '状态迁移未命中（会话不存在或阶段已变更）| session={} 预期={} 目标={}',
            session_id,
            expected,
            to
        )
        raise self._rejection(session, session_id, expected, to)

    # 迁移未命中时按实际状态给出具体异常
    def _rejection(
        self,
        session: Session,
        session_id: str,
        expected: LearningPhase,
        to: LearningPhase
    ) -> DomainError:
        row = session.get(StudySessionRow, session_id)
        if row is None:
            return SessionNotFound('会话不存在', session_id=session_id)
        current = LearningPhase(row.phase)
        if current is to:
            return AlreadyCompleted(
                '会话已处于目标状态，并发请求中已有一次迁移成功',
                session_id=session_id,
                current_phase=str(current)
            )
        return IllegalTransition(
            '会话实际状态与调用方预期不一致，可能已被并发变更',
            session_id=session_id,
            expected=str(expected),
            actual=str(current)
        )
