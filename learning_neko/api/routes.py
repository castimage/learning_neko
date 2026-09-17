# 路由依赖取值入口，以及系统探针与学习流程的全部路由
from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from learning_neko import __version__
from learning_neko.api.envelope import ApiResponse, ok
from learning_neko.api.schemas import (
    AnswersRequest,
    check_view,
    grade_view,
    material_view,
    MaterialView,
    QaView,
    QuestionRequest,
    session_summary_view,
    session_view,
    SessionSummaryView,
    SessionView,
    StartSessionRequest,
    SummaryView,
    VerdictView
)
from learning_neko.application.study_service import generated_sections_of, StudyService
from learning_neko.config import PROVIDER_PROFILES, Settings
from learning_neko.container import Container, ServiceKey
from learning_neko.domain.errors import ArtifactNotFound, AssetNotFound
from learning_neko.domain.models.enums import StructuredMode
from learning_neko.domain.models.memory import SessionOutcome, TopicMemory
from learning_neko.ports.assets import AssetCatalogPort

health_router = APIRouter(tags=['system'])
study_router = APIRouter(prefix='/sessions', tags=['study'])
assets_router = APIRouter(prefix='/assets', tags=['assets'])

READINESS_KEYS: tuple[ServiceKey, ...] = (
    ServiceKey.LLM,
    ServiceKey.PROMPT_REPOSITORY,
    ServiceKey.MEMORY_REPOSITORY,
    ServiceKey.SESSION_REPOSITORY,
    ServiceKey.STATE_STORE
)


# 取应用装配好的容器
def get_container(request: Request) -> Container:
    return cast(Container, request.app.state.container)


# 取应用配置
def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


# 取学习流程服务
def get_study_service(request: Request) -> StudyService:
    return cast(StudyService, get_container(request).resolve(ServiceKey.STUDY_SERVICE))


# 取素材库
def get_asset_catalog(request: Request) -> AssetCatalogPort:
    return cast(AssetCatalogPort, get_container(request).resolve(ServiceKey.ASSET_CATALOG))


# 取一份素材的原始字节，这是全项目唯一不走统一信封的接口
@assets_router.get('/{asset_key}', summary='按key取素材原始字节')
async def read_asset(
    asset_key: str,
    catalog: AssetCatalogPort = Depends(get_asset_catalog)
) -> FileResponse:
    handle = catalog.locate(asset_key)
    if handle is None:
        raise AssetNotFound('素材不存在或已被剔除，请检查索引与文件', asset_key=asset_key)
    return FileResponse(handle.path, media_type=handle.media_type)


# 存活探针的响应体
class HealthPayload(BaseModel):
    status: str
    version: str


# 单个关键组件的装配状态
class ComponentStatus(BaseModel):
    name: str
    status: str


# 就绪探针的响应体
class ReadyPayload(BaseModel):
    ready: bool
    components: list[ComponentStatus]


# 单个厂商的对外描述
class ProviderInfo(BaseModel):
    name: str
    label: str
    base_url: str | None
    default_model: str
    structured_mode: StructuredMode
    requires_api_key: bool


# 厂商信息的响应体
class ProvidersPayload(BaseModel):
    active: str
    failover: list[str]
    available: list[ProviderInfo]


# 返回服务存活状态与版本
@health_router.get('/health', response_model=ApiResponse[HealthPayload], summary='存活探针')
async def health() -> ApiResponse[HealthPayload]:
    return ok(HealthPayload(status='ok', version=__version__))


# 返回关键组件的装配状态
@health_router.get('/ready', response_model=ApiResponse[ReadyPayload], summary='依赖就绪探针')
async def ready(container: Container = Depends(get_container)) -> ApiResponse[ReadyPayload]:
    components = [
        ComponentStatus(name=str(key), status='ready' if container.is_registered(key) else 'not_configured')
        for key in READINESS_KEYS
    ]

    return ok(ReadyPayload(ready=all(item.status == 'ready' for item in components), components=components))


# 返回生效厂商与全部内置厂商
@health_router.get('/providers', response_model=ApiResponse[ProvidersPayload], summary='可用模型厂商')
async def providers(settings: Settings = Depends(get_settings)) -> ApiResponse[ProvidersPayload]:
    available = [
        ProviderInfo(
            name=profile.name,
            label=profile.label,
            base_url=profile.base_url,
            default_model=profile.default_model,
            structured_mode=profile.structured_mode,
            requires_api_key=profile.requires_api_key
        )
        for profile in PROVIDER_PROFILES
    ]

    return ok(
        ProvidersPayload(
            active=settings.llm.active_provider,
            failover=list(settings.llm.failover_providers),
            available=available
        )
    )


# 提交资料与需求并创建会话
@study_router.post('', response_model=ApiResponse[SessionView], status_code=201, summary='提交学习资料与需求，开始一次学习')
async def start_session(
    request: StartSessionRequest,
    service: StudyService = Depends(get_study_service)
) -> ApiResponse[SessionView]:
    record, memory_context = await service.start_session(request.source_doc, request.user_request)
    return ok(session_view(record, [], memory_context))


# 读取会话快照列表
@study_router.get('', response_model=ApiResponse[list[SessionSummaryView]], summary='列出最近的学习会话，供前端发现既有会话')
async def list_sessions(
    limit: int = Query(default=20, ge=1, le=200),
    service: StudyService = Depends(get_study_service)
) -> ApiResponse[list[SessionSummaryView]]:
    records = await service.list_recent_sessions(limit)
    return ok([session_summary_view(record) for record in records])

# 读取会话快照
@study_router.get('/{session_id}', response_model=ApiResponse[SessionView], summary='读取会话快照，可用于重启后续跑')
async def read_session(
    session_id: str,
    service: StudyService = Depends(get_study_service)
) -> ApiResponse[SessionView]:
    record, materials = await service.snapshot(session_id)
    return ok(session_view(record, generated_sections_of(materials)))


# 读取某分节已生成的学习资料
@study_router.get(
    '/{session_id}/sections/{section_index}/material',
    response_model=ApiResponse[MaterialView],
    summary='读取某分节已生成的学习资料'
)
async def read_material(
    session_id: str,
    section_index: int,
    service: StudyService = Depends(get_study_service)
) -> ApiResponse[MaterialView]:
    stored = await service.read_material(session_id, section_index)
    return ok(material_view(stored))


# 把会话切到学习完毕
@study_router.post('/{session_id}/complete', response_model=ApiResponse[SessionView], summary='确认没有疑问，切换到学习完毕')
async def complete_session(
    session_id: str,
    service: StudyService = Depends(get_study_service)
) -> ApiResponse[SessionView]:
    record = await service.complete(session_id)
    generated = await service.generated_sections(session_id)
    return ok(session_view(record, generated_sections=generated))


# 生成某分节的资料、例题与关系图
@study_router.post(
    '/{session_id}/sections/{section_index}/material',
    response_model=ApiResponse[MaterialView],
    summary='生成某分节的学习资料、例题与知识点关系图'
)
async def generate_material(
    session_id: str,
    section_index: int,
    service: StudyService = Depends(get_study_service)
) -> ApiResponse[MaterialView]:
    _, stored = await service.generate_material(session_id, section_index)
    return ok(material_view(stored))


# 就当前分节提问
@study_router.post('/{session_id}/qa', response_model=ApiResponse[QaView], summary='就当前分节的资料提问')
async def ask_question(
    session_id: str,
    request: QuestionRequest,
    service: StudyService = Depends(get_study_service)
) -> ApiResponse[QaView]:
    _, answer = await service.ask(session_id, request.question)
    return ok(
        QaView(
            session_id=session_id,
            answer=answer.answer,
            point=answer.record.point,
            explained=answer.record.explained
        )
    )


# 提交例题作答并逐题判定
@study_router.post('/{session_id}/examples/check', response_model=ApiResponse[VerdictView], summary='提交例题作答并逐题判定')
async def check_examples(
    session_id: str,
    request: AnswersRequest,
    service: StudyService = Depends(get_study_service)
) -> ApiResponse[VerdictView]:
    _, verdicts, total = await service.check_examples(session_id, request.answers)
    return ok(check_view(session_id, verdicts, total))


# 生成课后测验
@study_router.post(
    '/{session_id}/exercises/generate',
    response_model=ApiResponse[MaterialView],
    summary='生成课后测验题'
)
async def generate_exercises(
    session_id: str,
    service: StudyService = Depends(get_study_service)
) -> ApiResponse[MaterialView]:
    _, stored = await service.generate_exercises(session_id)
    return ok(material_view(stored))


# 提交课后作答并逐题批阅
@study_router.post('/{session_id}/exercises/grade', response_model=ApiResponse[VerdictView], summary='提交课后作答并逐题批阅')
async def grade_exercises(
    session_id: str,
    request: AnswersRequest,
    service: StudyService = Depends(get_study_service)
) -> ApiResponse[VerdictView]:
    _, verdicts, total = await service.grade(session_id, request.answers)
    return ok(grade_view(session_id, verdicts, total))


# 汇总本次学习并写入记忆
@study_router.post('/{session_id}/summary', response_model=ApiResponse[SummaryView], summary='汇总本次学习并写入记忆文件')
async def summarize(
    session_id: str,
    service: StudyService = Depends(get_study_service)
) -> ApiResponse[SummaryView]:
    record, report, mastery, next_focus = await service.summarize(session_id)
    memory = await service.memory_of(record.topic)
    outcome = find_outcome(memory, session_id)

    return ok(
        SummaryView(
            session_id=session_id,
            report=report,
            score='' if outcome is None else outcome.score,
            comment='' if outcome is None else outcome.comment,
            mastery=mastery,
            next_focus=next_focus
        )
    )


# 读取已归档的学习报告
@study_router.get('/{session_id}/report', response_model=ApiResponse[SummaryView], summary='读取已归档的学习报告')
async def read_report(
    session_id: str,
    service: StudyService = Depends(get_study_service)
) -> ApiResponse[SummaryView]:
    record, _ = await service.snapshot(session_id)
    memory = await service.memory_of(record.topic)
    outcome = find_outcome(memory, session_id)
    if memory is None or outcome is None:
        raise ArtifactNotFound(
            '本次学习尚未归档，请先调用总结接口',
            session_id=session_id,
            topic=record.topic
        )

    return ok(
        SummaryView(
            session_id=session_id,
            report=outcome.report,
            score=outcome.score,
            comment=outcome.comment,
            mastery=memory.mastery,
            next_focus=memory.next_focus
        )
    )


# 从记忆中找出本次会话的归档
def find_outcome(memory: TopicMemory | None, session_id: str) -> SessionOutcome | None:
    if memory is None:
        return None

    for item in memory.sessions:
        if item.session_id == session_id:
            return item

    return None
