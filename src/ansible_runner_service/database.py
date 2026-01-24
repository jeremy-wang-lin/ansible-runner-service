import os
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session


def get_database_url() -> str:
    """Get database URL from environment."""
    return os.getenv(
        "DATABASE_URL",
        "mysql+pymysql://root:devpassword@localhost:3306/ansible_runner"
    )


def get_engine(url: str | None = None) -> Engine:
    """Create SQLAlchemy engine."""
    db_url = url or get_database_url()
    return create_engine(db_url, pool_pre_ping=True)


def get_session(engine: Engine) -> sessionmaker[Session]:
    """Create session factory."""
    return sessionmaker(bind=engine, expire_on_commit=False)
