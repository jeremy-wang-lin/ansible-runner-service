# tests/test_health.py
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, MagicMock

from ansible_runner_service.main import app


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestHealthLive:
    async def test_health_live_returns_ok(self, client: AsyncClient):
        response = await client.get("/health/live")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestHealthReady:
    async def test_health_ready_success(self, client: AsyncClient):
        """Returns 200 when Redis and MariaDB are reachable."""
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True

        with patch("ansible_runner_service.main.get_redis", return_value=mock_redis):
            with patch("ansible_runner_service.health.check_mariadb", return_value=(True, 5)):
                response = await client.get("/health/ready")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
