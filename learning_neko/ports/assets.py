# 素材库端口的抽象契约
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


# 一份可取的素材，指向文件并声明其媒体类型
@dataclass(frozen=True, slots=True)
class AssetHandle:
    path: Path
    media_type: str


# 素材库的抽象契约，供提示词渲染与取素材接口共用
@runtime_checkable
class AssetCatalogPort(Protocol):
    # 渲染成提示词里可读的清单
    def describe_for_prompt(self) -> str: ...

    # 按key定位素材文件，不存在时返回空
    def locate(self, key: str) -> AssetHandle | None: ...
