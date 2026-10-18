"""设备健康诊断评估: 与已知故障弱标签窗口做合理性核对。

故障窗口与评估阈值全部来自 configs/health.yaml, 不在代码中硬编码。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from air_station.models.health import anomaly, features as HF, rules as HR, scoring


def _fault_windows(config: dict) -> dict[int, list[tuple[str, str]]]:
    raw = config.get("fault_weak_labels", {}).get("windows", {})
    out: dict[int, list[tuple[str, str]]] = {}
    for k, v in raw.items():
        out[int(k)] = [(w["start"], w["end"]) for w in v]
    return out


def _device_series(processed_dir: str, number: int, config: dict) -> pd.DataFrame:
    frame = HF.value_frame(processed_dir, number, config)
    frame = frame.set_index(pd.to_datetime(frame["time"])).drop(columns=["time"])
    rules_cfg = config.get("rules", [])
    rule_map = {r["column"]: {"warn": r["warn"], "alarm": r["alarm"]} for r in rules_cfg}
    penalties = HR.evaluate_rules(frame, rule_map)
    rule_penalty = penalties.mean(axis=1) if not penalties.empty else pd.Series(0.0, index=frame.index)
    an_score = anomaly.detect(frame, config)
    combined = scoring.combine(rule_penalty, an_score, config)
    combined["rule_penalties"] = penalties.apply(
        lambda r: json.dumps({k: round(float(v), 3) for k, v in r.items() if v > 0}), axis=1)
    return combined


def _lead_time(hi: pd.Series, fault_start: pd.Timestamp,
               threshold: float, lookback_hours: int) -> float | None:
    window_start = fault_start - pd.Timedelta(hours=lookback_hours)
    mask = (hi.index >= window_start) & (hi.index < fault_start) & (hi.values < threshold)
    if not mask.any():
        return None
    first = hi.index[mask][0]
    return float((fault_start - first).total_seconds() / 3600.0)


def evaluate(processed_dir: str = "data/processed",
             config_path: str = "configs/health.yaml",
             report_dir: str = "artifacts/metrics") -> dict:
    config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    ecfg = config.get("evaluate", {})
    lead_threshold = float(ecfg.get("lead_time_threshold", 60))
    lookback_hours = int(ecfg.get("lead_time_lookback_hours", 72))
    windows = _fault_windows(config)

    results: dict = {"devices": {}, "lead_time_threshold": lead_threshold}
    for number in config.get("devices", [4, 5]):
        series = _device_series(processed_dir, number, config)
        hi = series["health_index"]
        in_fault = pd.Series(False, index=hi.index)
        for s, e in windows.get(number, []):
            in_fault |= (hi.index >= pd.Timestamp(s)) & (hi.index <= pd.Timestamp(e))
        normal_mean = float(hi[~in_fault].mean())
        fault_mean = float(hi[in_fault].mean()) if in_fault.any() else float("nan")
        leads = [lt for s, _e in windows.get(number, [])
                 if (lt := _lead_time(hi, pd.Timestamp(s), lead_threshold, lookback_hours)) is not None]
        results["devices"][number] = {
            "hours": int(len(hi)),
            "normal_mean_hi": round(normal_mean, 2),
            "fault_mean_hi": round(fault_mean, 2) if not np.isnan(fault_mean) else None,
            "separation": round(normal_mean - fault_mean, 2) if not np.isnan(fault_mean) else None,
            "lead_time_hours": [round(x, 1) for x in leads],
            "min_hi": round(float(hi.min()), 2),
        }
    out = Path(report_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "health_metrics.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="评估健康诊断")
    ap.add_argument("--processed-dir", default="data/processed")
    ap.add_argument("--config", default="configs/health.yaml")
    ap.add_argument("--report-dir", default="artifacts/metrics")
    args = ap.parse_args()
    res = evaluate(args.processed_dir, args.config, args.report_dir)
    for n, m in res["devices"].items():
        print(f"  机组 {n}#: 正常HI={m['normal_mean_hi']} 故障HI={m['fault_mean_hi']} "
              f"分离度={m['separation']} 提前={m['lead_time_hours']}h")
    print("[OK] 健康评估完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
