"""
SQLAlchemy engine and session factory.

Engine and session factory are created lazily on first access so that
importing this module does not require a live database driver / connection.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import settings

_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine():
    """Return the singleton SQLAlchemy engine (created on first call)."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.DATABASE_URL,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the singleton session factory (created on first call)."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(),
            autocommit=False,
            autoflush=False,
        )
    return _SessionLocal


# ── Backward-compatible aliases ──────────────────────────────
# Lazily evaluated so existing code like `from src.database import engine`
# keeps working once the engine has been initialised.


class _LazyProxy:
    """Thin proxy that defers object creation until first attribute access."""

    def __init__(self, factory):
        object.__setattr__(self, "_factory", factory)
        object.__setattr__(self, "_obj", None)

    def _resolve(self):
        obj = object.__getattribute__(self, "_obj")
        if obj is None:
            obj = object.__getattribute__(self, "_factory")()
            object.__setattr__(self, "_obj", obj)
        return obj

    def __getattr__(self, name):
        return getattr(self._resolve(), name)

    def __call__(self, *args, **kwargs):
        return self._resolve()(*args, **kwargs)


engine = _LazyProxy(get_engine)
SessionLocal = _LazyProxy(get_session_factory)


def get_db():
    """FastAPI dependency — yields a DB session and closes it after request."""
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()
