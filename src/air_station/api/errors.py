"""统一错误响应 (阶段 C)。

所有服务层异常在 API 边界统一转换为:
{
  "status": "error",
  "error": {"code": "...", "message": "...", "detail": {...}, "request_id": "..."}
}
禁止把异常伪装成 status: success。
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

CODE_STATUS = {
    "E_VALIDATION": 400,
    "E_DATA_MISSING": 404,
    "E_NOT_FOUND": 404,
    "E_CONFLICT": 409,
    "E_RUNTIME": 500,
    "E_INTERNAL": 500,
    "E_METHOD": 405,
}


class ApiError(Exception):
    """服务层可抛出的显式错误, 携带统一错误码。"""

    def __init__(self, code: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


def _payload(code: str, message: str, detail: dict | None, request_id: str) -> dict:
    return {"status": "error",
            "error": {"code": code, "message": message,
                      "detail": detail or {}, "request_id": request_id}}


def _map_exception(exc: Exception) -> tuple[str, str, dict]:
    if isinstance(exc, ApiError):
        return exc.code, exc.message, exc.detail
    if isinstance(exc, FileNotFoundError):
        return "E_DATA_MISSING", str(exc) or "依赖的数据/配置缺失", {}
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return "E_VALIDATION", str(exc) or "请求参数不合法", {}
    if isinstance(exc, RuntimeError):
        return "E_RUNTIME", str(exc) or "运行时错误", {}
    return "E_INTERNAL", "服务内部错误", {"type": type(exc).__name__}


def install_error_handlers(app: FastAPI) -> None:
    @app.middleware("http")
    async def _request_id(request: Request, call_next):
        rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        return response

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        rid = getattr(request.state, "request_id", uuid.uuid4().hex)
        status = CODE_STATUS.get(exc.code, 400)
        return JSONResponse(status_code=status,
                            content=_payload(exc.code, exc.message, exc.detail, rid))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        rid = getattr(request.state, "request_id", uuid.uuid4().hex)
        return JSONResponse(status_code=422,
                            content=_payload("E_VALIDATION", "请求体校验失败",
                                             {"errors": exc.errors()}, rid))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        rid = getattr(request.state, "request_id", uuid.uuid4().hex)
        code = {404: "E_NOT_FOUND", 405: "E_METHOD"}.get(exc.status_code, "E_INTERNAL")
        return JSONResponse(status_code=exc.status_code,
                            content=_payload(code, str(exc.detail), {}, rid))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", uuid.uuid4().hex)
        code, message, detail = _map_exception(exc)
        status = CODE_STATUS.get(code, 500)
        return JSONResponse(status_code=status,
                            content=_payload(code, message, detail, rid))
