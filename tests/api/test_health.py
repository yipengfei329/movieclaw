from httpx import ASGITransport, AsyncClient

from movieclaw_api.app import create_app


async def test_healthcheck() -> None:
    app = create_app()

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
