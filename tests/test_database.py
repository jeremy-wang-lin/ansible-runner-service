import pytest
from unittest.mock import patch, MagicMock

from ansible_runner_service.database import get_database_url, get_engine, get_session


class TestGetDatabaseUrl:
    def test_returns_env_var_when_set(self):
        with patch.dict("os.environ", {"DATABASE_URL": "mysql+pymysql://custom@host/db"}):
            result = get_database_url()
            assert result == "mysql+pymysql://custom@host/db"

    def test_returns_default_when_env_not_set(self):
        with patch.dict("os.environ", {}, clear=True):
            result = get_database_url()
            assert result == "mysql+pymysql://root:devpassword@localhost:3306/ansible_runner"


class TestGetEngine:
    def test_creates_engine_with_url(self):
        with patch("ansible_runner_service.database.create_engine") as mock_create:
            mock_engine = MagicMock()
            mock_create.return_value = mock_engine

            engine = get_engine("mysql+pymysql://user:pass@localhost/db")

            mock_create.assert_called_once_with(
                "mysql+pymysql://user:pass@localhost/db",
                pool_pre_ping=True,
            )
            assert engine == mock_engine

    def test_uses_default_url_when_not_provided(self):
        with patch("ansible_runner_service.database.create_engine") as mock_create:
            with patch("ansible_runner_service.database.get_database_url") as mock_url:
                mock_url.return_value = "mysql+pymysql://default@host/db"
                mock_create.return_value = MagicMock()

                get_engine()

                mock_url.assert_called_once()
                mock_create.assert_called_once_with(
                    "mysql+pymysql://default@host/db",
                    pool_pre_ping=True,
                )


class TestGetSession:
    def test_creates_session_factory(self):
        mock_engine = MagicMock()

        session_factory = get_session(mock_engine)

        # Verify it's a sessionmaker
        assert callable(session_factory)
        # Verify configuration
        assert session_factory.kw.get("expire_on_commit") is False
