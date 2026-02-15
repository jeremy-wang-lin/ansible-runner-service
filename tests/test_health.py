# tests/test_health.py
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, MagicMock

from ansible_runner_service.main import app
from ansible_runner_service.health import get_worker_info, get_version_info


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

    async def test_health_ready_redis_down(self, client: AsyncClient):
        """Returns 503 when Redis is unreachable."""
        with patch("ansible_runner_service.main.check_redis", return_value=(False, 0)):
            with patch("ansible_runner_service.main.check_mariadb", return_value=(True, 5)):
                response = await client.get("/health/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "error"
        assert "redis" in data["reason"]

    async def test_health_ready_mariadb_down(self, client: AsyncClient):
        """Returns 503 when MariaDB is unreachable."""
        with patch("ansible_runner_service.main.check_redis", return_value=(True, 5)):
            with patch("ansible_runner_service.main.check_mariadb", return_value=(False, 0)):
                response = await client.get("/health/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "error"
        assert "mariadb" in data["reason"]


class TestHealthDetails:
    async def test_health_details_structure(self, client: AsyncClient):
        """Returns full health details with correct structure."""
        with patch("ansible_runner_service.main.check_redis", return_value=(True, 2)):
            with patch("ansible_runner_service.main.check_mariadb", return_value=(True, 3)):
                with patch("ansible_runner_service.main.get_worker_info", return_value={"count": 1, "queues": ["default"]}):
                    with patch("ansible_runner_service.main.get_queue_depth", return_value=5):
                        with patch("ansible_runner_service.main.get_jobs_last_hour", return_value=42):
                            with patch("ansible_runner_service.main.get_version_info", return_value={"app": "0.1.0", "ansible_core": "2.20.2", "python": "3.11.5"}):
                                response = await client.get("/health/details")

        assert response.status_code == 200
        data = response.json()

        # Check top-level structure
        assert data["status"] == "ok"
        assert "dependencies" in data
        assert "workers" in data
        assert "metrics" in data
        assert "version" in data

        # Check dependencies structure
        assert data["dependencies"]["redis"]["status"] == "ok"
        assert "latency_ms" in data["dependencies"]["redis"]
        assert data["dependencies"]["mariadb"]["status"] == "ok"

        # Check workers structure
        assert data["workers"]["count"] == 1
        assert "default" in data["workers"]["queues"]

        # Check metrics structure
        assert data["metrics"]["queue_depth"] == 5
        assert data["metrics"]["jobs_last_hour"] == 42

        # Check version structure
        assert data["version"]["app"] == "0.1.0"
        assert data["version"]["ansible_core"] == "2.20.2"
        assert data["version"]["python"] == "3.11.5"


class TestHealthHelpers:
    def test_get_worker_info(self):
        """Get worker count and queues from Redis."""
        mock_redis = MagicMock()
        mock_redis.smembers.return_value = {b"rq:worker:worker1", b"rq:worker:worker2"}
        mock_redis.keys.return_value = [b"rq:queue:default", b"rq:queue:high"]

        info = get_worker_info(mock_redis)

        assert info["count"] == 2
        assert "default" in info["queues"]
        assert "high" in info["queues"]

    def test_get_version_info(self):
        """Get app and ansible versions."""
        info = get_version_info()

        assert "app" in info
        assert "ansible_core" in info
        assert "python" in info
