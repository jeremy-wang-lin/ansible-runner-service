import pytest
from unittest.mock import patch, MagicMock


class TestGetEngine:
    def test_creates_engine_with_url(self):
        from ansible_runner_service.database import get_engine

        with patch("ansible_runner_service.database.create_engine") as mock_create:
            mock_engine = MagicMock()
            mock_create.return_value = mock_engine

            engine = get_engine("mysql+pymysql://user:pass@localhost/db")

            mock_create.assert_called_once_with(
                "mysql+pymysql://user:pass@localhost/db",
                pool_pre_ping=True,
            )
            assert engine == mock_engine


class TestGetSession:
    def test_creates_session(self):
        from ansible_runner_service.database import get_session, get_engine

        with patch("ansible_runner_service.database.create_engine"):
            engine = get_engine("mysql+pymysql://user:pass@localhost/db")
            session = get_session(engine)

            # Session should be a sessionmaker instance
            assert callable(session)
