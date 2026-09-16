# 文件系统素材库：读索引并按key定位文件
from __future__ import annotations

import json
import mimetypes
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, ConfigDict, ValidationError

from learning_neko.domain.models.enums import MediaKind
from learning_neko.ports.assets import AssetHandle

EMPTY_CATALOG_HINT = (
    '当前素材库为空，不存在任何可用素材：media 一律返回空数组，'
    '需要图示时改用 diagram 或 animation 自行给出规格，不要引用任何 asset_key。'
)


# 索引里的一条素材声明
class AssetEntry(BaseModel):
    model_config = ConfigDict(extra='forbid')

    key: str
    kind: MediaKind
    file: str
    media_type: str = ''
    title: str
    description: str = ''


# 索引文件的整体结构
class AssetIndex(BaseModel):
    model_config = ConfigDict(extra='forbid')

    version: int = 1
    assets: list[AssetEntry]


# 从磁盘索引读取素材，索引缺失或损坏时降级为空库
class FileAssetCatalog:
    # 读取索引，任何异常都按空库处理而不阻断启动
    def __init__(self, *, path: Path, root: Path) -> None:
        self._root = root
        self._entries: dict[str, AssetEntry] = {}
        try:
            index = AssetIndex.model_validate(json.loads(path.read_text(encoding='utf-8')))
        except FileNotFoundError:
            logger.info('素材索引不存在，素材库按空处理 | path={}', path)
            return
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            logger.warning('素材索引无法解析，素材库按空处理 | path={} reason={}', path, exc)
            return
        self._entries = {entry.key: entry for entry in index.assets}
        logger.info('素材库已装载 {} 条 | root={}', len(self._entries), root)

    # 渲染成提示词里可读的清单
    def describe_for_prompt(self) -> str:
        if not self._entries:
            return EMPTY_CATALOG_HINT
        lines = [f'- {item.key} | {item.kind} | {item.title} | {item.description}' for item in self._entries.values()]
        lines.append('（以上之外不存在任何素材，引用素材只能使用上面列出的 key）')
        return '\n'.join(lines)

    # 按key定位素材文件，越出素材目录或文件缺失时返回空
    def locate(self, key: str) -> AssetHandle | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        path = (self._root / entry.file).resolve()
        if not path.is_relative_to(self._root.resolve()) or not path.is_file():
            return None
        guessed = mimetypes.guess_type(path.name)[0]
        return AssetHandle(path=path, media_type=entry.media_type or guessed or 'application/octet-stream')
