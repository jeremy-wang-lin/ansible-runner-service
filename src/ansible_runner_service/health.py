# src/ansible_runner_service/health.py
import importlib.metadata
import platform
import subprocess
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


def get_worker_info(redis_client) -> dict:
    """Get RQ worker info from Redis."""
    try:
        workers = redis_client.smembers("rq:workers")
        worker_count = len(workers) if workers else 0

        queue_keys = redis_client.keys("rq:queue:*")
        queues = [k.decode().replace("rq:queue:", "") for k in queue_keys] if queue_keys else []

        return {"count": worker_count, "queues": sorted(queues)}
    except Exception:
        return {"count": 0, "queues": []}


def get_version_info() -> dict:
    """Get version information."""
    try:
        app_version = importlib.metadata.version("ansible-runner-service")
    except importlib.metadata.PackageNotFoundError:
        app_version = "unknown"

    try:
        result = subprocess.run(
            ["ansible", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        first_line = result.stdout.split("\n")[0]
        # Parse "ansible [core 2.20.2]"
        ansible_version = first_line.split("[core ")[1].rstrip("]") if "[core " in first_line else "unknown"
    except Exception:
        ansible_version = "unknown"

    return {
        "app": app_version,
        "ansible_core": ansible_version,
        "python": platform.python_version()
    }
