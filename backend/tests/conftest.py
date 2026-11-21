import os

from cryptography.fernet import Fernet

# Must be set before anything from `app` is imported. Tests use their own database.
SERVER = "postgresql+psycopg://desk:desk@127.0.0.1:5432"
os.environ.update(
    DESK_ENVIRONMENT="dev",
    DESK_DATABASE_URL=f"{SERVER}/desk_test",
    DESK_SESSION_SECRET="test-secret",
    DESK_ALLOWED_DOMAINS="example.com",
    DESK_BOOTSTRAP_ADMIN_EMAIL="boss@example.com",
    DESK_DEV_LOGIN_ENABLED="true",
    DESK_TOKEN_KEY=Fernet.generate_key().decode(),
    DESK_ANTHROPIC_API_KEY="",  # deterministic: LLM is off unless a test configures it
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

with create_engine(f"{SERVER}/desk", isolation_level="AUTOCOMMIT").connect() as conn:
    if not conn.scalar(text("select 1 from pg_database where datname = 'desk_test'")):
        conn.execute(text("create database desk_test"))

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AppUser  # noqa: E402

HEADERS = {"X-Requested-With": "desk"}


@pytest.fixture(scope="session", autouse=True)
def schema():
    # Build the test schema from the real Alembic migrations, not metadata.create_all,
    # so a broken or incomplete migration fails the tests (e.g. a missing sequence).
    from alembic import command
    from alembic.config import Config

    Base.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.execute(text("drop table if exists alembic_version"))
        conn.execute(text("drop sequence if exists ticket_number_seq"))
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def empty_tables(schema):
    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"truncate {names} restart identity cascade"))


@pytest.fixture
def client():
    return TestClient(app, headers=HEADERS)


@pytest.fixture
def add_user():
    def add(email: str, role: str = "executive", **fields) -> int:
        with SessionLocal() as db:
            user = AppUser(email=email, role=role, **fields)
            db.add(user)
            db.commit()
            return user.id

    return add


@pytest.fixture
def login():
    def login(email: str) -> TestClient:
        c = TestClient(app, headers=HEADERS)
        assert c.post("/api/v1/auth/dev-login", json={"email": email}).status_code == 200, email
        return c

    return login
