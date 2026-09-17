# 后端 HTTP 客户端：统一拆信封、把失败翻译成 ApiError，全程零 Qt 依赖
from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field


# 后端统一信封里的错误体，字段与 learning_neko.api.envelope.ErrorBody 一致
class ApiErrorBody(BaseModel):
    model_config = ConfigDict(extra='ignore')

    code: str = 'unknown'
    message: str = '未知错误'
    detail: dict[str, Any] = Field(default_factory=dict)


# 客户端侧唯一需要捕获的异常，界面只需处理这一种
class ApiError(Exception):
    # 保存状态码、机器可读错误码与附加上下文
    def __init__(self, status_code: int, body: ApiErrorBody) -> None:
        super().__init__(body.message)
        self.status_code = status_code
        self.code = body.code
        self.message = body.message
        self.detail = body.detail

    # 便于日志与调试时直接打印
    def __str__(self) -> str:
        return f'[{self.status_code}/{self.code}] {self.message}'


# 把后端原始响应体解析成客户端异常
def _as_api_error(response: httpx.Response) -> ApiError:
    try:
        payload = response.json()
    except ValueError:
        return ApiError(
            response.status_code,
            ApiErrorBody(code='invalid_response', message='后端返回了非 JSON 内容')
        )
    raw = payload.get('error')
    if not isinstance(raw, dict):
        return ApiError(
            response.status_code,
            ApiErrorBody(code='unknown', message=f'后端返回了未预期的响应结构（HTTP {response.status_code}）')
        )
    return ApiError(response.status_code, ApiErrorBody.model_validate(raw))


# 学习系统的 HTTP 客户端，同步接口，调用方负责放到后台线程
class LearningClient:
    # 连接超时给短，读超时给足，因为生成类接口要跑多轮模型调用
    def __init__(
        self,
        base_url: str = 'http://127.0.0.1:8000',
        *,
        connect_timeout: float = 5.0,
        read_timeout: float = 600.0,
        transport: httpx.BaseTransport | None = None
    ) -> None:
        self.base_url = base_url.rstrip('/')
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(
                connect=connect_timeout,
                read=read_timeout,
                write=30.0,
                pool=5.0
            ),
            transport=transport
        )

    # 统一拆信封：ok 为真取 data，否则抛 ApiError
    def _unwrap(self, response: httpx.Response) -> Any:
        try:
            payload = response.json()
        except ValueError:
            raise _as_api_error(response) from None

        if not isinstance(payload, dict) or 'ok' not in payload:
            raise ApiError(
                response.status_code,
                ApiErrorBody(
                    code='invalid_response',
                    message=f'后端未按统一信封返回（HTTP {response.status_code}）'
                )
            )

        if not payload['ok']:
            raise _as_api_error(response)

        return payload.get('data')

    # 发起请求并拆信封，把网络层异常也统一成 ApiError
    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise ApiError(
                0,
                ApiErrorBody(
                    code='client_timeout',
                    message=f'请求超时，后端可能仍在处理：{exc}',
                    detail={'path': path}
                )
            ) from exc
        except httpx.TransportError as exc:
            raise ApiError(
                0,
                ApiErrorBody(
                    code='client_offline',
                    message='无法连接后端，请确认服务已启动',
                    detail={'path': path, 'base_url': self.base_url, 'reason': str(exc)}
                )
            ) from exc

        return self._unwrap(response)

    # 关闭底层连接池
    def close(self) -> None:
        self._http.close()

    # 支持 with 语句，退出时自动关闭
    def __enter__(self) -> LearningClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ---------- 探针 ----------

    # 存活探针
    def health(self) -> dict[str, Any]:
        return self._request('GET', '/health')

    # 依赖就绪探针
    def ready(self) -> dict[str, Any]:
        return self._request('GET', '/ready')

    # 可用模型厂商
    def providers(self) -> dict[str, Any]:
        return self._request('GET', '/providers')

    # ---------- 学习流程 ----------

    # 提交资料与需求，创建学习会话
    def start_session(self, source_doc: str, user_request: str) -> dict[str, Any]:
        return self._request(
            'POST',
            '/sessions',
            json={'source_doc': source_doc, 'user_request': user_request}
        )

    # 列出最近的学习会话，供「打开历史会话」使用
    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        safe = max(1, min(int(limit), 200))     # 与后端 Query(ge=1, le=200) 对齐
        return self._request('GET', '/sessions', params={'limit': safe})

    # 读取会话快照，用于重启后续跑
    def snapshot(self, session_id: str) -> dict[str, Any]:
        return self._request('GET', f'/sessions/{session_id}')

    # 生成指定分节的学习资料、例题与关系图
    def generate_material(self, session_id: str, section_index: int) -> dict[str, Any]:
        return self._request(
            'POST',
            f'/sessions/{session_id}/sections/{section_index}/material'
        )

    # 读取指定分节已生成的学习资料，用于重新打开会话时回填
    def read_material(self, session_id: str, section_index: int) -> dict[str, Any]:
        return self._request(
            'GET',
            f'/sessions/{session_id}/sections/{section_index}/material'
        )

    # 就当前分节提问
    def ask(self, session_id: str, question: str) -> dict[str, Any]:
        return self._request(
            'POST',
            f'/sessions/{session_id}/qa',
            json={'question': question}
        )

    # 提交例题作答并逐题判定
    def check_examples(self, session_id: str, answers: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request(
            'POST',
            f'/sessions/{session_id}/examples/check',
            json={'answers': answers}
        )

    # 切换到学习完毕
    def complete(self, session_id: str) -> dict[str, Any]:
        return self._request('POST', f'/sessions/{session_id}/complete')

    # 生成课后测验
    def generate_exercises(self, session_id: str) -> dict[str, Any]:
        return self._request('POST', f'/sessions/{session_id}/exercises/generate')

    # 读取已生成的课后测验，用于重新打开测验页时回填
    def read_exercises(self, session_id: str) -> dict[str, Any]:
        return self._request('GET', f'/sessions/{session_id}/exercises')

    # 提交课后作答并逐题批阅
    def grade_exercises(self, session_id: str, answers: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request(
            'POST',
            f'/sessions/{session_id}/exercises/grade',
            json={'answers': answers}
        )

    # 汇总本次学习并写入记忆
    def summarize(self, session_id: str) -> dict[str, Any]:
        return self._request('POST', f'/sessions/{session_id}/summary')

    # 读取已归档的学习报告
    def report(self, session_id: str) -> dict[str, Any]:
        return self._request('GET', f'/sessions/{session_id}/report')

    # ---------- 素材 ----------

    # 取素材原始字节，这是全项目唯一不走统一信封的接口
    def asset_bytes(self, asset_key: str) -> bytes:
        try:
            response = self._http.get(f'/assets/{asset_key}')
        except httpx.TransportError as exc:
            raise ApiError(
                0,
                ApiErrorBody(
                    code='client_offline',
                    message='无法连接后端，请确认服务已启动',
                    detail={'asset_key': asset_key, 'reason': str(exc)}
                )
            ) from exc

        if response.status_code != 200:
            raise _as_api_error(response)

        return response.content

    # 拼出素材的完整 URL，供 QNetworkAccessManager 之类的组件直接使用
    def asset_url(self, asset_key: str) -> str:
        return f'{self.base_url}/assets/{asset_key}'