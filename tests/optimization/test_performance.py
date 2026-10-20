"""B2 单机性能模型测试。"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from air_station.optimization import contracts as C
from air_station.optimization import performance as P


# 停机零流量 ------------------------------------------------------- #
def test_off_zero_flow_power_zero(power_model_path):
    r = P.estimate_power("4#", 0.0, model_path=power_model_path)
    assert r["status"] == "success"
    assert r["estimated_power_kw"] == 0.0
    assert r["specific_power_kw_per_m3min"] is None
    assert r["extrapolated"] is False


# 无效输入 --------------------------------------------------------- #
@pytest.mark.parametrize("bad", [-5.0, None, float("nan"), float("inf")])
def test_invalid_flow(power_model_path, bad):
    r = P.estimate_power("4#", bad, model_path=power_model_path)
    assert r["status"] == C.RC_INVALID_INPUT


def test_invalid_device(power_model_path):
    r = P.estimate_power("3#", 200.0, model_path=power_model_path)
    assert r["status"] == C.RC_INVALID_INPUT


# 模型缺失 --------------------------------------------------------- #
def test_model_missing():
    r = P.estimate_power("4#", 200.0, model_path="artifacts/models/no_such_power_model.json")
    assert r["status"] == C.RC_MODEL_UNAVAILABLE


# 外推 ------------------------------------------------------------- #
def test_extrapolation_flag(power_model_path):
    low = P.estimate_power("4#", 60.0, model_path=power_model_path)
    assert low["status"] == "success" and low["extrapolated"] is True and low["warnings"]
    high = P.estimate_power("4#", 285.0, model_path=power_model_path)
    assert high["status"] == "success" and high["extrapolated"] is True
    assert any("持续上限" in w or "安全容量" in w for w in high["warnings"])


# 逐机模型差异 ----------------------------------------------------- #
def test_per_device_models_differ(power_model_path):
    a = P.estimate_power("4#", 240.0, model_path=power_model_path)
    b = P.estimate_power("5#", 240.0, model_path=power_model_path)
    assert a["method"] != "off" and b["method"] != "off"
    assert a["estimated_power_kw"] != b["estimated_power_kw"]
    art = json.loads(open(power_model_path, encoding="utf-8").read())
    assert art["devices"]["4#"]["params"] != art["devices"]["5#"]["params"]


# 可选特征缺省 (只用流量) ------------------------------------------ #
def test_estimate_with_flow_only(power_model_path):
    r = P.estimate_power("4#", 240.0, model_path=power_model_path)
    assert r["status"] == "success" and np.isfinite(r["estimated_power_kw"])


# JSON 序列化 ------------------------------------------------------ #
def test_json_serializable(power_model_path):
    r = P.estimate_power("5#", 240.0, discharge_pressure_mpa=0.6, igv_percent=90.0,
                         ambient_temp_c=30.0, model_path=power_model_path)
    s = json.dumps(r, ensure_ascii=False, allow_nan=False)
    assert "NaN" not in s and "Infinity" not in s


# 样本筛选与异常观测 ----------------------------------------------- #
def _synthetic_device_frame():
    t = pd.date_range("2026-03-12 00:00", periods=12, freq="1min")
    rows = [
        dict(time=t[0], device_id="4#", running=False, flow_m3_min=0.0, power_kw=0.0, load_ratio=0.0),
        dict(time=t[1], device_id="4#", running=True, flow_m3_min=0.0, power_kw=5.0, load_ratio=0.0),   # 运行零流量
        dict(time=t[2], device_id="4#", running=True, flow_m3_min=250.0, power_kw=np.nan, load_ratio=0.9),  # 异常
        dict(time=t[3], device_id="4#", running=True, flow_m3_min=250.0, power_kw=-5.0, load_ratio=0.9),  # 异常
        dict(time=t[4], device_id="4#", running=True, flow_m3_min=50.0, power_kw=800.0, load_ratio=0.2),   # 低于下限
        dict(time=t[5], device_id="4#", running=True, flow_m3_min=290.0, power_kw=1400.0, load_ratio=1.1), # 超额
    ] + [dict(time=t[i], device_id="4#", running=True, flow_m3_min=250.0, power_kw=1300.0, load_ratio=0.9)
         for i in range(6, 12)]
    df = pd.DataFrame(rows)
    df["discharge_pressure_mpa"] = 0.58
    df["igv_percent"] = 100.0
    df["ambient_temp_c"] = 28.0
    return df


def test_screen_samples_excludes_off_zero_and_anomaly():
    cfg = P.load_config()
    df = _synthetic_device_frame()
    fit, stats = P.screen_samples(df, "4#", cfg)
    assert stats["stopped"] == 1
    assert stats["running_zero_flow"] == 1
    assert stats["below_validated_min"] == 1
    assert stats["above_validated_max"] == 1
    assert stats["nonfinite"] == 1
    # 拟合集只含 [lo,hi] 内且功率>0 的样本 (6 个正常 + 1 个负功率被剔 -> 6)
    assert len(fit) == 6
    assert (fit["flow_m3_min"] >= 120).all() and (fit["flow_m3_min"] <= 268.9).all()
    assert (fit["power_kw"] > 0).all()


# 时间切分不重叠 --------------------------------------------------- #
def test_time_split_disjoint():
    cfg = P.load_config()
    t = pd.Series(pd.date_range("2026-03-12", "2026-09-12", freq="1D"))
    m = P.split_masks(t, cfg)
    assert not (m["train"] & m["valid"]).any()
    assert not (m["train"] & m["test"]).any()
    assert not (m["valid"] & m["test"]).any()
    assert m["train"].sum() > 0 and m["valid"].sum() > 0 and m["test"].sum() > 0


# 模型指标有限且非负 ---------------------------------------------- #
def test_artifact_metrics_finite(power_model_path):
    art = json.loads(open(power_model_path, encoding="utf-8").read())
    assert art["validation_mode"] == "simulation"
    for did, d in art["devices"].items():
        for seg in ("train", "valid", "test"):
            mm = d["metrics"][seg]
            assert mm is not None and mm["MAE"] >= 0 and np.isfinite(mm["RMSE"])
        assert d["observed_max_m3min"] > d["validated_operating_max_m3min"]
        assert d["rated_capacity_m3min"] == pytest.approx(268.9)


# domain 与合计接口 ------------------------------------------------ #
def test_power_domain_and_total(power_model_path):
    d = P.power_domain("5#", model_path=power_model_path)
    assert d["status"] == "success" and d["min_flow_m3min"] < d["max_flow_m3min"]
    tot = P.estimate_total_power([{"device_id": "4#", "flow_m3_min": 230.0},
                                  {"device_id": "5#", "flow_m3_min": 230.0}],
                                 model_path=power_model_path)
    assert tot["status"] == "success" and tot["estimated_power_kw"] > 0
