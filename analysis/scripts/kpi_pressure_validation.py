"""压力合格率: 历史 SCADA 母管压力校验 + 仿真钳制/过供核查。

- 历史母管压力 (SCADA, state.header_pressure_mpa) 在开发/验证窗口的分布与合格率。
- 分"全时"与"站运行(total_flow>0 且 quality 有效)"两种样本。
- 对 B1 策略: 用历史压力直接算合格率。
- 仿真: 引用 pressure_sim_dynamics_v2 的钳制 [0.40,0.75] 与过供 -> 上界钳制说明。
输出: analysis/reports/b/kpi_pressure_validation.json
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from air_station.baseline import pipeline as PIPE

ROOT = Path(__file__).resolve().parents[2]
BAND = (0.55, 0.66)
WIN = {"dev": ("2026-03-26", "2026-08-20"), "valid": ("2026-08-21", "2026-09-11")}


def main():
    dev, st = PIPE.load_processed()
    st = st.copy(); st["time"] = pd.to_datetime(st["time"])
    rep = {"band_mpa": list(BAND), "band_source": "simulation assumption (CAL-05 pending)",
           "note": "历史为 SCADA 实测母管压力; 仿真压力为模型反事实, 上界钳制会引起超带伪影"}
    for w, (a, b) in WIN.items():
        m = st[(st.time >= a) & (st.time <= b)]
        hp = m["header_pressure_mpa"].astype(float)
        run = hp[(m["total_flow_m3_min"].astype(float) > 0)]
        valid = hp[hp > 0]
        def comp(x):
            return round(float(((x >= BAND[0]) & (x <= BAND[1])).mean() * 100), 3) if len(x) else None
        rep[w] = {
            "n_all_min": int(len(hp)),
            "all": {"mean": round(float(hp.mean()), 4), "min": round(float(hp.min()), 4),
                    "max": round(float(hp.max()), 4), "compliance_pct": comp(hp),
                    "below_band_pct": round(float((hp < BAND[0]).mean() * 100), 3),
                    "above_band_pct": round(float((hp > BAND[1]).mean() * 100), 3)},
            "station_running": {"n": int(len(run)), "mean": round(float(run.mean()), 4),
                                "min": round(float(run.min()), 4), "max": round(float(run.max()), 4),
                                "compliance_pct": comp(run)},
            "nonzero": {"n": int(len(valid)), "mean": round(float(valid.mean()), 4),
                        "compliance_pct": comp(valid)},
        }
    # 仿真钳制/过供
    sim_path = ROOT / "analysis/reports/b/pressure_sim_dynamics_v2.json"
    if sim_path.exists():
        sv = json.loads(sim_path.read_text(encoding="utf-8"))
        rep["simulation"] = {"clamp": [0.40, 0.75], "p0": 0.60, "band": list(BAND),
                             "source_file": sim_path.name,
                             "note": "过供 push 压力至上钳制 0.75 (>0.66) 产生超带伪影; 非真实越限"}
    OUT = ROOT / "analysis/reports/b/kpi_pressure_validation.json"
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
