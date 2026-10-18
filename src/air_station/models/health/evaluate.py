"""设备健康诊断离线指标与报告 (阶段 A5)。

输出:
  - artifacts/metrics/health_metrics.json
  - analysis/reports/phaseA5/health_report.md

直接读取 health_features.parquet; 加载后先过滤 running==1。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.models.health import anomaly, rules, scoring, service

FAULT_CFG = "configs/health.yaml"


def _fault_windows(cfg: dict) -> dict[int, list[tuple[str, str]]]:
    raw = cfg.get("fault_weak_labels", {}).get("windows", {})
    return {int(k): [(w["start"], w["end"]) for w in v] for k, v in raw.items()}


def _device_scores(sub: pd.DataFrame, thresholds: dict):
    rules_cfg = thresholds.get("expert_rules", [])
    pen = rules.rule_penalties(sub, rules_cfg)
    rule_max = pen.max(axis=1).to_numpy() if not pen.empty else np.zeros(len(sub))
    acfg = thresholds.get("anomaly", {})
    anom = anomaly.anomaly_score_series(sub, acfg.get("dev_features", []),
                                        acfg.get("dev_scale", 3.0)).to_numpy()
    score = scoring.combine(rule_max, anom, thresholds)
    return pd.Series(score, index=sub.index), pd.Series(anom, index=sub.index), pen


def evaluate(processed_dir: str = "data/processed",
             thresholds_path: str = rules.DEFAULT_THRESHOLDS,
             health_cfg_path: str = FAULT_CFG,
             metrics_dir: str = "artifacts/metrics",
             report_path: str = "analysis/reports/phaseA5/health_report.md") -> dict:
    import yaml
    thresholds = rules.load_thresholds(thresholds_path)
    health_cfg = yaml.safe_load(Path(health_cfg_path).read_text(encoding="utf-8"))
    windows = _fault_windows(health_cfg)
    warning_thr = thresholds.get("levels", {}).get("warning", 50)

    df = service._load_features(processed_dir, thresholds)   # 已过滤 running==1
    devices: dict = {}
    eval_time = None
    for number, sub in df.sort_values(["device_id", "time"]).groupby("device_id", observed=True):
        sub = sub.reset_index(drop=True)
        eval_time = max(eval_time, pd.Timestamp(sub["time"].iloc[-1])) if eval_time is not None \
            else pd.Timestamp(sub["time"].iloc[-1])
        score, anom, pen = _device_scores(sub, thresholds)
        t = pd.to_datetime(sub["time"])
        in_fault = pd.Series(False, index=sub.index)
        for s, e in windows.get(int(number), []):
            in_fault |= ((t >= pd.Timestamp(s)) & (t <= pd.Timestamp(e)))
        normal_mean = float(score[~in_fault].mean())
        fault_mean = float(score[in_fault].mean()) if in_fault.any() else None
        latest_score = float(score.iloc[-1])
        devices[f"{int(number)}#"] = {
            "health_score_latest": int(round(latest_score)),
            "risk_level_latest": scoring.risk_level(latest_score, thresholds),
            "health_score_mean": round(float(score.mean()), 2),
            "health_score_min": round(float(score.min()), 2),
            "anomaly_rate": round(float((score < warning_thr).mean()), 4),
            "normal_mean_score": round(normal_mean, 2),
            "fault_mean_score": round(fault_mean, 2) if fault_mean is not None else None,
            "separation": round(normal_mean - fault_mean, 2) if fault_mean is not None else None,
        }

    out = {
        "validation_mode": "unsupervised",
        "evaluation_time": service._to_utc_z(eval_time) if eval_time is not None else None,
        "warning_threshold": warning_thr,
        "devices": devices,
    }
    md = Path(metrics_dir)
    md.mkdir(parents=True, exist_ok=True)
    (md / "health_metrics.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # 接口样例
    sample = service.health_json(processed_dir, thresholds_path)
    rep_dir = Path(report_path).parent
    rep_dir.mkdir(parents=True, exist_ok=True)
    (rep_dir / "health_interface_sample.json").write_text(
        json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_report(Path(report_path), out, sample)
    return out


def _write_report(path: Path, out: dict, sample: dict) -> None:
    lines = ["# 阶段 A5 设备健康诊断报告", "",
             f"- 验证模式：{out['validation_mode']}（无监督，无多类故障标签）",
             f"- 评估时刻：{out['evaluation_time']}",
             f"- 数据源：`data/processed/health_features.parquet`（已过滤 running==1）",
             f"- 阈值来源：`configs/health_thresholds.yaml`（外部化，可调）", "",
             "## 1. 方法", "",
             "健康评分 = 100 × (1 − (权重×专家规则扣分 + 权重×相对偏差异常分))。"
             "专家规则基于当前值对 warn/alarm 软阈值扣分；异常分基于 `dev_*`（相对同负荷区间"
             "正常基准的偏差，按加载率分档）归一化。停机状态在加载后第一时间过滤，不参与统计。", "",
             "## 2. 各机组指标", "",
             "| 机组 | 最新健康分 | 风险等级 | 均值 | 最低 | 异常率 | 正常期均值 | 故障期均值 | 分离度 |",
             "|------|-----------|----------|------|------|--------|------------|------------|--------|"]
    for dv, m in out["devices"].items():
        lines.append(f"| {dv} | {m['health_score_latest']} | {m['risk_level_latest']} | "
                     f"{m['health_score_mean']} | {m['health_score_min']} | {m['anomaly_rate']} | "
                     f"{m['normal_mean_score']} | {m['fault_mean_score']} | {m['separation']} |")
    lines += ["", "> 故障期均值基于运行事件记录中的故障码 26 弱标签窗口，仅作合理性核对；"
                  "两机故障高度同步，疑似站级共因事件，非独立故障真值。", "",
              "## 3. 最新诊断（接口输出）", "",
              "| 机组 | 健康分 | 风险 | 异常分 | 主因 | 当前值 | 正常范围 | 趋势 |",
              "|------|--------|------|--------|------|--------|----------|------|"]
    for d in sample["devices"]:
        lines.append(f"| {d['device_id']} | {d['health_score']} | {d['risk_level']} | "
                     f"{d['anomaly_score']} | {d['top_anomaly_factor']} | {d['current_value']} | "
                     f"{d['normal_range']} | {d['trend_direction']} |")
    lines += ["", "## 4. 局限", "",
              "- 无多类故障标签与 RUL 标签，健康评分与风险分级为无监督结果，需人工/机理复核；",
              "- 专家规则阈值来自工程经验，可在 `configs/health_thresholds.yaml` 调整；",
              "- 喘振相关诊断需结合喘振边界（当前缺失），不在本阶段范围。", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="健康诊断指标与报告")
    ap.add_argument("--processed-dir", default="data/processed")
    ap.add_argument("--thresholds", default=rules.DEFAULT_THRESHOLDS)
    ap.add_argument("--health-config", default=FAULT_CFG)
    ap.add_argument("--metrics-dir", default="artifacts/metrics")
    ap.add_argument("--report", default="analysis/reports/phaseA5/health_report.md")
    args = ap.parse_args()
    out = evaluate(args.processed_dir, args.thresholds, args.health_config,
                   args.metrics_dir, args.report)
    for dv, m in out["devices"].items():
        print(f"  {dv}: 健康分={m['health_score_latest']} 风险={m['risk_level_latest']} "
              f"异常率={m['anomaly_rate']} 分离度={m['separation']}")
    print("[OK] health_metrics.json 与 health_report.md 已生成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
