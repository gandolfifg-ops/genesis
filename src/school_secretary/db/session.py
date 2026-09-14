from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from school_secretary.config import Settings, get_settings
from school_secretary.db.models import Base

_engine = None
_SessionLocal = None
_engine_url = None


def _url(settings: Settings) -> str:
    settings.ensure_dirs()
    return f"sqlite:///{settings.database_path}"


def get_engine(settings: Settings | None = None):
    global _engine, _SessionLocal, _engine_url
    settings = settings or get_settings()
    url = _url(settings)
    if _engine is None or _engine_url != url:
        _engine = create_engine(url, echo=False, future=True)
        _SessionLocal = sessionmaker(_engine, expire_on_commit=False, future=True)
        _engine_url = url
    return _engine


def init_db(settings: Settings | None = None) -> None:
    engine = get_engine(settings)
    Base.metadata.create_all(engine)


def get_session(settings: Settings | None = None) -> Session:
    get_engine(settings)
    assert _SessionLocal is not None
    return _SessionLocal()


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    session = get_session(settings)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    global _engine, _SessionLocal, _engine_url
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
    _engine_url = None
