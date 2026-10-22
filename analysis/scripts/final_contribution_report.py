"""B 组模块贡献报告生成 (调度消融 + 压力 MPC/ML + 喘振 GRU/B4)。

读取: contribution_dispatch.json / contribution_pressure.json /
      stage2/surge/surge_eval.json / stage3/surge/surge_optimized.json
输出: analysis/reports/b/final/CONTRIBUTION_REPORT.md
"""
from __future__ import annotations

import json
from pathlib import Path

FIN = Path("analysis/reports/b/final")
ROOT = Path("analysis/reports/b")


def _load(p):
    p = Path(p)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def main():
    disp = _load(FIN / "contribution_dispatch.json")
    pres = _load(FIN / "contribution_pressure.json")
    s2 = _load(ROOT / "stage2/surge/surge_eval.json")
    s3 = _load(ROOT / "stage3/surge/surge_optimized.json")
    rt = _load(FIN / "backtest_dev.json")

    L = ["# B 组模块贡献与必要性报告", "",
         "> 目的: 判断各新增模块是否真正改善比赛指标、是否值得在最终版本保留。",
         "> 全部对照在同一真实历史口径 (相同步/容量/安全/功率模型/指标定义) 下进行。", ""]

    # ---- 调度消融 ----
    if disp:
        t = disp["table"]; st = disp["stats"]
        L += ["## 1. 调度算法消融 (真实历史 pooled, service 口径)", "",
              "| 策略 | SEC降% | 同服务节电% | 低负荷降% | D3 | 平均偏差pp | Δ供气%vs b1 |",
              "|---|---|---|---|---|---|---|"]
        for s in ("b1_rule", "b3_b4", "bal", "lrb_t10", "b3_lrb", "joint_nompc", "mpc_hist", "joint"):
            m = t[s]
            L.append(f"| {s} | {m['sec_reduction_pct']} | {m['saving_pct']} | {m['low_reduction_pct']} | "
                     f"{m['d3_both']} | {m['gap_pp_mean']} | {m['supply_vs_b1_pct']} |")
        L += ["", "### 配对差异 (bootstrap 95% CI, 对步重采样)", "",
              "| 对比 | SEC 差 (kWh/m³) | 95% CI | P(后者更省) |",
              "|---|---|---|---|"]
        for k, v in st.items():
            if not isinstance(v, dict):
                continue
            L.append(f"| {k} | {v['sec_diff']} | [{v['ci95_low']}, {v['ci95_high']}] | {v['p_gt_0']} |")
        if "joint_vs_b3" in st:
            v = st["joint_vs_b3"]
            sig = "显著优于 0" if v["ci95_low"] > 0 else ("跨 0 (不显著)" if v["ci95_high"] > 0 else "显著劣于 0")
            L += ["", f"> joint vs B3: SEC 差 {v['sec_diff']} kWh/m³, 95%CI [{v['ci95_low']}, {v['ci95_high']}] → **{sig}**。",
                  "> 换算成百分比约 " +
                  (f"{v['sec_diff']/t['b3_b4']['sec_kwh_per_m3']*100:.3f}pp。" if v['sec_diff'] else "n/a")]
        L += ["", "### 分窗口 SEC 改善", "",
              "| 窗口 | b3 | bal | lrb_t10 | b3_lrb | joint_nompc | joint |", "|---|---|---|---|---|---|---|"]
        for w, d in disp["per_window"].items():
            L.append(f"| {w} | {d['b3_b4']['sec_reduction_pct']} | {d['bal']['sec_reduction_pct']} | "
                     f"{d['lrb_t10']['sec_reduction_pct']} | {d['b3_lrb']['sec_reduction_pct']} | "
                     f"{d['joint_nompc']['sec_reduction_pct']} | {d['joint']['sec_reduction_pct']} |")

    # ---- 压力 MPC / ML ----
    if pres:
        L += ["", "## 2. 压力: MPC 与 ML 压力模型增量收益 (真实需求驱动闭环)", "",
              f"- ML 压力模型精度: R²={pres['pressure_model_metrics']['test']['R2']}, MAE={pres['pressure_model_metrics']['test']['MAE']} MPa",
              "", "| 窗口 | 控制器 | 合格率 | 波动std | 需求满足度 | 未满足缺口m³ | 满载步占比 | 耗时ms/步 |",
              "|---|---|---|---|---|---|---|---|"]
        for w, o in pres["windows"].items():
            for k in ("reactive_pi", "mpc_simple", "mpc_ml", "b1", "joint_nompc", "joint"):
                m = o[k]
                L.append(f"| {w} | {k} | {m['compliance']} | {m['std']} | {m['demand_satisfaction']} | "
                         f"{m['unmet_flow_m3']} | {m['cap_step_share']} | {m['decide_ms_per_step']} |")

    # ---- 喘振 ----
    if s2:
        L += ["", "## 3. 喘振: GRU 相对 B4 代理的增量 (独立高频仿真, test 冻结)", "",
              "| 方案 | Acc | Prec | Recall | F1 | 事件召回 | 提前≥30s | 误报次数 | 误报/h |",
              "|---|---|---|---|---|---|---|---|---|"]
        for k in ("b4_ew_rule", "lightgbm", "gru", "lstm", "ensemble", "gru_and_b4"):
            v = s2["models"].get(k)
            if not v:
                continue
            tl, el = v["test"]["time_level"], v["test"]["event_level"]
            L.append(f"| {k} | {tl['accuracy']} | {tl['precision']} | {tl['recall']} | {tl['f1']} | "
                     f"{el['recall_event_level']} | {el['lead_ge30_coverage']} | {el['false_alarms']} | "
                     f"{el['false_alarms_per_hour']} |")
    if s3:
        t = s3["after_stabilized"]["test"]; tl, el = t["time_level"], t["event_level"]
        L += ["", f"> GRU 优化后 (校准+迟滞+冷却): Acc={tl['accuracy']}, F1={tl['f1']}, 事件召回={el['recall_event_level']}, "
              f"提前≥30s={el['lead_ge30_coverage']}, 误报={el['false_alarms']} (parms {s3['after_stabilized']['params']})",
              "> B4 仍作为**独立即时安全阻断**层 (high/unknown → 不执行); GRU 只做预测性提前提示。"]

    # ---- 复杂度 ----
    if rt:
        L += ["", "## 4. 计算复杂度 (真实回测实测, p50 ms/步)", "",
              "| 策略 | p50 | p95 | max |", "|---|---|---|---|"]
        for s, m in rt["runtime_ms"].items():
            L.append(f"| {s} | {m['p50']} | {m['p95']} | {m['max']} |")

    # ---- 结论表 ----
    L += ["", "## 5. 模块贡献结论", "",
          "| 模块 | 提升的指标 | 幅度 | 证据 | 复杂度增量 | 是否保留 |",
          "|---|---|---|---|---|---|",
          "| joint 相对 B3 (能效) | 综合能效 | SEC 仅 +0.024pp, **配对 CI 跨 0 (不显著)** | §1 bootstrap | — | 不能作为 joint 优于 B3 的证据 |",
          "| joint 相对 B3 (加载率) | 加载率 D3 | 0.072→1.000 | §1 | — | **保留** (真正优势) |",
          "| joint 相对 B3 (低负荷) | 大马拉小车 | 46.5%→68.4% (+21.9pp) | §1 | — | **保留** |",
          "| MPC 压力控制 | 未满足缺口/波动 (服务) | 相对反应式: 缺口 −26%~−51%, test 波动 −49% | §2 | 决策 ~10-24ms/步 | **保留** (容量受限/扰动场景) |",
          "| joint 压力项 (horizon+pressure) | 真实 valid/test 合格率 | ~0 (joint_nompc 已 1.0) | §2 | +11ms/步 | 视场景保留 |",
          "| ML 压力模型 | 无 | 0 (mpc 与 mpc_ml 逐指标相同) | §2 | 训练+模型文件 | **不保留** (回退解析模型) |",
          "| GRU 喘振预警 | 提前≥30s 覆盖率 | 0.49 (B4) → 0.91 | §3 | 序列推理 | **保留** (独立预警) |",
          "| B4 安全阻断 | 误操作=0 | — | tests/safety + 回测 | 极低 | **保留** |",
          "| DQN/RL | 无 | 未超越 MPC | 第二阶段 | 高 | **不部署** (研究记录) |",
          ]
    L += ["", "## 6. 推荐最简有效架构", "",
          "- **调度 (能效/低负荷/加载率)**: 多目标单步决策 (energy + 低负荷 + 机组间≤10pp 均衡) = joint_nompc; "
          "SEC 7.83%、低负荷 69.7%、D3 1.0, 复杂度最低。",
          "- **压力控制**: 保留解析压力模型的 MPC (压力项), 用于 step/peak/fault 等扰动场景; 真实平滑工况下增量≈0。",
          "- **ML 压力模型**: 不部署 (零增量)。",
          "- **安全**: B4 独立阻断 (误操作 0)。",
          "- **预警**: GRU 独立模块 (提前≥30s 覆盖 0.91); 明确 rapid 无前兆漏报与误报率。",
          "- **不部署**: DQN/RL。",]
    (FIN / "CONTRIBUTION_REPORT.md").write_text("\n".join(L), encoding="utf-8")
    print("written", FIN / "CONTRIBUTION_REPORT.md")


if __name__ == "__main__":
    main()
