# 模型、提示词、智能体与校验四个边界的抽象契约
from __future__ import annotations

from typing import Generic, Protocol, runtime_checkable, Self, TypeVar

from pydantic import BaseModel, ConfigDict

from learning_neko.domain.models.enums import AgentKind, AgentMode, StructuredMode
from learning_neko.domain.models.memory import VerificationError

Tout = TypeVar('Tout', bound=BaseModel)


# 单次调用的可选覆盖参数，为空即沿用全局配置
class CompletionOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    max_attempts: int | None = None


# 单次调用的运行元数据，用于落库与排障
class CompletionMeta(BaseModel):
    provider: str
    model: str
    structured_mode: StructuredMode | None = None
    latency_ms: float
    prompt_chars: int
    completion_chars: int
    degraded_from: str | None = None


# 纯文本调用的返回结果
class TextCompletion(BaseModel):
    text: str
    meta: CompletionMeta


# 结构化调用的返回结果
class StructuredCompletion(BaseModel, Generic[Tout]):
    value: Tout
    meta: CompletionMeta


# 带有运行元数据的调用结果的共同形状
@runtime_checkable
class CompletionEnvelope(Protocol):
    meta: CompletionMeta

    # 复制结果并替换指定字段
    def model_copy(self, *, update: dict[str, object] | None = None) -> Self: ...


# 模型调用的唯一入口，屏蔽厂商与绑定方式差异
@runtime_checkable
class ChatModelPort(Protocol):
    # 发起一次纯文本补全
    async def complete_text(
        self,
        *,
        system: str,
        user: str,
        options: CompletionOptions | None = None
    ) -> TextCompletion: ...

    # 发起一次结构化补全并按给定schema校验结果
    async def complete_structured(
        self,
        schema: type[Tout],
        *,
        system: str,
        user: str,
        options: CompletionOptions | None = None
    ) -> StructuredCompletion[Tout]: ...


# 能产出提示词变量的输入契约
@runtime_checkable
class PromptRenderable(Protocol):
    # 输出模板所需的变量字典
    def to_prompt_vars(self) -> dict[str, object]: ...


# 可被校验且能回灌错误的输入契约
@runtime_checkable
class VerifiablePayload(PromptRenderable, Protocol):
    source_doc: str

    # 复制自身并带上上一轮校验错误
    def with_previous_errors(self, errors: list[VerificationError]) -> Self: ...


# 单个智能体的静态声明，把变化点收敛为数据
class AgentSpec(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    kind: AgentKind
    mode: AgentMode | None = None
    prompt_name: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]


# 智能体的结构化产物与本次调用元数据
class AgentResult(BaseModel, Generic[Tout]):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    agent: AgentKind
    mode: AgentMode | None = None
    output: Tout
    meta: CompletionMeta
    prompt_fingerprint: str


# 定位一份提示词的版本与文件名
class PromptRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

    version: str
    name: str


# 一次渲染后的角色切分结果
class RenderedPrompt(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

    ref: PromptRef
    system: str
    user: str
    fingerprint: str


# 提示词模板的读取与渲染入口
@runtime_checkable
class PromptRepositoryPort(Protocol):
    # 返回模板声明的全部变量名
    def declared_variables(self, ref: PromptRef) -> frozenset[str]: ...

    # 按变量渲染出系统消息与用户消息
    def render(self, ref: PromptRef, variables: dict[str, object]) -> RenderedPrompt: ...

    # 列出当前版本下可用的模板名
    def available(self) -> tuple[str, ...]: ...


# 调用校验智能体复核产物的入口
@runtime_checkable
class VerifierPort(Protocol):
    # 复核一个产物并返回判定
    async def run(self, payload: PromptRenderable, *, session_id: str | None = None) -> AgentResult: ...
