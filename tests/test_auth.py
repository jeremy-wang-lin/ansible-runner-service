import hashlib
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from httpx import AsyncClient, ASGITransport

from ansible_runner_service.main import app, get_repository, get_client_repository


class TestAuthConfig:
    def test_get_admin_key_hash_from_env(self):
        from ansible_runner_service.auth import get_admin_key_hash
        with patch.dict("os.environ", {"ADMIN_API_KEY": "my-secret-key"}):
            result = get_admin_key_hash()
            expected = hashlib.sha256("my-secret-key".encode()).hexdigest()
            assert result == expected

    def test_get_admin_key_hash_returns_none_when_not_set(self):
        from ansible_runner_service.auth import get_admin_key_hash
        with patch.dict("os.environ", {}, clear=True):
            result = get_admin_key_hash()
            assert result is None

    def test_auth_enabled_defaults_to_true(self):
        from ansible_runner_service.auth import is_auth_enabled
        with patch.dict("os.environ", {}, clear=True):
            assert is_auth_enabled() is True

    def test_auth_enabled_false(self):
        from ansible_runner_service.auth import is_auth_enabled
        with patch.dict("os.environ", {"AUTH_ENABLED": "false"}):
            assert is_auth_enabled() is False

    def test_hash_api_key(self):
        from ansible_runner_service.auth import hash_api_key
        key = "test-key-123"
        result = hash_api_key(key)
        expected = hashlib.sha256(key.encode()).hexdigest()
        assert result == expected

    def test_generate_api_key_length(self):
        from ansible_runner_service.auth import generate_api_key
        key = generate_api_key()
        assert len(key) == 64  # 32 bytes = 64 hex chars


class TestAuthMiddleware:
    @pytest.fixture
    def auth_client(self):
        return AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )

    async def test_health_endpoints_exempt(self, auth_client: AsyncClient):
        """Health endpoints should not require authentication."""
        with patch.dict("os.environ", {"AUTH_ENABLED": "true", "ADMIN_API_KEY": "admin"}):
            response = await auth_client.get("/health/live")
            # /health/live may not exist yet (returns 404), but must NOT return 401
            assert response.status_code != 401

    async def test_api_returns_401_without_key(self, auth_client: AsyncClient):
        """API endpoints should return 401 without X-API-Key header."""
        with patch.dict("os.environ", {"AUTH_ENABLED": "true", "ADMIN_API_KEY": "admin"}):
            response = await auth_client.get("/api/v1/jobs")
            assert response.status_code == 401
            assert response.json()["detail"] == "Missing API key"

    async def test_api_returns_401_with_invalid_key(self, auth_client: AsyncClient):
        """API endpoints should return 401 with an invalid key."""
        with patch.dict("os.environ", {"AUTH_ENABLED": "true", "ADMIN_API_KEY": "admin"}):
            with patch("ansible_runner_service.main.get_client_cache", return_value={}):
                response = await auth_client.get(
                    "/api/v1/jobs",
                    headers={"X-API-Key": "invalid-key"},
                )
                assert response.status_code == 401
                assert response.json()["detail"] == "Invalid API key"

    async def test_auth_disabled_passes_all_requests(self, auth_client: AsyncClient):
        """When AUTH_ENABLED=false, all requests pass without key."""
        with patch.dict("os.environ", {"AUTH_ENABLED": "false"}):
            mock_repo_instance = MagicMock()
            mock_repo_instance.list_jobs.return_value = ([], 0)
            app.dependency_overrides[get_repository] = lambda: mock_repo_instance
            try:
                response = await auth_client.get("/api/v1/jobs")
            finally:
                app.dependency_overrides.clear()
            assert response.status_code == 200

    async def test_admin_endpoint_requires_admin_key(self, auth_client: AsyncClient):
        """Admin endpoints should reject client keys."""
        client_key = "client-key-123"
        client_hash = hashlib.sha256(client_key.encode()).hexdigest()
        with patch.dict("os.environ", {"AUTH_ENABLED": "true", "ADMIN_API_KEY": "admin-secret"}):
            with patch("ansible_runner_service.main.get_client_cache", return_value={client_hash: "svc-a"}):
                response = await auth_client.get(
                    "/admin/clients",
                    headers={"X-API-Key": client_key},
                )
                assert response.status_code == 401

    async def test_admin_endpoint_accepts_admin_key(self, auth_client: AsyncClient):
        """Admin endpoints should accept ADMIN_API_KEY."""
        with patch.dict("os.environ", {"AUTH_ENABLED": "true", "ADMIN_API_KEY": "admin-secret"}):
            response = await auth_client.get(
                "/admin/clients",
                headers={"X-API-Key": "admin-secret"},
            )
            # May return 404/405 since admin endpoints aren't implemented yet,
            # but should NOT return 401
            assert response.status_code != 401

    async def test_api_passes_with_valid_client_key(self, auth_client: AsyncClient):
        """API endpoints should accept valid client keys."""
        client_key = "valid-client-key"
        client_hash = hashlib.sha256(client_key.encode()).hexdigest()
        with patch.dict("os.environ", {"AUTH_ENABLED": "true", "ADMIN_API_KEY": "admin"}):
            with patch("ansible_runner_service.main.get_client_cache",
                       return_value={client_hash: "svc-deploy"}):
                mock_repo = MagicMock()
                mock_repo.list_jobs.return_value = ([], 0)
                app.dependency_overrides[get_repository] = lambda: mock_repo
                try:
                    response = await auth_client.get(
                        "/api/v1/jobs",
                        headers={"X-API-Key": client_key},
                    )
                finally:
                    app.dependency_overrides.clear()
                assert response.status_code == 200


class TestAdminCreateClient:
    @pytest.fixture
    def auth_client(self):
        return AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )

    async def test_create_client_returns_key(self, auth_client: AsyncClient):
        """POST /admin/clients returns the plaintext key once."""
        mock_repo = MagicMock()
        mock_repo.get_by_name.return_value = None
        mock_client = MagicMock()
        mock_client.name = "svc-deploy"
        mock_client.created_at = datetime(2026, 2, 27, tzinfo=timezone.utc)
        mock_repo.create.return_value = mock_client
        mock_repo.get_all_active_key_hashes.return_value = {}

        app.dependency_overrides[get_client_repository] = lambda: mock_repo
        try:
            with patch.dict("os.environ", {"AUTH_ENABLED": "true", "ADMIN_API_KEY": "admin-secret"}):
                response = await auth_client.post(
                    "/admin/clients",
                    json={"name": "svc-deploy"},
                    headers={"X-API-Key": "admin-secret"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "svc-deploy"
        assert "api_key" in data
        assert len(data["api_key"]) == 64
        assert "created_at" in data

    async def test_create_duplicate_client_returns_409(self, auth_client: AsyncClient):
        """POST /admin/clients returns 409 if name exists."""
        mock_repo = MagicMock()
        mock_repo.get_by_name.return_value = MagicMock()  # client exists

        app.dependency_overrides[get_client_repository] = lambda: mock_repo
        try:
            with patch.dict("os.environ", {"AUTH_ENABLED": "true", "ADMIN_API_KEY": "admin-secret"}):
                response = await auth_client.post(
                    "/admin/clients",
                    json={"name": "svc-deploy"},
                    headers={"X-API-Key": "admin-secret"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 409
