from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool
from app.config import settings 


_connect_args = {}
_engine_kwargs = {}
if settings.DATABASE_URL.startswith("sqlite"):
    # allow multi-thread access and, for in-memory DB used in tests,
    # ensure the same in-memory database is shared across connections by
    # using StaticPool.
    _connect_args = {"check_same_thread": False}
    if settings.DATABASE_URL == "sqlite:///:memory:":
        _engine_kwargs["poolclass"] = StaticPool

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=_connect_args,
    **_engine_kwargs
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()