"""数据库引擎与会话 (阶段 C)。

默认 SQLite 落盘到 artifacts/air_station.db; 可通过环境变量
AIRSTATION_DB_URL 覆盖(例如测试用 sqlite:///:memory:)。
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DEFAULT_DB_URL = "sqlite:///artifacts/air_station.db"


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


def get_db_url() -> str:
    return os.environ.get("AIRSTATION_DB_URL", DEFAULT_DB_URL)


def _ensure_sqlite_dir(url: str) -> None:
    prefix = "sqlite:///"
    if url.startswith(prefix):
        path = url[len(prefix):]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)


def get_engine(url: str | None = None, echo: bool = False):
    global _engine, _SessionLocal
    url = url or get_db_url()
    if _engine is None or url != str(_engine.url):
        _ensure_sqlite_dir(url)
        kwargs = {"future": True, "echo": echo}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        _engine = create_engine(url, **kwargs)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_session_factory(url: str | None = None):
    get_engine(url)
    assert _SessionLocal is not None
    return _SessionLocal


def init_db(url: str | None = None):
    from air_station.db import models  # noqa: F401  确保模型注册
    engine = get_engine(url)
    Base.metadata.create_all(engine)
    return engine


def reset_engine() -> None:
    """测试辅助: 释放缓存引擎。"""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
