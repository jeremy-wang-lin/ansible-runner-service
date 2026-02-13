# src/ansible_runner_service/health.py
import time
from sqlalchemy import text
from sqlalchemy.orm import Session


def check_redis(redis_client) -> tuple[bool, int]:
    """Check Redis connectivity. Returns (is_ok, latency_ms)."""
    try:
        start = time.perf_counter()
        redis_client.ping()
        latency_ms = int((time.perf_counter() - start) * 1000)
        return True, latency_ms
    except Exception:
        return False, 0


def check_mariadb(session: Session) -> tuple[bool, int]:
    """Check MariaDB connectivity. Returns (is_ok, latency_ms)."""
    try:
        start = time.perf_counter()
        session.execute(text("SELECT 1"))
        latency_ms = int((time.perf_counter() - start) * 1000)
        return True, latency_ms
    except Exception:
        return False, 0
