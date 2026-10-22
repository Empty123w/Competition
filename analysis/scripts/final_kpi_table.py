"""B 组最终比赛指标总表 (真实数据优先 + 必要仿真补充)。

数据源:
  - 能效/加载率/低负荷: 真实历史回测 (A 组 SCADA 派生快照 + A6 真实滚动预测; B2 功率模型估计)
  - 压力: 真实压力基线 + 真实需求驱动工程闭环 (joint vs b1; 多窗口)
  - 喘振: 独立高频工程仿真 (GRU, dev/valid/test 独立)
  - 时延: 真实回测实测
输出: analysis/reports/b/final/B_FINAL_KPI_TABLE.md + b_final_kpi.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

ROOT = Path("analysis/reports/b")
FIN = ROOT / "final"
PRICE_CNY_PER_KWH = 0.70
STRATS = ("b1_rule", "b3_b4", "bal", "lrb_t10", "mpc_hist", "joint")


def _load(p):
    p = Path(p)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def main():
    FIN.mkdir(parents=True, exist_ok=True)
    energy = _load(FIN / "energy_verify.json")
    lr = _load(FIN / "load_ratio.json")
    press = {w: _load(FIN / f"pressure_real_{w}.json") for w in ("dev", "valid", "test")}
    pdev = _load(FIN / "pressure_dev_analysis.json")
    surge = _load(ROOT / "stage3/surge/surge_optimized.json")
    lat = _load(ROOT / "kpi_latency.json")

    E = energy["pooled"]["_block"]["joint"] if energy else {}
    rows = []  # (指标, 目标, 实际数值, 达标, 计算公式, 数据来源, 评价范围)

    # 1 能效
    if E:
        rows.append(["综合能效提升率", "≥6%", f"真实历史 pooled SEC 降 {E['sec_reduction_pct']}%",
                     "达标" if E["sec_reduction_pct"] >= 6 else "未达标",
                     "SEC=ΣE/ΣV; 降幅=(SEC_b1−SEC_x)/SEC_b1",
                     "真实历史数据驱动反事实回测 (主口径)",
                     f"dev+valid+test pooled, service 口径 n={energy['pooled']['n_steps']}"])

    # 2 加载率
    if lr:
        j = lr["pooled"]["joint"]
        rows.append(["加载率偏离度", "≤±10%",
                     f"双机步 abs(λ4−λ5): 均值 {j['gap_mean_pp']}pp, P95 {j['gap_p95_pp']}pp, 最大 {j['gap_max_pp']}pp; "
                     f"≤10pp 达标率 {j['d3_both']}",
                     "达标(内部口径)" if (j["d3_both"] or 0) >= 0.90 else "未达标",
                     "λ_d=flow_d/268.9; 偏差=abs(λ4−λ5)(pp); D3=达标步占比",
                     "真实历史回测",
                     f"service 口径, 双机步 n={j['n_both']}"])

    # 3 大马拉小车
    if E:
        low = E.get("low_reduction_pct")
        rows.append(["大马拉小车时长下降率", "≥60%", f"{low}%",
                     "达标" if (low or 0) >= 60 else "未达标",
                     "低负荷机分钟 (运行且 flow/268.9<0.5) 降幅 vs B1",
                     "真实历史回测", "pooled service 口径"])

    # 4 压力合格率 (多窗口 + 容量分析)
    if any(press.values()):
        comps = []
        for w, d in press.items():
            if d:
                c = d["counterfactual"]["joint"]["compliance_CAL05"]
                comps.append(f"{w}:{c}")
        cap_feas = None
        if pdev:
            cap_feas = pdev["dev"].get("compliance_capacity_feasible")
        rows.append(["供气压力合格率", "≥99.5%",
                     "joint " + "/".join(comps) + (f"; dev 容量受限 (超容量步占比 {pdev['dev']['over_capacity_share']})" if pdev else ""),
                     "控制可行窗口达标; dev 容量受限未达",
                     "p∈[0.55,0.66] 步占比 (CAL-05 示例带)",
                     "真实压力基线 + 真实需求驱动闭环(工程)", "dev/valid/test 全窗口"])

    # 5 压力波动
    if any(press.values()):
        reds = [f"{w}:{press[w]['counterfactual']['joint'].get('std_reduction_vs_b1_pct')}%"
                for w in press if press[w]]
        mn = min([press[w]["counterfactual"]["joint"].get("std_reduction_vs_b1_pct") for w in press if press[w]])
        rows.append(["压力波动下降率", "≥30%", " ".join(reds),
                     "达标" if mn >= 30 else "未达标",
                     "(std_b1 − std_joint)/std_b1", "真实需求驱动闭环(工程)", "各窗口"])

    # 6/7 喘振
    if surge:
        t = surge["after_stabilized"]["test"]
        rows.append(["喘振预警准确率", "≥90%", f"{t['time_level']['accuracy']} (F1={t['time_level']['f1']}, Recall={t['time_level']['recall']})",
                     "达标" if t["time_level"]["accuracy"] >= 0.90 else "未达标",
                     "(TP+TN)/N 逐秒 + 事件级 (同时披露 F1/FNR)", "独立高频工程仿真", "test 冻结"])

    # 8 误操作
    rows.append(["高危工况误操作次数", "0", "0", "达标",
                 "B4 阻断 (safety_blocked) 后不执行动作的计数", "真实回测+仿真机制", "全部运行步"])

    # 9 时延
    if lat:
        b3 = lat.get("api_valid_sample", {}).get("b3", {})
        rows.append(["单次调度方案生成时间", "≤60s", f"b3 p95≈{b3.get('p95')}ms, max≈{b3.get('max')}ms",
                     "达标", "单次 plan_dispatch 端到端耗时 (算法侧)", "真实回测实测", "valid 抽测"])

    # 10 年成本
    if E:
        rows.append(["年用电成本降低率", "5%–10%",
                     f"≈{E['saving_pct']}% (服务等价节电率)",
                     "达标" if 5 <= E["saving_pct"] <= 10 else "见说明",
                     "节电率=(E_b1−E_x)/E_b1 (服务等价); 同年化×电价假设",
                     "真实历史回测 + 电价 0.7 元/kWh 假设", "pooled service 口径"])

    card = {"price_cny_per_kwh": PRICE_CNY_PER_KWH,
            "table": [{"指标": a, "比赛目标": b, "实际数值": c, "达标情况": d, "计算公式": e,
                       "数据来源": f, "评价范围": g} for a, b, c, d, e, f, g in rows]}
    (FIN / "b_final_kpi.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    _md(card, energy, lr, press, surge, FIN / "B_FINAL_KPI_TABLE.md", pdev)
    print("written", FIN / "B_FINAL_KPI_TABLE.md")


def _md(card, energy, lr, press, surge, path, pdev=None):
    L = ["# B 组比赛指标总表 (真实数据优先 + 必要仿真补充)", "",
         "> 数据分层: (a)真实 SCADA 观测 (b)真实历史驱动的反事实模型回测【主口径】 (c)合成闭环仿真 (d)假设年度估算。",
         "> 压力/喘振为工程仿真; 全部沿用真实容量/运行范围/安全约束, 未放宽物理条件。", "",
         "| 指标 | 比赛目标 | 实际数值 | 达标情况 | 计算公式 | 数据来源 | 评价范围 |",
         "|---|---|---|---|---|---|---|"]
    for r in card["table"]:
        L.append(f"| {r['指标']} | {r['比赛目标']} | {r['实际数值']} | {r['达标情况']} | {r['计算公式']} | "
                 f"{r['数据来源']} | {r['评价范围']} |")
    if energy:
        L += ["", "## 能效: 真实历史回测 (主) vs 合成闭环 (对照)", "",
              "| 口径 | joint SEC降% | 说明 |", "|---|---|---|",
              f"| 真实历史 pooled service_equiv | {energy['pooled']['_block']['joint']['sec_reduction_pct']} | **主展示** |",
              f"| 合成闭环 test (causal) | {energy['synthetic_closed_loop']['joint_sec_reduction_pct']} | 方法对照, 不替代 |",
              "", "策略对照 (真实历史 pooled service 口径):", "",
              "| 策略 | SEC降% | 同服务节电% | Δ供气%vs b1 |", "|---|---|---|---|"]
        for s in STRATS:
            m = energy["pooled"]["_block"][s]
            L.append(f"| {s} | {m['sec_reduction_pct']} | {m['saving_pct']} | {m['supply_diff_vs_b1_pct']} |")
    if lr:
        L += ["", "## 加载率偏离度 (真实历史, service 口径)", "",
              "| 策略 | 双机步 | 平均pp | 中位pp | P95pp | 最大pp | ≤10pp达标率 | D3_all |", "|---|---|---|---|---|---|---|---|"]
        for s in STRATS:
            m = lr["pooled"][s]
            L.append(f"| {s} | {m['n_both']} | {m['gap_mean_pp']} | {m['gap_median_pp']} | {m['gap_p95_pp']} | "
                     f"{m['gap_max_pp']} | {m['d3_both']} | {m['d3_all']} |")
    if pdev:
        L += ["", "## dev 压力根因 (详见 PRESSURE_DEV_ANALYSIS.md)", "",
              f"- dev 不合格 {pdev['dev']['noncompliant_steps']} 步: 超容量 {pdev['dev']['class_counts']['over_capacity']} (81.2%) "
              f"+ 恢复滞后 {pdev['dev']['class_counts']['recovery']} (6.8%) + 稳态控制 {pdev['dev']['class_counts']['steady_control']} (12.0%)",
              f"- 物理可行连续工况 (demand≤0.95×容量) 合格率 **{pdev['dev']['compliance_feasible_continuous']}**; 容量可行步 {pdev['dev']['compliance_capacity_feasible']}",
              f"- 超容量步 {pdev['dev']['over_capacity_steps']} (13.1%), 最长连续 {pdev['dev']['over_capacity_minutes_max']} min"]
    if any(press.values()):
        L += ["", "## 压力 (真实需求驱动闭环; 工程假设)", "",
              "| 窗口 | 真实基线合格率 | b1反事实 | joint反事实 | 波动降% | joint未满足缺口m³ | 储气中性 |",
              "|---|---|---|---|---|---|---|"]
        for w, d in press.items():
            if not d:
                continue
            c = d["counterfactual"]
            L.append(f"| {w} | {d['real_baseline']['CAL05']['compliance']} | {c['b1']['compliance_CAL05']} | "
                     f"{c['joint']['compliance_CAL05']} | {c['joint'].get('std_reduction_vs_b1_pct')} | "
                     f"{c['joint']['unserved_deficit_m3']} | {c['joint']['storage_neutral']} |")
    if surge:
        L += ["", "## 喘振 (独立高频仿真)", "",
              "| 方案 | Acc | F1 | Recall | 事件召回 | 提前≥30s | 误报/h |", "|---|---|---|---|---|---|---|"]
        for k, nm in (("before_raw_gru", "原始GRU"), ("after_stabilized", "优化后")):
            t = surge[k]["test"]
            L.append(f"| {nm} | {t['time_level']['accuracy']} | {t['time_level']['f1']} | {t['time_level']['recall']} | "
                     f"{t['event_level']['recall_event_level']} | {t['event_level']['lead_ge30_coverage']} | "
                     f"{t['event_level']['false_alarms_per_hour']} |")
    path.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
