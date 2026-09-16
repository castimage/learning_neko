# 阶段守卫与校验回流两个横切装饰器
from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Concatenate, ParamSpec, Protocol, TypeVar

from loguru import logger

from learning_neko.application.verification import VerificationOutcome, VerifyLoop
from learning_neko.domain.errors import DomainError
from learning_neko.domain.models.enums import ArtifactKind
from learning_neko.domain.models.memory import SessionRecord
from learning_neko.domain.state import assert_action_allowed
from learning_neko.ports.llm import AgentResult, VerifiablePayload

P = ParamSpec('P')
R = TypeVar('R')
PayloadT = TypeVar('PayloadT', bound=VerifiablePayload)


# 能按会话id加载会话的服务
class GuardedService(Protocol):
    # 按会话id加载会话
    async def load_session(self, session_id: str) -> SessionRecord: ...


# 持有校验循环的服务
class VerifyingService(Protocol):
    # 该服务使用的校验循环
    @property
    def verify_loop(self) -> VerifyLoop: ...


# 把原方法的名字复制给包装函数
def rename_to(wrapper: object, method: object) -> None:
    wrapper.__name__ = method.__name__
    wrapper.__qualname__ = method.__qualname__


# 由产物内容生成稳定的产物引用
def artifact_ref_of(kind: ArtifactKind, payload: VerifiablePayload, session_id: str | None) -> str:
    variables = payload.to_prompt_vars()
    variables.pop('previous_errors', None)
    raw = json.dumps(variables, ensure_ascii=False, sort_keys=True)
    fingerprint = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:8]
    return f"{session_id or '-'}/{kind}/{fingerprint}"


# 从关键字参数中取出会话id
def session_id_of(kwargs: Mapping[str, object]) -> str | None:
    value = kwargs.get('session_id')
    return value if isinstance(value, str) else None


# 在进入方法前校验当前阶段是否允许该动作
def guarded(
    action: str
) -> Callable[
    [Callable[Concatenate[GuardedService, SessionRecord, P], Awaitable[R]]],
    Callable[Concatenate[GuardedService, str, P], Awaitable[R]]
]:
    # 包装受守卫的方法
    def decorator(
        method: Callable[Concatenate[GuardedService, SessionRecord, P], Awaitable[R]]
    ) -> Callable[Concatenate[GuardedService, str, P], Awaitable[R]]:
        # 加载会话并校验阶段后调用原方法
        async def wrapper(
            self: GuardedService,
            session_id: str,
            *args: P.args,
            **kwargs: P.kwargs
        ) -> R:
            logger.debug('进入受守卫操作 | action={} session={}', action, session_id)
            record = await self.load_session(session_id)
            try:
                assert_action_allowed(record.phase, action)
            except DomainError:
                logger.warning(
                    '阶段守卫拒绝操作 | action={} session={} 当前阶段={}',
                    action,
                    session_id,
                    record.phase
                )
                raise

            return await method(self, record, *args, **kwargs)

        rename_to(wrapper, method)
        return wrapper

    return decorator


# 把生成过程包进校验回流循环
def verified(
    kind: ArtifactKind
) -> Callable[
    [Callable[Concatenate[VerifyingService, PayloadT, P], Awaitable[AgentResult]]],
    Callable[Concatenate[VerifyingService, PayloadT, P], Awaitable[VerificationOutcome]]
]:
    # 包装带校验的生成方法
    def decorator(
        method: Callable[Concatenate[VerifyingService, PayloadT, P], Awaitable[AgentResult]]
    ) -> Callable[Concatenate[VerifyingService, PayloadT, P], Awaitable[VerificationOutcome]]:
        # 驱动校验循环重新生成直到通过
        async def wrapper(
            self: VerifyingService,
            payload: PayloadT,
            *args: P.args,
            **kwargs: P.kwargs
        ) -> VerificationOutcome:
            session_id = session_id_of(kwargs)
            reference = artifact_ref_of(kind, payload, session_id)
            logger.info('进入带校验的生成流程 | kind={} ref={}', kind, reference)

            return await self.verify_loop.run(
                kind=kind,
                generate=lambda errors: method(
                    self,
                    payload.with_previous_errors(errors),
                    *args,
                    **kwargs
                ),
                source_doc=payload.source_doc,
                session_id=session_id
            )

        rename_to(wrapper, method)
        return wrapper

    return decorator
