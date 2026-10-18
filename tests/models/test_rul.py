"""阶段 A5-RUL 测试: 可复现、无 episode 泄漏、区间顺序、JSON 合法、指标存在。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from air_station.models.rul import features as F
from air_station.models.rul import simulator, train as TRAIN

SEQ = Path("data/simulated/rul_sequences.parquet")
MODEL_DIR = Path("artifacts/models")
METRICS = Path("artifacts/metrics/rul_metrics.json")


def _cfg():
    return simulator.load_config("configs/rul.yaml")


def test_simulator_reproducible():
    a = simulator.generate(_cfg())
    b = simulator.generate(_cfg())
    pd.testing.assert_frame_equal(a, b)


def test_no_episode_leakage():
    cfg = _cfg()
    df = simulator.generate(cfg)
    splits = TRAIN.split_episodes(sorted(df["episode_id"].unique()), cfg)
    s = {k: set(v) for k, v in splits.items()}
    assert s["train"].isdisjoint(s["val"])
    assert s["train"].isdisjoint(s["test"])
    assert s["val"].isdisjoint(s["test"])
    # 每个 episode 只属于一个集合
    assert len(s["train"]) + len(s["val"]) + len(s["test"]) == df["episode_id"].nunique()


def test_simulator_reset_and_multiple_episodes():
    df = simulator.generate(_cfg())
    assert df["episode_id"].nunique() == 2 * _cfg()["episodes_per_device"]
    assert (df["origin"] == "simulated").all()
    # 每个设备有多个 episode
    assert (df.groupby("device_id")["episode_id"].nunique() == _cfg()["episodes_per_device"]).all()
    # 每个 episode 内 RUL 总体下降; 存在保养复位标记
    for ep, sub in df.groupby("episode_id"):
        assert sub["rul_hours"].iloc[0] > sub["rul_hours"].iloc[-1]
    assert df["maintenance_reset"].sum() == 2 * (_cfg()["episodes_per_device"] - 1)


def test_features_exclude_target():
    assert "rul_hours" not in F.FEATURES
    assert "time" not in F.FEATURES


def _need_rul():
    if not SEQ.exists() or not (MODEL_DIR / "rul_median.pkl").exists():
        pytest.skip("[lightweight-no-data] 缺少 RUL 序列/模型, 先运行 simulator+train")


def test_rul_json_bounds_and_schema():
    _need_rul()
    from air_station.models.rul import service
    out = service.rul_json()
    assert set(out.keys()) == {"status", "validation_mode", "evaluation_time", "devices"}
    assert out["status"] == "success"
    assert out["validation_mode"] == "simulation"
    assert [d["device_id"] for d in out["devices"]] == ["4#", "5#"]
    for d in out["devices"]:
        assert set(d.keys()) == {"device_id", "estimated_rul_hours",
                                 "lower_bound_hours", "upper_bound_hours", "method"}
        assert d["lower_bound_hours"] <= d["estimated_rul_hours"] <= d["upper_bound_hours"]
        for k in ("estimated_rul_hours", "lower_bound_hours", "upper_bound_hours"):
            assert np.isfinite(d[k]) and d[k] >= 0


def test_rul_json_serializable():
    _need_rul()
    from air_station.models.rul import service
    s = json.dumps(service.rul_json(), ensure_ascii=False, allow_nan=False)
    assert "NaN" not in s and "Infinity" not in s


def test_metrics_recorded():
    assert METRICS.exists(), "缺少 rul_metrics.json (先运行 evaluate)"
    d = json.loads(METRICS.read_text(encoding="utf-8"))
    assert d["validation_mode"] == "simulation"
    m = d["metrics"]
    for k in ("MAE", "RMSE", "MAPE"):
        assert k in m and np.isfinite(m[k])
    for k in ("interval_coverage", "monotonic_decreasing_ratio", "spearman_pred_true",
              "model_beats_baseline_MAE", "baseline"):
        assert k in d
    assert d["origin"] == "simulated"
