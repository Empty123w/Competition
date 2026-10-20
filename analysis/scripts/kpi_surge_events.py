"""B4 喘振事件级评估 (代理): 历史设备分钟 -> 代理裕度/等级 -> 事件。

严格区分:
  - 代理事件 (proxy): 基于历史运行点包络边界 (surge_boundary.json), 非厂家真实喘振线;
  - 真实标签: 数据集不含真实喘振正例 -> 误报/漏报/提前量 不可计算, 仅报代理指标。
输出: analysis/reports/b/kpi_surge_events.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.baseline import pipeline as PIPE
from air_station.safety import boundaries as BD
from air_station.safety import surge as SG

ROOT = Path(__file__).resolve().parents[2]
WIN = {"dev": ("2026-03-26", "2026-08-20"), "valid": ("2026-08-21", "2026-09-11")}
BLOCK_HIGH, BLOCK_UNKNOWN = True, True


def _events(levels: list[str], times: list[pd.Timestamp]):
    """连续 medium/high 段 -> 事件的 level 序列; 返回事件数、各等级事件数、代理提前量(min)。"""
    ev = 0
    high_ev = med_ev = 0
    lead = []
    cur = None; cur_start = None
    for lv, t in zip(levels, times):
        wl = lv in ("medium", "high")
        if wl and cur is None:
            cur = lv; cur_start = t
        elif wl and cur is not None:
            if lv == "high" and cur == "medium":
                lead.append((t - cur_start).total_seconds() / 60.0)
            cur = SG._WORST and (lv if SG._WORST.get(lv, 0) > SG._WORST.get(cur, 0) else cur)
        elif not wl and cur is not None:
            ev += 1; high_ev += 1 if cur == "high" else 0; med_ev += 1 if cur == "medium" else 0
            cur = None; cur_start = None
    if cur is not None:
        ev += 1; high_ev += 1 if cur == "high" else 0; med_ev += 1 if cur == "medium" else 0
    return ev, high_ev, med_ev, (round(float(np.median(lead)), 1) if lead else None), len(lead)


def main():
    dev, _ = PIPE.load_processed()
    dev = dev.copy(); dev["time"] = pd.to_datetime(dev["time"])
    cfg = BD.load_config()
    art = BD.load_boundary_artifact()
    warn = float(cfg["boundary"].get("warn_margin", 0.10))
    rep = {"boundary_version": art.get("boundary_version"),
           "boundary_type": "proxy (历史运行点包络, 非真实喘振线)",
           "validation_mode": "simulation", "warn_margin": warn,
           "block_on_high": BLOCK_HIGH, "block_on_unknown": BLOCK_UNKNOWN,
           "real_label_note": "数据无真实喘振正例标签 -> 误报/漏报/真实提前量不可计算"}
    for w, (a, b) in WIN.items():
        m = dev[(dev.time >= a) & (dev.time <= b) & dev["running"].astype(bool)].sort_values("time")
        out = {}
        for d in ("4#", "5#"):
            g = m[m.device_id == d]
            levels, times = [], []
            unknown = 0
            for _, r in g.iterrows():
                p = r["discharge_pressure_mpa"]; q = r["flow_m3_min"]
                if p is None or q is None or not np.isfinite(p) or not np.isfinite(q) or not BD._pressure_in_domain(float(p), cfg):
                    levels.append("unknown"); times.append(r["time"]); unknown += 1; continue
                qb, _ = BD.boundary_flow(d, float(p), cfg, art)
                if qb is None or qb <= 0:
                    levels.append("unknown"); times.append(r["time"]); unknown += 1; continue
                margin = (float(q) - qb) / qb
                levels.append("high" if margin < 0 else ("medium" if margin < warn else "low"))
                times.append(r["time"])
            n = len(levels)
            cnt = {lv: levels.count(lv) for lv in ("low", "medium", "high", "unknown")}
            ev, high_ev, med_ev, lead_med, lead_n = _events(levels, times)
            out[d] = {
                "minutes": n,
                "level_minutes": cnt,
                "level_pct": {k: round(v / n * 100, 3) if n else None for k, v in cnt.items()},
                "proxy_events": ev, "proxy_high_events": high_ev, "proxy_medium_events": med_ev,
                "blocked_proxy_steps": int(cnt["high"] + (cnt["unknown"] if BLOCK_UNKNOWN else 0)),
                "proxy_median_medium_to_high_lead_min": lead_med, "lead_pairs": lead_n,
            }
        rep[w] = out
    OUT = ROOT / "analysis/reports/b/kpi_surge_events.json"
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
