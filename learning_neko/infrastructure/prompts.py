# 提示词实现：八段骨架切分、jinja2渲染与文件系统仓储
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, meta, StrictUndefined, Undefined
from loguru import logger

from learning_neko.config import PromptSettings
from learning_neko.domain.errors import PromptNotFound, PromptTemplateError
from learning_neko.ports.llm import PromptRef, RenderedPrompt

SECTION_PATTERN = re.compile(r'^##[ \t]+([A-Z][A-Z0-9_-]*)[ \t]*$', re.MULTILINE)

SYSTEM_HEAD_ORDER: tuple[str, ...] = ('ROLE', 'CONTEXT', 'RULES')
USER_ORDER: tuple[str, ...] = ('INPUT', 'TASK')
SYSTEM_TAIL_ORDER: tuple[str, ...] = ('OUTPUT', 'EXAMPLES', 'SELF-CHECK')
SYSTEM_ORDER: tuple[str, ...] = SYSTEM_HEAD_ORDER + SYSTEM_TAIL_ORDER
KNOWN_SECTIONS: tuple[str, ...] = SYSTEM_ORDER + USER_ORDER

AUTO_VARIABLES: frozenset[str] = frozenset({'shared'})
REQUIRED_SECTIONS: tuple[str, ...] = ('ROLE', 'TASK', 'OUTPUT')


# 解析出模板的全部命名分节并校验合法性
def parse_sections(text: str) -> tuple[tuple[str, str], ...]:
    matches = list(SECTION_PATTERN.finditer(text))
    if not matches:
        raise PromptTemplateError('提示词模板不含任何合法分节', expected=list(KNOWN_SECTIONS))
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        name = match.group(1)
        if name not in KNOWN_SECTIONS:
            raise PromptTemplateError('提示词模板出现未定义的分节名', section=name, allowed=list(KNOWN_SECTIONS))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if not body:
            raise PromptTemplateError('提示词模板的分节内容为空', section=name)
        sections.append((name, body))
    duplicates = {name for name, _ in sections if [item[0] for item in sections].count(name) > 1}
    if duplicates:
        raise PromptTemplateError('提示词模板存在重复分节', sections=sorted(duplicates))
    return tuple(sections)


# 校验模板包含全部必需分节
def ensure_required_sections(sections: tuple[tuple[str, str], ...], required: tuple[str, ...]) -> None:
    present = {name for name, _ in sections}
    missing = [name for name in required if name not in present]
    if missing:
        raise PromptTemplateError('提示词模板缺少必需分节', missing=missing)


# 用jinja2渲染单个提示词片段
class PromptRenderer:
    # 按是否容忍未定义变量建立渲染环境
    def __init__(self, *, strict_undefined: bool) -> None:
        self._environment = Environment(
            undefined=StrictUndefined if strict_undefined else Undefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True
        )

    # 列出模板声明但未提供的变量
    def declared_variables(self, text: str) -> frozenset[str]:
        try:
            ast = self._environment.parse(text)
        except Exception as exc:
            raise PromptTemplateError('提示词模板语法错误', error=str(exc)) from exc
        return frozenset(meta.find_undeclared_variables(ast))

    # 按变量渲染模板片段
    def render(self, text: str, variables: Mapping[str, object], *, label: str) -> str:
        try:
            return self._environment.from_string(text).render(**variables).strip()
        except Exception as exc:
            raise PromptTemplateError(
                '提示词渲染失败',
                template=label,
                error=f'{type(exc).__name__}: {exc}'
            ) from exc


# 一份已解析的提示词模板及其指纹
@dataclass(frozen=True, slots=True)
class ParsedPrompt:
    ref: PromptRef
    sections: tuple[tuple[str, str], ...]
    declared: frozenset[str]
    fingerprint: str


# 从磁盘读取提示词并按角色切分系统与用户消息
class FilePromptRepository:
    # 保存提示词设置并建立解析缓存
    def __init__(self, *, settings: PromptSettings) -> None:
        self._settings = settings
        self._renderer = PromptRenderer(strict_undefined=settings.strict_undefined)
        self._cache: dict[PromptRef, ParsedPrompt] = {}
        self._shared: dict[str, str] = {}
        self._shared_loaded = False

    # 返回模板声明的业务变量，剔除自动注入的片段
    def declared_variables(self, ref: PromptRef) -> frozenset[str]:
        return self._load(ref).declared - AUTO_VARIABLES

    # 渲染各分节并按角色拼成系统与用户消息
    def render(self, ref: PromptRef, variables: dict[str, object]) -> RenderedPrompt:
        parsed = self._load(ref)
        missing = sorted(parsed.declared - AUTO_VARIABLES - set(variables))
        if missing:
            raise PromptTemplateError('提示词渲染缺少必需变量', template=ref.name, missing=missing)
        scope: dict[str, object] = {**variables, 'shared': self._shared_fragments()}
        rendered = {
            name: self._renderer.render(body, scope, label=f'{ref.version}/{ref.name}#{name}')
            for name, body in parsed.sections
        }
        system = '\n\n'.join(rendered[name] for name in SYSTEM_ORDER if name in rendered)
        user = '\n\n'.join(rendered[name] for name in USER_ORDER if name in rendered)
        return RenderedPrompt(ref=ref, system=system, user=user, fingerprint=parsed.fingerprint)

    # 列出指定版本下可用的模板名
    def available(self, version: str | None = None) -> tuple[str, ...]:
        directory = self._settings.root / (version or self._settings.version)
        if not directory.is_dir():
            return ()
        return tuple(sorted(path.stem for path in directory.glob('*.txt')))

    # 读取并解析模板，结果进缓存
    def _load(self, ref: PromptRef) -> ParsedPrompt:
        cached = self._cache.get(ref)
        if cached is not None:
            return cached
        path = self._path_for(ref)
        raw = path.read_text(encoding='utf-8')
        sections = parse_sections(raw)
        ensure_required_sections(sections, REQUIRED_SECTIONS)
        declared = self._renderer.declared_variables('\n\n'.join(body for _, body in sections))
        parsed = ParsedPrompt(
            ref=ref,
            sections=sections,
            declared=declared,
            fingerprint=hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
        )
        if len(self._cache) >= self._settings.cache_size:
            self._cache.clear()
        self._cache[ref] = parsed
        logger.debug('加载提示词 {} 分节={} 变量={}', path.name, len(sections), sorted(declared))
        return parsed

    # 定位模板文件，缺失时报错
    def _path_for(self, ref: PromptRef) -> Path:
        path = self._settings.root / ref.version / f'{ref.name}.txt'
        if not path.is_file():
            raise PromptNotFound('提示词文件不存在', prompt=ref.name, version=ref.version, path=str(path))
        return path

    # 加载跨智能体共用的提示词片段
    def _shared_fragments(self) -> dict[str, str]:
        if self._shared_loaded:
            return self._shared
        directory = self._settings.root / self._settings.shared_dir
        if directory.is_dir():
            for path in sorted(directory.glob('*.txt')):
                self._shared[path.stem] = path.read_text(encoding='utf-8').strip()
        self._shared_loaded = True
        logger.debug('加载共用提示词片段 {}', sorted(self._shared))
        return self._shared
