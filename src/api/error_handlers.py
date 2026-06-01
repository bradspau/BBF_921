"""
Global exception handlers for the TMF921 FastAPI application.

All error responses conform to the TMF error body format:
  {"code": "...", "reason": "...", "message": "...", "@type": "Error"}
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

_REASONS: dict[int, str] = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    415: "Unsupported Media Type",
    422: "Unprocessable Entity",
    500: "Internal Server Error",
}


def _error_body(status_code: int, message: str) -> dict:
    return {
        "code":    str(status_code),
        "reason":  _REASONS.get(status_code, "Error"),
        "message": message,
        "@type":   "Error",
    }


async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(exc.status_code, detail),
    )


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content=_error_body(500, "An unexpected error occurred"),
    )


def register_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
