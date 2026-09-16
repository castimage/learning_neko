# loguru全局配置与请求级日志中间件
from __future__ import annotations

import sys

from loguru import logger
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CONSOLE_FORMAT = (
    '<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | '
    '<level>{level: <8}</level> | '
    '<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - '
    '<level>{message}</level>'
)


# 按配置输出彩色日志或json日志
def configure_logging(level: str, json_output: bool) -> None:
    logger.remove()
    if json_output:
        logger.add(
            sys.stderr,
            level=level,
            serialize=True,
            backtrace=True,
            diagnose=False
        )
        return
    logger.add(
        sys.stderr,
        level=level,
        format=CONSOLE_FORMAT,
        colorize=True,
        backtrace=True,
        diagnose=True,
        enqueue=False
    )


# 为每个请求记录起止与响应状态码
class RequestLogMiddleware:
    # 保存下游应用
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    # 记录请求开始、结束与异常
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        method = scope.get('method', '-')
        path = scope.get('path', '-')
        seen = {'status': 0}
        logger.info('{} {} 开始', method, path)
        try:
            await self.app(scope, receive, _status_recorder(send, seen))
        except Exception as exc:
            logger.error('{} {} 异常 | {}: {}', method, path, type(exc).__name__, exc)
            raise
        finally:
            logger.info('{} {} 结束 | status={}', method, path, seen['status'])


# 包装发送函数以便读到响应状态码
def _status_recorder(send: Send, seen: dict[str, int]) -> Send:
    # 在响应开始时记下状态码
    async def sender(message: Message) -> None:
        if message['type'] == 'http.response.start':
            seen['status'] = int(message['status'])
        await send(message)

    return sender
