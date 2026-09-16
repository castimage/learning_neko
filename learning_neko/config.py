# 分组配置、内置厂商声明与各智能体运行参数，取值全部可由.env覆盖
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from learning_neko.domain.errors import ProviderNotConfigured
from learning_neko.domain.models.enums import AgentKind, ExhaustedPolicy, StructuredMode

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / '.env'

load_dotenv(ENV_FILE, override=False)

_ENV_SHARED: dict[str, object] = {
    'env_file': ENV_FILE,
    'env_file_encoding': 'utf-8',
    'case_sensitive': False,
    'extra': 'ignore'
}


# 应用与运行期设置
class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='APP_', **_ENV_SHARED)

    name: str = 'learning-neko'
    host: str = '127.0.0.1'
    port: int = 8000
    reload: bool = False
    log_level: str = 'DEBUG'
    log_json: bool = False
    data_dir: Path = PROJECT_ROOT / 'data'


# sqlite持久化设置
class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='DB_', **_ENV_SHARED)

    filename: str = 'learning.db'
    echo: bool = False
    create_all_on_startup: bool = True
    connect_timeout_seconds: float = 15.0


# 模型调用与厂商切换设置
class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='LLM_', **_ENV_SHARED)

    active_provider: str = 'deepseek'
    failover_providers: list[str] = Field(default_factory=list)
    base_url: str | None = None
    model: str | None = None
    temperature: float = 0.3
    timeout_seconds: float = 120.0
    max_attempts: int = 3
    backoff_seconds: float = 1.0
    max_tokens: int = 4096
    structured_repair_attempts: int = 1


# 提示词仓储设置
class PromptSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='PROMPT_', **_ENV_SHARED)

    root: Path = PROJECT_ROOT / 'prompts'
    version: str = 'v1'
    shared_dir: str = 'shared'
    strict_undefined: bool = True
    cache_size: int = 64


# 校验横切设置
class VerificationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='VERIFY_', **_ENV_SHARED)

    max_attempts: int = 3
    exhausted_policy: ExhaustedPolicy = ExhaustedPolicy.ACCEPT_WITH_WARNING


# 素材库设置
class AssetSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='ASSET_', **_ENV_SHARED)

    dir_name: str = 'assets'
    index_filename: str = 'index.json'


# 全部配置的聚合根
class Settings(BaseModel):
    app: AppSettings = Field(default_factory=AppSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    prompt: PromptSettings = Field(default_factory=PromptSettings)
    verification: VerificationSettings = Field(default_factory=VerificationSettings)
    asset: AssetSettings = Field(default_factory=AssetSettings)

    # sqlite文件的绝对路径
    @property
    def database_path(self) -> Path:
        return self.app.data_dir / self.database.filename

    # 素材库根目录
    @property
    def assets_dir(self) -> Path:
        return self.app.data_dir / self.asset.dir_name

    # 素材索引文件
    @property
    def assets_index_path(self) -> Path:
        return self.assets_dir / self.asset.index_filename


# 单个厂商的接入声明
class ProviderProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

    name: str
    label: str
    base_url: str | None = None
    api_key_env: str
    default_model: str
    structured_mode: StructuredMode
    requires_api_key: bool = True
    extra_body: dict[str, object] = Field(default_factory=dict)


# 内置厂商声明的数据表，新增厂商只需追加一条
PROVIDER_PROFILES: tuple[ProviderProfile, ...] = (
    ProviderProfile(
        name='openai',
        label='OpenAI',
        base_url=None,
        api_key_env='OPENAI_API_KEY',
        default_model='gpt-4.1-mini',
        structured_mode=StructuredMode.JSON_SCHEMA
    ),
    ProviderProfile(
        name='deepseek',
        label='DeepSeek',
        base_url='https://api.deepseek.com/v1',
        api_key_env='DEEPSEEK_API_KEY',
        default_model='deepseek-chat',
        structured_mode=StructuredMode.FUNCTION_CALLING
    ),
    ProviderProfile(
        name='dashscope',
        label='通义千问',
        base_url='https://dashscope.aliyuncs.com/compatible-mode/v1',
        api_key_env='DASHSCOPE_API_KEY',
        default_model='qwen-plus',
        structured_mode=StructuredMode.FUNCTION_CALLING,
        extra_body={'enable_thinking': False}
    )
)

PROVIDER_REGISTRY: dict[str, ProviderProfile] = {profile.name: profile for profile in PROVIDER_PROFILES}


# 按标识取厂商声明
def resolve_provider(name: str) -> ProviderProfile:
    profile = PROVIDER_REGISTRY.get(name)
    if profile is None:
        raise ProviderNotConfigured('未知的模型厂商标识', provider=name, available=sorted(PROVIDER_REGISTRY))
    return profile


# 单个智能体的采样与重试参数
class AgentRuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

    temperature: float
    max_tokens: int
    max_attempts: int
    provider: str | None = None
    model: str | None = None


# 构造一份运行参数
def _runtime(
    temperature: float,
    max_tokens: int,
    max_attempts: int,
    provider: str | None = None,
    model: str | None = None
) -> AgentRuntimeConfig:
    return AgentRuntimeConfig(
        temperature=temperature,
        max_tokens=max_tokens,
        max_attempts=max_attempts,
        provider=provider,
        model=model
    )


# 各智能体的运行参数
AGENT_RUNTIME_CONFIG: dict[AgentKind, AgentRuntimeConfig] = {
    AgentKind.OUTLINE: _runtime(temperature=0.2, max_tokens=3000, max_attempts=3),
    AgentKind.MATERIAL: _runtime(temperature=0.6, max_tokens=6000, max_attempts=3),
    AgentKind.QA: _runtime(temperature=0.3, max_tokens=2000, max_attempts=3),
    AgentKind.CHECK: _runtime(temperature=0.0, max_tokens=2000, max_attempts=3),
    AgentKind.VERIFY: _runtime(temperature=0.0, max_tokens=2000, max_attempts=2),
    AgentKind.SUMMARY: _runtime(temperature=0.3, max_tokens=3000, max_attempts=3)
}


# 按智能体种类取运行参数
def runtime_config_for(kind: AgentKind) -> AgentRuntimeConfig:
    return AGENT_RUNTIME_CONFIG[kind]
