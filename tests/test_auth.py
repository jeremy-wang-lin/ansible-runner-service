import hashlib
from unittest.mock import patch


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
