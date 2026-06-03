"""
Unit tests for src/api/error_handlers.py.

Covers both the HTTPException handler and the catch-all 500 handler.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api.error_handlers import register_handlers


def _make_app() -> FastAPI:
    app = FastAPI()
    register_handlers(app)

    @app.get("/http-error")
    async def raise_http():
        raise HTTPException(status_code=404, detail="not found")

    @app.get("/unhandled-error")
    async def raise_unhandled():
        raise RuntimeError("something exploded")

    return app


class TestHttpExceptionHandler:
    def test_http_exception_returns_tmf_error_body(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/http-error")
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "404"
        assert body["reason"] == "Not Found"
        assert body["message"] == "not found"
        assert body["@type"] == "Error"


class TestUnhandledExceptionHandler:
    def test_unhandled_exception_returns_500(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/unhandled-error")
        assert resp.status_code == 500
        body = resp.json()
        assert body["code"] == "500"
        assert body["reason"] == "Internal Server Error"
        assert body["@type"] == "Error"
