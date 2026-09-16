# 统一响应信封，以及异常到信封的翻译
from __future__ import annotations

from typing import Generic, TypeVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from loguru import logger
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from learning_neko.domain.errors import DomainError

T = TypeVar('T')


# 失败的机器可读描述
class ErrorBody(BaseModel):
    code: str
    message: str
    detail: dict[str, object] = Field(default_factory=dict)


# 全部接口共用的统一响应结构
class ApiResponse(BaseModel, Generic[T]):
    ok: bool
    data: T | None = None
    error: ErrorBody | None = None


# 构造成功响应
def ok(data: T) -> ApiResponse[T]:
    return ApiResponse[T](ok=True, data=data, error=None)


# 构造失败响应
def fail(code: str, message: str, detail: dict[str, object] | None = None) -> ApiResponse[None]:
    return ApiResponse[None](
        ok=False,
        data=None,
        error=ErrorBody(code=code, message=message, detail=detail or {})
    )


# 把各类异常登记到应用
def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _handle_domain_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)


# 把错误渲染成统一信封的json响应
def _render(status_code: int, code: str, message: str, detail: dict[str, object]) -> JSONResponse:
    body = fail(code=code, message=message, detail=detail)
    return JSONResponse(status_code=status_code, content=body.model_dump(mode='json'))


# 处理领域异常并按其状态码返回
async def _handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
    logger.warning('domain error code={} message={} detail={}', exc.code, exc.message, exc.detail)
    return _render(exc.status_code, exc.code, exc.message, exc.detail)


# 处理请求参数校验失败
async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {
            'loc': [str(part) for part in item.get('loc', ())],
            'message': str(item.get('msg', '')),
            'type': str(item.get('type', ''))
        }
        for item in exc.errors()
    ]

    return _render(422, 'request_invalid', '请求参数校验未通过', {'errors': errors})


# 处理框架自身抛出的http异常
async def _handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _render(exc.status_code, f'http_{exc.status_code}', str(exc.detail), {})


# 兜底处理未预期异常
async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception('unhandled error type={} detail={}', type(exc).__name__, exc)
    return _render(500, 'internal_error', '服务内部错误', {'type': type(exc).__name__})
