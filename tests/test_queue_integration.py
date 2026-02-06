# tests/test_queue_integration.py
"""Component integration tests for job queue (rq).

These tests verify that job arguments are correctly preserved through
the rq enqueue/dequeue cycle. They test the queue layer directly
without requiring a running rq worker.

Run with: pytest tests/test_queue_integration.py -v -m integration
Requires: Redis (docker-compose up -d)
"""
import pytest
from redis import Redis
from rq.job import Job

pytestmark = pytest.mark.integration


@pytest.fixture
def redis():
    """Real Redis connection for queue tests."""
    r = Redis()
    r.flushdb()  # Clean slate
    yield r
    r.flushdb()


def _find_job_by_kwarg(redis, key, value):
    """Find an rq job whose kwargs[key] == value."""
    for k in redis.keys("rq:job:*"):
        job_id = k.decode().replace("rq:job:", "")
        try:
            job = Job.fetch(job_id, connection=redis)
            if job.kwargs.get(key) == value:
                return job
        except Exception:
            continue
    return None


class TestQueueArgumentPreservation:
    """Test that job arguments survive the rq enqueue cycle."""

    def test_enqueue_preserves_all_job_arguments(self, redis):
        """Verify all job arguments are correctly stored in rq job.

        This catches bugs like rq's reserved 'job_id' kwarg collision
        where our job_id parameter was intercepted by rq instead of
        being passed to the worker function.
        """
        from ansible_runner_service.queue import enqueue_job

        # Enqueue a job with all arguments
        enqueue_job(
            job_id="test-queue-123",
            playbook="hello.yml",
            extra_vars={"name": "World", "count": 42},
            inventory="localhost,",
            redis=redis,
        )

        job = _find_job_by_kwarg(redis, "job_id", "test-queue-123")
        assert job is not None, "Enqueued job not found in Redis"
        assert job.kwargs["playbook"] == "hello.yml"
        assert job.kwargs["extra_vars"] == {"name": "World", "count": 42}
        assert job.kwargs["inventory"] == "localhost,"

    def test_enqueue_references_correct_worker_function(self, redis):
        """Verify enqueued job references the correct worker function."""
        from ansible_runner_service.queue import enqueue_job

        enqueue_job(
            job_id="test-func-123",
            playbook="test.yml",
            extra_vars={},
            inventory="localhost,",
            redis=redis,
        )

        job = _find_job_by_kwarg(redis, "job_id", "test-func-123")
        assert job is not None, "Enqueued job not found in Redis"
        assert job.func_name == "ansible_runner_service.worker.execute_job"

    def test_enqueue_with_empty_extra_vars(self, redis):
        """Verify empty extra_vars dict is preserved."""
        from ansible_runner_service.queue import enqueue_job

        enqueue_job(
            job_id="test-empty-vars",
            playbook="test.yml",
            extra_vars={},
            inventory="localhost,",
            redis=redis,
        )

        job = _find_job_by_kwarg(redis, "job_id", "test-empty-vars")
        assert job is not None, "Enqueued job not found in Redis"
        assert job.kwargs["extra_vars"] == {}
