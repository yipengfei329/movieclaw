from __future__ import annotations

import logging

import pytest
from fastapi import Body
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from movieclaw_api.app import create_app
from movieclaw_api.exceptions import BadRequestException
from movieclaw_api.schemas.response import ApiResponse, ok

REQUEST_BODY = Body(...)


class MoviePayload(BaseModel):
    title: str


@pytest.fixture
def test_app():
    app = create_app()

    @app.get("/api/v1/test/success", response_model=ApiResponse[dict[str, str]])
    async def success_route() -> ApiResponse[dict[str, str]]:
        return ok({"title": "Inception"})

    @app.post("/api/v1/test/validation")
    async def validation_route(
        payload: MoviePayload = REQUEST_BODY,
    ) -> ApiResponse[dict[str, str]]:
        return ok({"title": payload.title})

    @app.get("/api/v1/test/app-error")
    async def app_error_route() -> None:
        raise BadRequestException(
            message="invalid query parameter",
            details=[{"field": "keyword", "message": "keyword is required"}],
        )

    @app.get("/api/v1/test/http-error")
    async def http_error_route() -> None:
        raise ValueError("database password leaked")

    return app


@pytest.fixture
async def client(test_app):
    async with AsyncClient(
        transport=ASGITransport(app=test_app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as async_client:
        yield async_client


async def test_success_response_uses_envelope(client: AsyncClient) -> None:
    response = await client.get("/api/v1/test/success")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "code": "OK",
        "message": "success",
        "data": {"title": "Inception"},
    }


async def test_healthcheck_remains_raw_response(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "success" not in response.json()


async def test_app_exception_returns_unified_error(client: AsyncClient) -> None:
    response = await client.get("/api/v1/test/app-error")

    assert response.status_code == 400
    assert response.json() == {
        "success": False,
        "code": "BAD_REQUEST",
        "message": "invalid query parameter",
        "details": [{"field": "keyword", "message": "keyword is required"}],
    }


async def test_validation_error_returns_standardized_payload(client: AsyncClient) -> None:
    response = await client.post("/api/v1/test/validation", json={})

    assert response.status_code == 422
    payload = response.json()
    assert payload["success"] is False
    assert payload["code"] == "VALIDATION_ERROR"
    assert payload["message"] == "request validation failed"
    assert payload["details"][0]["location"] == ["body", "title"]


async def test_unhandled_exception_hides_internal_details(client: AsyncClient) -> None:
    response = await client.get("/api/v1/test/http-error")

    assert response.status_code == 500
    assert response.json() == {
        "success": False,
        "code": "INTERNAL_SERVER_ERROR",
        "message": "internal server error",
    }
    assert "password" not in response.text.lower()


async def test_access_logs_are_emitted(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    await client.get("/api/v1/test/success")

    assert any(
        "method=GET path=/api/v1/test/success status_code=200" in message
        for message in caplog.messages
    )


async def test_error_logs_are_emitted_without_sensitive_details(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    await client.get("/api/v1/test/http-error")

    access_log_seen = any(
        "method=GET path=/api/v1/test/http-error status_code=500" in message
        for message in caplog.messages
    )
    internal_error_logged = any(
        "Unhandled application error" in message
        for message in caplog.messages
    )
    leaked_secret = any(
        "database password leaked" in message
        for message in caplog.messages
    )

    assert access_log_seen
    assert internal_error_logged
    assert leaked_secret is False
