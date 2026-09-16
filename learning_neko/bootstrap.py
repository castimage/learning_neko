# 装配根，按序注册全部服务并创建应用
from __future__ import annotations

from collections.abc import Callable
from typing import cast

from fastapi import FastAPI
from loguru import logger

from learning_neko import __version__
from learning_neko.agents import AgentRegistry
from learning_neko.api import routes
from learning_neko.api.envelope import register_error_handlers
from learning_neko.application.study_service import ArtifactReader, ContextBuilder, StudyService
from learning_neko.application.verification import VerifyLoop, VerifyPipeline
from learning_neko.config import Settings
from learning_neko.container import Container, ServiceKey
from learning_neko.domain.models.enums import AgentKind
from learning_neko.infrastructure.assets import FileAssetCatalog
from learning_neko.infrastructure.llm import FailoverChatModel, ProviderRegistry, StructuredOutputAdapter
from learning_neko.infrastructure.logging import configure_logging, RequestLogMiddleware
from learning_neko.infrastructure.persistence import (
    Database,
    SqlLearningStateStore,
    SqlMemoryRepository,
    SqlObservabilityRepository,
    SqlSessionRepository
)
from learning_neko.infrastructure.prompts import FilePromptRepository
from learning_neko.ports.llm import ChatModelPort, PromptRepositoryPort
from learning_neko.ports.assets import AssetCatalogPort
from learning_neko.ports.persistence import (
    AgentObserverPort,
    LearningStateStorePort,
    MemoryRepositoryPort,
    SessionRepositoryPort
)

Registrar = Callable[[Container, Settings], None]


# 注册配置
def register_config(container: Container, settings: Settings) -> None:
    container.register(ServiceKey.SETTINGS, settings)


# 注册厂商注册表与带降级的模型端口
def register_llm(container: Container, settings: Settings) -> None:
    registry = ProviderRegistry(settings.llm)
    adapter = StructuredOutputAdapter(repair_attempts=settings.llm.structured_repair_attempts)
    container.register(
        ServiceKey.LLM,
        FailoverChatModel(
            registry=registry,
            adapter=adapter,
            settings=settings.llm,
            chain=registry.chain()
        )
    )


# 注册文件系统提示词仓储
def register_prompts(container: Container, settings: Settings) -> None:
    container.register(
        ServiceKey.PROMPT_REPOSITORY,
        FilePromptRepository(settings=settings.prompt)
    )


# 注册素材库，索引缺失时按空库降级而不是阻断启动
def register_assets(container: Container, settings: Settings) -> None:
    container.register(
        ServiceKey.ASSET_CATALOG,
        FileAssetCatalog(path=settings.assets_index_path, root=settings.assets_dir)
    )


# 注册数据库与会话、记忆、状态、可观测性仓储
def register_persistence(container: Container, settings: Settings) -> None:
    database = Database(settings=settings.database, path=settings.database_path)
    if settings.database.create_all_on_startup:
        database.create_all()
    container.register(ServiceKey.DATABASE, database)
    container.register(ServiceKey.MEMORY_REPOSITORY, SqlMemoryRepository(database))
    container.register(ServiceKey.SESSION_REPOSITORY, SqlSessionRepository(database))
    container.register(ServiceKey.STATE_STORE, SqlLearningStateStore(database))
    container.register(ServiceKey.OBSERVABILITY, SqlObservabilityRepository(database))


# 注册智能体注册表并做启动期预检
def register_agents(container: Container, settings: Settings) -> None:
    registry = AgentRegistry(
        llm=cast(ChatModelPort, container.resolve(ServiceKey.LLM)),
        prompts=cast(PromptRepositoryPort, container.resolve(ServiceKey.PROMPT_REPOSITORY)),
        prompt_version=settings.prompt.version,
        observer=cast(AgentObserverPort, container.resolve(ServiceKey.OBSERVABILITY))
    )
    container.register(ServiceKey.AGENT_REGISTRY, registry)
    logger.info('智能体预检通过，已装配 {}', registry.preflight())


# 注册用例层依赖与学习流程服务
def register_application(container: Container, settings: Settings) -> None:
    sessions = cast(SessionRepositoryPort, container.resolve(ServiceKey.SESSION_REPOSITORY))
    memory = cast(MemoryRepositoryPort, container.resolve(ServiceKey.MEMORY_REPOSITORY))
    agents = cast(AgentRegistry, container.resolve(ServiceKey.AGENT_REGISTRY))
    observer = cast(AgentObserverPort, container.resolve(ServiceKey.OBSERVABILITY))
    container.register(
        ServiceKey.STUDY_SERVICE,
        StudyService(
            agents=agents,
            sessions=sessions,
            memory=memory,
            state=cast(LearningStateStorePort, container.resolve(ServiceKey.STATE_STORE)),
            context=ContextBuilder(memory=memory),
            artifacts=ArtifactReader(sessions=sessions),
            verify_loop=VerifyLoop(
                pipeline=VerifyPipeline(
                    agent=agents.build(AgentKind.VERIFY),
                    observer=observer
                ),
                default_budget=settings.verification.max_attempts,
                policy=settings.verification.exhausted_policy
            ),
            assets=cast(AssetCatalogPort, container.resolve(ServiceKey.ASSET_CATALOG))
        )
    )


REGISTRARS: tuple[Registrar, ...] = (
    register_config,
    register_persistence,
    register_llm,
    register_prompts,
    register_assets,
    register_agents,
    register_application
)


# 按注册器顺序装配容器
def build_container(settings: Settings) -> Container:
    container = Container()
    for register in REGISTRARS:
        register(container, settings)
    return container


# 配置日志、装配容器并创建fastapi应用
def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings()
    configure_logging(resolved.app.log_level, resolved.app.log_json)
    container = build_container(resolved)

    app = FastAPI(title=resolved.app.name, version=__version__)
    app.state.container = container
    app.state.settings = resolved

    app.add_middleware(RequestLogMiddleware)
    register_error_handlers(app)
    app.include_router(routes.health_router)
    app.include_router(routes.study_router)
    app.include_router(routes.assets_router)
    return app
