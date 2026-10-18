"""阶段 A1 校验器测试。"""
from __future__ import annotations

import pandas as pd

from air_station.data import validators


def _df(times) -> pd.DataFrame:
    return pd.DataFrame({"time": pd.to_datetime(times)})


def test_duplicate_timestamp_detected():
    df = _df(["2026-03-12 00:00:00", "2026-03-12 00:00:00", "2026-03-12 00:01:00"])
    issues, _, _ = validators.check_time_series({"T": df}, 60)
    assert any(i["code"] == "time_duplicate" for i in issues)


def test_out_of_order_detected():
    df = _df(["2026-03-12 00:01:00", "2026-03-12 00:00:00", "2026-03-12 00:02:00"])
    issues, _, _ = validators.check_time_series({"T": df}, 60)
    assert any(i["code"] == "time_unordered" for i in issues)


def test_non_minute_interval_warns():
    df = _df(["2026-03-12 00:00:00", "2026-03-12 00:05:00", "2026-03-12 00:06:00"])
    issues, _, _ = validators.check_time_series({"T": df}, 60)
    assert any(i["code"] == "freq_mismatch" for i in issues)


def test_alignment_ok_and_misaligned():
    a = _df(["2026-03-12 00:00:00", "2026-03-12 00:01:00"])
    b = _df(["2026-03-12 00:00:00", "2026-03-12 00:01:00"])
    _, al_ok, _ = validators.check_time_series({"A": a, "B": b}, 60)
    assert al_ok["aligned"] is True

    c = _df(["2026-03-12 00:00:00", "2026-03-12 00:03:00"])
    issues, al_bad, _ = validators.check_time_series({"A": a, "C": c}, 60)
    assert al_bad["aligned"] is False
    assert any(i["code"] == "alignment" for i in issues)


def test_missing_field_reports_file_and_field():
    df = pd.DataFrame({"time": pd.to_datetime(["2026-03-12 00:00:00"])})
    issues = validators.check_required_fields({"M": df}, {"M": ["time", "foo"]})
    hit = [i for i in issues if i["code"] == "field_missing"][0]
    assert hit["field"] == "foo"
    assert hit["file"] == "M"


def test_missing_file_reports_name(tmp_path):
    issues = validators.check_files_exist(tmp_path, ["nope.csv"])
    assert issues[0]["file"] == "nope.csv"
    assert issues[0]["code"] == "file_missing"


def test_hash_manifest(tmp_path):
    (tmp_path / "a.csv").write_text("x\n1\n")
    m = validators.hash_manifest(tmp_path, ["a.csv"])
    assert m["a.csv"]["exists"] is True
    assert len(m["a.csv"]["md5"]) == 32
    assert len(m["a.csv"]["sha256"]) == 64


def test_hash_is_deterministic(tmp_path):
    (tmp_path / "a.csv").write_text("x\n1\n")
    h1 = validators.file_hash(tmp_path / "a.csv")["digest"]
    h2 = validators.file_hash(tmp_path / "a.csv")["digest"]
    assert h1 == h2
