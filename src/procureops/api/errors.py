"""Stable HTTP error envelopes and request correlation for the public API."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class APIError(Exception):
    """An expected application error that is safe to expose to an API client."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.details = details or {}


def request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", "")
    if value:
        return str(value)
    value = request.headers.get("x-request-id", "").strip() or f"req-{uuid4().hex}"
    request.state.request_id = value
    return value


def _envelope(
    request: Request,
    *,
    code: str,
    message: str,
    status_code: int,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error_code": code,
            "message": message,
            # Keep FastAPI's familiar field during the migration to the
            # governed envelope so existing clients can upgrade safely.
            "detail": message,
            "request_id": request_id(request),
            "retryable": retryable,
            "details": details or {},
        },
        headers={"X-Request-ID": request_id(request)},
    )


def install_error_handling(app: FastAPI) -> None:
    """Install request IDs plus deterministic error envelopes.

    The handler intentionally keeps `HTTPException` compatibility while making
    errors from dependencies, validation and unexpected failures observable in
    one stable shape.
    """

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next: Callable):
        request.state.request_id = (
            request.headers.get("x-request-id", "").strip() or f"req-{uuid4().hex}"
        )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id(request)
        return response

    @app.exception_handler(APIError)
    async def application_error_handler(request: Request, exc: APIError) -> JSONResponse:
        return _envelope(
            request,
            code=exc.code,
            message=exc.message,
            status_code=exc.status_code,
            retryable=exc.retryable,
            details=exc.details,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _envelope(
            request,
            code="REQUEST_VALIDATION_ERROR",
            message="请求参数校验失败",
            status_code=422,
            details={"errors": jsonable_encoder(exc.errors())},
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "请求失败"
        return _envelope(
            request,
            code=f"HTTP_{exc.status_code}",
            message=detail,
            status_code=exc.status_code,
            retryable=exc.status_code in {408, 425, 429, 502, 503, 504},
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, _exc: Exception) -> JSONResponse:
        return _envelope(
            request,
            code="INTERNAL_SERVER_ERROR",
            message="服务暂时不可用，请稍后重试",
            status_code=500,
            retryable=True,
        )
