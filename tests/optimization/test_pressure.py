"""B2 管网压力近似模型测试。"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from air_station.optimization import contracts as C
from air_station.optimization import pressure as PR


# 持续性预测 ------------------------------------------------------- #
def test_persistence_prediction(pressure_model_path):
    art = json.loads(open(pressure_model_path, encoding="utf-8").read())
    r = PR.predict_pressure(0.58, total_flow_m3_min=480.0, flow_delta_m3_min=0.0,
                            running_count=2, model_path=pressure_model_path)
    assert r["status"] == "success"
    if art["method"] == "persistence":
        assert r["predicted_pressure_mpa"] == pytest.approx(0.58, abs=1e-6)
    else:
        assert np.isfinite(r["predicted_pressure_mpa"])


# 无效输入 --------------------------------------------------------- #
@pytest.mark.parametrize("bad", [None, float("nan"), float("inf")])
def test_invalid_pressure_input(pressure_model_path, bad):
    r = PR.predict_pressure(bad, model_path=pressure_model_path)
    assert r["status"] == C.RC_INVALID_INPUT


# 模型缺失 --------------------------------------------------------- #
def test_pressure_model_missing():
    r = PR.predict_pressure(0.58, model_path="artifacts/models/no_such_pressure_model.json")
    assert r["status"] == C.RC_MODEL_UNAVAILABLE


# JSON 序列化 ------------------------------------------------------ #
def test_pressure_json_serializable(pressure_model_path):
    r = PR.predict_pressure(0.58, total_flow_m3_min=500.0, model_path=pressure_model_path)
    s = json.dumps(r, ensure_ascii=False, allow_nan=False)
    assert "NaN" not in s and "Infinity" not in s


# 能力声明: 不支持反事实 ------------------------------------------- #
def test_capability_flags_counterfactual_unsupported(pressure_model_path):
    r = PR.predict_pressure(0.58, model_path=pressure_model_path)
    assert r["validation_mode"] == "simulation"
    assert "NOT supported" in r["capability"]["counterfactual_after_dispatch"]
    assert r["caveats"]


# 合格带检查 ------------------------------------------------------- #
def test_check_pressure_band():
    assert PR.check_pressure_band(0.58)["pass"] is True
    assert PR.check_pressure_band(0.50)["pass"] is False
    assert PR.check_pressure_band(None)["status"] == C.RC_INVALID_INPUT


# 时间不泄漏: 目标 next_pressure 不得进入特征; 特征只用当前/过去 -------- #
def test_no_future_feature_leakage():
    assert "next_pressure_mpa" not in PR.FEATURES
    assert "current_pressure_mpa" in PR.FEATURES
    df = PR.build_station_frame()
    assert len(df) > 100
    # flow_delta 为向后(过去)差分, 与向前差分不同
    fwd = (df["total_flow_m3_min"].shift(-1) - df["total_flow_m3_min"]).dropna()
    assert not np.allclose(df["flow_delta_m3_min"].dropna().to_numpy()[:len(fwd)],
                           fwd.to_numpy(), equal_nan=True)
    # 目标列独立存在, 且与当前压力不同时刻
    assert "next_pressure_mpa" in df.columns


# 指标与证据已记录 ------------------------------------------------- #
def test_pressure_metrics_recorded(pressure_model_path):
    art = json.loads(open(pressure_model_path, encoding="utf-8").read())
    for seg in ("train", "valid", "test"):
        assert art["metrics"][seg]["MAE"] >= 0
    ev = art["evidence"]
    assert ev["lag1_autocorr"] > 0.9            # 强持续性
    assert abs(ev["dp_flow_delta_corr"]) < 0.3  # 与流量变化弱相关
    assert art["capability_statement"]["counterfactual_after_dispatch"].startswith("NOT")
