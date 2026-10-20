"""第二阶段: 安全约束强化学习评估运行器。

训练: dev 场景混合 (normal/low/step/peak), DQN, 多随机种子 (3)。
评估: 冻结策略在全部 5 场景独立评估; 与 reactive_pi / mpc 对照。
报告: 压力合格率/波动/能耗/SEC/需求满足度/切换/安全阻断; 多种子均值与极差。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from air_station.simulation import closed_loop as CL
from air_station.stage2 import pressure_sim as PS
from air_station.stage2 import rl_agent as RA
from air_station.stage2 import rl_env as RE

TRAIN_SCEN = ("normal", "low", "step", "peak")


def collect_expert(env, n_episodes, horizon=4):
    """用 MPC 专家在环境中收集 (状态, 动作) 用于行为克隆 (RL+MPC 混合)。"""
    ccfg = PS.CtrlParams(horizon=horizon)
    X, A = [], []
    for _ in range(n_episodes):
        obs = env.reset()
        while True:
            k = env.k
            fut = [float(env.dem[min(env.n - 1, k + j)]) for j in range(horizon)]
            S, _ = PS.mpc_decide(env.p, fut, ccfg, env.sim, env.model_path, env.cache,
                                 None, horizon, env._avail(), env.prev_S)
            a = int(np.argmin(np.abs(RE.SUPPLY_GRID - S)))
            X.append(obs); A.append(a)
            obs, r, done, _ = env.step(a)
            if done:
                break
    return np.array(X), np.array(A)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", type=int, default=250)
    ap.add_argument("--expert-episodes", type=int, default=12)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--out", default="analysis/reports/b/stage2/rl")
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    def env_factory(seed):
        return RE.AirStationEnv([CL.SCENARIOS[s] for s in TRAIN_SCEN],
                                rl=RE.RLParams(seed=seed))

    agents = {}
    for sd in args.seeds:
        env = env_factory(sd)
        ag = RA.DQNAgent(env.state_dim, RE.N_ACTIONS, seed=sd)
        Xs, As = collect_expert(env_factory(sd), args.expert_episodes)
        ws = ag.warm_start(Xs, As, epochs=40)
        info = ag.train(env, episodes=args.episodes, log_every=0)
        info["warm_start"] = ws
        agents[sd] = (ag, info)
    report = {"meta": {"validation_mode": "simulation", "episodes": args.episodes,
                       "expert_episodes": args.expert_episodes,
                       "seeds": list(args.seeds), "train_scenarios": list(TRAIN_SCEN)},
              "scenarios": {}}

    for name, sc in CL.SCENARIOS.items():
        # 基线
        base_pi = PS.simulate("reactive_pi", sc)["metrics"]
        base_mpc = PS.simulate("mpc", sc)["metrics"]
        per_seed = []
        for sd, (ag, info) in agents.items():
            env = RE.AirStationEnv([sc], rl=RE.RLParams(seed=sd))
            recs = RE.rollout(env, ag.greedy(), seed=sd)
            per_seed.append(RE.summarize(recs, env.sim))
        def agg(key):
            vals = [m[key] for m in per_seed if m[key] is not None]
            return {"mean": round(float(np.mean(vals)), 4), "min": round(float(np.min(vals)), 4),
                    "max": round(float(np.max(vals)), 4)}
        block = {"baseline_reactive_pi": base_pi, "baseline_mpc": base_mpc,
                 "per_seed": per_seed,
                 "rl_mean": {k: agg(k) for k in
                             ("pressure_compliance", "pressure_std", "energy_kwh",
                              "sec_kwh_per_m3", "demand_satisfaction", "switch_events",
                              "safety_blocks", "unserved_deficit_m3")}}
        report["scenarios"][name] = block

    (out / "rl_eval.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_md(report, out / "RL_EVAL.md")
    print("[stage2-rl] done")
    for name, b in report["scenarios"].items():
        r = b["rl_mean"]
        print(f"  {name:7s} RL(mean over seeds) comp={r['pressure_compliance']['mean']} "
              f"std={r['pressure_std']['mean']} E={r['energy_kwh']['mean']} "
              f"sat={r['demand_satisfaction']['mean']} blocks={r['safety_blocks']['mean']} | "
              f"PI comp={b['baseline_reactive_pi']['pressure_compliance']} "
              f"E={b['baseline_reactive_pi']['energy_kwh']} | "
              f"MPC comp={b['baseline_mpc']['pressure_compliance']} E={b['baseline_mpc']['energy_kwh']}")


def _write_md(report, path):
    L = ["# 安全约束强化学习评估 (第二阶段; validation_mode=simulation)", "",
         f"- 训练场景: {report['meta']['train_scenarios']}, episodes={report['meta']['episodes']}, "
         f"seeds={report['meta']['seeds']}", "",
         "| 场景 | 策略 | 合格率 | 波动std | 能耗kWh | SEC | 需求满足度 | 切换 | 安全阻断 |",
         "|---|---|---|---|---|---|---|---|---|"]
    for name, b in report["scenarios"].items():
        for tag, m in (("reactive_pi", b["baseline_reactive_pi"]), ("mpc", b["baseline_mpc"])):
            L.append(f"| {name} | {tag} | {m['pressure_compliance']} | {m['pressure_std']} | "
                     f"{m['energy_kwh']} | {m.get('sec_kwh_per_m3')} | {m['demand_satisfaction']} | - | - |")
        r = b["rl_mean"]
        L.append(f"| {name} | RL(mean) | {r['pressure_compliance']['mean']} | {r['pressure_std']['mean']} | "
                 f"{r['energy_kwh']['mean']} | {r['sec_kwh_per_m3']['mean']} | {r['demand_satisfaction']['mean']} | "
                 f"{r['switch_events']['mean']} | {r['safety_blocks']['mean']} |")
        L.append(f"| {name} | RL(min–max) | {r['pressure_compliance']['min']}–{r['pressure_compliance']['max']} | "
                 f"{r['pressure_std']['min']}–{r['pressure_std']['max']} | — | — | "
                 f"{r['demand_satisfaction']['min']}–{r['demand_satisfaction']['max']} | — | — |")
    path.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
