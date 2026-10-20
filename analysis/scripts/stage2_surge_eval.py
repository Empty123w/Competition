"""第二阶段: 喘振预警评估运行器。

流程 (评价协议在实验前冻结, 见 configs/stage2_surge.yaml):
  1. 生成 dev / valid / test 三段独立高频仿真;
  2. dev 训练 LightGBM / (可选) GRU/LSTM; valid 选阈值; test 仅冻结评估;
  3. 与 B4 代理基线对照;
  4. 输出时间级 (Acc/P/R/F1/FPR/FNR) 与事件级 (recall/误报/提前时间) 指标。

用法: python3 analysis/scripts/stage2_surge_eval.py [--config ...] [--no-torch]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from air_station.stage2 import surge_sim as SS
from air_station.stage2 import surge_warn as SW


def eval_predictor(prob_fn, series_list, hz, window_s, horizon_s, thr,
                   match_window_s, lead_target_s, min_alarm_s=0.0):
    Y, P = [], []
    per = []
    for s in series_list:
        feats = SW.window_features(s.frame(noisy=True), hz, window_s)
        y = SW.hazard_label(s.t, s.events, horizon_s)
        prob = prob_fn(feats)
        pred = np.asarray(prob) >= thr
        Y.append(y); P.append(pred.astype(int))
        per.append((s, pred))
    tmetrics = SW.time_level_metrics(np.concatenate(Y), np.concatenate(P))
    hours = sum(len(s.t) for s in series_list) / hz / 3600.0
    tot_ev = sum(len(s.events) for s in series_list)
    det = 0; leads = []; fa = 0; runs = 0; matched = 0
    bysc = {}
    for s, pred in per:
        m = SW.event_level_metrics(s.events, pred, hz, match_window_s, lead_target_s, 0.0, min_alarm_s)
        det += m["detected"]; leads += m["lead_s"]; fa += m["false_alarms"]
        runs += m["n_alarm_runs"]; matched += m["true_warnings"]
        b = bysc.setdefault(s.scenario, {"events": 0, "detected": 0, "lead_ge30": 0, "lead_s": []})
        b["events"] += m["events"]; b["detected"] += m["detected"]
        b["lead_ge30"] += sum(1 for x in m["lead_s"] if x >= lead_target_s)
        b["lead_s"] += m["lead_s"]
    lead_ge30 = sum(1 for x in leads if x >= lead_target_s)
    ep = (matched / runs) if runs else None
    er = (lead_ge30 / tot_ev) if tot_ev else None
    ef1 = (2 * ep * er / (ep + er)) if (ep and er) else None
    emetrics = {
        "events": tot_ev, "detected": det,
        "recall_event_level": round(det / tot_ev, 4) if tot_ev else None,
        "lead_s": leads,
        "lead_median_s": round(float(np.median(leads)), 3) if leads else None,
        "lead_min_s": round(float(np.min(leads)), 3) if leads else None,
        "lead_ge30_coverage": round(er, 4) if er is not None else None,
        "n_alarm_runs": runs, "true_warnings": matched,
        "precision_event_level": round(ep, 4) if ep is not None else None,
        "event_f1": round(ef1, 4) if ef1 is not None else None,
        "fnr_event_level": round((tot_ev - lead_ge30) / tot_ev, 4) if tot_ev else None,
        "false_alarms": fa,
        "false_alarms_per_hour": round(fa / hours, 4) if hours else None,
        "by_scenario": {k: {"events": v["events"], "detected": v["detected"],
                            "lead_ge30_coverage": round(v["lead_ge30"] / v["events"], 4) if v["events"] else None,
                            "lead_median_s": round(float(np.median(v["lead_s"])), 2) if v["lead_s"] else None}
                        for k, v in bysc.items()},
    }
    return {"time_level": tmetrics, "event_level": emetrics}


def b4_ew_fn(hz, window_s, band):
    def fn(feats):
        p = feats["pressure"].to_numpy(dtype=float)
        f = feats["flow"].to_numpy(dtype=float)
        qb4 = SW.b4_boundary_flow(p)
        return (f < (qb4 + band)).astype(float)
    return fn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/stage2_surge.yaml")
    ap.add_argument("--no-torch", action="store_true")
    args = ap.parse_args()
    t0 = time.time()
    cfg = SS.load_config(args.config)
    hz = float(cfg["sampling"]["hz"])
    window_s = int(cfg["predictor"]["window_s"])
    horizon_s = float(cfg["predictor"]["horizon_s"])
    match_w = float(cfg["predictor"]["match_window_s"])
    lead_target = float(cfg["predictor"]["lead_target_s"])
    thr_grid = list(cfg["predictor"]["thresholds"])
    min_alarm_s = float(cfg["predictor"].get("min_alarm_s", 8.0))
    gate_band = float(cfg["predictor"].get("gate_margin", 0.06))

    splits = {seg: SS.build_split(cfg, seg) for seg in ("dev", "valid", "test")}
    dev = SW.assemble(splits["dev"], hz, window_s, horizon_s)
    valid = SW.assemble(splits["valid"], hz, window_s, horizon_s)
    test = SW.assemble(splits["test"], hz, window_s, horizon_s)

    report = {"meta": {"validation_mode": "simulation", "config": args.config,
                       "window_s": window_s, "horizon_s": horizon_s,
                       "events": {"dev": int(dev.y.sum() > 0), "dev_pos_seconds": int(dev.y.sum()),
                                  "valid_pos_seconds": int(valid.y.sum()),
                                  "test_pos_seconds": int(test.y.sum())},
                       "n_true_events": {"dev": sum(len(s.events) for s in splits["dev"]),
                                         "valid": sum(len(s.events) for s in splits["valid"]),
                                         "test": sum(len(s.events) for s in splits["test"])}}}

    # ---------------- LightGBM ----------------
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score
    clf = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=31,
                             subsample=0.9, colsample_bytree=0.9, random_state=42,
                             n_jobs=2, verbose=-1)
    clf.fit(dev.X, dev.y)
    report["lightgbm_auc"] = {"dev": round(float(roc_auc_score(dev.y, clf.predict_proba(dev.X)[:, 1])), 4),
                              "valid": round(float(roc_auc_score(valid.y, clf.predict_proba(valid.X)[:, 1])), 4)}

    # 阈值: valid 上选 (lexicographic: lead coverage -> precision -> -fpr)
    def prob_lgb(ds):
        return clf.predict_proba(ds.X)[:, 1]

    def choose_threshold(ds, fn, series):
        best = None
        for thr in thr_grid:
            m = eval_predictor(fn, series, hz, window_s, horizon_s, thr, match_w, lead_target, min_alarm_s)
            tl = m["time_level"]; el = m["event_level"]
            key = (1 if (tl["accuracy"] or 0.0) >= 0.90 else 0,
                   el["lead_ge30_coverage"] or 0.0,
                   tl["accuracy"] or 0.0, el["event_f1"] or 0.0)
            if best is None or key > best[0]:
                best = (key, thr, m)
        return best[1], best[2]

    def gate(fn):
        # ML 预警 + B4 代理安全过滤: 仅当代理裕度进入警戒带时允许告警
        def g(feats):
            m = feats["margin"].to_numpy(dtype=float)
            p = np.asarray(fn(feats), dtype=float)
            return np.where(m < gate_band, p, 0.0)
        return g

    models = {}
    lgb_fn = lambda feats: clf.predict_proba(feats[SW.FEATURE_COLS].to_numpy(dtype=float))[:, 1]

    # B4 代理提前预警规则: 在 valid 上选警戒带宽
    best_b4 = None
    for band in (3.0, 5.0, 8.0, 12.0, 20.0, 30.0):
        fn = b4_ew_fn(hz, window_s, band)
        m = eval_predictor(fn, splits["valid"], hz, window_s, horizon_s, 0.5, match_w, lead_target, min_alarm_s)
        key = (m["event_level"]["event_f1"] or 0.0, m["event_level"]["lead_ge30_coverage"] or 0.0,
               m["time_level"]["accuracy"] or 0.0)
        if best_b4 is None or key > best_b4[0]:
            best_b4 = (key, band, m)
    band_b4 = best_b4[1]
    models["b4_ew_rule"] = {"band_m3min": band_b4, "threshold": 0.5, "valid": best_b4[2],
                            "test": eval_predictor(b4_ew_fn(hz, window_s, band_b4), splits["test"], hz,
                                                   window_s, horizon_s, 0.5, match_w, lead_target, min_alarm_s)}

    # ML + B4 规则融合: ML 概率 ∧ 规则门 (安全过滤, 抑制高裕度误报)
    rule = b4_ew_fn(hz, window_s, band_b4)
    fused = lambda feats: np.asarray(lgb_fn(feats)) * np.asarray(rule(feats))
    for name, fn in (("lightgbm", lgb_fn), ("lgb_and_b4", fused)):
        thr, vm = choose_threshold(valid, fn, splits["valid"])
        models[name] = {"threshold": thr, "valid": vm,
                        "test": eval_predictor(fn, splits["test"], hz, window_s, horizon_s, thr,
                                               match_w, lead_target, min_alarm_s)}
    report["models"] = models

    # ---------------- 可选: GRU / LSTM ----------------
    if not args.no_torch:
        try:
            from air_station.stage2 import surge_nn as SNN
            torch_ok = SNN.available()
        except Exception:
            torch_ok = False
        if torch_ok:
            for arch in ("gru", "lstm"):
                thr_d, valid_d, test_d = SNN.train_eval(
                    splits, hz, window_s, horizon_s, arch, thr_grid, eval_predictor,
                    match_w, lead_target)
                report["models"][arch] = {"threshold": thr_d, "valid": valid_d, "test": test_d,
                                          "torch": True}
            # ensemble: lgb + gru mean prob
            report["models"]["ensemble"] = _ensemble(report, clf, splits, hz, window_s, horizon_s,
                                                     thr_grid, match_w, lead_target, eval_predictor)

    out_dir = Path(cfg["paths"]["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "surge_eval.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    _write_md(report, out_dir / "SURGE_EVAL.md")
    print(f"[stage2-surge] done in {time.time()-t0:.1f}s -> {out_dir/'surge_eval.json'}")
    for name, m in report["models"].items():
        t = m["test"]
        print(f"  {name:10s} acc={t['time_level']['accuracy']} P={t['time_level']['precision']} "
              f"R={t['time_level']['recall']} F1={t['time_level']['f1']} FPR={t['time_level']['fpr']} "
              f"lead>=30={t['event_level']['lead_ge30_coverage']} FA/h={t['event_level']['false_alarms_per_hour']}")


def _ensemble(report, clf, splits, hz, window_s, horizon_s, thr_grid, match_w, lead_target, eval_predictor):
    from air_station.stage2 import surge_nn as SNN
    def fn(feats):
        pl = clf.predict_proba(feats[SW.FEATURE_COLS].to_numpy(dtype=float))[:, 1]
        pn = SNN.predict_proba(feats, hz, window_s)
        return 0.5 * pl + 0.5 * pn
    valid_m = None; best = None
    for thr in thr_grid:
        m = eval_predictor(fn, splits["valid"], hz, window_s, horizon_s, thr, match_w, lead_target)
        key = (m["time_level"]["f1"] or 0.0, m["time_level"]["accuracy"] or 0.0,
               m["event_level"]["lead_ge30_coverage"] or 0.0)
        if best is None or key > best[0]:
            best = (key, thr, m)
    thr, valid_m = best[1], best[2]
    return {"threshold": thr, "valid": valid_m,
            "test": eval_predictor(fn, splits["test"], hz, window_s, horizon_s, thr, match_w, lead_target)}


def _write_md(report, path):
    L = ["# 喘振预警评估 (第二阶段; validation_mode=simulation)", "",
         f"- 窗口/预测域: window={report['meta']['window_s']}s, horizon={report['meta']['horizon_s']}s",
         f"- 真值事件数: dev={report['meta']['n_true_events']['dev']} "
         f"valid={report['meta']['n_true_events']['valid']} test={report['meta']['n_true_events']['test']}",
         f"- LightGBM AUC: dev={report.get('lightgbm_auc',{}).get('dev')} valid={report.get('lightgbm_auc',{}).get('valid')}",
         "", "| 模型 | thr | Acc | Prec | Recall | F1 | FPR | FNR | 事件召回 | 提前>=30s覆盖率 | 误报/h |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, m in report["models"].items():
        t = m["test"]; tl = t["time_level"]; el = t["event_level"]
        L.append(f"| {name} | {m['threshold']} | {tl['accuracy']} | {tl['precision']} | {tl['recall']} | "
                 f"{tl['f1']} | {tl['fpr']} | {tl['fnr']} | {el['recall_event_level']} | "
                 f"{el['lead_ge30_coverage']} | {el['false_alarms_per_hour']} |")
    L += ["", "## 提前时间明细 (test)", ""]
    for name, m in report["models"].items():
        el = m["test"]["event_level"]
        L.append(f"- **{name}**: median={el['lead_median_s']}s min={el['lead_min_s']}s leads={el['lead_s']}")
    path.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
