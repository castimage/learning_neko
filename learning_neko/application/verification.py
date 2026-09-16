# 校验横切：校验管线与校验回流循环
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime

from loguru import logger
from pydantic import BaseModel, ConfigDict

from learning_neko.agents import VerifyInput
from learning_neko.domain.errors import VerificationFailed
from learning_neko.domain.models.enums import ArtifactKind, ArtifactStatus, ExhaustedPolicy, VerificationSource
from learning_neko.domain.models.memory import VerificationError, VerificationLogRecord, VerificationResult
from learning_neko.ports.llm import AgentResult, VerifierPort
from learning_neko.ports.persistence import AgentObserverPort

CRITERIA: dict[ArtifactKind, str] = {
    ArtifactKind.OUTLINE: (
        '1. 每个分节是否都覆盖了资料正文中的核心概念，且没有引入资料之外的内容。\n'
        '2. 分节顺序是否由浅入深；后一节依赖前一节时，是否在该节目标中写明依赖关系。\n'
        '3. 每节的学习目标是否可检验，是否出现「了解」「熟悉」「掌握」这类无法验证的表述。\n'
        '4. keywords 是否只包含概念名词，是否能在资料正文中找到对应表述。'
    ),
    ArtifactKind.MATERIAL: (
        '1. 正文是否严格限定在本分节目标范围内，是否越界讲了后续分节的内容。\n'
        '2. 正文中的每个结论、公式与术语是否能在资料正文中找到依据；是否存在与资料正文矛盾或方向相反的表述。\n'
        '3. 正文是否至少包含定义、机制解释、具体例子三个层次。\n'
        '4. 例题是否仅凭本分节正文即可作答，答案与解析是否自洽。\n'
        '5. 关系图中每个节点的 label 是否为资料正文中出现过的概念；其 detail 是否在说明这个节点自己的含义'
        '（说明它是什么、或它会带来什么），而不是在说明另一个节点的含义。\n'
        '6. 正文里的每个 ![图注](media:id) 占位符是否都能在 media 列表里找到对应的 id。\n'
        '7. media 中 kind 为 "image"、"audio"、"video" 的条目，其 asset_key 是否逐字出现在 available_assets 里；'
        'kind 为 "diagram" 或 "animation" 的条目，asset_key 必须是空字符串，'
        '此时不要因为它是空字符串而判为问题——那是这类素材的正确取值。'
    ),
    ArtifactKind.VISUALIZATION: (
        '1. 每个节点的 label 是否为资料正文中出现过的概念，detail 是否在解释这个节点自己的概念。\n'
        '2. 图中是否存在与资料正文矛盾的标注。'
    ),
    ArtifactKind.EXERCISES: (
        '1. 每个分节是否都至少有一道对应题目，考点分布是否与大纲篇幅匹配。\n'
        '2. 题干是否自足，是否出现「如上文所述」这类需要回看正文才能理解的指代。\n'
        '3. 选择题是否至少三个选项且只有一个正确，是否出现「以上都对」这类选项。\n'
        '4. 每题的解析是否说明了正确原因并点出易错项，考点是否全部落在资料正文范围内。\n'
        '5. 每道题挂载的素材是否与题干直接相关，且不看资料正文即可理解。'
    )
}


# 组织校验智能体的复核
class VerifyPipeline:
    # 保存校验智能体与可观测性仓储
    def __init__(
        self,
        *,
        agent: VerifierPort,
        observer: AgentObserverPort | None = None
    ) -> None:
        self._agent = agent
        self._observer = observer

    # 调用校验智能体复核产物并落校验记录
    async def run(
        self,
        *,
        kind: ArtifactKind,
        output: BaseModel,
        source_doc: str,
        session_id: str | None = None,
        attempt: int = 1,
        artifact_ref: str | None = None
    ) -> VerificationResult:
        result = await self._agent.run(
            VerifyInput(
                artifact_kind=kind,
                artifact=output.model_dump_json(),
                basis=source_doc,
                criteria=CRITERIA[kind]
            ),
            session_id=session_id
        )
        verdict = result.output
        await self._record(kind, verdict, session_id, attempt, artifact_ref)
        return verdict

    # 落一条校验记录
    async def _record(
        self,
        kind: ArtifactKind,
        verdict: VerificationResult,
        session_id: str | None,
        attempt: int,
        artifact_ref: str | None
    ) -> None:
        if self._observer is None:
            return
        await self._observer.record_verification(
            VerificationLogRecord(
                session_id=session_id,
                artifact_ref=artifact_ref or f"{session_id or '-'}/{kind}",
                artifact_kind=kind,
                attempt=attempt,
                source=VerificationSource.LLM,
                passed=verdict.passed,
                errors=list(verdict.errors),
                created_at=datetime.now()
            )
        )


# 一次被采纳的生成结果及其校验轨迹
class VerificationOutcome(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    result: AgentResult
    verification: VerificationResult
    attempts: int
    status: ArtifactStatus
    history: list[VerificationResult]


# 在给定预算内反复生成直到产物通过校验
class VerifyLoop:
    # 保存校验管线、默认预算与预算耗尽策略
    def __init__(
        self,
        *,
        pipeline: VerifyPipeline,
        default_budget: int,
        policy: ExhaustedPolicy
    ) -> None:
        self._pipeline = pipeline
        self._default_budget = default_budget
        self._policy = policy

    # 循环生成与校验，通过即返回
    async def run(
        self,
        *,
        kind: ArtifactKind,
        generate: Callable[[list[VerificationError]], Awaitable[AgentResult]],
        source_doc: str,
        session_id: str | None = None,
        budget: int | None = None,
        artifact_ref: str | None = None
    ) -> VerificationOutcome:
        limit = max(1, self._default_budget if budget is None else budget)
        errors: list[VerificationError] = []
        history: list[VerificationResult] = []
        result: AgentResult | None = None
        for attempt in range(1, limit + 1):
            result = await generate(errors)
            verdict = await self._pipeline.run(
                kind=kind,
                output=result.output,
                source_doc=source_doc,
                session_id=session_id,
                attempt=attempt,
                artifact_ref=artifact_ref
            )
            history.append(verdict)
            if verdict.passed:
                return VerificationOutcome(
                    result=result,
                    verification=verdict,
                    attempts=attempt,
                    status=ArtifactStatus.PASSED,
                    history=history
                )
            errors = list(verdict.errors)
            logger.warning(
                '第 {} 轮校验未通过 kind={} 问题数={} 剩余预算={}',
                attempt,
                kind,
                len(errors),
                limit - attempt
            )
        return self._exhausted(kind, result, history, limit)

    # 预算耗尽时按策略决定降级交付还是报错
    def _exhausted(
        self,
        kind: ArtifactKind,
        result: AgentResult,
        history: list[VerificationResult],
        limit: int
    ) -> VerificationOutcome:
        verdict = history[-1] if history else VerificationResult(passed=False, errors=[])
        if self._policy is ExhaustedPolicy.RAISE:
            raise VerificationFailed(
                '校验智能体提出的问题在预算内未被消除',
                artifact_kind=str(kind),
                attempts=limit,
                errors=[item.model_dump(mode='json') for item in verdict.errors],
                exhausted_policy=str(self._policy)
            )
        logger.warning(
            '校验智能体的语义问题在预算内未消除，按策略降级交付 kind={} 残留问题数={}',
            kind,
            len(verdict.errors)
        )
        return VerificationOutcome(
            result=result,
            verification=verdict,
            attempts=limit,
            status=ArtifactStatus.ACCEPTED_WITH_WARNING,
            history=history
        )
