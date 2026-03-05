# Authentication Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add per-client API key authentication with admin management endpoints.

**Architecture:** FastAPI middleware intercepts all requests, checks X-API-Key header against SHA-256 hashes stored in MariaDB `clients` table. Health endpoints exempt. Admin endpoints use bootstrap key from env var. In-memory cache avoids DB lookup per request.

**Tech Stack:** FastAPI middleware, SQLAlchemy ORM, Alembic migration, SHA-256 hashing, hashlib, secrets

---

### Task 1: ClientModel + Alembic Migration

**Files:**
- Modify: `src/ansible_runner_service/models.py`
- Create: `alembic/versions/<auto>_create_clients_table.py` (via autogenerate)

**Step 1: Add ClientModel to models.py**

Add after the `JobModel` class:

```python
class ClientModel(Base):
    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __init__(self, **kwargs):
        kwargs.setdefault("id", str(uuid4()))
        kwargs.setdefault("created_at", datetime.now(timezone.utc))
        super().__init__(**kwargs)
```

Note: `uuid4`, `datetime`, `timezone` are already imported in models.py for `JobModel`.

**Step 2: Generate Alembic migration**

Run: `cd /Users/jeremy.lin/work/claude_code/ansible-runner-service && source .venv/bin/activate && alembic revision --autogenerate -m "create clients table"`

Verify the generated migration has `op.create_table("clients", ...)` in `upgrade()` and `op.drop_table("clients")` in `downgrade()`.

**Step 3: Run migration**

Run: `source .venv/bin/activate && alembic upgrade head`
Expected: Migration applies successfully.

**Step 4: Verify tests still pass**

Run: `source .venv/bin/activate && pytest tests/ -v --tb=short`
Expected: All existing tests pass (no behavior change).

**Step 5: Commit**

```bash
git add src/ansible_runner_service/models.py alembic/versions/*create_clients*
git commit -m "feat: add ClientModel and clients table migration"
```

---

### Task 2: ClientRepository with Tests

**Files:**
- Modify: `src/ansible_runner_service/repository.py`
- Create: `tests/test_client_repository.py`

**Step 1: Write the failing tests**

Create `tests/test_client_repository.py`:

```python
import hashlib
import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ansible_runner_service.models import Base, ClientModel
from ansible_runner_service.repository import ClientRepository


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


class TestClientRepository:
    def test_create_client(self, session: Session):
        repo = ClientRepository(session)
        client = repo.create("svc-deploy", "abc123hash")

        assert client.name == "svc-deploy"
        assert client.api_key_hash == "abc123hash"
        assert client.revoked_at is None

    def test_get_active_client_by_key_hash(self, session: Session):
        repo = ClientRepository(session)
        repo.create("svc-deploy", "abc123hash")

        client = repo.get_by_key_hash("abc123hash")
        assert client is not None
        assert client.name == "svc-deploy"

    def test_get_by_key_hash_returns_none_for_revoked(self, session: Session):
        repo = ClientRepository(session)
        repo.create("svc-deploy", "abc123hash")
        repo.revoke("svc-deploy")

        client = repo.get_by_key_hash("abc123hash")
        assert client is None

    def test_list_all_clients(self, session: Session):
        repo = ClientRepository(session)
        repo.create("svc-a", "hash-a")
        repo.create("svc-b", "hash-b")

        clients = repo.list_all()
        assert len(clients) == 2
        names = [c.name for c in clients]
        assert "svc-a" in names
        assert "svc-b" in names

    def test_revoke_client(self, session: Session):
        repo = ClientRepository(session)
        repo.create("svc-deploy", "abc123hash")

        result = repo.revoke("svc-deploy")
        assert result is True
        assert repo.get_by_name("svc-deploy").revoked_at is not None

    def test_revoke_nonexistent_returns_false(self, session: Session):
        repo = ClientRepository(session)
        result = repo.revoke("nonexistent")
        assert result is False

    def test_get_by_name(self, session: Session):
        repo = ClientRepository(session)
        repo.create("svc-deploy", "abc123hash")

        client = repo.get_by_name("svc-deploy")
        assert client is not None
        assert client.name == "svc-deploy"

    def test_get_all_active_key_hashes(self, session: Session):
        repo = ClientRepository(session)
        repo.create("svc-a", "hash-a")
        repo.create("svc-b", "hash-b")
        repo.revoke("svc-b")

        active = repo.get_all_active_key_hashes()
        assert active == {"hash-a": "svc-a"}
```

**Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_client_repository.py -v`
Expected: FAIL — `ClientRepository` does not exist or is missing methods.

**Step 3: Implement ClientRepository**

Add to `src/ansible_runner_service/repository.py`, after the `JobRepository` class:

```python
class ClientRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self, name: str, api_key_hash: str) -> ClientModel:
        client = ClientModel(name=name, api_key_hash=api_key_hash)
        self.session.add(client)
        self.session.commit()
        return client

    def get_by_name(self, name: str) -> ClientModel | None:
        return self.session.query(ClientModel).filter(
            ClientModel.name == name
        ).first()

    def get_by_key_hash(self, key_hash: str) -> ClientModel | None:
        return self.session.query(ClientModel).filter(
            ClientModel.api_key_hash == key_hash,
            ClientModel.revoked_at.is_(None),
        ).first()

    def list_all(self) -> list[ClientModel]:
        return self.session.query(ClientModel).order_by(
            ClientModel.created_at
        ).all()

    def revoke(self, name: str) -> bool:
        client = self.get_by_name(name)
        if client is None:
            return False
        client.revoked_at = datetime.now(timezone.utc)
        self.session.commit()
        return True

    def get_all_active_key_hashes(self) -> dict[str, str]:
        clients = self.session.query(ClientModel).filter(
            ClientModel.revoked_at.is_(None)
        ).all()
        return {c.api_key_hash: c.name for c in clients}
```

Add import at top of repository.py: `from ansible_runner_service.models import JobModel, ClientModel`

Also ensure `from datetime import datetime, timezone` is imported (already present for `JobRepository.count_jobs_since`).

**Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_client_repository.py -v`
Expected: All 8 tests pass.

**Step 5: Run full test suite**

Run: `source .venv/bin/activate && pytest tests/ -v --tb=short`
Expected: All tests pass.

**Step 6: Commit**

```bash
git add src/ansible_runner_service/repository.py tests/test_client_repository.py
git commit -m "feat: add ClientRepository with CRUD operations"
```

---

### Task 3: Auth Configuration Module

**Files:**
- Create: `src/ansible_runner_service/auth.py`
- Create: `tests/test_auth.py`

**Step 1: Write the failing tests**

Create `tests/test_auth.py`:

```python
import hashlib
import pytest
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
```

**Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_auth.py -v`
Expected: FAIL — `auth` module does not exist.

**Step 3: Implement auth module**

Create `src/ansible_runner_service/auth.py`:

```python
import hashlib
import os
import secrets


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
```

**Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_auth.py -v`
Expected: All 6 tests pass.

**Step 5: Commit**

```bash
git add src/ansible_runner_service/auth.py tests/test_auth.py
git commit -m "feat: add auth config module with key hashing and generation"
```

---

### Task 4: Auth Middleware with Tests

**Files:**
- Modify: `src/ansible_runner_service/main.py`
- Modify: `tests/test_auth.py`

**Step 1: Write the failing tests**

Add to `tests/test_auth.py`:

```python
from httpx import AsyncClient, ASGITransport
from ansible_runner_service.main import app


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
            assert response.status_code == 200

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
            with patch("ansible_runner_service.main.get_repository") as mock_repo:
                mock_repo_instance = MagicMock()
                mock_repo_instance.list_jobs.return_value = ([], 0)
                mock_repo.return_value = mock_repo_instance
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
            # May return 404/500 since admin endpoints aren't implemented yet,
            # but should NOT return 401
            assert response.status_code != 401
```

Add these imports to the top of `tests/test_auth.py`:

```python
from unittest.mock import patch, MagicMock
from ansible_runner_service.repository import ClientRepository
```

**Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_auth.py::TestAuthMiddleware -v`
Expected: FAIL — middleware not implemented yet, API calls won't return 401.

**Step 3: Implement auth middleware**

Add to `src/ansible_runner_service/main.py`:

1. Add imports at top:
```python
from starlette.requests import Request
from starlette.responses import JSONResponse as StarletteJSONResponse

from ansible_runner_service.auth import hash_api_key, get_admin_key_hash, is_auth_enabled
```

2. Add client cache (module-level):
```python
_client_cache: dict[str, str] = {}  # key_hash -> client_name


def get_client_cache() -> dict[str, str]:
    return _client_cache


def reload_client_cache(repository: ClientRepository):
    global _client_cache
    _client_cache = repository.get_all_active_key_hashes()
```

Add `from ansible_runner_service.repository import JobRepository, ClientRepository` (update existing import).

3. Add middleware after `app = FastAPI(...)`:
```python
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if not is_auth_enabled():
        return await call_next(request)

    path = request.url.path

    # Health endpoints and docs are exempt
    if path.startswith("/health") or path in ("/docs", "/openapi.json", "/redoc"):
        return await call_next(request)

    api_key = request.headers.get("X-API-Key")
    if not api_key:
        return StarletteJSONResponse(
            status_code=401,
            content={"detail": "Missing API key"},
        )

    key_hash = hash_api_key(api_key)

    # Admin endpoints require ADMIN_API_KEY
    if path.startswith("/admin"):
        admin_hash = get_admin_key_hash()
        if admin_hash is None or key_hash != admin_hash:
            return StarletteJSONResponse(
                status_code=401,
                content={"detail": "Invalid API key"},
            )
        return await call_next(request)

    # API endpoints require valid client key
    cache = get_client_cache()
    if key_hash not in cache:
        return StarletteJSONResponse(
            status_code=401,
            content={"detail": "Invalid API key"},
        )

    return await call_next(request)
```

4. Add cache loading to lifespan startup (inside the existing `try` block, after `recover_stale_jobs`):
```python
client_repo = ClientRepository(session)
reload_client_cache(client_repo)
```

**Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_auth.py -v`
Expected: All tests pass (both TestAuthConfig and TestAuthMiddleware).

**Step 5: Verify existing tests still pass**

Existing tests should pass because `AUTH_ENABLED` defaults to `true`, but the test environment doesn't set it. We need to ensure tests work. Check if tests are affected:

Run: `source .venv/bin/activate && AUTH_ENABLED=false pytest tests/ -v --tb=short`
Expected: All tests pass with auth disabled.

Note: We'll address the test env setup properly in Task 7.

**Step 6: Commit**

```bash
git add src/ansible_runner_service/main.py src/ansible_runner_service/auth.py tests/test_auth.py
git commit -m "feat: add auth middleware with health exemption and key validation"
```

---

### Task 5: Admin Create Client Endpoint with Tests

**Files:**
- Modify: `src/ansible_runner_service/main.py`
- Modify: `src/ansible_runner_service/schemas.py`
- Modify: `tests/test_auth.py`

**Step 1: Add schemas**

Add to `src/ansible_runner_service/schemas.py`:

```python
class CreateClientRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class CreateClientResponse(BaseModel):
    name: str
    api_key: str
    created_at: str


class ClientSummary(BaseModel):
    name: str
    created_at: str
    revoked_at: str | None
```

**Step 2: Write the failing tests**

Add to `tests/test_auth.py`:

```python
class TestAdminCreateClient:
    @pytest.fixture
    def admin_client(self):
        return AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )

    async def test_create_client_returns_key(self, admin_client: AsyncClient):
        """POST /admin/clients returns the plaintext key once."""
        mock_repo = MagicMock()
        mock_repo.get_by_name.return_value = None
        mock_repo.create.return_value = MagicMock(
            name="svc-deploy",
            created_at=datetime(2026, 2, 27, tzinfo=timezone.utc),
        )
        mock_repo.get_all_active_key_hashes.return_value = {}

        with patch.dict("os.environ", {"AUTH_ENABLED": "true", "ADMIN_API_KEY": "admin-secret"}):
            with patch("ansible_runner_service.main.get_client_repository", return_value=mock_repo):
                response = await admin_client.post(
                    "/admin/clients",
                    json={"name": "svc-deploy"},
                    headers={"X-API-Key": "admin-secret"},
                )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "svc-deploy"
        assert "api_key" in data
        assert len(data["api_key"]) == 64

    async def test_create_duplicate_client_returns_409(self, admin_client: AsyncClient):
        """POST /admin/clients returns 409 if name exists."""
        mock_repo = MagicMock()
        mock_repo.get_by_name.return_value = MagicMock()  # client exists

        with patch.dict("os.environ", {"AUTH_ENABLED": "true", "ADMIN_API_KEY": "admin-secret"}):
            with patch("ansible_runner_service.main.get_client_repository", return_value=mock_repo):
                response = await admin_client.post(
                    "/admin/clients",
                    json={"name": "svc-deploy"},
                    headers={"X-API-Key": "admin-secret"},
                )

        assert response.status_code == 409
```

Add imports to top of `tests/test_auth.py`:

```python
from datetime import datetime, timezone
```

**Step 3: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_auth.py::TestAdminCreateClient -v`
Expected: FAIL — endpoint not implemented yet.

**Step 4: Implement create client endpoint**

Add dependency to `src/ansible_runner_service/main.py`:

```python
def get_client_repository():
    """Dependency that provides a ClientRepository."""
    engine = get_engine_singleton()
    Session = get_session(engine)
    session = Session()
    try:
        yield ClientRepository(session)
    finally:
        session.close()
```

Add imports for new schemas:

```python
from ansible_runner_service.schemas import (
    ...,  # existing imports
    CreateClientRequest,
    CreateClientResponse,
    ClientSummary,
)
from ansible_runner_service.auth import hash_api_key, get_admin_key_hash, is_auth_enabled, generate_api_key
```

Add endpoint:

```python
@app.post("/admin/clients", response_model=CreateClientResponse, status_code=201)
def create_client(
    request: CreateClientRequest,
    repository: ClientRepository = Depends(get_client_repository),
):
    """Create a new API client. Returns the plaintext key once."""
    if repository.get_by_name(request.name):
        raise HTTPException(status_code=409, detail="Client already exists")

    api_key = generate_api_key()
    key_hash = hash_api_key(api_key)
    client = repository.create(request.name, key_hash)
    reload_client_cache(repository)

    return JSONResponse(
        status_code=201,
        content=CreateClientResponse(
            name=client.name,
            api_key=api_key,
            created_at=client.created_at.isoformat(),
        ).model_dump(),
    )
```

**Step 5: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_auth.py::TestAdminCreateClient -v`
Expected: All tests pass.

**Step 6: Commit**

```bash
git add src/ansible_runner_service/main.py src/ansible_runner_service/schemas.py tests/test_auth.py
git commit -m "feat: add POST /admin/clients endpoint"
```

---

### Task 6: Admin List and Revoke Endpoints with Tests

**Files:**
- Modify: `src/ansible_runner_service/main.py`
- Modify: `tests/test_auth.py`

**Step 1: Write the failing tests**

Add to `tests/test_auth.py`:

```python
class TestAdminListAndRevoke:
    @pytest.fixture
    def admin_client(self):
        return AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )

    async def test_list_clients(self, admin_client: AsyncClient):
        """GET /admin/clients lists all clients."""
        mock_client_a = MagicMock()
        mock_client_a.name = "svc-a"
        mock_client_a.created_at = datetime(2026, 2, 27, tzinfo=timezone.utc)
        mock_client_a.revoked_at = None

        mock_repo = MagicMock()
        mock_repo.list_all.return_value = [mock_client_a]

        with patch.dict("os.environ", {"AUTH_ENABLED": "true", "ADMIN_API_KEY": "admin-secret"}):
            with patch("ansible_runner_service.main.get_client_repository", return_value=mock_repo):
                response = await admin_client.get(
                    "/admin/clients",
                    headers={"X-API-Key": "admin-secret"},
                )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "svc-a"
        assert "api_key_hash" not in data[0]

    async def test_revoke_client(self, admin_client: AsyncClient):
        """DELETE /admin/clients/{name} revokes the client."""
        mock_repo = MagicMock()
        mock_repo.revoke.return_value = True
        mock_repo.get_all_active_key_hashes.return_value = {}

        with patch.dict("os.environ", {"AUTH_ENABLED": "true", "ADMIN_API_KEY": "admin-secret"}):
            with patch("ansible_runner_service.main.get_client_repository", return_value=mock_repo):
                response = await admin_client.delete(
                    "/admin/clients/svc-deploy",
                    headers={"X-API-Key": "admin-secret"},
                )

        assert response.status_code == 200
        assert response.json()["status"] == "revoked"

    async def test_revoke_nonexistent_returns_404(self, admin_client: AsyncClient):
        """DELETE /admin/clients/{name} returns 404 if not found."""
        mock_repo = MagicMock()
        mock_repo.revoke.return_value = False

        with patch.dict("os.environ", {"AUTH_ENABLED": "true", "ADMIN_API_KEY": "admin-secret"}):
            with patch("ansible_runner_service.main.get_client_repository", return_value=mock_repo):
                response = await admin_client.delete(
                    "/admin/clients/nonexistent",
                    headers={"X-API-Key": "admin-secret"},
                )

        assert response.status_code == 404
```

**Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_auth.py::TestAdminListAndRevoke -v`
Expected: FAIL — endpoints not implemented.

**Step 3: Implement list and revoke endpoints**

Add to `src/ansible_runner_service/main.py`:

```python
@app.get("/admin/clients")
def list_clients(
    repository: ClientRepository = Depends(get_client_repository),
):
    """List all API clients."""
    clients = repository.list_all()
    return [
        ClientSummary(
            name=c.name,
            created_at=c.created_at.isoformat(),
            revoked_at=c.revoked_at.isoformat() if c.revoked_at else None,
        ).model_dump()
        for c in clients
    ]


@app.delete("/admin/clients/{name}")
def revoke_client(
    name: str,
    repository: ClientRepository = Depends(get_client_repository),
):
    """Revoke an API client."""
    if not repository.revoke(name):
        raise HTTPException(status_code=404, detail="Client not found")

    reload_client_cache(repository)
    return {"status": "revoked"}
```

**Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_auth.py::TestAdminListAndRevoke -v`
Expected: All 3 tests pass.

**Step 5: Commit**

```bash
git add src/ansible_runner_service/main.py tests/test_auth.py
git commit -m "feat: add GET and DELETE /admin/clients endpoints"
```

---

### Task 7: Verify Full Test Suite with Auth

**Files:**
- Modify: `tests/conftest.py` (create if not exists)
- Possibly modify: `tests/test_auth.py`

**Step 1: Run full test suite to check auth impact**

Run: `source .venv/bin/activate && pytest tests/ -v --tb=short`

Expected: Some existing tests may fail with 401 because auth middleware is now active.

**Step 2: Create conftest.py to disable auth for existing tests**

Create `tests/conftest.py` (or modify if exists):

```python
import os

# Disable auth for all tests by default.
# Auth-specific tests override this with patch.dict.
os.environ.setdefault("AUTH_ENABLED", "false")
```

This must be at the top of conftest.py so it runs before any test imports the app.

**Step 3: Run full test suite**

Run: `source .venv/bin/activate && pytest tests/ -v --tb=short`
Expected: ALL tests pass — existing tests run with auth disabled, auth tests explicitly enable it via `patch.dict`.

**Step 4: Verify auth tests still work**

Run: `source .venv/bin/activate && pytest tests/test_auth.py -v`
Expected: All auth tests pass (they use `patch.dict` to set `AUTH_ENABLED=true`).

**Step 5: Commit**

```bash
git add tests/conftest.py
git commit -m "test: add conftest to disable auth for existing tests"
```

---

### Task 8: Documentation Updates

**Files:**
- Modify: `README.md`
- Modify: `docs/usage-guide.md`

**Step 1: Update README.md**

Add to Features list:
```markdown
- **API key authentication** - Per-client API keys with admin management endpoints
```

Add to Quick Start (after existing setup steps):
```markdown
# Set admin API key
export ADMIN_API_KEY=your-secret-admin-key
```

**Step 2: Update docs/usage-guide.md**

Add a new "Authentication" section with:

1. Overview of auth model (per-client API keys, admin bootstrap)
2. Environment variables (`ADMIN_API_KEY`, `AUTH_ENABLED`)
3. Admin endpoint examples (create, list, revoke clients)
4. Client usage example (X-API-Key header)
5. Exempt endpoints (/health/*)

Include curl examples for all admin operations and client API usage.

**Step 3: Commit**

```bash
git add README.md docs/usage-guide.md
git commit -m "docs: add authentication to README and usage guide"
```
