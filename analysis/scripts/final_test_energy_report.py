"""生成 TEST_ENERGY_DIAGNOSIS.md (读取 test_energy_diagnosis.json)。"""
from __future__ import annotations

import json
from pathlib import Path

FIN = Path("analysis/reports/b/final")
S = ("b1_rule", "b3_b4", "bal", "lrb_t10", "joint_nompc", "joint")


def _load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def main():
    d = _load(FIN / "test_energy_diagnosis.json")
    L = ["# test 节能不足 6% 的根因诊断与主调度方案选择", "",
         "> 真实历史 (A 组 SCADA 派生快照 + A6 真实滚动预测), B2 功率模型估计; 统一口径如下。",
         "> pooled 差异核查: 6 策略=10793 步; 纳入 b3_lrb 后=10789 (其 4 步不可行); joint_nompc 不改变样本范围。", ""]

    # 1 六策略对照
    L += ["## 1. 六策略指标对照 (统一 service 口径)", ""]
    for scope in ("dev", "valid", "test", "pooled"):
        b = d["per_window"][scope] if scope != "pooled" else d["pooled"]
        L += [f"### {scope} (n={b['_steps']}, service_eq_volume={b.get('service_eq_volume_m3', '-')} m³)", "",
              "| 策略 | SEC降% | 同服务节电% | 低负荷降% | 偏离度均pp | P95pp | 最大pp | 双机覆盖 | Δ供气% |",
              "|---|---|---|---|---|---|---|---|---|"]
        for s in S:
            m = b[s]; dv = b["_deviation"][s]
            L.append(f"| {s} | {m['sec_reduction_pct']} | {m['energy_saving_equalV_pct']} | {m['low_reduction_pct']} | "
                     f"{dv['mean_pp']} | {dv['p95_pp']} | {dv['max_pp']} | {dv['both_coverage']} | {m['supply_diff_pct']} |")
        L.append("")

    # 2 工况与基线
    L += ["## 2. 三窗口工况分布与 B1 基线效率", "",
          "| 窗口 | 需求均值 | P10 | P50 | P90 | 最大 | 均值/容量 | 低负荷<50% | 中50-90% | 高≥90% | 超容量 | 单机 | 双机 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for w in ("dev", "valid", "test"):
        x = d["workload"][w]
        L.append(f"| {w} | {x['mean']} | {x['p10']} | {x['p50']} | {x['p90']} | {x['max']} | {x['mean_ratio_to_cap']} | "
                 f"{x['bands']['low_<50%cap']} | {x['bands']['mid_50-90%cap']} | {x['bands']['high_>=90%cap']} | "
                 f"{x['bands']['over_cap_>537.8']} | {x['single_share']} | {x['dual_share']} |")
    L += ["", "| 窗口 | B1 SEC | B1 总电耗kWh | B1 总供气m³ | 加载率均值 | 加载率P50 | <0.5 占比 | <0.7 占比 | 低负荷min |",
          "|---|---|---|---|---|---|---|---|---|"]
    for w in ("dev", "valid", "test"):
        x = d["b1_baseline"][w]
        L.append(f"| {w} | {x['b1_sec']} | {x['b1_energy_kwh']} | {x['b1_supply_m3']} | {x['load_ratio_mean']} | "
                 f"{x['load_ratio_p50']} | {x['load_ratio_below_0.5_share']} | {x['load_ratio_below_0.7_share']} | {x['b1_low_min']} |")

    # 3 分负荷带贡献
    L += ["", "## 3. 分负荷带 SEC 改善贡献", ""]
    for w in ("test", "valid", "dev"):
        L += [f"### {w}", "", "| 需求带 | 步数 | obs均值 | B1 SEC | B3 SEC | B3 vs B1% | joint vs B3 能耗% | joint_nompc vs B3 能耗% |",
              "|---|---|---|---|---|---|---|---|"]
        for lb, row in d["band_contribution"][w].items():
            if not row.get("steps"):
                continue
            L.append(f"| {lb} | {row['steps']} | {row['obs_mean']} | {row['b1_rule']['sec']} | {row['b3_b4']['sec']} | "
                     f"{row['b3_vs_b1_sec_pct']} | {row['joint_vs_b3_E_pct']} | {row['nompc_vs_b3_E_pct']} |")
        L.append("")

    # 4 约束代价
    c = d["constraints"]
    L += ["## 4. 约束代价 (基于已有账本)", "",
          f"- 机组间均衡约束 (b3 → b3_lrb, pooled): SEC 代价 **{c['pooled']['balance_constraint_cost_pp']} pp**",
          f"- MPC 压力项 (joint vs joint_nompc): pooled **{c['pooled']['joint_vs_nompc_pp']} pp**; "
          f"dev {c['dev']['joint_vs_nompc_sec_pp']}, valid {c['valid']['joint_vs_nompc_sec_pp']}, test {c['test']['joint_vs_nompc_sec_pp']} pp",
          "- 低负荷惩罚: 换取低负荷降幅 (joint_nompc 69.8% vs joint 68.4% pooled)", ""]

    # 5 功率模型
    L += ["## 5. B2 功率模型分窗口误差与覆盖", "",
          "| 窗口 | n | MAE kW | bias kW | flow均值 | flow P95 | >268.9 外推占比 |", "|---|---|---|---|---|---|---|"]
    for w in ("dev", "valid", "test"):
        x = d["power_model"][w]
        L.append(f"| {w} | {x['n']} | {x['MAE_kw']} | {x['bias_kw']} | {x['flow_mean']} | {x['flow_p95']} | {x['extrap_share_gt268.9']} |")

    # 6 理想下界
    L += ["", "## 6. 模型可行节能潜力 (下界估计, 忽略时序切换/爬坡; 非同服务等体量口径)", "",
          "| 窗口 | 无均衡约束理想节能% | 加 10pp 均衡约束理想节能% | B3 实测% | joint 实测% |",
          "|---|---|---|---|---|"]
    for w in ("dev", "valid", "test"):
        g = d["ideal_lower_bound"][w]
        b3 = d["per_window"][w]["b3_b4"]["sec_reduction_pct"]
        jt = d["per_window"][w]["joint"]["sec_reduction_pct"]
        L.append(f"| {w} | {g['ideal_energy']['energy_saving_vs_b1_equalV_pct']} | "
                 f"{g['ideal_balanced']['energy_saving_vs_b1_equalV_pct']} | {b3} | {jt} |")
    L += ["", "> 口径: 以 service 等体量 V=Σmin(obs,537.8) 计算节电率; 下界为逐步最小估算功率, 忽略切换/最短开停机;",
          "> 属**模型参考下界**, 非现场可达上限。", ""]

    # 7/8 结论
    L += ["## 7. 结论与主调度方案推荐", "",
          "**test 节能低主要因为:**",
          f"1. 工况高负荷: 均值 {d['workload']['test']['mean']} m³/min ({d['workload']['test']['mean_ratio_to_cap']}×容量), "
          f"高负荷(≥90%) 占 {d['workload']['test']['bands']['high_>=90%cap']}, 低负荷仅 {d['workload']['test']['bands']['low_<50%cap']}。",
          f"2. **B1 在 test 已高度高效**: B1 SEC {d['b1_baseline']['test']['b1_sec']} (低于 dev {d['b1_baseline']['dev']['b1_sec']}), "
          f"加载率均值 {d['b1_baseline']['test']['load_ratio_mean']}, <0.5 占比仅 {d['b1_baseline']['test']['load_ratio_below_0.5_share']}。",
          "3. 主体负荷带 (400–537.8, 占 test 97%) 中 B1→B3 仅 2.8–4.8% (见 §3)。",
          f"4. **模型下界**: test 无约束理想 {d['ideal_lower_bound']['test']['ideal_energy']['energy_saving_vs_b1_equalV_pct']}%、"
          f"加均衡约束 {d['ideal_lower_bound']['test']['ideal_balanced']['energy_saving_vs_b1_equalV_pct']}% → **6% 不可达**。",
          "",
          "**joint 在 test 不如 B3 的原因:** 10pp 机组间均衡约束在双机高负荷段强制等分, 400–480 带多用 "
          f"{abs(d['band_contribution']['test']['400-480']['joint_vs_b3_E_pct'])}% 能量、480–537.8 带多用 "
          f"{abs(d['band_contribution']['test']['480-537.8']['joint_vs_b3_E_pct'])}%; 均衡约束 pooled 代价 "
          f"{c['pooled']['balance_constraint_cost_pp']} pp。",
          "",
          "**推荐主调度方案: `joint_nompc`** (理由见下表)。", "",
          "| 方案 | pooled SEC降% | valid SEC降% | test SEC降% | 低负荷降% | 偏离度最大pp | 说明 |",
          "|---|---|---|---|---|---|---|"]
    for s in ("b3_b4", "lrb_t10", "joint_nompc", "joint"):
        b = d["pooled"]; m = b[s]; dv = b["_deviation"][s]
        L.append(f"| {s} | {m['sec_reduction_pct']} | {d['per_window']['valid'][s]['sec_reduction_pct']} | "
                 f"{d['per_window']['test'][s]['sec_reduction_pct']} | {m['low_reduction_pct']} | {dv['max_pp']} | "
                 f"{'同时满足能效/低负荷/加载率' if s == 'joint_nompc' else ''} |")
    L += ["", "推荐理由: joint_nompc 在 pooled 能效 (**7.84%**, 高于 B3 7.71%)、低负荷 (69.8%, ≥60%)、"
          "加载率偏离度 (最大 9.999pp ≤10pp) 三项同时达标, 且复杂度最低; test 单窗口 2.98% 系工况与"
          "均衡约束的结构性限制, 非算法缺陷。", "",
          "**是否需要再修改算法**: 诊断为结构性 (test 高负荷 + B1 已高效 + 均衡约束), 不存在明显多余惩罚项;",
          "建议**主调度=joint_nompc**, 由解析 MPC 独立承担压力控制 (真实平滑窗口 MPC 增量≈0, 仅扰动/容量场景需要);",
          "不建议为抬升 test 数字而放松均衡约束或供气约束; 若未来调参, 应使用开发/验证窗口并另设未见时间段复评。", ""]
    (FIN / "TEST_ENERGY_DIAGNOSIS.md").write_text("\n".join(L), encoding="utf-8")
    print("written", FIN / "TEST_ENERGY_DIAGNOSIS.md")


if __name__ == "__main__":
    main()
