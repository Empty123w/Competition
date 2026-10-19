"""健康/RUL as-of 接入与保守回退测试 (含 A6 真实历史表集成)。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from air_station.optimization import health_rul_adapter as HR

ROOT = Path(__file__).resolve().parents[2]
H = ROOT / "artifacts" / "metrics" / "health_history.parquet"
R = ROOT / "artifacts" / "metrics" / "rul_history.parquet"


def _health_df(rows):
    df = pd.DataFrame(rows, columns=["time", "device_id", "health_score", "risk_level",
                                     "quality_valid", "quality_reason", "valid_until",
                                     "max_staleness_minutes"])
    df["time"] = pd.to_datetime(df["time"]); df["valid_until"] = pd.to_datetime(df["valid_until"])
    return _prep(df)


def _prep(df):
    df = df.copy(); df["_dev"] = df["device_id"].map(HR.normalize_device_id)
    return df.sort_values("time").reset_index(drop=True)


def _rul_df(rows):
    df = pd.DataFrame(rows, columns=["time", "device_id", "estimated_rul_hours", "quality_valid",
                                     "quality_reason", "out_of_distribution", "fresh_prediction",
                                     "valid_until", "max_staleness_hours", "model_training_origin"])
    df["time"] = pd.to_datetime(df["time"]); df["valid_until"] = pd.to_datetime(df["valid_until"])
    return _prep(df)


def test_asof_health_ok_and_backward_join():
    h = _health_df([
        ["2026-08-01 00:00:00", 4, 95.0, "正常", True, "ok", "2026-08-01 01:00:00", 60],
        ["2026-08-01 01:00:00", 4, 80.0, "关注", True, "ok", "2026-08-01 02:00:00", 60]])
    r = HR.asof_health(h, "4#", "2026-08-01 00:30:00")   # 取 00:00 记录
    assert r["status"] == "ok" and r["risk_level"] == "normal"


def test_health_invalid_when_quality_false():
    h = _health_df([["2026-08-01 00:00:00", 4, 100.0, "正常", False, "not_running",
                     "2026-08-01 01:00:00", 60]])
    assert HR.asof_health(h, "4#", "2026-08-01 00:30:00")["status"] == "invalid"


def test_health_expired_when_stale():
    h = _health_df([["2026-08-01 00:00:00", 4, 90.0, "预警", True, "ok",
                     "2026-08-01 01:00:00", 60]])
    assert HR.asof_health(h, "4#", "2026-08-01 02:00:00")["status"] == "expired"


def test_rul_ood_is_not_usable():
    r = _rul_df([["2026-08-01 00:00:00", 4, 1000.0, False, "out_of_distribution", True, True,
                  "2026-08-01 06:00:00", 6, "simulated"]])
    assert HR.asof_rul(r, "4#", "2026-08-01 01:00:00")["status"] == "ood"


def test_rul_valid_usable_but_flagged_simulation():
    r = _rul_df([["2026-08-01 00:00:00", 4, 1200.0, True, "ok", False, True,
                  "2026-08-01 06:00:00", 6, "simulated"]])
    out = HR.asof_rul(r, "4#", "2026-08-01 01:00:00")
    assert out["status"] == "ok" and out["evidence"] == "simulation"


def test_dispatch_fallback_rul_invalid_to_health():
    h = _health_df([["2026-08-01 00:00:00", 4, 70.0, "预警", True, "ok", "2026-08-01 01:00:00", 60]])
    r = _rul_df([["2026-08-01 00:00:00", 4, 1000.0, False, "out_of_distribution", True, True,
                  "2026-08-01 06:00:00", 6, "simulated"]])
    out = HR.dispatch_health_rul(h, r, "4#", "2026-08-01 00:30:00")
    assert out["fallback"] == "health" and out["risk_level"] == "warning"


def test_dispatch_fallback_unknown_when_health_and_rul_invalid():
    h = _health_df([["2026-08-01 00:00:00", 4, 100.0, "正常", False, "not_running",
                     "2026-08-01 01:00:00", 60]])
    r = _rul_df([["2026-08-01 00:00:00", 4, 1000.0, False, "out_of_distribution", True, True,
                  "2026-08-01 06:00:00", 6, "simulated"]])
    out = HR.dispatch_health_rul(h, r, "4#", "2026-08-01 00:30:00")
    assert out["fallback"] == "unknown" and out["risk_level"] == "unknown"


@pytest.mark.skipif(not (H.exists() and R.exists()), reason="A6 历史表缺失")
def test_real_a6_history_tables():
    h = HR.load_history(H)
    r = HR.load_history(R)
    # 存在可用与不可用记录, 适配器均返回结构化状态
    t = "2026-06-01 12:00:00"
    hh = HR.asof_health(h, "4#", t)
    rr = HR.asof_rul(r, "4#", t)
    assert hh["status"] in {"ok", "invalid", "expired", "unknown"}
    assert rr["status"] in {"ok", "ood", "invalid", "expired", "unknown"}
    # RUL OOD 占比高 (如实反映)
    assert (r["out_of_distribution"]).mean() > 0.5
