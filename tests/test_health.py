# tests/test_health.py
import pytest
from httpx import AsyncClient, ASGITransport

from ansible_runner_service.main import app


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestHealthLive:
    async def test_health_live_returns_ok(self, client: AsyncClient):
        response = await client.get("/health/live")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
