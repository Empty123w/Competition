"""B5 关键逐步轨迹: 抽取过渡缺口步、B4 unknown 步、连续不可行步邻域。

输出: analysis/reports/b/b5_state_trace.json
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

LED = Path("analysis/reports/b/b5_ledger.csv")
OUT = Path("analysis/reports/b/b5_state_trace.json")
COLS = ["t", "required", "b1_status", "b1_reason", "b1_f4", "b1_f5",
        "b3_status", "b3_reason", "b3_n", "b3_f4", "b3_f5", "b3_surge", "b3_blocked", "b3_exec"]


def nbhd(df, idx, before=2, after=2):
    lo, hi = max(0, idx - before), min(len(df), idx + after + 1)
    return df.iloc[lo:hi][COLS].to_dict("records")


def main():
    df = pd.read_csv(LED)
    rep = {}

    # 过渡容量缺口步
    gi = df.index[df.b3_reason.fillna("").str.contains("TRANSITION_CAPACITY_GAP")]
    if len(gi):
        rep["transition_gap"] = nbhd(df, int(gi[0]))
        rep["transition_gap_note"] = "success 但过渡存在容量缺口 -> 不可执行; 状态不推进"

    # B4 unknown 阻断步
    ui = df.index[df.b3_surge.eq("unknown") & df.b3_blocked]
    if len(ui):
        rep["b4_unknown_blocked"] = nbhd(df, int(ui[0]))
        rep["b4_unknown_note"] = "机组排气压力缺失 -> 风险 unknown -> 阻断; 不推进状态"

    # 连续不可行 (最长连续段)
    ins = (df.b1_status != "success").astype(int)
    best_len, best_start, cur = 0, None, 0
    for i, v in enumerate(ins):
        if v:
            cur = cur + 1 if cur else 1
            if cur > best_len:
                best_len, best_start = cur, i - cur + 1
        else:
            cur = 0
    if best_start is not None:
        rep["longest_infeasible_run"] = nbhd(df, int(best_start), before=1, after=best_len)
        rep["longest_infeasible_run_note"] = ("连续容量不可行; 期间模拟开停/流量冻结, "
                                              "但时间前进, 锁定时钟按真实时间推进 (见 tests/b5)")

    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2)[:3000])


if __name__ == "__main__":
    main()
