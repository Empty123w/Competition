"""C 模块测试公共夹具: 每个测试使用独立的临时 SQLite 数据库。"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    from air_station.db import base as db_base
    url = f"sqlite:///{tmp_path}/test.db"
    monkeypatch.setenv("AIRSTATION_DB_URL", url)
    db_base.reset_engine()
    db_base.init_db()
    yield
    db_base.reset_engine()


@pytest.fixture()
def sim_reset():
    from air_station.control import simulator
    simulator.reset()
    yield
    simulator.reset()
