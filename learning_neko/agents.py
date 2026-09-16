# 智能体层：输入契约、统一管线实现与注册表
from __future__ import annotations

import json
from datetime import datetime
from time import perf_counter
from typing import Generic, Self, TypeVar

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from learning_neko.config import AgentRuntimeConfig, runtime_config_for
from learning_neko.domain.errors import ConfigurationError, DomainError, PromptTemplateError
from learning_neko.domain.models.enums import AgentKind, AgentMode, ArtifactKind, LearnerLevel
from learning_neko.domain.models.learning import (
    CheckResult,
    ExerciseSet,
    GradeResult,
    MaterialBundle,
    Outline,
    QaAnswer
)
from learning_neko.domain.models.memory import (
    LlmCallRecord,
    SummaryOutput,
    VerificationError,
    VerificationResult
)
from learning_neko.ports.llm import (
    AgentResult,
    AgentSpec,
    ChatModelPort,
    CompletionMeta,
    CompletionOptions,
    PromptRef,
    PromptRenderable,
    PromptRepositoryPort,
    RenderedPrompt
)
from learning_neko.ports.persistence import AgentObserverPort

Tin = TypeVar('Tin', bound=PromptRenderable)

ModeKey = tuple[AgentKind, AgentMode | None]


# 所有智能体输入的共同基类
class AgentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    # 把输入序列化为模板变量字典
    def to_prompt_vars(self) -> dict[str, object]:
        return self.model_dump(mode='json')


# 支持回灌上一轮校验错误的输入基类
class VerifiableInput(AgentInput):
    previous_errors: list[VerificationError] = Field(default_factory=list)

    # 复制自身并带上校验错误
    def with_previous_errors(self, errors: list[VerificationError]) -> Self:
        return self.model_copy(update={'previous_errors': errors})


# 分析大纲智能体的输入
class OutlineInput(VerifiableInput):
    source_doc: str
    user_request: str
    is_first_time: bool
    history: str = ''
    weak_points: list[str] = Field(default_factory=list)


# 资料生成智能体的输入
class MaterialInput(VerifiableInput):
    topic: str
    learner_level: LearnerLevel
    section_title: str
    section_goal: str
    section_keywords: list[str]
    gen_instruction: str
    source_doc: str
    available_assets: str


# 课后命题智能体的输入
class ExerciseGenInput(VerifiableInput):
    topic: str
    learner_level: LearnerLevel
    outline: str
    knowledge_points: list[str]
    source_doc: str
    available_assets: str


# 答疑智能体的输入
class QaInput(AgentInput):
    topic: str
    section_title: str
    material_excerpt: str
    question: str


# 例题检测智能体的输入
class CheckInput(AgentInput):
    topic: str
    section_title: str
    exercises: str
    answers: str


# 课后批阅智能体的输入
class GradeInput(AgentInput):
    topic: str
    exercises: str
    answers: str


# 校验智能体的输入
class VerifyInput(AgentInput):
    artifact_kind: ArtifactKind
    artifact: str
    basis: str
    criteria: str


# 总结归档智能体的输入
class SummaryInput(AgentInput):
    topic: str
    outline: str
    quiz_summary: str
    doubts: str
    process_mistakes: str
    post_mistakes: str


# 承载提示词渲染、模型调用、输出校验与可观测性的统一管线
class BaseStructuredAgent(Generic[Tin]):
    # 装配模型端口与提示词仓储并校验提示词契约
    def __init__(
        self,
        *,
        spec: AgentSpec,
        llm: ChatModelPort,
        prompts: PromptRepositoryPort,
        prompt_version: str,
        runtime: AgentRuntimeConfig,
        observer: AgentObserverPort | None = None
    ) -> None:
        self._spec = spec
        self._llm = llm
        self._prompts = prompts
        self._ref = PromptRef(version=prompt_version, name=spec.prompt_name)
        self._runtime = runtime
        self._observer = observer
        self._assert_prompt_contract()

    # 该智能体的静态声明
    @property
    def spec(self) -> AgentSpec:
        return self._spec

    # 该智能体的种类
    @property
    def kind(self) -> AgentKind:
        return self._spec.kind

    # 该智能体的阶段模式
    @property
    def mode(self) -> AgentMode | None:
        return self._spec.mode

    # 该智能体所用的提示词引用
    @property
    def prompt_ref(self) -> PromptRef:
        return self._ref

    # 渲染提示词并调用模型，落可观测性后返回结构化产物
    async def run(self, payload: PromptRenderable, *, session_id: str | None = None) -> AgentResult:
        rendered = self._prompts.render(self._ref, payload.to_prompt_vars())
        options = CompletionOptions(
            provider=self._runtime.provider,
            model=self._runtime.model,
            temperature=self._runtime.temperature,
            max_tokens=self._runtime.max_tokens,
            max_attempts=self._runtime.max_attempts
        )
        logger.info(
            '智能体开始 | agent={} mode={} prompt={} session={} | 变量 {} 项 system {} 字 user {} 字',
            self._spec.kind,
            self._spec.mode,
            self._ref.name,
            session_id or '-',
            len(payload.to_prompt_vars()),
            len(rendered.system),
            len(rendered.user)
        )
        started = perf_counter()
        try:
            completion = await self._llm.complete_structured(
                self._spec.output_model,
                system=rendered.system,
                user=rendered.user,
                options=options
            )
        except DomainError as exc:
            logger.error(
                '智能体失败 | agent={} mode={} session={} | {}',
                self._spec.kind,
                self._spec.mode,
                session_id or '-',
                exc
            )
            await self._observe(
                session_id=session_id,
                rendered=rendered,
                meta=None,
                latency_ms=(perf_counter() - started) * 1000.0,
                ok=False,
                error=f'{type(exc).__name__}: {exc}'
            )
            raise
        latency_ms = (perf_counter() - started) * 1000.0
        await self._observe(
            session_id=session_id,
            rendered=rendered,
            meta=completion.meta,
            latency_ms=latency_ms,
            ok=True,
            error=None
        )
        logger.success(
            '智能体完成 | agent={} mode={} prompt={} session={} | provider={} model={} 耗时 {:.0f}ms 输出 {} 字',
            self._spec.kind,
            self._spec.mode,
            self._ref.name,
            session_id or '-',
            completion.meta.provider,
            completion.meta.model,
            latency_ms,
            completion.meta.completion_chars
        )
        return AgentResult(
            agent=self._spec.kind,
            mode=self._spec.mode,
            output=completion.value,
            meta=completion.meta,
            prompt_fingerprint=rendered.fingerprint
        )

    # 把一次模型调用连同请求正文落进可观测性仓储
    async def _observe(
        self,
        *,
        session_id: str | None,
        rendered: RenderedPrompt,
        meta: CompletionMeta | None,
        latency_ms: float,
        ok: bool,
        error: str | None
    ) -> None:
        if self._observer is None:
            return
        await self._observer.record_llm_call(
            LlmCallRecord(
                session_id=session_id,
                agent=str(self._spec.kind),
                provider='-' if meta is None else meta.provider,
                model='-' if meta is None else meta.model,
                structured_mode=None if meta is None else meta.structured_mode,
                degraded_from=None if meta is None else meta.degraded_from,
                latency_ms=round(latency_ms, 3),
                prompt_chars=len(rendered.system) + len(rendered.user),
                completion_chars=0 if meta is None else meta.completion_chars,
                prompt_fingerprint=rendered.fingerprint,
                request_json=json.dumps(
                    {'system': rendered.system, 'user': rendered.user},
                    ensure_ascii=False
                ),
                ok=ok,
                error=error,
                created_at=datetime.now()
            )
        )

    # 断言提示词声明的变量与输入模型字段完全一致
    def _assert_prompt_contract(self) -> None:
        declared = self._prompts.declared_variables(self._ref)
        expected = set(self._spec.input_model.model_fields)
        if declared != expected:
            raise PromptTemplateError(
                '提示词声明的变量与该智能体的输入模型字段不一致',
                prompt=self._ref.name,
                version=self._ref.version,
                only_in_prompt=sorted(declared - expected),
                only_in_model=sorted(expected - declared)
            )
        logger.debug(
            '提示词契约校验通过 agent={} mode={} prompt={} 变量数={}',
            self._spec.kind,
            self._spec.mode,
            self._ref.name,
            len(declared)
        )


# 八个智能体的静态声明，把变化点收敛为数据
AGENT_SPECS: tuple[AgentSpec, ...] = (
    AgentSpec(kind=AgentKind.OUTLINE, prompt_name='analysis_outline_prompt', input_model=OutlineInput, output_model=Outline),
    AgentSpec(
        kind=AgentKind.MATERIAL,
        mode=AgentMode.PROCESS,
        prompt_name='material_prompt',
        input_model=MaterialInput,
        output_model=MaterialBundle
    ),
    AgentSpec(
        kind=AgentKind.MATERIAL,
        mode=AgentMode.POST,
        prompt_name='exercise_gen_prompt',
        input_model=ExerciseGenInput,
        output_model=ExerciseSet
    ),
    AgentSpec(kind=AgentKind.QA, prompt_name='qa_prompt', input_model=QaInput, output_model=QaAnswer),
    AgentSpec(
        kind=AgentKind.CHECK,
        mode=AgentMode.PROCESS,
        prompt_name='check_prompt',
        input_model=CheckInput,
        output_model=CheckResult
    ),
    AgentSpec(
        kind=AgentKind.CHECK,
        mode=AgentMode.POST,
        prompt_name='grade_prompt',
        input_model=GradeInput,
        output_model=GradeResult
    ),
    AgentSpec(kind=AgentKind.VERIFY, prompt_name='verify_prompt', input_model=VerifyInput, output_model=VerificationResult),
    AgentSpec(kind=AgentKind.SUMMARY, prompt_name='summary_prompt', input_model=SummaryInput, output_model=SummaryOutput)
)

AGENT_REGISTRY: dict[ModeKey, AgentSpec] = {(spec.kind, spec.mode): spec for spec in AGENT_SPECS}


# 按种类与模式取智能体声明
def spec_for(kind: AgentKind, mode: AgentMode | None = None) -> AgentSpec:
    spec = AGENT_REGISTRY.get((kind, mode))
    if spec is None:
        raise ConfigurationError(
            '未注册该智能体，请检查 AgentKind 与 AgentMode 的组合',
            kind=str(kind),
            mode=str(mode),
            available=[f'{item.kind}/{item.mode}' for item in AGENT_SPECS]
        )
    return spec


# 按声明装配并缓存智能体实例
class AgentRegistry:
    # 保存模型端口、提示词仓储与共享依赖
    def __init__(
        self,
        *,
        llm: ChatModelPort,
        prompts: PromptRepositoryPort,
        prompt_version: str,
        observer: AgentObserverPort | None = None
    ) -> None:
        self._llm = llm
        self._prompts = prompts
        self._prompt_version = prompt_version
        self._observer = observer
        self._cache: dict[ModeKey, BaseStructuredAgent] = {}

    # 取或构造指定种类与模式的智能体
    def build(self, kind: AgentKind, mode: AgentMode | None = None) -> BaseStructuredAgent:
        key = (kind, mode)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        agent = self.with_spec(spec_for(kind, mode))
        self._cache[key] = agent
        return agent

    # 按声明构造一个智能体实例
    def with_spec(self, spec: AgentSpec) -> BaseStructuredAgent:
        return BaseStructuredAgent(
            spec=spec,
            llm=self._llm,
            prompts=self._prompts,
            prompt_version=self._prompt_version,
            runtime=runtime_config_for(spec.kind),
            observer=self._observer
        )

    # 预装配全部智能体以在启动期暴露配置错误
    def preflight(self) -> tuple[str, ...]:
        return tuple(self.build(spec.kind, spec.mode).prompt_ref.name for spec in AGENT_SPECS)
