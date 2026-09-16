# 本地启动入口
from __future__ import annotations

import uvicorn

from learning_neko.bootstrap import create_app
from learning_neko.config import Settings

settings = Settings()
app = create_app(settings)


if __name__ == '__main__':
    uvicorn.run(
        'main:app',
        host=settings.app.host,
        port=settings.app.port,
        reload=settings.app.reload
    )
