# 显式注册表的依赖容器
from __future__ import annotations

from enum import StrEnum

from learning_neko.domain.errors import ConfigurationError


# 容器中各服务的注册键
class ServiceKey(StrEnum):
    SETTINGS = 'settings'
    DATABASE = 'database'
    LLM = 'llm'
    PROMPT_REPOSITORY = 'prompt_repository'
    MEMORY_REPOSITORY = 'memory_repository'
    SESSION_REPOSITORY = 'session_repository'
    STATE_STORE = 'state_store'
    OBSERVABILITY = 'observability'
    AGENT_REGISTRY = 'agent_registry'
    ASSET_CATALOG = 'asset_catalog'
    STUDY_SERVICE = 'study_service'


# 按注册键存取服务实例
class Container:
    # 初始化空的服务表
    def __init__(self) -> None:
        self._services: dict[ServiceKey, object] = {}

    # 注册服务，重复注册时报错
    def register(self, key: ServiceKey, service: object) -> None:
        if key in self._services:
            raise ConfigurationError('该服务已注册，不允许重复装配', service=str(key))
        self._services[key] = service

    # 判断服务是否已注册
    def is_registered(self, key: ServiceKey) -> bool:
        return key in self._services

    # 按注册键取服务，未注册时报错
    def resolve(self, key: ServiceKey) -> object:
        service = self._services.get(key)
        if service is None:
            raise ConfigurationError('容器中未注册该服务', service=str(key))
        return service
