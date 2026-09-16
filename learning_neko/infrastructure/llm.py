# 模型调用实现：厂商声明、结构化输出、重试与故障转移
from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Generic, TypeVar

import httpx
import openai
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from loguru import logger
from pydantic import BaseModel, SecretStr, ValidationError

from learning_neko.config import LLMSettings, ProviderProfile, resolve_provider
from learning_neko.domain.errors import (
    ModelOutputInvalid,
    ProviderNotConfigured,
    ProviderUnavailable,
    ProviderUnusable
)
from learning_neko.domain.models.enums import StructuredMode
from learning_neko.ports.llm import (
    ChatModelPort,
    CompletionEnvelope,
    CompletionMeta,
    CompletionOptions,
    StructuredCompletion,
    TextCompletion
)

T = TypeVar('T')
TOut = TypeVar('TOut', bound=BaseModel)
TEnvelope = TypeVar('TEnvelope', bound=CompletionEnvelope)

RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.InternalServerError,
    httpx.TimeoutException,
    httpx.TransportError
)

TRANSLATABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    openai.OpenAIError,
    httpx.HTTPError
)

REPAIRABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    ValidationError,
    json.JSONDecodeError,
    OutputParserException,
    ValueError,
    ModelOutputInvalid
)

UNSUPPORTED_FORMAT_HINTS: tuple[str, ...] = ('response_format', 'json_schema', 'json schema')

ClientKey = tuple[str, str, float, int]


# 反复尝试直到成功或耗尽次数
async def run_with_retries(
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int,
    backoff_seconds: float
) -> T:
    attempt = 0
    while True:
        attempt += 1
        try:
            return await operation()
        except RETRYABLE_EXCEPTIONS as exc:
            if attempt >= max_attempts:
                raise
            delay = backoff_seconds * (2 ** (attempt - 1))
            logger.warning(
                '模型调用可重试失败 attempt={}/{} delay={:.2f}s reason={}',
                attempt,
                max_attempts,
                delay,
                type(exc).__name__
            )
            await asyncio.sleep(delay)


# 结构化调用的结果与所用绑定方式
class StructuredOutcome(BaseModel, Generic[TOut]):
    value: TOut
    mode: StructuredMode


# 判断异常是否因厂商不支持该响应格式
def unsupported_response_format(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(hint in message for hint in UNSUPPORTED_FORMAT_HINTS)


# 把结构化输出绑到客户端并处理降级与修复重试
class StructuredOutputAdapter:
    # 保存修复重试的追加次数
    def __init__(self, *, repair_attempts: int) -> None:
        self._repair_attempts = repair_attempts

    # 绑定结构化输出，厂商拒绝时降级绑定方式
    async def invoke(
        self,
        client: BaseChatModel,
        schema: type[TOut],
        messages: list[BaseMessage],
        mode: StructuredMode
    ) -> StructuredOutcome[TOut]:
        try:
            return await self._invoke_with(client, schema, messages, mode)
        except Exception as exc:
            if mode is not StructuredMode.JSON_SCHEMA or not unsupported_response_format(exc):
                raise
            logger.warning(
                '厂商声明支持 json_schema 但运行期拒绝，自动降级为 json_mode schema={} reason={}',
                schema.__name__,
                str(exc)[:200]
            )
            return await self._invoke_with(client, schema, messages, StructuredMode.JSON_MODE)

    # 按给定绑定方式调用，解析失败时追加修复消息重试
    async def _invoke_with(
        self,
        client: BaseChatModel,
        schema: type[TOut],
        messages: list[BaseMessage],
        mode: StructuredMode
    ) -> StructuredOutcome[TOut]:
        bound = self._bind(client, schema, mode)
        attempts = list(messages)
        attempt = 0
        while True:
            attempt += 1
            try:
                raw = await bound.ainvoke(attempts)
                return StructuredOutcome[TOut](value=self._coerce(raw, schema), mode=mode)
            except Exception as exc:
                if unsupported_response_format(exc) or not isinstance(exc, REPAIRABLE_EXCEPTIONS):
                    raise
                if attempt > self._repair_attempts:
                    raise ModelOutputInvalid(
                        '模型未能产出符合 schema 的结构化输出',
                        schema_name=schema.__name__,
                        structured_mode=str(mode),
                        attempts=attempt,
                        error=f'{type(exc).__name__}: {exc}'
                    ) from exc
                logger.warning(
                    '结构化输出解析失败，发起修复重试 attempt={} schema={} reason={}',
                    attempt,
                    schema.__name__,
                    type(exc).__name__
                )
                attempts = [*attempts, self._repair_message(schema, exc)]

    # 按绑定方式把schema绑到客户端
    def _bind(
        self,
        client: BaseChatModel,
        schema: type[TOut],
        mode: StructuredMode
    ) -> Runnable[list[BaseMessage], object]:
        if mode is StructuredMode.JSON_MODE:
            return client.with_structured_output(schema, method='json_mode', include_raw=False)
        if mode is StructuredMode.FUNCTION_CALLING:
            return client.with_structured_output(schema, method='function_calling', include_raw=False)
        return client.with_structured_output(schema, method='json_schema', strict=True, include_raw=False)

    # 把模型返回的原始结果归一到目标schema
    def _coerce(self, raw: object, schema: type[TOut]) -> TOut:
        if isinstance(raw, schema):
            return raw
        if isinstance(raw, dict):
            return schema.model_validate(raw)
        raise ModelOutputInvalid(
            '模型返回了无法归一到目标 schema 的结果',
            schema_name=schema.__name__,
            raw_type=type(raw).__name__
        )

    # 构造带解析错误与目标schema的修复消息
    def _repair_message(self, schema: type[BaseModel], exc: Exception) -> HumanMessage:
        contract = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        return HumanMessage(
            content=(
                '上一次输出无法解析为目标结构。请严格按下面的 JSON Schema 重新输出，不要包含任何额外说明文字。\n'
                f'解析错误：{type(exc).__name__}: {exc}\n'
                f'目标 JSON Schema：{contract}'
            )
        )


# 构建并缓存厂商客户端
class ProviderRegistry:
    # 保存模型调用设置与客户端缓存
    def __init__(self, settings: LLMSettings) -> None:
        self._settings = settings
        self._clients: dict[ClientKey, BaseChatModel] = {}

    # 解析厂商标识对应的声明
    def resolve_profile(self, name: str | None = None) -> ProviderProfile:
        return resolve_provider(name or self._settings.active_provider)

    # 解析生效的基础地址
    def effective_base_url(self, profile: ProviderProfile) -> str | None:
        if profile.name == self._settings.active_provider and self._settings.base_url:
            return self._settings.base_url
        return profile.base_url

    # 解析生效的模型名
    def effective_model(self, profile: ProviderProfile, options: CompletionOptions | None = None) -> str:
        if options is not None and options.model:
            return options.model
        if profile.name == self._settings.active_provider and self._settings.model:
            return self._settings.model
        return profile.default_model

    # 按厂商与调用参数构建或复用客户端
    def build(self, profile: ProviderProfile, options: CompletionOptions | None = None) -> BaseChatModel:
        resolved = options or CompletionOptions()
        model_name = self.effective_model(profile, resolved)
        temperature = self._settings.temperature if resolved.temperature is None else resolved.temperature
        max_tokens = self._settings.max_tokens if resolved.max_tokens is None else resolved.max_tokens
        cache_key: ClientKey = (profile.name, model_name, temperature, max_tokens)
        cached = self._clients.get(cache_key)
        if cached is not None:
            return cached
        client = ChatOpenAI(
            model=model_name,
            base_url=self.effective_base_url(profile),
            api_key=self.api_key_for(profile),
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=self._settings.timeout_seconds,
            max_retries=0,
            extra_body=profile.extra_body or None
        )
        self._clients[cache_key] = client
        logger.debug(
            '构建模型客户端 provider={} model={} base_url={}',
            profile.name,
            model_name,
            self.effective_base_url(profile)
        )
        return client

    # 取厂商的api key，缺失且必需时报错
    def api_key_for(self, profile: ProviderProfile) -> SecretStr:
        raw = os.environ.get(profile.api_key_env, '').strip()
        if raw:
            return SecretStr(raw)
        if profile.requires_api_key:
            raise ProviderNotConfigured(
                '该厂商缺少 API Key，请填写对应的环境变量',
                provider=profile.name,
                env_var=profile.api_key_env
            )
        return SecretStr('EMPTY')

    # 返回去重并校验过的厂商尝试顺序
    def chain(self) -> tuple[str, ...]:
        ordered = [self._settings.active_provider, *self._settings.failover_providers]
        deduped: list[str] = []
        for name in ordered:
            if name not in deduped:
                deduped.append(name)
        for name in deduped:
            resolve_provider(name)
        return tuple(deduped)


# 基于langchain的openai兼容客户端实现
class OpenAICompatibleChatModel:
    # 保存厂商注册表、结构化适配器与调用设置
    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        adapter: StructuredOutputAdapter,
        settings: LLMSettings,
        provider: str | None = None
    ) -> None:
        self._registry = registry
        self._adapter = adapter
        self._settings = settings
        self._provider = provider

    # 发起一次纯文本补全
    async def complete_text(
        self,
        *,
        system: str,
        user: str,
        options: CompletionOptions | None = None
    ) -> TextCompletion:
        resolved = options or CompletionOptions()
        profile = self._profile(resolved)
        client = self._registry.build(profile, resolved)
        messages = _messages(system, user)
        logger.info(
            '发起文本调用 | provider={} model={} | system {} 字 user {} 字',
            profile.name,
            self._model_name(profile, resolved),
            len(system),
            len(user)
        )
        started = perf_counter()
        try:
            response = await run_with_retries(
                lambda: client.ainvoke(messages),
                max_attempts=self._attempts(resolved),
                backoff_seconds=self._settings.backoff_seconds
            )
        except TRANSLATABLE_EXCEPTIONS as exc:
            logger.error(
                '文本调用失败 | provider={} model={} | {}: {}',
                profile.name,
                self._model_name(profile, resolved),
                type(exc).__name__,
                str(exc)[:200]
            )
            raise _translate(exc, profile) from exc
        latency_ms = (perf_counter() - started) * 1000.0
        text = _as_text(response.content)
        meta = _meta(profile, self._model_name(profile, resolved), None, latency_ms, messages, text)
        logger.debug('文本响应前 200 字 | {}', text[:200].replace(chr(10), ' '))
        return TextCompletion(text=text, meta=meta)

    # 按厂商声明的绑定方式发起一次结构化补全
    async def complete_structured(
        self,
        schema: type[TOut],
        *,
        system: str,
        user: str,
        options: CompletionOptions | None = None
    ) -> StructuredCompletion[TOut]:
        resolved = options or CompletionOptions()
        profile = self._profile(resolved)
        mode = _mode_for(profile)
        client = self._registry.build(profile, resolved)
        messages = _messages(system, user)
        logger.info(
            '发起结构化调用 | provider={} model={} mode={} schema={} | system {} 字 user {} 字',
            profile.name,
            self._model_name(profile, resolved),
            mode,
            schema.__name__,
            len(system),
            len(user)
        )
        started = perf_counter()
        try:
            outcome = await run_with_retries(
                lambda: self._adapter.invoke(client, schema, messages, mode),
                max_attempts=self._attempts(resolved),
                backoff_seconds=self._settings.backoff_seconds
            )
        except TRANSLATABLE_EXCEPTIONS as exc:
            logger.error(
                '结构化调用失败 | provider={} model={} schema={} | {}: {}',
                profile.name,
                self._model_name(profile, resolved),
                schema.__name__,
                type(exc).__name__,
                str(exc)[:200]
            )
            raise _translate(exc, profile) from exc
        latency_ms = (perf_counter() - started) * 1000.0
        meta = _meta(
            profile,
            self._model_name(profile, resolved),
            outcome.mode,
            latency_ms,
            messages,
            outcome.value.model_dump_json()
        )
        logger.debug('结构化响应 | {}', outcome.value.model_dump_json()[:200])
        return StructuredCompletion[TOut](value=outcome.value, meta=meta)

    # 解析本次调用实际使用的厂商
    def _profile(self, options: CompletionOptions) -> ProviderProfile:
        return self._registry.resolve_profile(self._provider or options.provider)

    # 解析本次调用实际使用的模型名
    def _model_name(self, profile: ProviderProfile, options: CompletionOptions) -> str:
        return self._registry.effective_model(profile, options)

    # 解析本次调用的最大尝试次数
    def _attempts(self, options: CompletionOptions) -> int:
        if options.max_attempts is not None:
            return options.max_attempts
        return self._settings.max_attempts


# 把系统消息与用户消息装配成消息列表
def _messages(system: str, user: str) -> list[BaseMessage]:
    return [SystemMessage(content=system), HumanMessage(content=user)]


# 把模型返回的多形态内容拼成纯文本
def _as_text(content: str | list[str | dict]) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            text = block.get('text')
            if isinstance(text, str):
                parts.append(text)
    return ''.join(parts)


# 取该厂商的结构化绑定方式
def _mode_for(profile: ProviderProfile) -> StructuredMode:
    return profile.structured_mode


# 组装本次调用的运行元数据
def _meta(
    profile: ProviderProfile,
    model_name: str,
    mode: StructuredMode | None,
    latency_ms: float,
    messages: list[BaseMessage],
    output_text: str
) -> CompletionMeta:
    prompt_text = ''.join(_as_text(message.content) for message in messages)
    logger.debug(
        '模型调用完成 provider={} model={} mode={} latency_ms={:.1f} prompt_chars={} completion_chars={}',
        profile.name,
        model_name,
        mode,
        latency_ms,
        len(prompt_text),
        len(output_text)
    )
    return CompletionMeta(
        provider=profile.name,
        model=model_name,
        structured_mode=mode,
        latency_ms=round(latency_ms, 3),
        prompt_chars=len(prompt_text),
        completion_chars=len(output_text)
    )


# 把厂商异常翻译成领域异常
def _translate(exc: Exception, profile: ProviderProfile) -> ProviderUnavailable:
    return ProviderUnavailable(
        '模型厂商调用失败',
        provider=profile.name,
        error_type=type(exc).__name__,
        reason=str(exc)[:400]
    )


# 把候选厂商串成一条降级链
class FailoverChatModel:
    # 按候选顺序构造各厂商的调用实例
    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        adapter: StructuredOutputAdapter,
        settings: LLMSettings,
        chain: tuple[str, ...]
    ) -> None:
        self._chain = chain
        self._members: list[tuple[str, ChatModelPort]] = [
            (
                name,
                OpenAICompatibleChatModel(
                    registry=registry,
                    adapter=adapter,
                    settings=settings,
                    provider=name
                )
            )
            for name in chain
        ]

    # 该降级链的候选顺序
    @property
    def chain(self) -> tuple[str, ...]:
        return self._chain

    # 发起一次带降级的纯文本补全
    async def complete_text(
        self,
        *,
        system: str,
        user: str,
        options: CompletionOptions | None = None
    ) -> TextCompletion:
        return await self._attempt(
            lambda member: member.complete_text(system=system, user=user, options=options)
        )

    # 发起一次带降级的结构化补全
    async def complete_structured(
        self,
        schema: type[TOut],
        *,
        system: str,
        user: str,
        options: CompletionOptions | None = None
    ) -> StructuredCompletion[TOut]:
        return await self._attempt(
            lambda member: member.complete_structured(schema, system=system, user=user, options=options)
        )

    # 依次尝试候选厂商，成功即记录降级来源并返回
    async def _attempt(self, call: Callable[[ChatModelPort], Awaitable[TEnvelope]]) -> TEnvelope:
        failures: list[dict[str, object]] = []
        for index, (name, member) in enumerate(self._members):
            try:
                result = await call(member)
            except ProviderUnusable as exc:
                failures.append({'provider': name, **exc.detail})
                logger.warning(
                    '厂商 {} 不可用，尝试下一个候选 | code={} error_type={} reason={}',
                    name,
                    exc.code,
                    exc.detail.get('error_type'),
                    exc.detail.get('reason')
                )
                continue
            if index == 0:
                return result
            origin = self._chain[0]
            logger.success('已降级到备用厂商 {} | degraded_from={}', name, origin)
            meta = result.meta.model_copy(update={'degraded_from': origin})
            return result.model_copy(update={'meta': meta})
        raise ProviderUnavailable(
            '全部候选厂商均不可用',
            attempted=[str(item.get('provider')) for item in failures],
            failures=failures
        )
