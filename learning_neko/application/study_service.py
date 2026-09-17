# 学习流程的门面：记忆上下文组装、产物读取与全部用例的编排
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import cast
from uuid import uuid4

from loguru import logger
from pydantic import BaseModel, ConfigDict

from learning_neko.agents import (
    AgentRegistry,
    CheckInput,
    ExerciseGenInput,
    GradeInput,
    MaterialInput,
    OutlineInput,
    QaInput,
    SummaryInput
)
from learning_neko.application.decorators import guarded, verified
from learning_neko.application.verification import VerifyLoop
from learning_neko.domain.errors import AlreadyCompleted, ArtifactNotFound, SessionNotFound
from learning_neko.domain.models import dump_models
from learning_neko.domain.models.enums import AgentKind, AgentMode, ArtifactKind, ExerciseStage, LearningPhase
from learning_neko.domain.models.learning import (
    AnswerSubmission,
    CheckResult,
    CheckResultItem,
    Exercise,
    ExerciseSet,
    GradeResult,
    GradeResultItem,
    MaterialBundle,
    Outline,
    QaAnswer
)
from learning_neko.domain.models.memory import (
    Doubt,
    MaterialRecord,
    Mistake,
    QuizOutcome,
    SessionOutcome,
    SessionRecord,
    SummaryOutput,
    TopicMemory
)
from learning_neko.domain.state import allowed_actions, assert_action_allowed
from learning_neko.ports.assets import AssetCatalogPort
from learning_neko.ports.llm import AgentResult
from learning_neko.ports.persistence import LearningStateStorePort, MemoryRepositoryPort, SessionRepositoryPort

EXCERPT_LIMIT = 6000


# 算出资料正文的sha256
def hash_source_document(source_doc: str) -> str:
    return hashlib.sha256(source_doc.encode('utf-8')).hexdigest()


# 把大纲渲染成供提示词使用的文本
def render_outline(outline: Outline) -> str:
    lines = [f'主题：{outline.topic}｜学习者水平：{outline.learner_level}', f"核心知识点：{'、'.join(outline.key_points)}"]
    for index, section in enumerate(outline.outline, start=1):
        lines.append(f"{index}. {section.title}｜目标：{section.goal}｜概念：{'、'.join(section.keywords)}")

    return '\n'.join(lines)


# 从产物列表里取出已生成过资料的分节下标，升序去重
def generated_sections_of(materials: Sequence[MaterialRecord]) -> list[int]:
    return sorted({
        item.section_index
        for item in materials
        if item.artifact_kind is ArtifactKind.MATERIAL
    })


# 由会话的测验结果算出得分文本
def score_of(record: SessionRecord) -> str:
    if record.quiz is None:
        return ''

    return f'{record.quiz.correct} / {record.quiz.total}'


# 一次学习开始时的记忆命中情况
class MemoryContext(BaseModel):
    model_config = ConfigDict(extra='forbid')

    topic: str
    is_first_time: bool
    same_material_as_last: bool
    weak_points: list[str] = []
    study_count: int = 0


# 由复习重点与低掌握度知识点合成薄弱点
def derive_weak_points(memory: TopicMemory, threshold: float, limit: int) -> list[str]:
    ordered = list(memory.next_focus)
    low_mastery = sorted(
        (item for item in memory.mastery.items() if item[1] < threshold),
        key=lambda item: item[1]
    )

    ordered.extend(point for point, _ in low_mastery)
    unique: list[str] = []

    for point in ordered:
        if point and point not in unique:
            unique.append(point)

    return unique[:limit]


# 把最近若干次学习归档渲染成文本
def render_history(memory: TopicMemory | None, limit: int) -> str:
    if memory is None or not memory.sessions:
        return ''

    return dump_models(list(memory.sessions[-limit:]))


# 按资料指纹解析历史记忆并生成上下文
class ContextBuilder:
    # 设置薄弱点判定阈值与各类条数上限
    def __init__(
        self,
        *,
        memory: MemoryRepositoryPort,
        weak_mastery_threshold: float = 0.6,
        history_limit: int = 3,
        weak_point_limit: int = 6
    ) -> None:
        self._memory = memory
        self._weak_mastery_threshold = weak_mastery_threshold
        self._history_limit = history_limit
        self._weak_point_limit = weak_point_limit

    # 按资料指纹命中记忆并生成记忆上下文
    async def resolve(self, source_doc_hash: str) -> tuple[TopicMemory | None, MemoryContext]:
        found = await self._memory.find_by_doc_hash(source_doc_hash)

        if found is None:
            return None, MemoryContext(topic='', is_first_time=True, same_material_as_last=False)

        return found, MemoryContext(
            topic=found.topic,
            is_first_time=not found.sessions,
            same_material_as_last=found.source_doc_hash == source_doc_hash,
            weak_points=derive_weak_points(found, self._weak_mastery_threshold, self._weak_point_limit),
            study_count=len(found.sessions)
        )

    # 渲染供提示词使用的历史归档文本
    def history_text(self, memory: TopicMemory | None) -> str:
        return render_history(memory, self._history_limit)


# 读取会话产物并转换成领域模型
class ArtifactReader:
    # 保存会话产物仓储
    def __init__(self, *, sessions: SessionRepositoryPort) -> None:
        self._sessions = sessions

    # 取指定分节的学习资料，缺失时报错
    async def material_bundle(self, record: SessionRecord, section_index: int) -> MaterialBundle:
        stored = await self._sessions.get_material(record.session_id, section_index, ArtifactKind.MATERIAL)
        if stored is None:
            raise ArtifactNotFound(
                '该分节尚未生成学习资料，请先生成后再继续',
                session_id=record.session_id,
                section_index=section_index,
                artifact_kind=str(ArtifactKind.MATERIAL)
            )

        return MaterialBundle.model_validate_json(stored.content)

    # 取课后测验，缺失时返回空
    async def find_exercise_set(self, record: SessionRecord) -> ExerciseSet | None:
        stored = await self._sessions.get_material(record.session_id, 0, ArtifactKind.EXERCISES)
        return None if stored is None else ExerciseSet.model_validate_json(stored.content)

    # 取课后测验，缺失时报错
    async def exercise_set(self, record: SessionRecord) -> ExerciseSet:
        found = await self.find_exercise_set(record)
        if found is None:
            raise ArtifactNotFound(
                '该会话尚未生成课后测验，请先出题再提交作答',
                session_id=record.session_id,
                artifact_kind=str(ArtifactKind.EXERCISES)
            )

        return found

    # 取资料正文的前若干字符供答疑使用
    async def material_excerpt(self, record: SessionRecord, section_index: int) -> str:
        bundle = await self.material_bundle(record, section_index)
        return bundle.material[:EXCERPT_LIMIT]


# 学习流程全部用例的统一入口
class StudyService:
    # 保存用例运行所需的全部依赖
    def __init__(
        self,
        *,
        agents: AgentRegistry,
        sessions: SessionRepositoryPort,
        memory: MemoryRepositoryPort,
        state: LearningStateStorePort,
        context: ContextBuilder,
        artifacts: ArtifactReader,
        verify_loop: VerifyLoop,
        assets: AssetCatalogPort
    ) -> None:
        self._agents = agents
        self._sessions = sessions
        self._memory = memory
        self._state = state
        self._context = context
        self._artifacts = artifacts
        self._verify_loop = verify_loop
        self._assets = assets

    # 该服务使用的校验循环
    @property
    def verify_loop(self) -> VerifyLoop:
        return self._verify_loop

    # 按会话id取会话，缺失时报错
    async def load_session(self, session_id: str) -> SessionRecord:
        record = await self._sessions.get(session_id)
        if record is None:
            logger.warning('会话不存在 | session={}', session_id)
            raise SessionNotFound('会话不存在或已被清理', session_id=session_id)

        return record

    # 解析记忆并生成大纲后创建学习会话
    async def start_session(self, source_doc: str, user_request: str) -> tuple[SessionRecord, MemoryContext]:
        document = source_doc.strip()
        request = user_request.strip()
        document_hash = hash_source_document(document)

        logger.info(
            '收到开始学习请求 | 资料 {} 字 | 需求 {!r} | 资料指纹 {}',
            len(document),
            request,
            document_hash[:12]
        )

        history_memory, memory_context = await self._context.resolve(document_hash)

        logger.debug(
            '历史记忆命中情况 | 首次={} 同资料={} 已学 {} 次 | 薄弱点 {}',
            memory_context.is_first_time,
            memory_context.same_material_as_last,
            memory_context.study_count,
            memory_context.weak_points
        )

        outcome = await self._draft_outline(
            OutlineInput(
                source_doc=document,
                user_request=request,
                is_first_time=memory_context.is_first_time,
                history=self._context.history_text(history_memory),
                weak_points=memory_context.weak_points
            )
        )
        outline = cast(Outline, outcome.result.output)
        now = datetime.now()
        record = SessionRecord(
            session_id=uuid4().hex,
            topic=outline.topic,
            phase=LearningPhase.LEARNING,
            current_section_index=0,
            source_doc=document,
            source_doc_hash=document_hash,
            user_request=request,
            outline=outline,
            created_at=now,
            updated_at=now
        )
        await self._sessions.create(record)

        logger.success(
            '学习会话已创建 | session={} | 主题 {!r} | 分节 {} 个 | 水平 {}',
            record.session_id,
            outline.topic,
            len(outline.outline),
            outline.learner_level
        )

        for index, item in enumerate(outline.outline):
            logger.debug('分节[{}] {} | 概念 {}', index, item.title, item.keywords)

        return record, memory_context

    # 取会话快照与全部产物
    async def snapshot(self, session_id: str) -> tuple[SessionRecord, list[MaterialRecord]]:
        logger.info('读取会话快照 | session={}', session_id)
        record = await self.load_session(session_id)
        materials = await self._sessions.list_materials(session_id)

        logger.debug(
            '快照内容 | 阶段={} 当前分节={} 疑点={} 条 错题={} 条 产物={} 件',
            record.phase,
            record.current_section_index,
            len(record.doubts),
            len(record.mistakes),
            len(materials)
        )

        for item in materials:
            logger.debug(
                '产物 session={} 分节={} 类型={} 状态={} 轮次={}',
                item.session_id,
                item.section_index,
                item.artifact_kind,
                item.status,
                item.attempts
            )

        return record, materials

    # 列出已生成过资料的分节下标，供前端展示学习进度
    async def generated_sections(self, session_id: str) -> list[int]:
        materials = await self._sessions.list_materials(session_id)
        return generated_sections_of(materials)

    # 按最近变更时间倒序列出会话，供前端发现既有会话
    async def list_recent_sessions(self, limit: int) -> list[SessionRecord]:
        records = await self._sessions.list_recent(limit)
        logger.info('列出最近会话 | 命中 {} 条（上限 {}）', len(records), limit)
        return records

    # 按主题取记忆
    async def memory_of(self, topic: str) -> TopicMemory | None:
        return await self._memory.get(topic)

    # 读取某分节已生成的材料，缺失时报错
    async def read_material(self, session_id: str, section_index: int) -> MaterialRecord:
        record = await self.load_session(session_id)
        stored = await self._sessions.get_material(record.session_id, section_index, ArtifactKind.MATERIAL)
        if stored is None:
            raise ArtifactNotFound(
                '该分节尚未生成学习资料',
                session_id=session_id,
                section_index=section_index
            )

        return stored

    # 生成指定分节的资料、例题与关系图并落库
    @guarded('generate_material')
    async def generate_material(self, record: SessionRecord, section_index: int) -> tuple[SessionRecord, MaterialRecord]:
        section = record.section_at(section_index)

        logger.info(
            '开始生成学习资料 | session={} | 分节[{}] {!r} | 概念 {}',
            record.session_id,
            section_index,
            section.title,
            section.keywords
        )

        outcome = await self._draft_material(
            MaterialInput(
                topic=record.topic,
                learner_level=record.outline.learner_level,
                section_title=section.title,
                section_goal=section.goal,
                section_keywords=section.keywords,
                gen_instruction=record.outline.gen_instruction,
                source_doc=record.source_doc,
                available_assets=self._assets.describe_for_prompt()
            ),
            session_id=record.session_id
        )
        stored = MaterialRecord(
            session_id=record.session_id,
            section_index=section_index,
            artifact_kind=ArtifactKind.MATERIAL,
            status=outcome.status,
            content=cast(MaterialBundle, outcome.result.output).model_dump_json(),
            attempts=outcome.attempts,
            updated_at=datetime.now()
        )
        await self._sessions.save_material(stored)
        await self._state.set_section_index(record.session_id, section_index)
        bundle = cast(MaterialBundle, outcome.result.output)

        logger.success(
            '学习资料已生成 | session={} | 分节[{}] | 状态={} 轮次={} | 正文 {} 字 例题 {} 道 图 {} 节点/{} 边',
            record.session_id,
            section_index,
            outcome.status,
            outcome.attempts,
            len(bundle.material),
            len(bundle.examples),
            len(bundle.visualization.nodes),
            len(bundle.visualization.edges)
        )

        return record, stored

    # 解答学习者提问并把疑点记进会话
    @guarded('qa')
    async def ask(self, record: SessionRecord, question: str) -> tuple[SessionRecord, QaAnswer]:
        section_index = record.current_section_index
        section = record.section_at(section_index)
        logger.info(
            '收到答疑请求 | session={} | 分节[{}] {!r} | 问题 {!r}',
            record.session_id,
            section_index,
            section.title,
            question.strip()[:80]
        )

        result = await self._agents.build(AgentKind.QA).run(
            QaInput(
                topic=record.topic,
                section_title=section.title,
                material_excerpt=await self._artifacts.material_excerpt(record, section_index),
                question=question.strip()
            ),
            session_id=record.session_id
        )

        answer = cast(QaAnswer, result.output)
        record.doubts.append(
            Doubt(
                point=answer.record.point,
                question=answer.record.question,
                resolved=answer.record.explained
            )
        )

        record.updated_at = datetime.now()
        await self._sessions.save(record)

        logger.success(
            '答疑完成 | session={} | 疑点 {!r} | 已解释={} | 疑点累计 {} 条',
            record.session_id,
            answer.record.point,
            answer.record.explained,
            len(record.doubts)
        )

        return record, answer

    # 判定例题作答并把错题记进会话
    @guarded('check_example')
    async def check_examples(
        self,
        record: SessionRecord,
        answers: list[AnswerSubmission]
    ) -> tuple[SessionRecord, CheckResult, int]:
        section_index = record.current_section_index

        logger.info(
            '收到例题作答 | session={} | 分节[{}] | 作答 {} 题',
            record.session_id,
            section_index,
            len(answers)
        )

        bundle = await self._artifacts.material_bundle(record, section_index)
        known = {item.q_id: item for item in bundle.examples}

        logger.debug(
            '例题池 {} 道 | 学习者的题号 {}',
            len(bundle.examples),
            [item.q_id for item in answers]
        )

        result = await self._agents.build(AgentKind.CHECK, AgentMode.PROCESS).run(
            CheckInput(
                topic=record.topic,
                section_title=record.section_at(section_index).title,
                exercises=json.dumps([item.model_dump(mode='json') for item in bundle.examples], ensure_ascii=False),
                answers=json.dumps([item.model_dump(mode='json') for item in answers], ensure_ascii=False)
            ),
            session_id=record.session_id
        )

        verdicts = cast(CheckResult, result.output)
        self._record_mistakes(record, verdicts.results, known, ExerciseStage.PROCESS)
        record.updated_at = datetime.now()
        await self._sessions.save(record)
        correct = sum(1 for item in verdicts.results if item.correct)

        logger.success(
            '例题判定完成 | session={} | 答对 {}/{} | 错题累计 {} 条',
            record.session_id,
            correct,
            len(bundle.examples),
            len(record.mistakes)
        )

        for item in verdicts.results:
            logger.debug('- {} {} | {}', item.q_id, '正确' if item.correct else '错误', item.reason[:60])

        return record, verdicts, len(bundle.examples)

    # 把会话阶段由学习切到已完成
    async def complete(self, session_id: str) -> SessionRecord:
        logger.info('收到状态切换请求 | session={} | 当前阶段={}', session_id, '未知')
        record = await self.load_session(session_id)
        logger.debug('会话当前阶段={} | 疑点 {} 条 错题 {} 条', record.phase, len(record.doubts), len(record.mistakes))

        if record.phase is LearningPhase.COMPLETED:
            logger.warning('会话已完成学习，拒绝重复迁移 | session={}', session_id)
            raise AlreadyCompleted(
                '该会话已完成学习，状态切换不可重复执行',
                session_id=session_id,
                current_phase=str(record.phase),
                allowed_actions=list(allowed_actions(record.phase))
            )

        assert_action_allowed(record.phase, 'complete')
        phase = await self._state.transition(
            record.session_id,
            expected=record.phase,
            to=LearningPhase.COMPLETED
        )

        logger.success('已切换到学习完毕 | session={} | {} -> {}', record.session_id, record.phase, phase)
        return record.model_copy(update={'phase': phase, 'updated_at': datetime.now()})

    # 生成覆盖全章的课后测验
    @guarded('generate_exercises')
    async def generate_exercises(self, record: SessionRecord) -> tuple[SessionRecord, MaterialRecord]:
        logger.info(
            '开始生成课后测验 | session={} | 主题 {!r} | 概念 {} 个 | 薄弱点 {}',
            record.session_id,
            record.topic,
            len(self._outline_keywords(record.outline)),
            record.outline.key_points[:3]
        )

        outcome = await self._draft_exercises(
            ExerciseGenInput(
                topic=record.topic,
                learner_level=record.outline.learner_level,
                outline=render_outline(record.outline),
                knowledge_points=self._outline_keywords(record.outline),
                source_doc=record.source_doc,
                available_assets=self._assets.describe_for_prompt()
            ),
            session_id=record.session_id
        )
        stored = MaterialRecord(
            session_id=record.session_id,
            section_index=0,
            artifact_kind=ArtifactKind.EXERCISES,
            status=outcome.status,
            content=cast(ExerciseSet, outcome.result.output).model_dump_json(),
            attempts=outcome.attempts,
            updated_at=datetime.now()
        )
        await self._sessions.save_material(stored)
        exercise_set = cast(ExerciseSet, outcome.result.output)

        logger.success(
            '课后测验已生成 | session={} | 题目 {} 道 | 状态={} 轮次={}',
            record.session_id,
            len(exercise_set.exercises),
            outcome.status,
            outcome.attempts
        )

        return record, stored

    # 读取已生成的课后习题
    async def read_exercises(self, session_id: str) -> MaterialRecord:
        record = await self.load_session(session_id)
        stored = await self._sessions.get_material(record.session_id, 0, ArtifactKind.EXERCISES)
        if stored is None:
            raise ArtifactNotFound(
                '该会话尚未生成课后测验，请先出题',
                session_id=session_id,
                artifact_kind=str(ArtifactKind.EXERCISES)
            )

        return stored

    # 批阅课后作答并记录得分与错题
    @guarded('grade_exercises')
    async def grade(
        self,
        record: SessionRecord,
        answers: list[AnswerSubmission]
    ) -> tuple[SessionRecord, GradeResult, int]:
        exercise_set = await self._artifacts.exercise_set(record)
        logger.info(
            '收到课后作答 | session={} | 作答 {} 题 / 题库 {} 题',
            record.session_id,
            len(answers),
            len(exercise_set.exercises)
        )

        known = {item.q_id: item for item in exercise_set.exercises}
        result = await self._agents.build(AgentKind.CHECK, AgentMode.POST).run(
            GradeInput(
                topic=record.topic,
                exercises=json.dumps(
                    [item.model_dump(mode='json') for item in exercise_set.exercises],
                    ensure_ascii=False
                ),
                answers=json.dumps([item.model_dump(mode='json') for item in answers], ensure_ascii=False)
            ),
            session_id=record.session_id
        )

        verdicts = cast(GradeResult, result.output)
        self._record_mistakes(record, verdicts.results, known, ExerciseStage.POST)
        total = len(exercise_set.exercises)
        record.quiz = QuizOutcome(
            total=total,
            correct=sum(1 for item in verdicts.results if item.correct)
        )
        record.updated_at = datetime.now()
        await self._sessions.save(record)

        logger.success(
            '课后批阅完成 | session={} | 答对 {}/{} | 得分 {!r}',
            record.session_id,
            record.quiz.correct,
            record.quiz.total,
            score_of(record)
        )

        for item in verdicts.results:
            logger.debug('   {} {} | 建议 {}', item.q_id, '正确' if item.correct else '错误', item.advice[:60])

        return record, verdicts, total

    # 生成学习报告并把本次结果并入记忆
    @guarded('summarize')
    async def summarize(self, record: SessionRecord) -> tuple[SessionRecord, str, dict[str, float], list[str]]:
        post_mistakes = [item for item in record.mistakes if item.stage is ExerciseStage.POST]
        logger.info(
            '开始总结归档 | session={} | 主题 {!r} | 疑点 {} 条 过程错题 {} 条 课后错题 {} 条',
            record.session_id,
            record.topic,
            len(record.doubts),
            len(record.mistakes) - len(post_mistakes),
            len(post_mistakes)
        )

        result = await self._agents.build(AgentKind.SUMMARY).run(
            SummaryInput(
                topic=record.topic,
                outline=render_outline(record.outline),
                quiz_summary=await self._quiz_summary(record, len(post_mistakes)),
                doubts=dump_models(record.doubts),
                process_mistakes=dump_models(
                    [item for item in record.mistakes if item.stage is ExerciseStage.PROCESS]
                ),
                post_mistakes=dump_models(post_mistakes)
            ),
            session_id=record.session_id
        )
        summary = self._with_computed_score(record, cast(SummaryOutput, result.output))
        await self._merge_into_memory(record, summary)

        logger.success(
            '已写入记忆 | 主题 {!r} | 得分 {!r} | 掌握度 {} 项 | 下次重点 {} 项',
            record.topic,
            summary.score,
            len(summary.mastery),
            len(summary.next_focus)
        )

        logger.debug('掌握度明细 | {}', summary.mastery)
        logger.debug('下次复习重点 | {}', summary.next_focus)
        logger.debug('学习报告前 200 字 | {}', summary.report[:200].replace(chr(10), ' '))
        return record, summary.report, summary.mastery, summary.next_focus

    # 带校验地生成大纲
    @verified(ArtifactKind.OUTLINE)
    async def _draft_outline(self, payload: OutlineInput) -> AgentResult:
        return await self._agents.build(AgentKind.OUTLINE).run(payload)

    # 带校验地生成学习资料
    @verified(ArtifactKind.MATERIAL)
    async def _draft_material(
        self,
        payload: MaterialInput,
        *,
        session_id: str | None = None
    ) -> AgentResult:
        return await self._agents.build(AgentKind.MATERIAL, AgentMode.PROCESS).run(
            payload,
            session_id=session_id
        )

    # 带校验地生成课后测验
    @verified(ArtifactKind.EXERCISES)
    async def _draft_exercises(
        self,
        payload: ExerciseGenInput,
        *,
        session_id: str | None = None
    ) -> AgentResult:
        return await self._agents.build(AgentKind.MATERIAL, AgentMode.POST).run(
            payload,
            session_id=session_id
        )

    # 汇总全章去重后的概念词
    def _outline_keywords(self, outline: Outline) -> list[str]:
        collected: list[str] = []
        for section in outline.outline:
            for keyword in section.keywords:
                if keyword not in collected:
                    collected.append(keyword)
        return collected

    # 把判定为错的题目记进会话错题
    def _record_mistakes(
        self,
        record: SessionRecord,
        results: Sequence[CheckResultItem | GradeResultItem],
        known: Mapping[str, Exercise],
        stage: ExerciseStage
    ) -> None:
        for item in results:
            if item.correct:
                continue
            source = known.get(item.q_id)
            record.mistakes.append(
                Mistake(
                    q_id=item.q_id,
                    knowledge_point='' if source is None else source.knowledge_point or '',
                    reason=item.reason,
                    stage=stage
                )
            )

    # 用系统算出的得分覆盖模型给出的得分
    def _with_computed_score(self, record: SessionRecord, summary: SummaryOutput) -> SummaryOutput:
        expected = score_of(record)
        if expected == summary.score:
            return summary
        logger.debug(
            '得分由系统计算覆盖模型输出 session={} 模型给出={!r} 实际应为={!r}',
            record.session_id,
            summary.score,
            expected
        )
        return summary.model_copy(update={'score': expected})

    # 描述本次课后测验的作答情况
    async def _quiz_summary(self, record: SessionRecord, wrong: int) -> str:
        if record.quiz is None:
            if await self._artifacts.find_exercise_set(record) is None:
                return '本次未进行课后测验，得分字段留空。'
            return '学习者尚未提交课后作答，得分字段留空。'
        return (
            f'课后测验共 {record.quiz.total} 题，答对 {record.quiz.correct} 题，答错 {wrong} 题，'
            f'得分应写成「{record.quiz.correct} / {record.quiz.total}」。'
        )

    # 把本次学习并入主题记忆
    async def _merge_into_memory(self, record: SessionRecord, summary: SummaryOutput) -> None:
        memory = await self._memory.get(record.topic)
        today = date.today()
        outcome = SessionOutcome(
            session_id=record.session_id,
            date=today,
            doubts=list(record.doubts),
            mistakes=list(record.mistakes),
            score=summary.score,
            comment=summary.comment,
            report=summary.report
        )
        merged = (
            self._fresh_memory(record, summary, outcome, today)
            if memory is None
            else self._append_outcome(memory, record, summary, outcome, today)
        )
        await self._memory.upsert(merged)

    # 为首次学习的主题构造记忆
    def _fresh_memory(
        self,
        record: SessionRecord,
        summary: SummaryOutput,
        outcome: SessionOutcome,
        today: date
    ) -> TopicMemory:
        return TopicMemory(
            topic=record.topic,
            last_study=today,
            sessions=[outcome],
            mastery=dict(summary.mastery),
            next_focus=list(summary.next_focus),
            source_doc_hash=record.source_doc_hash
        )

    # 把本次归档并入已有记忆并刷新掌握度
    def _append_outcome(
        self,
        memory: TopicMemory,
        record: SessionRecord,
        summary: SummaryOutput,
        outcome: SessionOutcome,
        today: date
    ) -> TopicMemory:
        kept = [item for item in memory.sessions if item.session_id != record.session_id]
        merged_sessions = sorted([*kept, outcome], key=lambda item: (item.date, item.session_id))
        return memory.model_copy(
            update={
                'last_study': today,
                'sessions': merged_sessions,
                'mastery': dict(summary.mastery),
                'next_focus': list(summary.next_focus),
                'source_doc_hash': record.source_doc_hash
            }
        )
