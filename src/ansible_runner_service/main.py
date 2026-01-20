# src/ansible_runner_service/main.py
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException

from ansible_runner_service.runner import run_playbook
from ansible_runner_service.schemas import JobRequest, JobResponse

app = FastAPI(title="Ansible Runner Service")

PLAYBOOKS_DIR = Path(__file__).parent.parent.parent / "playbooks"


def get_playbooks_dir() -> Path:
    """Dependency for playbooks directory (allows test override)."""
    return PLAYBOOKS_DIR


@app.post("/api/v1/jobs", response_model=JobResponse)
def submit_job(
    request: JobRequest,
    playbooks_dir: Path = Depends(get_playbooks_dir),
) -> JobResponse:
    """Submit a playbook job for execution."""
    # Block path traversal attempts
    if ".." in request.playbook or request.playbook.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid playbook name")

    print(f"Playbook path: {playbooks_dir}")
    playbook_path = playbooks_dir / request.playbook

    if not playbook_path.exists():
        raise HTTPException(status_code=404, detail=f"Playbook not found: {request.playbook}")

    result = run_playbook(
        playbook=request.playbook,
        extra_vars=request.extra_vars,
        inventory=request.inventory,
        playbooks_dir=playbooks_dir,
    )

    return JobResponse(
        status=result.status,
        rc=result.rc,
        stdout=result.stdout,
        stats=result.stats,
    )
