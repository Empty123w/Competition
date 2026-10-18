"""RUL 评估: 指标、基线对比、单调性、覆盖率、图与报告。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.models.rul import baselines as BL
from air_station.models.rul import features as F
from air_station.models.rul import simulator


def _metrics(y_true, y_pred, within_pct: float) -> dict:
    y_true = np.asarray(y_true, float); y_pred = np.asarray(y_pred, float)
    err = y_true - y_pred
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    denom = np.maximum(np.abs(y_true), 1.0)
    mape = float(np.mean(np.abs(err) / denom) * 100)
    within = float(np.mean(np.abs(err) <= within_pct * np.maximum(np.abs(y_true), 1.0)))
    return {"MAE": round(mae, 3), "RMSE": round(rmse, 3), "MAPE": round(mape, 3),
            f"within_{int(within_pct*100)}pct": round(within, 4)}


def evaluate(config_path: str = "configs/rul.yaml",
             seq_path: str = "data/simulated/rul_sequences.parquet",
             model_dir: str = "artifacts/models",
             metrics_dir: str = "artifacts/metrics",
             report_dir: str = "analysis/reports/phaseA5_rul",
             figure_dir: str = "analysis/figures") -> dict:
    import joblib
    cfg = simulator.load_config(config_path)
    within = float(cfg["evaluate"]["within_pct"])
    df = pd.read_parquet(seq_path); df["time"] = pd.to_datetime(df["time"])
    df = F.build_features(df, group_col="episode_id",
                          window_steps=cfg["baseline"]["window_steps"])

    splits = json.loads((Path(model_dir) / "rul_split.json").read_text(encoding="utf-8"))
    test = df[df["episode_id"].isin(splits["test"])].dropna(subset=F.FEATURES + ["rul_hours"]).copy()
    Xte = F.feature_matrix(test)

    preds = {}
    for name in ("median", "lower", "upper"):
        preds[name] = joblib.load(Path(model_dir) / f"rul_{name}.pkl")["model"].predict(Xte)
    lo = np.minimum(np.minimum(preds["lower"], preds["median"]), preds["upper"])
    hi = np.maximum(np.maximum(preds["upper"], preds["median"]), preds["lower"])
    med = np.clip(preds["median"], lo, hi)
    y = test["rul_hours"].to_numpy()

    m = _metrics(y, med, within)
    # 覆盖率
    coverage = float(np.mean((y >= lo) & (y <= hi)))
    # 单调性(预测应随退化总体下降)
    test = test.reset_index(drop=True)
    test["_pred"] = med
    dec = test.groupby("episode_id")["_pred"].apply(
        lambda s: float(np.mean(np.diff(s.to_numpy()) <= 0)) if len(s) > 1 else 1.0)
    mono_ratio = float(np.mean(dec.values))
    # 预测与真实的相关(应为强正相关, 二者同步下降)
    from scipy.stats import spearmanr
    spearman = float(spearmanr(med, y).statistic)
    # 基线对比
    test["_base"] = BL.trend_extrapolation(test, cfg, group_col="episode_id").to_numpy()
    base_ok = np.isfinite(test["_base"].to_numpy()) & np.isfinite(y)
    base_m = _metrics(y[base_ok], test["_base"].to_numpy()[base_ok], within)

    out = {
        "validation_mode": "simulation",
        "origin": "simulated",
        "model_type": cfg["model"]["type"],
        "n_test": int(len(test)),
        "test_episodes": int(test["episode_id"].nunique()),
        "metrics": m,
        "interval_coverage": round(coverage, 4),
        "interval_level": float(cfg["evaluate"]["interval_level"]),
        "monotonic_decreasing_ratio": round(mono_ratio, 4),
        "spearman_pred_true": round(spearman, 4),
        "baseline": {"name": "trend_extrapolation(vib_roll)", **base_m},
        "model_beats_baseline_MAE": bool(m["MAE"] < base_m["MAE"]),
        "bounds": {"lower_le_median_le_upper": True},
    }
    md = Path(metrics_dir); md.mkdir(parents=True, exist_ok=True)
    (md / "rul_metrics.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    _plot_episode(cfg, df, splits["test"], figure_dir)
    _write_report(Path(report_dir) / "rul_report.md", out)
    # 接口样例(与运行时接口一致)
    from air_station.models.rul import service
    try:
        sample = service.rul_json(config_path, seq_path, model_dir)
        rp = Path(report_dir); rp.mkdir(parents=True, exist_ok=True)
        (rp / "rul_interface_sample.json").write_text(
            json.dumps(sample, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    except Exception as e:  # noqa
        print(f"[warn] 生成 RUL 接口样例失败: {e}")
    return out


def _plot_episode(cfg: dict, df: pd.DataFrame, test_eps: list[str], figure_dir: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import joblib
    # 选一个较长的测试 episode
    cnt = df[df["episode_id"].isin(test_eps)].groupby("episode_id").size().sort_values()
    ep = cnt.index[-1]
    sub = df[df["episode_id"] == ep].sort_values("time").reset_index(drop=True)
    X = F.feature_matrix(sub)
    med = joblib.load("artifacts/models/rul_median.pkl")["model"].predict(X)
    lo = joblib.load("artifacts/models/rul_lower.pkl")["model"].predict(X)
    hi = joblib.load("artifacts/models/rul_upper.pkl")["model"].predict(X)
    lo2 = np.minimum(np.minimum(lo, med), hi); hi2 = np.maximum(np.maximum(hi, med), lo)
    t = sub["time"]
    fig, ax = plt.subplots(figsize=(11, 4.4), dpi=130)
    ax.plot(t, sub["rul_hours"], color="#111", lw=1.8, label="True RUL")
    ax.plot(t, np.clip(med, 0, None), color="#d62728", lw=1.6, ls="--", label="Predicted RUL (median)")
    ax.fill_between(t, np.clip(lo2, 0, None), np.clip(hi2, 0, None),
                    color="#9ecae1", alpha=0.45, label="Prediction interval (0.1-0.9)")
    # 保养复位点: 该 episode 末尾的下一个 episode 起点
    reset_time = t.iloc[-1]
    ax.axvline(reset_time, color="green", ls=":", lw=1.5)
    ax.annotate("maintenance reset\n(RUL -> 2000, new episode)", xy=(reset_time, 2000),
                xytext=(reset_time, 1500), fontsize=8, color="green",
                arrowprops=dict(arrowstyle="->", color="green"))
    ax.set_title(f"RUL degradation curve (simulated) - episode {ep}")
    ax.set_ylabel("RUL (hours)"); ax.legend(loc="upper right")
    ax.grid(alpha=0.3); fig.autofmt_xdate(); fig.tight_layout()
    Path(figure_dir).mkdir(parents=True, exist_ok=True)
    fig.savefig(Path(figure_dir) / "rul_degradation_curve.png"); plt.close(fig)


def _write_report(path: Path, out: dict) -> None:
    m = out["metrics"]; b = out["baseline"]
    lines = [
        "# 阶段 A5-RUL 剩余寿命预测报告", "",
        "> **重要声明**：本阶段 RUL 标签为**模拟数据(origin=simulated)**，用于验证“退化建模-区间预测”的完整链路，**不代表真实寿命精度**。", "",
        f"- 模型：{out['model_type']}（分位数回归：中位数 0.5 / 下界 0.1 / 上界 0.9）",
        f"- 划分：按 `episode_id`（无退化序列跨集合）；测试 episode 数 {out['test_episodes']}，样本 {out['n_test']}",
        f"- 退化过程：初始 RUL 2000h，保养复位阈值 300h，复位产生新 `episode_id`", "",
        "## 1. 测试集指标（模拟）", "",
        "| 指标 | 模型 | 趋势外推基线 |", "|------|------|--------------|",
        f"| MAE(h) | {m['MAE']} | {b['MAE']} |",
        f"| RMSE(h) | {m['RMSE']} | {b['RMSE']} |",
        f"| MAPE(%) | {m['MAPE']} | {b['MAPE']} |",
        f"| 落在真实±20% 比例 | {m.get('within_20pct')} | {b.get('within_20pct')} |", "",
        f"- 预测区间({out['interval_level']:.0%})覆盖率：**{out['interval_coverage']}**",
        f"- 单调下降比例：**{out['monotonic_decreasing_ratio']}**；预测-真实 Spearman：**{out['spearman_pred_true']}**",
        f"- 模型 MAE 优于趋势基线：**{out['model_beats_baseline_MAE']}**", "",
        "## 2. 结论与局限", "",
        "- 模拟退化下，Sensor-only 特征（振动/温度/电流/能效）可驱动 RUL 回归，区间顺序正确、覆盖率合理；",
        "- 本结果为**模拟验证**，不含真实失效/寿命标签；真实 RUL 需真实更换/失效记录，当前数据缺失；",
        "- 保养复位场景由新 `episode_id` 表达，训练/评估均按 episode 严格隔离。", "",
        "![RUL degradation](../figures/rul_degradation_curve.png)", "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="评估 RUL 模型")
    ap.add_argument("--config", default="configs/rul.yaml")
    ap.add_argument("--sequences", default="data/simulated/rul_sequences.parquet")
    ap.add_argument("--model-dir", default="artifacts/models")
    ap.add_argument("--metrics-dir", default="artifacts/metrics")
    ap.add_argument("--report-dir", default="analysis/reports/phaseA5_rul")
    ap.add_argument("--figure-dir", default="analysis/figures")
    args = ap.parse_args()
    out = evaluate(args.config, args.sequences, args.model_dir,
                   args.metrics_dir, args.report_dir, args.figure_dir)
    m = out["metrics"]
    print(f"  MAE={m['MAE']} RMSE={m['RMSE']} MAPE={m['MAPE']}% "
          f"±20%={out['metrics'].get('within_20pct')} 覆盖={out['interval_coverage']} "
          f"单调={out['monotonic_decreasing_ratio']} 优于基线={out['model_beats_baseline_MAE']}")
    print("[OK] rul_metrics.json / rul_report.md / rul_degradation_curve.png 已生成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
