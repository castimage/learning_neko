# asgi入口与启动说明
from __future__ import annotations

import uvicorn
from fastapi import FastAPI

from learning_neko.bootstrap import create_app
from learning_neko.config import Settings

app: FastAPI = create_app()


# 汇总本次启动的关键配置
def describe(settings: Settings) -> str:
    return '\n'.join(
        [
            f'启动 {settings.app.name} - http://{settings.app.host}:{settings.app.port}',
            f'数据目录 {settings.app.data_dir}',
            f'模型厂商 {settings.llm.active_provider}（备用链 {settings.llm.failover_providers}）',
            f'提示词版本 {settings.prompt.version}'
        ]
    )


# 打印启动说明并拉起uvicorn
def run() -> None:
    settings = Settings()
    print(describe(settings))
    uvicorn.run(
        'learning_neko.server:app',
        host=settings.app.host,
        port=settings.app.port,
        reload=settings.app.reload
    )
