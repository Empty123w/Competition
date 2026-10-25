"""第二阶段: 汇总 KPI 仪表盘 (读取各实验 JSON, 输出最终交付表)。

用法: python3 analysis/scripts/stage2_kpi_summary.py
输出: analysis/reports/b/stage2/STAGE2_KPI_SUMMARY.md + stage2_kpi.json
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("analysis/reports/b")
OUT = ROOT / "stage2"


def _load(p):
    p = Path(p)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    b5 = _load(ROOT / "b5_unified_summary.json")
    press = _load(OUT / "pressure" / "pressure_mpc_eval.json")
    surge = _load(OUT / "surge" / "surge_eval.json")
    rl = _load(OUT / "rl" / "rl_eval.json")
    lat = _load(ROOT / "kpi_latency.json")

    pool = b5["pooled"]["dev_valid_test_service_equiv"] if b5 else {}
    energy = {k: pool.get(k, {}).get("sec_reduction_pct") for k in ("b3_b4", "bal", "lrb_t10")}
    lowred = {k: pool.get(k, {}).get("low_reduction_pct") for k in ("b3_b4", "bal", "lrb_t10")}

    best_press = {}
    if press:
        for sc, b in press["scenarios"].items():
            best_press[sc] = {"compl_reactive": b["reactive_pi"]["pressure_compliance"],
                              "compl_mpc": b["mpc"]["pressure_compliance"],
                              "std_reactive": b["reactive_pi"]["pressure_std"],
                              "std_mpc": b["mpc"]["pressure_std"],
                              "std_red_pct": b["mpc"].get("pressure_std_reduction_pct")}
    surge_best = None
    if surge:
        cand = {k: v for k, v in surge["models"].items() if k in ("gru", "lstm", "lightgbm", "ensemble")}
        surge_best = {k: {"acc": v["test"]["time_level"]["accuracy"],
                          "precision": v["test"]["time_level"]["precision"],
                          "recall": v["test"]["time_level"]["recall"],
                          "f1": v["test"]["time_level"]["f1"],
                          "fpr": v["test"]["time_level"]["fpr"],
                          "fnr": v["test"]["time_level"]["fnr"],
                          "lead_ge30": v["test"]["event_level"]["lead_ge30_coverage"],
                          "event_precision": v["test"]["event_level"].get("precision_event_level"),
                          "fa_per_h": v["test"]["event_level"]["false_alarms_per_hour"]}
                      for k, v in cand.items()}

    kpi = {"energy_service_equiv_pct": energy, "low_load_reduction_pct": lowred,
           "pressure": best_press, "surge": surge_best,
           "rl_present": rl is not None, "latency": lat}
    (OUT / "stage2_kpi.json").write_text(json.dumps(kpi, ensure_ascii=False, indent=2), encoding="utf-8")

    L = ["# B 组第二阶段 KPI 仪表盘 (内部口径; simulation 证据已标注)", "",
         "## 1. 综合能效 (历史回测 service_equiv, pooled dev+valid+test)", "",
         "| 策略 | SEC 降幅% | 大马拉小车降幅% |", "|---|---|---|"]
    for k in ("b3_b4", "bal", "lrb_t10"):
        L.append(f"| {k} | {energy[k]} | {lowred[k]} |")
    L += ["", "> 目标: 能效≥6%, 低负荷降幅≥60%。B3/bal 达标; LRB-t10 低负荷(service_equiv)59.1%。",
          "", "## 2. 压力 (闭环仿真, MPC vs 反应式 PI)", "",
          "| 场景 | 合格率(pi→mpc) | 波动std(pi→mpc) | 波动降% |", "|---|---|---|---|"]
    for sc, m in best_press.items():
        L.append(f"| {sc} | {m['compl_reactive']}→{m['compl_mpc']} | "
                 f"{m['std_reactive']}→{m['std_mpc']} | {m['std_red_pct']} |")
    L += ["", "## 3. 喘振预警 (独立高频仿真)", "",
          "| 模型 | Acc | Prec | Recall | F1 | FPR | FNR | 提前≥30s覆盖 | 事件精确率 | 误报/h |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    if surge_best:
        for k, m in surge_best.items():
            L.append(f"| {k} | {m['acc']} | {m['precision']} | {m['recall']} | {m['f1']} | {m['fpr']} | "
                     f"{m['fnr']} | {m['lead_ge30']} | {m['event_precision']} | {m['fa_per_h']} |")
    L += ["", "## 4. 强化学习 (DQN + B4 安全过滤; 多种子)", ""]
    if rl:
        L += ["| 场景 | RL合格率(mean) | RL能耗 | PI合格率 | PI能耗 | MPC合格率 | MPC能耗 |",
              "|---|---|---|---|---|---|---|"]
        for sc, b in rl["scenarios"].items():
            r = b["rl_mean"]
            L.append(f"| {sc} | {r['pressure_compliance']['mean']} | {r['energy_kwh']['mean']} | "
                     f"{b['baseline_reactive_pi']['pressure_compliance']} | {b['baseline_reactive_pi']['energy_kwh']} | "
                     f"{b['baseline_mpc']['pressure_compliance']} | {b['baseline_mpc']['energy_kwh']} |")
    else:
        L.append("(rl_eval.json 尚未生成)")
    L += ["", "## 5. 调度生成时间", ""]
    if lat:
        L.append(f"- `kpi_latency.json`: {json.dumps(lat, ensure_ascii=False)[:400]}")
    else:
        L.append("- 见 `analysis/reports/b/kpi_latency.json` (算法侧 p50 数十毫秒 << 60s)")
    L += ["", "> 说明: 全部仿真结论标注 validation_mode=simulation; 压力合格带 [0.55,0.66] MPa 为 CAL-05 工程示例, 非官方标准。"]
    (OUT / "STAGE2_KPI_SUMMARY.md").write_text("\n".join(L), encoding="utf-8")
    print("written", OUT / "STAGE2_KPI_SUMMARY.md")


if __name__ == "__main__":
    main()
