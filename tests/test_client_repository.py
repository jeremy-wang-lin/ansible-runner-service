import pytest
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

    def test_revoke_already_revoked_returns_false(self, session: Session):
        repo = ClientRepository(session)
        repo.create("svc-deploy", "abc123hash")
        repo.revoke("svc-deploy")
        original_revoked_at = repo.get_by_name("svc-deploy").revoked_at

        result = repo.revoke("svc-deploy")
        assert result is False
        assert repo.get_by_name("svc-deploy").revoked_at == original_revoked_at

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
