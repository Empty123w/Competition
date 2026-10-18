"""阶段 A2 标准化测试: 清洗规则与可重复性。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from air_station.data import cleaners

TIMES = pd.to_datetime(["2026-03-12 00:00:00", "2026-03-12 00:01:00", "2026-03-12 00:02:00",
                        "2026-03-12 00:03:00", "2026-03-12 00:04:00"])


def _make_raw(raw: Path) -> None:
    raw.mkdir(parents=True, exist_ok=True)
    op = pd.DataFrame({
        "time": TIMES,
        "4#排气压力": [0.6, 0.6, 0.6, 0.6, 0.6], "5#排气压力": [0.58, 0.58, 0.58, 0.58, 0.58],
        "4#实时流量": [200, 0, 180, 200, 0], "5#实时流量": [150, 150, 150, 150, 150],
        "4#A相电流": [90, 0, 88, 90, 0], "5#A相电流": [80, 80, 80, 80, 80],
        "4#B相电流": [0, 0, 0, 0, 0], "5#B相电流": [0, 0, 0, 0, 0],
        "4#C相电流": [90, 0, 88, 90, 0], "5#C相电流": [80, 80, 80, 80, 80],
        "4#功率": [1400, 0, 1300, 1400, 0], "5#功率": [1200, 1200, 1200, 1200, 1200],
        "4#IGV开度": [80, 0, 78, 80, 0], "5#IGV开度": [70, 70, 70, 70, 70],
        "4#BOV闭度": [100, 100, 100, 100, 100], "5#BOV闭度": [100, 100, 100, 100, 100],
    })
    op.to_csv(raw / "设备运行参数.csv", index=False, encoding="utf-8-sig")

    st = pd.DataFrame({
        "time": TIMES,
        "4#一级震动": [4.0, 4.1, 4.2, 4.3, 4.4], "5#一级震动": [5.0, 5.1, 5.2, 5.3, 5.4],
        "4#二级震动": [3.0, 3.1, 3.2, 3.3, 3.4], "5#二级震动": [3.5, 3.6, 3.7, 3.8, 3.9],
        "4#三级震动": [6.0, 6.1, 6.2, 6.3, 6.4], "5#三级震动": [6.5, 6.6, 6.7, 6.8, 6.9],
        "4#排气温度": [90, 91, 92, 93, 94], "5#排气温度": [88, 89, 90, 91, 92],
    })
    st.to_csv(raw / "设备状态监测.csv", index=False, encoding="utf-8-sig")

    load = pd.DataFrame({"time": TIMES, "总流量": [350, 150, 330, 350, 150],
                         "负荷率": [0.65, 0.28, 0.61, 0.65, 0.28],
                         "负荷等级": [1, 0, 1, 1, 0], "波动率_15min": [0, 0, 0, 0, 0],
                         "波动率_日级": [0, 0, 0, 0, 0]})
    load.to_csv(raw / "用气负荷数据.csv", index=False, encoding="gbk")

    pd.DataFrame({"time": TIMES, "母管压力": [0.62] * 5,
                  "4#支路流量": [200, 0, 180, 200, 0], "5#支路流量": [150] * 5,
                  "4#压差": [0.0] * 5, "5#压差": [0.0] * 5}).to_excel(
        raw / "管网运行数据.xlsx", index=False)
    pd.DataFrame({"time": TIMES, "站房温度": [14.0, 0.0, 15.0, 16.0, 17.0],
                  "站房湿度": [50.0, 0.0, 51.0, 52.0, 53.0],
                  "大气压": [0.101325] * 5}).to_excel(raw / "环境参数.xlsx", index=False)
    pd.DataFrame({"time": TIMES,
                  "4#状态": [8, 10, 999, 8, 10], "5#状态": [8, 8, 8, 8, 8],
                  "4#加卸载": [np.nan] * 5, "5#加卸载": [np.nan] * 5,
                  "4#预警": [0] * 5, "5#预警": [0] * 5,
                  "4#故障": [0, 0, 26, 0, 0], "5#故障": [0, 0, 0, 0, 0]}).to_excel(
        raw / "运行事件记录.xlsx", index=False)
    pd.DataFrame({"time": TIMES,
                  "4#总运行时间": [10, 65535, 12, 13, 14], "5#总运行时间": [8, 9, 10, 11, 12],
                  "4#总负载时间": [9, 65535, 11, 12, 13], "5#总负载时间": [7, 8, 9, 10, 11],
                  "4#下次保养剩余时间": [100, 4294970000, 99, 98, 97],
                  "5#下次保养剩余时间": [50, 49, 48, 47, 46]}).to_excel(
        raw / "维护保养记录.xlsx", index=False)

    specs = {"设备档案": [
        {"编号": 4, "机组": "4#", "额定功率_kW": 1250, "额定气量_m3min": 268.9},
        {"编号": 5, "机组": "5#", "额定功率_kW": 1250, "额定气量_m3min": 268.9}]}
    (raw / "设备档案.json").write_text(json.dumps(specs, ensure_ascii=False), encoding="utf-8")
    (raw / "指标释义.json").write_text('{"额定总流量_m3min": 537.8}', encoding="utf-8")


@pytest.fixture()
def raw_dir(tmp_path):
    raw = tmp_path / "raw"
    _make_raw(raw)
    return raw


@pytest.fixture()
def config():
    import yaml
    return yaml.safe_load(Path("configs/data_quality.yaml").read_text(encoding="utf-8"))


def test_proxy_current_and_dropped_columns(raw_dir, config, tmp_path):
    tables = cleaners.build_standard_tables(raw_dir, tmp_path / "p", config)
    dev = tables["device"]
    assert "current_proxy_a" in dev.columns
    assert "4#B相电流" not in dev.columns
    # 代理电流 = (A+C)/2
    d4 = dev[dev["device_id"] == 4].reset_index(drop=True)
    assert d4.loc[0, "current_proxy_a"] == pytest.approx((90 + 90) / 2)


def test_sentinel_removed(raw_dir, config, tmp_path):
    dev = cleaners.build_standard_tables(raw_dir, tmp_path / "p", config)["device"]
    d4 = dev[dev["device_id"] == 4].reset_index(drop=True)
    assert pd.isna(d4.loc[1, "runtime_hours"])
    assert bool(d4.loc[1, "maintenance_sentinel_removed"]) is True
    assert not dev["runtime_hours"].isin([65535, 4294970000]).any()


def test_state_source_power_fallback(raw_dir, config, tmp_path):
    dev = cleaners.build_standard_tables(raw_dir, tmp_path / "p", config)["device"]
    d4 = dev[dev["device_id"] == 4].reset_index(drop=True)
    assert d4.loc[0, "state_source"] == "event_record"
    assert d4.loc[2, "state_source"] == "power_fallback"      # 状态=999 异常
    assert bool(d4.loc[2, "running"]) is True                # 功率 1300 > 1kW


def test_long_gap_temperature_flag(raw_dir, config, tmp_path):
    dev = cleaners.build_standard_tables(raw_dir, tmp_path / "p", config)["device"]
    # 所有时间点都在前6天长缺失窗口内
    assert dev["temperature_available"].eq(False).all()


def test_environment_interpolated_flag(raw_dir, config, tmp_path):
    st = cleaners.build_standard_tables(raw_dir, tmp_path / "p", config)["station"]
    assert bool(st.loc[1, "environment_interpolated"]) is True   # 站房温度=0


def test_station_sum_matches_devices(raw_dir, config, tmp_path):
    t = cleaners.build_standard_tables(raw_dir, tmp_path / "p", config)
    g = t["device"].groupby("time", as_index=False)[["flow_m3_min", "power_kw"]].sum()
    m = t["station"][["time", "total_flow_m3_min", "total_power_kw"]].merge(g, on="time")
    assert np.allclose(m["total_flow_m3_min"], m["flow_m3_min"], rtol=1e-4, atol=1e-2)
    assert np.allclose(m["total_power_kw"], m["power_kw"], rtol=1e-6, atol=1e-2)


def test_repeatable(raw_dir, config, tmp_path):
    a = cleaners.build_standard_tables(raw_dir, tmp_path / "a", config)
    b = cleaners.build_standard_tables(raw_dir, tmp_path / "b", config)
    for name in ("station", "device", "events", "maintenance"):
        pd.testing.assert_frame_equal(a[name].reset_index(drop=True),
                                      b[name].reset_index(drop=True))


def test_outputs_written(raw_dir, config, tmp_path):
    pdir = tmp_path / "p"
    cleaners.build_standard_tables(raw_dir, pdir, config)
    for f in ("station_timeseries.parquet", "device_timeseries.parquet",
              "events.parquet", "maintenance.parquet"):
        assert (pdir / f).exists()

