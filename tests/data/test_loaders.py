"""阶段 A1 加载器测试。"""
from __future__ import annotations

import pandas as pd

from air_station.data import loaders


def test_detect_encoding_gbk(tmp_path):
    p = tmp_path / "g.csv"
    p.write_bytes("时间,值\n甲,1\n".encode("gbk"))
    assert loaders.detect_encoding(p) in ("gbk", "gb18030")


def test_detect_encoding_utf8(tmp_path):
    p = tmp_path / "u.csv"
    p.write_text("time,值\n2026-03-12,1\n", encoding="utf-8")
    assert loaders.detect_encoding(p) in ("utf-8", "utf-8-sig")


def test_load_csv_parses_time(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("time,flow\n2026-03-12 00:00:00,5\n2026-03-12 00:01:00,6\n")
    df = loaders.load_csv(p)
    assert str(df["time"].dtype).startswith("datetime64")
    assert len(df) == 2


def test_load_json(tmp_path):
    p = tmp_path / "j.json"
    p.write_text('{"a": 1}')
    assert loaders.load_json(p) == {"a": 1}


def test_functions_are_readonly(tmp_path):
    p = tmp_path / "ro.csv"
    p.write_text("time,x\n2026-03-12 00:00:00,1\n")
    before = p.read_bytes()
    loaders.load_csv(p)
    assert p.read_bytes() == before


def test_load_all_sources_real_data():
    import os
    from pathlib import Path

    raw = Path(os.environ.get("AIR_STATION_RAW", "data"))
    required = loaders.REQUIRED_FILES
    if not all((raw / f).exists() for f in required):
        import pytest
        pytest.skip("原始数据不完整, 跳过真实数据集成测试")
    src = loaders.load_all_sources(raw)
    assert len(src["tables"]) == 7
    assert len(src["device_specs"]) == 2
    assert src["load_errors"] == {}
