# 领域模型包标识，并提供把模型列表序列化成json文本的共用助手
from __future__ import annotations

import json
from collections.abc import Sequence

from pydantic import BaseModel


# 把一组模型序列化成json文本
def dump_models(models: Sequence[BaseModel]) -> str:
    return json.dumps([item.model_dump(mode='json') for item in models], ensure_ascii=False)
