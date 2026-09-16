# 领域异常层次，把领域内的失败统一成带错误码与http状态码的异常
from __future__ import annotations

from typing import ClassVar


# 所有领域异常的基类，子类各自声明错误码与http状态码
class DomainError(Exception):
    code: ClassVar[str] = 'domain_error'
    status_code: ClassVar[int] = 400

    # 保存错误摘要与附加上下文
    def __init__(self, message: str, **detail: object) -> None:
        super().__init__(message)
        self.message = message
        self.detail: dict[str, object] = dict(detail)


# 会话不存在或已被清理
class SessionNotFound(DomainError):
    code: ClassVar[str] = 'session_not_found'
    status_code: ClassVar[int] = 404


# 分节下标超出大纲范围
class SectionNotFound(DomainError):
    code: ClassVar[str] = 'section_not_found'
    status_code: ClassVar[int] = 404


# 请求的产物尚未生成
class ArtifactNotFound(DomainError):
    code: ClassVar[str] = 'artifact_not_found'
    status_code: ClassVar[int] = 404


# 素材索引里没有该key，或该条目在加载期就已被剔除
class AssetNotFound(DomainError):
    code: ClassVar[str] = 'asset_not_found'
    status_code: ClassVar[int] = 404


# 状态迁移不在允许的迁移表里，或与调用方的预期不符
class IllegalTransition(DomainError):
    code: ClassVar[str] = 'illegal_transition'
    status_code: ClassVar[int] = 409


# 目标状态与当前状态相同，无需迁移
class AlreadyCompleted(DomainError):
    code: ClassVar[str] = 'already_completed'
    status_code: ClassVar[int] = 409


# 当前学习阶段不允许执行该操作
class PhaseGuardViolation(DomainError):
    code: ClassVar[str] = 'phase_guard_violation'
    status_code: ClassVar[int] = 409


# 模型输出无法归一到目标schema，修复重试后仍失败
class ModelOutputInvalid(DomainError):
    code: ClassVar[str] = 'model_output_invalid'
    status_code: ClassVar[int] = 422


# 校验预算耗尽仍未产出可交付的产物
class VerificationFailed(DomainError):
    code: ClassVar[str] = 'verification_failed'
    status_code: ClassVar[int] = 422


# 厂商不可用的共同基类，故障转移据此判断是否切换下一家
class ProviderUnusable(DomainError):
    code: ClassVar[str] = 'provider_unusable'
    status_code: ClassVar[int] = 503


# 厂商标识未知，或缺少该厂商的api key
class ProviderNotConfigured(ProviderUnusable):
    code: ClassVar[str] = 'provider_not_configured'


# 厂商调用失败，属于连接或鉴权层面的问题
class ProviderUnavailable(ProviderUnusable):
    code: ClassVar[str] = 'provider_unavailable'


# 提示词模板语法错误，或渲染时缺少必需变量
class PromptTemplateError(DomainError):
    code: ClassVar[str] = 'prompt_template_error'
    status_code: ClassVar[int] = 500


# 提示词文件或版本目录不存在
class PromptNotFound(DomainError):
    code: ClassVar[str] = 'prompt_not_found'
    status_code: ClassVar[int] = 500


# 装配期的配置错误，通常是注册表里缺少条目
class ConfigurationError(DomainError):
    code: ClassVar[str] = 'configuration_error'
    status_code: ClassVar[int] = 500
