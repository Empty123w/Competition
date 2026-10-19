"""滚动回放预测核心 (阶段 A6)。

扩展窗口 + 每 7 天重训 + 每折前 3 个完整自然日区间校准;
同折三时域用 joblib 受控并行, 每个 LightGBM 限 n_jobs=2;
折级检查点与断点续跑; 不修改冻结产物。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.models.forecasting import rolling_features as RF

TIME = "time"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest().upper()


def _config_sha(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def build_folds(cfg: dict) -> list[dict]:
    first = pd.Timestamp(cfg["first_as_of"])
    valid_end = pd.Timestamp(cfg["valid"]["end"])
    step = pd.Timedelta(days=int(cfg["refit_interval_days"]))
    folds, s = [], first
    while s <= valid_end:
        folds.append({"fold_id": f"fold_{s.strftime('%Y%m%d')}", "S": s,
                      "pred_end": min(s + step, valid_end + pd.Timedelta(minutes=1))})
        s = s + step
    return folds


def _build_model(cfg: dict):
    m = cfg["model"]
    if m.get("type", "lightgbm") == "lightgbm":
        try:
            from lightgbm import LGBMRegressor
            return LGBMRegressor(random_state=m.get("random_state", 42),
                                 n_jobs=int(m.get("n_jobs", 2)), verbose=-1, **m["params"]), "LightGBM"
        except Exception:
            pass
    from sklearn.ensemble import HistGradientBoostingRegressor
    p = m.get("params", {})
    return HistGradientBoostingRegressor(
        random_state=m.get("random_state", 42),
        max_iter=p.get("n_estimators", 300), learning_rate=p.get("learning_rate", 0.05),
        max_leaf_nodes=p.get("num_leaves", 31)), "HistGradientBoosting"


def _fit_horizon(fold: dict, h: int, feat5: pd.DataFrame, cfg: dict, model_dir: Path) -> dict:
    """单折单时域: 训练 + 校准 + 预测。返回 dict(preds, audit, meta)。"""
    import joblib
    import time as _t
    S = fold["S"]; fold_id = fold["fold_id"]
    cal_start = (S - pd.Timedelta(days=int(cfg["calibration_days"]))).normalize()
    cal_end = S - pd.Timedelta(minutes=1)

    sub = feat5.copy()
    sub["target_time"] = sub[TIME] + pd.Timedelta(minutes=h)

    train = sub[sub["target_time"] < cal_start]
    cal = sub[(sub["target_time"] < S) & (sub[TIME] >= cal_start) & (sub[TIME] <= cal_end)]
    pred = sub[(sub[TIME] >= S) & (sub[TIME] < fold["pred_end"])]

    # 环境特征方案: 仅用本折训练+校准窗口的缺失率决定
    win = pd.concat([train, cal]) if len(train) else cal
    tr_rate, hm_rate = RF.environment_missing_rate(win)
    thr = float(cfg["features"].get("environment_missing_rate_fallback_threshold", 0.30))
    disabled = bool(tr_rate > thr or hm_rate > thr)
    profile = "core_no_environment" if disabled else "full_features"
    fcols = RF.feature_columns(profile, cfg)

    tr = train.dropna(subset=["target"])
    cal = cal.dropna(subset=["target"])
    base_end = tr[TIME].max(); tgt_end = tr["target_time"].max()
    cal_tgt_end = cal["target_time"].max() if len(cal) else pd.NaT
    cutoff = sub[sub[TIME] <= S][TIME].max()
    gt = train[TIME].max()
    gate_ok = (len(tr) >= cfg["qc"]["min_train_rows"]) and (len(cal) >= cfg["qc"]["min_calibration_rows"])

    t0 = _t.perf_counter()
    model, mtype = _build_model(cfg)
    quality_reason = "ok"
    if not gate_ok:
        quality_reason = "model_warmup_missing"
    else:
        model.fit(tr[fcols], tr["target"])

    def _predict(df):
        if df.empty or quality_reason != "ok":
            return np.full(len(df), np.nan)
        return np.asarray(model.predict(df[fcols]), dtype=float)

    # 校准残差分位数
    if quality_reason == "ok" and len(cal) > 0:
        r = cal["target"].to_numpy() - _predict(cal)
        q_lo = float(np.nanquantile(r, cfg["interval"]["lower_quantile"]))
        q_hi = float(np.nanquantile(r, cfg["interval"]["upper_quantile"]))
    else:
        q_lo = q_hi = 0.0
    if len(cal) < cfg["qc"]["min_calibration_rows"] and quality_reason == "ok":
        quality_reason = "interval_calibration_insufficient"

    pv = _predict(pred)
    elapsed = _t.perf_counter() - t0

    # 物理约束: 流量非负 (单调截断保持上下界有序)
    pf = np.clip(pv, 0.0, None)
    plo = np.clip(pv + q_lo, 0.0, None)
    phi = np.clip(pv + q_hi, 0.0, None)
    preds = pd.DataFrame({
        "as_of": pred[TIME].to_numpy(), "target_time": pred["target_time"].to_numpy(),
        "horizon_minutes": h, "predicted_flow": pf,
        "lower_bound": plo, "upper_bound": phi,
    })
    qv = np.isfinite(preds["predicted_flow"]) & (preds["lower_bound"] <= preds["predicted_flow"]) \
        & (preds["predicted_flow"] <= preds["upper_bound"])
    preds["quality_valid"] = qv
    fv = pred[TIME] >= pd.Timestamp(cfg["valid"]["start"])

    # 保存模型
    fp = model_dir / fold_id
    fp.mkdir(parents=True, exist_ok=True)
    mpath = fp / f"h{h}.pkl"
    model_version = f"rolling-lgbm-v1-{fold_id}-h{h}"
    if quality_reason == "ok":
        joblib.dump({"model": model, "features": fcols, "horizon": h,
                     "model_type": mtype, "version": model_version}, mpath)

    n = len(preds)
    audit = pd.DataFrame({
        "as_of": preds["as_of"].to_numpy(),
        "horizon_minutes": h,
        "fold_id": fold_id,
        "target_time": preds["target_time"].to_numpy(),
        "target_type": cfg["target"]["type"], "target_unit": cfg["target"]["unit"],
        "model_type": mtype, "model_version": model_version,
        "model_refit_time": S.isoformat(),
        "model_train_base_end": str(base_end), "model_train_target_end": str(tgt_end),
        "calibration_start": str(cal_start), "calibration_end": str(cal_end),
        "calibration_target_end": str(cal_tgt_end),
        "input_data_cutoff_time": str(cutoff),
        "training_sample_count": int(len(tr)), "calibration_sample_count": int(len(cal)),
        "feature_profile": profile,
        "ambient_temp_missing_rate": round(tr_rate, 4),
        "ambient_humidity_missing_rate": round(hm_rate, 4),
        "environment_features_disabled": disabled,
        "hyperparameter_source": cfg["audit"]["hyperparameter_source"],
        "model_selection_method": cfg["audit"]["model_selection_method"],
        "model_selection_data_cutoff": cfg["audit"]["model_selection_data_cutoff"],
        "feature_selection_provenance": cfg["audit"]["feature_selection_provenance"],
        "feature_selection_data_cutoff": cfg["audit"]["feature_selection_data_cutoff"],
        "evaluation_mode": np.where(fv.to_numpy(), "rolling_update_evaluation", "development"),
        "rolling_update": fv.to_numpy(),
        "interval_level": cfg["interval"]["level"],
        "fit_elapsed_seconds": round(elapsed, 3),
        "quality_valid": qv,
        "quality_reason": quality_reason,
    })
    meta = {"model_path": str(mpath) if mpath.exists() else None,
            "model_sha256": _sha256(mpath) if mpath.exists() else None,
            "features": fcols, "quality_reason": quality_reason,
            "train_rows": int(len(tr)), "cal_rows": int(len(cal)), "pred_rows": int(len(pred))}
    return {"preds": preds, "audit": audit, "meta": meta}


def run(config_path: str = "configs/rolling_forecast.yaml",
        fold_ids: list[str] | None = None, resume: bool = True) -> dict:
    from joblib import Parallel, delayed
    cfg = RF.load_config(config_path)
    pdir = Path(cfg["paths"]["processed_dir"])
    model_dir = Path(cfg["paths"]["model_dir"]); model_dir.mkdir(parents=True, exist_ok=True)
    cfg_sha = _config_sha(config_path)

    feat1 = RF.build_features(str(pdir), cfg)
    feat5 = RF.sample_grid(feat1, int(cfg["issue_frequency_minutes"]))
    folds = build_folds(cfg)
    if fold_ids:
        folds = [f for f in folds if f["fold_id"] in set(fold_ids)]

    all_preds, all_audit, fold_meta = [], [], {}
    horizon_step = int(cfg["issue_frequency_minutes"])
    for fold in folds:
        fid = fold["fold_id"]
        ck = model_dir / fid / "fold_complete.json"
        if resume and ck.exists():
            d = json.loads(ck.read_text(encoding="utf-8"))
            if d.get("config_sha256") == cfg_sha:
                for h in cfg["horizons_minutes"]:
                    k = f"{fid}:{h}"
                    fold_meta[k] = d["meta"][str(h)]
                pf = pd.read_parquet(model_dir / fid / "preds.parquet")
                af = pd.read_parquet(model_dir / fid / "audit.parquet")
                all_preds.append(pf); all_audit.append(af)
                print(f"[skip] {fid} 已完成")
                continue
        results = Parallel(n_jobs=int(cfg["parallel"]["n_jobs_fold"]))(
            delayed(_fit_horizon)(fold, h, feat5, cfg, model_dir) for h in cfg["horizons_minutes"])
        pf = pd.concat([r["preds"] for r in results], ignore_index=True)
        af = pd.concat([r["audit"] for r in results], ignore_index=True)
        for h, r in zip(cfg["horizons_minutes"], results):
            fold_meta[f"{fid}:{h}"] = r["meta"]
        d = model_dir / fid; d.mkdir(parents=True, exist_ok=True)
        pf.to_parquet(d / "preds.parquet", index=False)
        af.to_parquet(d / "audit.parquet", index=False)
        ck.write_text(json.dumps({"fold_id": fid, "config_sha256": cfg_sha,
                                  "meta": {str(h): r["meta"] for h, r in zip(cfg["horizons_minutes"], results)}},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
        all_preds.append(pf); all_audit.append(af)
        print(f"[done] {fid} train={[r['meta']['train_rows'] for r in results]} "
              f"pred={[r['meta']['pred_rows'] for r in results]}")
    preds = pd.concat(all_preds, ignore_index=True) if all_preds else pd.DataFrame()
    audit = pd.concat(all_audit, ignore_index=True) if all_audit else pd.DataFrame()
    return {"preds": preds, "audit": audit, "fold_meta": fold_meta, "config_sha256": cfg_sha, "folds": folds}


def main() -> int:
    ap = argparse.ArgumentParser(description="滚动回放预测核心")
    ap.add_argument("--config", default="configs/rolling_forecast.yaml")
    ap.add_argument("--folds", default="", help="逗号分隔 fold_id; 空=全部")
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args()
    ids = [x for x in args.folds.split(",") if x] or None
    r = run(args.config, ids, resume=not args.no_resume)
    print(f"[OK] 折数={len(r['folds'])} 预测行={len(r['preds'])} 审计行={len(r['audit'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
