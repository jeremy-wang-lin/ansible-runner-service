import hashlib
import os
import secrets

API_KEY_HEADER = "X-API-Key"


def hash_api_key(key: str) -> str:
    """Hash an API key using SHA-256."""
    return hashlib.sha256(key.encode()).hexdigest()


def generate_api_key() -> str:
    """Generate a random 32-byte hex API key."""
    return secrets.token_hex(32)


def get_admin_key_hash() -> str | None:
    """Get the hashed admin API key from environment."""
    key = os.environ.get("ADMIN_API_KEY")
    if key is None:
        return None
    return hash_api_key(key)


def is_auth_enabled() -> bool:
    """Check if authentication is enabled."""
    return os.environ.get("AUTH_ENABLED", "true").lower() != "false"
