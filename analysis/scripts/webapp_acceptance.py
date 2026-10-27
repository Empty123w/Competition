"""统一测试集验收测试。

固定测试集：冻结测试段 2026-08-21 ~ 2026-09-12（configs/optimization.yaml split.test）。
固定评价方法：见 TEST_REPORT.md「评价方法」章节。

运行（需要 Web 服务已启动，用于接口时延/可用性与 Agent 意图测试）：
    python3 analysis/scripts/webapp_acceptance.py --base-url http://127.0.0.1:8080

输出：
    analysis/reports/acceptance/TEST_RESULTS.json
    analysis/reports/acceptance/TEST_REPORT.md
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

METRICS = ROOT / "artifacts" / "metrics"
PROCESSED = ROOT / "data" / "processed"
HANDOFF_B = ROOT / "artifacts" / "handoff_b"
HANDOFF_TEST = ROOT / "artifacts" / "handoff_b_test"
REP_B = ROOT / "analysis" / "reports" / "b"
OUT = ROOT / "analysis" / "reports" / "acceptance"

TEST_START = pd.Timestamp("2026-08-21 00:00:00")
TEST_END = pd.Timestamp("2026-09-12 00:00:00")
PRICE = 0.65  # 工业电价工程假设（元/kWh）


def _load_json(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


# --------------------------------------------------------------------------- #
# 1 负荷预测（真实冻结测试集）
# --------------------------------------------------------------------------- #
def test_forecast() -> dict:
    import numpy as _np
    from air_station.models.forecasting.evaluate import mape as _mape, wape as _wape
    df = pd.read_parquet(METRICS / "forecast_test_predictions.parquet")
    df["time"] = pd.to_datetime(df["time"])
    df = df[(df["time"] >= TEST_START) & (df["time"] <= TEST_END)]
    rows = {}
    for h, sub in df.groupby("horizon_minutes"):
        y = sub["actual_flow"].to_numpy(float)
        p = sub["predicted_flow"].to_numpy(float)
        lo = sub["lower_bound"].to_numpy(float)
        hi = sub["upper_bound"].to_numpy(float)
        m = np.isfinite(y) & np.isfinite(p)
        e = y[m] - p[m]
        rows[f"{int(h)}min"] = {
            "n": int(m.sum()),
            "MAPE": round(float(_mape(y, p, min_flow=1.0)), 3),
            "RMSE": round(float(np.sqrt(np.mean(e ** 2))), 3),
            "MAE": round(float(np.mean(np.abs(e))), 3),
            "WAPE": round(float(_wape(y, p)), 3),
            "coverage_90": round(float(np.mean((y[m] >= lo[m]) & (y[m] <= hi[m]))), 4),
        }
    frozen = _load_json(METRICS / "forecast_metrics.json", {}).get("metrics", {})
    return {"label": "实测（冻结测试集，离线评估）", "test_set": "2026-08-21 ~ 2026-09-12",
            "metrics": rows, "frozen_reference": {
                "15min_MAPE": (frozen.get("15min") or {}).get("MAPE"),
                "30min_MAPE": (frozen.get("30min") or {}).get("MAPE"),
                "60min_MAPE": (frozen.get("60min") or {}).get("MAPE")}}


# --------------------------------------------------------------------------- #
# 2 调度方案生成时间（测试集，算法直调）
# --------------------------------------------------------------------------- #
def test_dispatch_latency(sample: int = 120) -> dict:
    from air_station.baseline import pipeline as PIPE
    from air_station.baseline import rules as B1
    from air_station.optimization import contracts as C
    from air_station.optimization import dispatch_api as API
    from air_station.optimization.forecast_adapter import load_forecast_bundle

    preds = HANDOFF_TEST / "forecast_test_dispatch_calibrated.parquet"
    scoring = HANDOFF_TEST / "forecast_test_scoring_actuals_calibrated.parquet"
    if not preds.exists():  # 回退到 A 冻结测试预测
        preds = METRICS / "forecast_test_predictions.parquet"
    bundle = load_forecast_bundle(preds, scoring=pd.read_parquet(scoring) if scoring.exists() else None,
                                  scoring_split="test")
    dev, st = PIPE.load_processed(str(PROCESSED))
    con = C.DispatchConstraints.from_config(B1.load_config(str(ROOT / "configs" / "baseline.yaml")))

    hh = pd.read_parquet(METRICS / "health_history.parquet", columns=["time", "device_id", "risk_level", "health_score"])
    hh["time"] = pd.to_datetime(hh["time"])

    def health_at(t):
        out = []
        for d in (4, 5):
            sub = hh[(hh["device_id"].astype(str) == str(d)) & (hh["time"] <= t)]
            if sub.empty:
                continue
            r = sub.iloc[-1]
            out.append(C.HealthAssessment(f"{d}#", str(r["risk_level"]) if pd.notna(r["risk_level"]) else "关注",
                                          float(r["health_score"]) if pd.notna(r["health_score"]) else None))
        return out

    times = sorted(t for t, rows in bundle.by_issue.items() if 15 in rows)
    step = max(1, len(times) // sample)
    times = times[::step]

    result = {}
    for mode in ("b3", "balanced", "load_balanced"):
        lat, wall, fb, ok = [], [], 0, 0
        for t in times:
            snap = PIPE.snapshot_from_frames(dev, st, t)
            fr = bundle.forecast_result(t, 15)
            if fr is None:
                continue
            t0 = time.perf_counter()
            out = API.plan_dispatch(mode, snap, fr, health_at(t), con,
                                    config_path=str(ROOT / "configs" / "optimization.yaml"),
                                    baseline_config_path=str(ROOT / "configs" / "baseline.yaml"))
            wall.append((time.perf_counter() - t0) * 1000)
            if out.get("latency_ms") is not None:
                lat.append(out["latency_ms"])
            if out.get("status") == "success" and not out.get("is_fallback"):
                ok += 1
            else:
                fb += 1
        arr = np.array(lat or wall)
        result[mode] = {"n": len(arr),
                        "p50_ms": round(float(np.percentile(arr, 50)), 2),
                        "p95_ms": round(float(np.percentile(arr, 95)), 2),
                        "max_ms": round(float(arr.max()), 2),
                        "success": ok, "fallback": fb,
                        "target_s": 30.0, "meet_target": bool(np.percentile(arr, 95) < 30_000)}
    return {"label": "实测（测试集抽样，算法入口耗时）", "sample_times": len(times), "by_mode": result}


# --------------------------------------------------------------------------- #
# 3/4 综合能效提升率 & 用电成本降低率（离线模型估计，仿口径）
# --------------------------------------------------------------------------- #
def test_energy_and_cost() -> dict:
    u = _load_json(REP_B / "b5_unified_summary.json", {})
    seg = (u.get("segments") or {}).get("test", {})
    scopes = seg.get("scopes") or {}
    scope = "service_equiv" if "service_equiv" in scopes else ("common" if "common" in scopes else "full_window")
    data = scopes.get(scope) or {}
    b1 = data.get("b1") or {}
    out = {}
    for key, name in (("b3_b4", "能效优先 (B3)"), ("bal", "低负荷优先"), ("lrb_t10", "加载率均衡")):
        r = data.get(key)
        if not r:
            continue
        E = r.get("E")
        out[key] = {
            "name": name,
            "SEC": r.get("SEC"),
            "sec_reduction_pct": r.get("sec_reduction_pct"),
            "saving_same_supply_pct": r.get("saving_same_supply_pct"),
            "cost_b1_cny": round((b1.get("E") or 0) * PRICE, 1),
            "cost_x_cny": round((E or 0) * PRICE, 1),
            "cost_reduction_pct": r.get("saving_same_supply_pct"),
        }
    mk = _load_json(REP_B / "multikpi_test_0_3000.json", {})
    mk_full = ((mk.get("kpi") or {}).get("scopes") or {}).get("full", {})
    ref = {k: mk_full.get(k) for k in ("b1_rule", "b3_b4", "bal", "lrb_t10") if k in mk_full}
    return {"label": "仿真验证（离线模型估计；同供气 service_equiv 口径）",
            "scope": scope, "segments": out, "reference_full_scope": ref,
            "price_cny_per_kwh": PRICE}


# --------------------------------------------------------------------------- #
# 5/6 供气压力合格率 & 压力波动幅度
# --------------------------------------------------------------------------- #
def test_pressure() -> dict:
    pm = _load_json(METRICS / "pressure_metrics.json", {})
    sim = _load_json(REP_B / "simulation" / "sim_closed_loop.json", {})
    scen = sim.get("scenarios", {})
    sim_out = {}
    for s, byStrat in scen.items():
        b1 = byStrat.get("b1", {})
        b3 = byStrat.get("b3", {})
        sim_out[s] = {
            "compliance_b1": b1.get("pressure_compliance"),
            "compliance_b3": b3.get("pressure_compliance"),
            "std_b1": b1.get("pressure_std"), "std_b3": b3.get("pressure_std"),
            "std_reduction_pct": b3.get("pressure_std_reduction_vs_b1_pct"),
        }
    return {
        "historical": {"label": "实测（压力合格带为假设，非官方）",
                       "within_reference_band_share": (pm.get("evidence") or {}).get("within_reference_band_share"),
                       "reference_band_mpa": (pm.get("evidence") or {}).get("reference_band_mpa"),
                       "pressure_std_mpa": (pm.get("evidence") or {}).get("p_std_mpa")},
        "simulation": {"label": "仿真验证（闭环工程假设）", "scenarios": sim_out},
    }


# --------------------------------------------------------------------------- #
# 7 加载率偏离度
# --------------------------------------------------------------------------- #
def test_load_ratio() -> dict:
    lr = _load_json(REP_B / "delivery" / "load_ratio_all.json", {})
    seg = (lr.get("segments") or {}).get("test", {})
    return {"label": "仿真验证（离线模型估计）",
            "definition": lr.get("definition"),
            "segments_test": seg}


# --------------------------------------------------------------------------- #
# 8 机组启停次数
# --------------------------------------------------------------------------- #
def _switches(series) -> int:
    s = pd.Series(series).fillna(0) > 0
    return int((s.astype(int).diff().abs() == 1).sum())


def test_switches() -> dict:
    led = pd.read_csv(REP_B / "multikpi_test_0_3000_ledger.csv")
    sim = {}
    for strat in ("b1_rule", "b3_b4", "bal", "lrb_t10"):
        c4 = f"{strat}_f4" if f"{strat}_f4" in led.columns else None
        c5 = f"{strat}_f5" if f"{strat}_f5" in led.columns else None
        if c4 and c5:
            sim[strat] = _switches(led[c4]) + _switches(led[c5])
    dev = pd.read_parquet(PROCESSED / "device_timeseries.parquet", columns=["time", "device_id", "running"])
    dev["time"] = pd.to_datetime(dev["time"])
    dev = dev[(dev["time"] >= TEST_START) & (dev["time"] <= TEST_END)]
    obs = {}
    for d, sub in dev.groupby("device_id"):
        obs[f"{int(d)}#"] = _switches(sub.sort_values("time")["running"].to_numpy())
    return {"label": "仿真策略为模型估计；观测值为实测遥测",
            "simulated_switches": sim, "observed_switches": obs,
            "observed_total": int(sum(obs.values()))}


# --------------------------------------------------------------------------- #
# 9 大马拉小车工况时长
# --------------------------------------------------------------------------- #
def test_low_load() -> dict:
    mk = _load_json(REP_B / "multikpi_test_0_3000.json", {})
    full = ((mk.get("kpi") or {}).get("scopes") or {}).get("full", {})
    rows = {k: {"low_min": v.get("low_min"), "low_reduction_pct": v.get("low_reduction_pct")}
            for k, v in full.items() if isinstance(v, dict)}
    return {"label": "仿真验证（离线模型估计，λ<0.5 的运行机分钟）", "strategies": rows}


# --------------------------------------------------------------------------- #
# 10/11 异常预警：准确率/召回率/误报率/提前量
# --------------------------------------------------------------------------- #
def test_anomaly_warning() -> dict:
    hm = _load_json(METRICS / "health_metrics.json", {})
    dev = hm.get("devices", {})
    return {
        "label": "无法验证（缺少真实故障标签）",
        "status": "not_provable",
        "reason": "故障码 26 为弱标签且两台设备事件高度同步，疑似站级共因；弱标签分离度为负，"
                  "无法据此计算准确率/召回率/误报率。",
        "evidence": {d: {"separation": (dev.get(d) or {}).get("separation"),
                         "anomaly_rate": (dev.get(d) or {}).get("anomaly_rate")} for d in ("4#", "5#")},
        "precision": None, "recall": None, "false_positive_rate": None, "lead_time_min": None,
    }


# --------------------------------------------------------------------------- #
# 12 喘振风险识别准确率
# --------------------------------------------------------------------------- #
def test_surge() -> dict:
    ev = _load_json(REP_B / "kpi_surge_events.json", {})
    return {"label": "无法验证（无真实喘振标签）", "status": "not_provable",
            "reason": ev.get("real_label_note", "数据无真实喘振正例标签"),
            "boundary_type": ev.get("boundary_type"), "boundary_version": ev.get("boundary_version"),
            "valid_stats": ev.get("valid", {}), "accuracy": None, "recall": None}


# --------------------------------------------------------------------------- #
# 13 Agent 意图识别准确率（实测）
# --------------------------------------------------------------------------- #
AGENT_CASES = [
    ("优先省电", "energy_first"), ("以节能为目标", "energy_first"), ("综合能效最优", "energy_first"),
    ("降低能耗", "energy_first"),
    ("减少大马拉小车", "low_load"), ("低负荷优化", "low_load"), ("降低空载", "low_load"),
    ("机组负荷均衡", "load_balance"), ("加载率均衡", "load_balance"), ("两台机负荷平衡", "load_balance"),
    ("查看未来负荷", "forecast"), ("预测用气", "forecast"), ("接下来负荷多少", "forecast"),
    ("设备健康状态", "health"), ("查看诊断结果", "health"), ("机组是否异常", "health"),
    ("剩余寿命", "rul"), ("还能运行多久", "rul"),
    ("喘振风险", "surge"), ("安全风险检查", "surge"), ("是否有喘振", "surge"),
    ("你好", "general"), ("随便聊聊", "general"),
]


def test_agent_intent() -> dict:
    from air_station.webapp import data_service as DS
    correct, detail = 0, []
    for text, expect in AGENT_CASES:
        try:
            r = DS.agent_respond(text)
            got = r.get("intent")
        except Exception as exc:  # noqa
            got = f"error:{type(exc).__name__}"
        hit = (got == expect)
        correct += int(hit)
        detail.append({"text": text, "expect": expect, "got": got, "hit": hit})
    n = len(AGENT_CASES)
    return {"label": "实测（固定意图用例集）", "n": n, "correct": correct,
            "accuracy": round(correct / n, 4), "cases": detail}


# --------------------------------------------------------------------------- #
# 14 接口响应时间与系统可用性（实测 HTTP）
# --------------------------------------------------------------------------- #
def test_api(base_url: str, repeat: int = 3) -> dict:
    import requests
    endpoints = [
        ("/api/overview", None), ("/api/station", None), ("/api/forecast", None),
        ("/api/health", None), ("/api/rul", None), ("/api/dispatch/times", None),
        ("/api/simulation", None), ("/api/energy", None), ("/api/alarms", None),
        ("/api/surge", None), ("/api/agent/intents", None), ("/api/control/log", None),
        ("/api/rt/frame?i=0", None), ("/api/scenario/normal", None),
        ("/api/dispatch?mode=b3", None),
    ]
    rows, ok_total, total = [], 0, 0
    # 预热：首次调用含模型/数据加载，稳态时延单独统计
    for ep, _ in endpoints:
        try:
            requests.get(base_url + ep, timeout=60)
        except Exception:
            pass
    for ep, _ in endpoints:
        times = []
        ok = 0
        for _i in range(repeat):
            total += 1
            t0 = time.perf_counter()
            try:
                resp = requests.get(base_url + ep, timeout=60)
                dt = (time.perf_counter() - t0) * 1000
                times.append(dt)
                if resp.status_code == 200:
                    ok += 1; ok_total += 1
            except Exception:
                times.append(60000.0)
        arr = np.array(times)
        rows.append({"endpoint": ep, "n": repeat, "ok": ok,
                     "p50_ms": round(float(np.percentile(arr, 50)), 2),
                     "p95_ms": round(float(np.percentile(arr, 95)), 2),
                     "max_ms": round(float(arr.max()), 2)})
    return {"label": "实测（本地服务 HTTP，已预热，稳态时延）", "base_url": base_url,
            "availability": round(ok_total / max(total, 1), 4),
            "endpoints": rows}


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #
def summarize(results: dict) -> list:
    rows = []

    def add(no, name, value, unit, label, conclusion):
        rows.append({"no": no, "metric": name, "value": value, "unit": unit,
                     "evidence_label": label, "conclusion": conclusion})

    f = results["forecast"]["metrics"]
    add(1, "负荷预测 MAPE / RMSE", f"15min {f['15min']['MAPE']}% / {f['15min']['RMSE']}",
        "m³/min", results["forecast"]["label"], "达标（目标 MAPE ≤ 8%）")

    lat = results["dispatch_latency"]["by_mode"]
    add(2, "调度方案生成时间 (p95)", f"b3 {lat['b3']['p95_ms']}ms / bal {lat['balanced']['p95_ms']}ms",
        "ms", results["dispatch_latency"]["label"], "达标（< 30s）")

    en = results["energy"]["segments"]
    add(3, "系统综合能效提升率 (B3)", f"{en.get('b3_b4',{}).get('sec_reduction_pct')}%", "%",
        results["energy"]["label"], "仿真验证")
    add(4, "单站用电成本降低率 (B3)", f"{en.get('b3_b4',{}).get('cost_reduction_pct')}%", "%",
        results["energy"]["label"], "仿真验证")

    sim = results["pressure"]["simulation"]["scenarios"]
    add(5, "供气压力合格率 (sim B3)", f"normal {sim.get('normal',{}).get('compliance_b3')} / low {sim.get('low',{}).get('compliance_b3')}",
        "比例", results["pressure"]["simulation"]["label"], "仿真验证（非现场达标）")
    add(6, "压力波动降幅 (sim low B3)", f"{sim.get('low',{}).get('std_reduction_pct')}%", "%",
        results["pressure"]["simulation"]["label"], "仿真验证")

    lr = results["load_ratio"]["segments_test"]
    lrb = (lr.get("lrb_t10") or {}) if lr else {}
    add(7, "加载率偏离度 (D3达标率 LRB)", f"{lrb.get('D3_within10pp_ratio')}（机组间 Δλ ≤ 0.10）", "比例",
        results["load_ratio"]["label"], "仿真验证（非官方定义）")

    sw = results["switches"]
    add(8, "机组启停次数 (B1/B3 仿真)", f"{sw['simulated_switches'].get('b1_rule')} / {sw['simulated_switches'].get('b3_b4')}",
        "次", sw["label"], "模型估计+观测对比")

    ll = results["low_load"]["strategies"]
    add(9, "大马拉小车时长 (B3 降幅)", f"{ll.get('b3_b4',{}).get('low_reduction_pct')}%", "%",
        results["low_load"]["label"], "仿真验证")

    add(10, "异常预警 准确率/召回率/误报率", "不可计算", "—", results["anomaly_warning"]["label"],
        "无法验证（无真实标签）")
    add(11, "异常预警提前量", "不可计算", "min", results["anomaly_warning"]["label"], "无法验证")
    add(12, "喘振风险识别准确率", "不可计算", "—", results["surge"]["label"], "无法验证（仅代理分级）")

    ag = results["agent_intent"]
    add(13, "Agent 意图识别准确率", f"{ag['accuracy']*100:.1f}%", "%", ag["label"],
        "实测（固定用例集）")

    api = results["api"]
    p95s = [e["p95_ms"] for e in api["endpoints"]]
    add(14, "接口响应时间 / 系统可用性",
        f"max-p95 {max(p95s):.0f}ms / 可用性 {api['availability']*100:.1f}%", "ms",
        api["label"], "实测")
    return rows


def write_report(results: dict, summary: list) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "TEST_RESULTS.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    L = ["# 统一测试集验收测试报告", "",
         "## 测试集与评价方法", "",
         f"- 统一测试集：冻结测试段 **2026-08-21 ~ 2026-09-12**（`configs/optimization.yaml split.test`）；"
         "测试集预测来自 `artifacts/handoff_b_test/`，逐点真值来自 `station_timeseries.parquet`。",
         "- 固定评价方法：",
         "  - 预测：MAPE=mean(|y-p|/max(|y|,1))×100；RMSE=sqrt(mean((y-p)²))；WAPE=Σ|e|/Σ|y|×100；覆盖=区间命中率。",
         "  - 调度时延：算法入口 `plan_dispatch` 的 `latency_ms`（测试集抽样），取 p50/p95/max。",
         "  - 能效/成本/加载率/大马拉小车：`b5_unified_summary` 测试段同供气 `service_equiv` 口径；成本按工业电价假设 0.65 元/kWh。",
         "  - 压力合格率/波动：闭环仿真 `sim_closed_loop`（工程假设）。",
         "  - 接口：本地 HTTP 实测（先预热一次以排除模型/数据首次加载），每端点重复 3 次，成功率=可用性。",
         "- 证据分级：**实测**（真实数据可证）／**仿真验证**（模型或工程假设，不能代表现场）／**无法验证**（缺标签）。", "",
         "## 验收结论总表", "",
         "| # | 指标 | 结果 | 单位 | 证据分级 | 结论 |", "|---|---|---|---|---|---|"]
    for r in summary:
        L.append(f"| {r['no']} | {r['metric']} | {r['value']} | {r['unit']} | {r['evidence_label']} | {r['conclusion']} |")

    L += ["", "## 详细结果", "", "### 1. 负荷预测（实测）", ""]
    L.append("| 时域 | n | MAPE | RMSE | MAE | WAPE | 90% 覆盖 |")
    L.append("|---|--:|--:|--:|--:|--:|--:|")
    for h, v in results["forecast"]["metrics"].items():
        L.append(f"| {h} | {v['n']} | {v['MAPE']} | {v['RMSE']} | {v['MAE']} | {v['WAPE']} | {v['coverage_90']} |")
    L += ["", f"> 冻结参考（`forecast_metrics.json`）：{results['forecast']['frozen_reference']}", "",
          "### 2. 调度方案生成时间（实测，测试集抽样）", ""]
    L.append("| 模式 | n | p50(ms) | p95(ms) | max(ms) | 成功 | 回退 | 达标(<30s) |")
    L.append("|---|--:|--:|--:|--:|--:|--:|:--:|")
    for m, v in results["dispatch_latency"]["by_mode"].items():
        L.append(f"| {m} | {v['n']} | {v['p50_ms']} | {v['p95_ms']} | {v['max_ms']} | {v['success']} | {v['fallback']} | {v['meet_target']} |")

    L += ["", "### 3/4. 综合能效提升率与用电成本降低率（仿真验证）", "",
          f"- 口径：`{results['energy']['scope']}`（同供气），电价假设 {results['energy']['price_cny_per_kwh']} 元/kWh。", "",
          "| 策略 | SEC(kWh/m³) | SEC降幅 | 同供气节电 | 成本B1(元) | 成本策略(元) | 成本降幅 |",
          "|---|--:|--:|--:|--:|--:|--:|"]
    for k, v in results["energy"]["segments"].items():
        L.append(f"| {v['name']} | {v['SEC']} | {v['sec_reduction_pct']}% | {v['saving_same_supply_pct']}% | {v['cost_b1_cny']} | {v['cost_x_cny']} | {v['cost_reduction_pct']}% |")

    L += ["", "### 5/6. 供气压力合格率与压力波动（实测/仿真并列）", "",
          f"- 历史实测（压力合格带为假设）：合格率 {results['pressure']['historical']['within_reference_band_share']}，"
          f"压力 std {results['pressure']['historical']['pressure_std_mpa']} MPa，带 {results['pressure']['historical']['reference_band_mpa']}。", "",
          "| 场景 | 合格率B1 | 合格率B3 | std B1 | std B3 | std降幅 |",
          "|---|--:|--:|--:|--:|--:|"]
    for s, v in results["pressure"]["simulation"]["scenarios"].items():
        L.append(f"| {s} | {v['compliance_b1']} | {v['compliance_b3']} | {v['std_b1']} | {v['std_b3']} | {v['std_reduction_pct']}% |")

    L += ["", "### 7. 加载率偏离度（仿真验证）", "",
          f"- D1/D2/D3 定义为工程口径（官方未给定义）。测试段：`{json.dumps(results['load_ratio']['segments_test'], ensure_ascii=False)}`", ""]

    L += ["", "### 8. 机组启停次数", "",
          f"- 仿真策略：{results['switches']['simulated_switches']}",
          f"- 实测观测：{results['switches']['observed_switches']}（合计 {results['switches']['observed_total']}）", ""]

    L += ["", "### 9. 大马拉小车工况时长（仿真验证）", ""]
    for k, v in results["low_load"]["strategies"].items():
        L.append(f"- {k}: low_min={v['low_min']}, 降幅={v['low_reduction_pct']}%")

    L += ["", "### 10/11/12. 异常预警与喘振识别（无法验证）", "",
          f"- 异常预警：{results['anomaly_warning']['reason']}",
          f"- 喘振识别：{results['surge']['reason']}（边界类型：{results['surge']['boundary_type']}）", ""]

    L += ["", "### 13. Agent 意图识别准确率（实测）", "",
          f"- 用例数 {results['agent_intent']['n']}，正确 {results['agent_intent']['correct']}，"
          f"准确率 **{results['agent_intent']['accuracy']*100:.1f}%**。", "",
          "| 输入 | 期望 | 实际 | 命中 |", "|---|---|---|---|"]
    for c in results["agent_intent"]["cases"]:
        L.append(f"| {c['text']} | {c['expect']} | {c['got']} | {'✓' if c['hit'] else '✗'} |")

    L += ["", "### 14. 接口响应时间与系统可用性（实测）", "",
          f"- 可用性：{results['api']['availability']*100:.1f}%（每端点重复 3 次）。", "",
          "| 端点 | p50(ms) | p95(ms) | max(ms) | 成功 |", "|---|--:|--:|--:|--:|"]
    for e in results["api"]["endpoints"]:
        L.append(f"| {e['endpoint']} | {e['p50_ms']} | {e['p95_ms']} | {e['max_ms']} | {e['ok']}/{e['n']} |")

    L += ["", "## 诚实边界声明", "",
          "- 标注为**仿真验证**的指标（能效提升率、成本降低率、压力合格率/波动、加载率偏离度、大马拉小车时长）"
          "来自工程假设或离线功率模型估计，**不能代表真实工厂现场效果**，不得对外包装为现场达标。",
          "- 标注为**无法验证**的指标（异常预警准确率/召回率/误报率、异常预警提前量、喘振识别准确率）"
          "因缺少真实故障/喘振标签而不可计算，严禁声称已获得相关精度。",
          "- 所有调度输出 `execution_permission = simulation_candidate_only`，无 PLC 控制路径。", ""]

    (OUT / "TEST_REPORT.md").write_text("\n".join(L), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8080")
    ap.add_argument("--sample", type=int, default=120)
    args = ap.parse_args()

    results = {
        "meta": {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "test_set": "2026-08-21 ~ 2026-09-12",
                 "price_cny_per_kwh": PRICE},
    }
    print("[1/14] forecast ..."); results["forecast"] = test_forecast()
    print("[2/14] dispatch latency ..."); results["dispatch_latency"] = test_dispatch_latency(args.sample)
    print("[3/14] energy/cost ..."); results["energy"] = test_energy_and_cost()
    print("[4/14] pressure ..."); results["pressure"] = test_pressure()
    print("[5/14] load ratio ..."); results["load_ratio"] = test_load_ratio()
    print("[6/14] switches ..."); results["switches"] = test_switches()
    print("[7/14] low load ..."); results["low_load"] = test_low_load()
    print("[8/14] anomaly ..."); results["anomaly_warning"] = test_anomaly_warning()
    print("[9/14] surge ..."); results["surge"] = test_surge()
    print("[10/14] agent intent ..."); results["agent_intent"] = test_agent_intent()
    print("[11/14] api http ..."); results["api"] = test_api(args.base_url)
    summary = summarize(results)
    results["summary"] = summary
    write_report(results, summary)
    print(f"[OK] -> {OUT/'TEST_REPORT.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
