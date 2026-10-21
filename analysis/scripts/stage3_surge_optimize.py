"""第三阶段: GRU 喘振预警优化 (概率校准 + 双阈值迟滞 + 连续确认 + 冷却 + B4 分级融合)。

流程 (协议冻结): dev 训练 -> valid 校准(Isotonic)+选告警参数 -> test 冻结评估。
对照: 原始 GRU (单阈值) vs 优化后 GRU (校准+稳定化) vs GRU+分级 B4 融合。
输出: analysis/reports/b/stage3/surge/{surge_optimized.json,SURGE_OPTIMIZED.md}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from air_station.stage2 import surge_nn as SNN
from air_station.stage2 import surge_sim as SS
from air_station.stage2 import surge_warn as SW

OUT = Path("analysis/reports/b/stage3/surge")


def _prob_fn(feats, hz, window_s):
    return SNN.predict_proba(feats, hz, window_s)


def _b4_margin_alarm(feats, band):
    p = feats["pressure"].to_numpy(float); f = feats["flow"].to_numpy(float)
    return (f < SW.b4_boundary_flow(p) + band).astype(float)


def eval_bool(pred_fn, series_list, horizon_s, match_w, lead_target, hz=1.0):
    Y, P = [], []
    per = []
    for s in series_list:
        feats = SW.window_features(s.frame(True), hz, 45)
        y = SW.hazard_label(s.t, s.events, horizon_s)
        pred = np.asarray(pred_fn(feats)).astype(bool)
        Y.append(y); P.append(pred.astype(int))
        per.append((s, pred))
    tm = SW.time_level_metrics(np.concatenate(Y), np.concatenate(P))
    hours = sum(len(s.t) for s in series_list) / hz / 3600.0
    tot = sum(len(s.events) for s in series_list)
    det = 0; leads = []; fa = 0; runs = 0; matched = 0
    bysc = {}
    for s, pred in per:
        m = SW.event_level_metrics(s.events, pred, hz, match_w, lead_target, 0.0, 0.0)
        det += m["detected"]; leads += m["lead_s"]; fa += m["false_alarms"]
        runs += m["n_alarm_runs"]; matched += m["true_warnings"]
        b = bysc.setdefault(s.scenario, {"events": 0, "detected": 0, "lg30": 0, "lead": []})
        b["events"] += m["events"]; b["detected"] += m["detected"]
        b["lg30"] += sum(1 for x in m["lead_s"] if x >= lead_target); b["lead"] += m["lead_s"]
    lg = sum(1 for x in leads if x >= lead_target)
    ep = matched / runs if runs else None
    er = lg / tot if tot else None
    ef1 = 2 * ep * er / (ep + er) if (ep and er) else None
    return {"time_level": tm,
            "event_level": {"events": tot, "detected": det, "n_alarm_runs": runs,
                            "true_warnings": matched,
                            "precision_event_level": round(ep, 4) if ep is not None else None,
                            "recall_event_level": round(det / tot, 4) if tot else None,
                            "lead_ge30_coverage": round(er, 4) if er is not None else None,
                            "event_f1": round(ef1, 4) if ef1 is not None else None,
                            "false_alarms": fa,
                            "false_alarms_per_hour": round(fa / hours, 4) if hours else None,
                            "lead_median_s": round(float(np.median(leads)), 2) if leads else None,
                            "lead_s": leads,
                            "by_scenario": {k: {"events": v["events"], "detected": v["detected"],
                                                "lead_ge30_coverage": (round(v["lg30"] / v["events"], 4)
                                                                       if v["events"] else None),
                                                "lead_median_s": (round(float(np.median(v["lead"])), 2)
                                                                  if v["lead"] else None)}
                                            for k, v in bysc.items()}}}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = SS.load_config("configs/stage2_surge.yaml")
    hz = float(cfg["sampling"]["hz"]); W = int(cfg["predictor"]["window_s"])
    H = float(cfg["predictor"]["horizon_s"]); match_w = 120.0; lead_t = 30.0
    thr_grid = list(cfg["predictor"]["thresholds"])
    splits = {seg: SS.build_split(cfg, seg) for seg in ("dev", "valid", "test")}

    # 训练 GRU (dev), 冻结
    SNN._train("gru", splits["dev"], hz, W, H)

    # ---- 概率校准: 在 valid 上拟合 Isotonic ----
    from sklearn.isotonic import IsotonicRegression
    Xv, Yv, Pv = [], [], []
    for s in splits["valid"]:
        feats = SW.window_features(s.frame(True), hz, W)
        y = SW.hazard_label(s.t, s.events, H)
        p = SNN.predict_proba(feats, hz, W)
        Xv.append(p); Yv.append(y)
    xv = np.concatenate(Xv); yv = np.concatenate(Yv)
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(xv, yv)

    def cal_prob(feats):
        return iso.predict(np.clip(SNN.predict_proba(feats, hz, W), 0.0, 1.0))

    report = {"meta": {"validation_mode": "simulation", "window_s": W, "horizon_s": H,
                       "n_events": {k: sum(len(s.events) for s in splits[k]) for k in splits}},
              "before_raw_gru": {}, "after_stabilized": {}, "graded_fusion": {}}

    # ---- before: 原始 GRU 单阈值 (valid 选 thr) ----
    rawfn = lambda feats: SNN.predict_proba(feats, hz, W)
    best = None
    for thr in thr_grid:
        m = eval_bool(lambda f, t=thr: rawfn(f) >= t, splits["valid"], H, match_w, lead_t, hz)
        key = (m["time_level"]["accuracy"] or 0, m["event_level"]["lead_ge30_coverage"] or 0)
        if best is None or key > best[0]:
            best = (key, thr, m)
    thr_raw = best[1]
    report["before_raw_gru"] = {"threshold": thr_raw, "valid": best[2],
                                "test": eval_bool(lambda f: rawfn(f) >= thr_raw, splits["test"], H, match_w, lead_t, hz)}

    # ---- after: 校准 + 双阈值迟滞 + 连续确认 + 冷却; 在 valid 上网格选择 ----
    cp = np.concatenate([cal_prob(SW.window_features(s.frame(True), hz, W)) for s in splits["valid"]])
    quant = [float(np.quantile(cp, q)) for q in (0.90, 0.95, 0.98, 0.99, 0.995, 0.999)]
    quant = sorted(set(round(q, 4) for q in quant))
    grid = [{"high": hi, "low": round(hi * f, 4), "confirm_s": c, "cooldown_s": cd}
            for hi in quant for f in (0.3, 0.5)
            for c in (0.0, 5.0, 10.0) for cd in (0.0, 60.0, 180.0)]
    best = None
    for g in grid:
        fn = lambda f, g=g: SW.form_alarms(cal_prob(f), hz, g["high"], g["low"],
                                           g["confirm_s"], g["cooldown_s"])
        m = eval_bool(fn, splits["valid"], H, match_w, lead_t, hz)
        key = (1 if (m["time_level"]["accuracy"] or 0) >= 0.90 else 0,
               m["event_level"]["lead_ge30_coverage"] or 0.0,
               -(m["event_level"]["false_alarms_per_hour"] or 0.0),
               m["event_level"]["event_f1"] or 0.0)
        if best is None or key > best[0]:
            best = (key, g, m)
    gsel = best[1]
    fn_after = lambda f: SW.form_alarms(cal_prob(f), hz, gsel["high"], gsel["low"],
                                        gsel["confirm_s"], gsel["cooldown_s"])
    report["after_stabilized"] = {"params": gsel, "valid": best[2],
                                  "test": eval_bool(fn_after, splits["test"], H, match_w, lead_t, hz)}

    # ---- graded fusion: GRU 预测性提示 OR B4 即时高危阻断 (band=0: flow<q_b4) ----
    fn_fuse = lambda f: (_b4_margin_alarm(f, 0.0) > 0) | fn_after(f)
    v = eval_bool(fn_fuse, splits["valid"], H, match_w, lead_t, hz)
    report["graded_fusion"] = {"b4_band_m3min": 0.0, "valid": v,
                               "test": eval_bool(fn_fuse, splits["test"], H, match_w, lead_t, hz)}

    (OUT / "surge_optimized.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _md(report, OUT / "SURGE_OPTIMIZED.md")
    print("[stage3-surge] done")
    for k in ("before_raw_gru", "after_stabilized", "graded_fusion"):
        t = report[k]["test"]; tl = t["time_level"]; el = t["event_level"]
        print(f"  {k:18s} acc={tl['accuracy']} P={tl['precision']} R={tl['recall']} F1={tl['f1']} "
              f"lead30={el['lead_ge30_coverage']} evP={el['precision_event_level']} FA/h={el['false_alarms_per_hour']}")


def _md(report, path):
    L = ["# GRU 喘振预警优化 (第三阶段; validation_mode=simulation)", "",
         f"- dev/valid/test 事件数: {report['meta']['n_events']}", "",
         "| 方案 | Acc | Prec | Recall | F1 | FPR | FNR | 事件召回 | 提前≥30s覆盖 | 事件精确率 | 误报/h | 中位提前s |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, name in (("before_raw_gru", "原始GRU"), ("after_stabilized", "校准+稳定化"),
                    ("graded_fusion", "分级融合(B4∪GRU)")):
        tl = report[k]["test"]["time_level"]; el = report[k]["test"]["event_level"]
        L.append(f"| {name} | {tl['accuracy']} | {tl['precision']} | {tl['recall']} | {tl['f1']} | "
                 f"{tl['fpr']} | {tl['fnr']} | {el['recall_event_level']} | {el['lead_ge30_coverage']} | "
                 f"{el['precision_event_level']} | {el['false_alarms_per_hour']} | {el['lead_median_s']} |")
    L += ["", f"- 稳定化参数: `{report['after_stabilized']['params']}`",
          f"- 融合 B4 带宽: {report['graded_fusion']['b4_band_m3min']} m3/min",
          "", "## 分场景 (优化后)", ""]
    for k, v in report["after_stabilized"]["test"]["event_level"]["by_scenario"].items():
        L.append(f"- **{k}**: events={v['events']} detected={v['detected']} "
                 f"lead≥30={v['lead_ge30_coverage']} median={v['lead_median_s']}s")
    path.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
