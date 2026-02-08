# src/ansible_runner_service/queue.py
from typing import Any

from redis import Redis
from rq import Queue

from ansible_runner_service.schemas import SourceConfig


def get_queue(redis: Redis) -> Queue:
    return Queue(connection=redis)


def enqueue_job(
    job_id: str,
    playbook: str,
    extra_vars: dict[str, Any],
    inventory: str | dict,
    source_config: SourceConfig | None = None,
    options: dict | None = None,
    redis: Redis | None = None,
) -> None:
    """Enqueue a job for async execution."""
    if redis is None:
        redis = Redis()
    queue = Queue(connection=redis)
    # Use explicit kwargs to avoid collision with rq's reserved keywords
    # (rq uses 'job_id' internally for its own job tracking)
    queue.enqueue(
        "ansible_runner_service.worker.execute_job",
        kwargs={
            "job_id": job_id,
            "playbook": playbook,
            "extra_vars": extra_vars,
            "inventory": inventory,
            "source_config": source_config,
            "options": options,
        },
    )
