# 客户端层自检：用 mock transport 验证拆信封、错误翻译与超时行为，不需要后端在跑
from __future__ import annotations

import json

import httpx

from desktop.api.client import ApiError, LearningClient


# 造一个固定返回 json 的传输层
def transport_returning(status: int, body: object) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body, request=request)

    return httpx.MockTransport(handler)


# 造一个固定返回原始字节的传输层
def transport_returning_bytes(status: int, content: bytes) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=content, request=request)

    return httpx.MockTransport(handler)


# 造一个必定抛超时的传输层
def transport_timing_out() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout('模拟读超时', request=request)

    return httpx.MockTransport(handler)


# 造一个必定连不上的传输层
def transport_offline() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('模拟连接失败', request=request)

    return httpx.MockTransport(handler)


PASSED: list[str] = []
FAILED: list[str] = []


# 断言辅助，失败时记录而不是中断，便于一次看完全部结果
def check(name: str, condition: bool, detail: str = '') -> None:
    if condition:
        PASSED.append(name)
        print(f'  [ok]   {name}')
        return
    FAILED.append(name)
    print(f'  [FAIL] {name}  {detail}')


# 用例一：成功响应应返回 data 部分
def test_success_unwrap() -> None:
    print('\n用例一：成功响应拆信封')
    transport = transport_returning(200, {'ok': True, 'data': {'status': 'ok', 'version': '0.1.0'}, 'error': None})
    with LearningClient(transport=transport) as client:
        result = client.health()
    check('返回 data 字段', result == {'status': 'ok', 'version': '0.1.0'}, str(result))


# 用例二：业务失败应抛出带错误码的 ApiError
def test_error_envelope() -> None:
    print('\n用例二：失败响应翻译成 ApiError')
    body = {
        'ok': False,
        'data': None,
        'error': {
            'code': 'phase_guard_violation',
            'message': '当前学习阶段不允许该操作',
            'detail': {'current_phase': 'learning', 'action': 'summarize'},
        },
    }
    transport = transport_returning(409, body)
    caught: ApiError | None = None
    with LearningClient(transport=transport) as client:
        try:
            client.summarize('sess-1')
        except ApiError as exc:
            caught = exc

    check('抛出 ApiError', caught is not None)
    if caught is not None:
        check('状态码为 409', caught.status_code == 409, str(caught.status_code))
        check('错误码正确', caught.code == 'phase_guard_violation', caught.code)
        check('detail 被保留', caught.detail.get('action') == 'summarize', str(caught.detail))
        check('str 输出可读', '409' in str(caught) and 'phase_guard_violation' in str(caught), str(caught))


# 用例三：非 JSON 响应不应崩溃
def test_non_json_response() -> None:
    print('\n用例三：非 JSON 响应')
    transport = transport_returning_bytes(502, b'<html>Bad Gateway</html>')
    caught: ApiError | None = None
    with LearningClient(transport=transport) as client:
        try:
            client.health()
        except ApiError as exc:
            caught = exc
    check('抛出 ApiError 而非崩溃', caught is not None, str(caught))
    if caught is not None:
        check('错误码为 invalid_response', caught.code == 'invalid_response', caught.code)


# 用例四：读超时应翻译成 client_timeout
def test_timeout() -> None:
    print('\n用例四：读超时')
    caught: ApiError | None = None
    with LearningClient(transport=transport_timing_out()) as client:
        try:
            client.generate_material('sess-1', 0)
        except ApiError as exc:
            caught = exc
    check('抛出 ApiError', caught is not None)
    if caught is not None:
        check('错误码为 client_timeout', caught.code == 'client_timeout', caught.code)
        check('提示后端可能仍在处理', '仍在处理' in caught.message, caught.message)


# 用例五：连不上后端应翻译成 client_offline
def test_offline() -> None:
    print('\n用例五：后端未启动')
    caught: ApiError | None = None
    with LearningClient(transport=transport_offline()) as client:
        try:
            client.health()
        except ApiError as exc:
            caught = exc
    check('抛出 ApiError', caught is not None)
    if caught is not None:
        check('错误码为 client_offline', caught.code == 'client_offline', caught.code)
        check('状态码为 0 表示未到达服务端', caught.status_code == 0, str(caught.status_code))


# 用例六：核对请求方法与路径是否与后端路由一致
def test_request_shape() -> None:
    print('\n用例六：请求方法与路径')
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={'ok': True, 'data': {}, 'error': None}, request=request)

    with LearningClient(transport=httpx.MockTransport(handler)) as client:
        client.start_session('资料', '需求')
        client.snapshot('abc')
        client.generate_material('abc', 2)
        client.read_material('abc', 2)
        client.ask('abc', '问题')
        client.check_examples('abc', [{'q_id': 'q1', 'answer': 'A'}])
        client.complete('abc')
        client.generate_exercises('abc')
        client.grade_exercises('abc', [{'q_id': 'q1', 'answer': 'A'}])
        client.summarize('abc')
        client.report('abc')

    expected = [
        ('POST', '/sessions'),
        ('GET', '/sessions/abc'),
        ('POST', '/sessions/abc/sections/2/material'),
        ('GET', '/sessions/abc/sections/2/material'),
        ('POST', '/sessions/abc/qa'),
        ('POST', '/sessions/abc/examples/check'),
        ('POST', '/sessions/abc/complete'),
        ('POST', '/sessions/abc/exercises/generate'),
        ('POST', '/sessions/abc/exercises/grade'),
        ('POST', '/sessions/abc/summary'),
        ('GET', '/sessions/abc/report'),
    ]
    check('全部请求形状与后端路由一致', seen == expected, json.dumps(seen, ensure_ascii=False))


# 用例七：素材接口应返回原始字节
def test_asset_bytes() -> None:
    print('\n用例七：素材原始字节')
    transport = transport_returning_bytes(200, b'\x89PNG\r\n\x1a\n')
    with LearningClient(transport=transport) as client:
        data = client.asset_bytes('img-001')
        url = client.asset_url('img-001')
    check('返回 bytes', isinstance(data, bytes) and data.startswith(b'\x89PNG'), str(data))
    check('asset_url 拼接正确', url.endswith('/assets/img-001'), url)


# 跑完全部用例并汇总
def main() -> int:
    print('=' * 60)
    print('LearningClient 自检')
    print('=' * 60)
    test_success_unwrap()
    test_error_envelope()
    test_non_json_response()
    test_timeout()
    test_offline()
    test_request_shape()
    test_asset_bytes()

    print('\n' + '=' * 60)
    print(f'通过 {len(PASSED)} 项，失败 {len(FAILED)} 项')
    if FAILED:
        print('失败项：')
        for item in FAILED:
            print(f'  - {item}')
    print('=' * 60)
    return 1 if FAILED else 0


if __name__ == '__main__':
    raise SystemExit(main())