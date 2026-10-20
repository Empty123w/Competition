"""安全约束强化学习环境 (第二阶段)。

双机空压站闭环决策: 每步选择**总供气水平** (离散动作), 由 allocate 映射到 4#/5#,
经压力物理推进与 B4 安全过滤。

状态: 压力及其偏差、需求与趋势、上次供气、机组运行标志、距上次切换时间。
奖励: -(能耗 + 压力越界罚 + 缺供罚 + 切换罚), 保证**不牺牲供气/压力博取虚假节能**。
安全过滤: 最短开停机锁定 + B4 代理喘振边界 (低于边界即禁止该动作, 退化为安全动作)。

validation_mode=simulation。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from air_station.simulation import closed_loop as CL
from air_station.stage2 import pressure_sim as PS

RATED = 268.9
DEVICES = ("4#", "5#")
SUPPLY_GRID = np.arange(0.0, 2 * RATED + 15.0, 15.0)   # 0..540 step 15
N_ACTIONS = int(len(SUPPLY_GRID))

# B4 代理边界 (线性): q_b4(p)=90+350*(p-0.66)
Q_REF, P_REF, SLOPE = 90.0, 0.66, 350.0


def q_b4(p):
    return Q_REF + SLOPE * (p - P_REF)


@dataclass
class RLParams:
    dt_min: float = 5.0
    w_energy: float = 0.01          # 每 kWh (缩放后)
    w_press: float = 20.0           # 压力偏离设定点 (归一化) 权重
    band_half: float = 0.05         # 归一化尺度
    w_switch: float = 0.5
    w_safety: float = 2.0
    min_run_min: float = 15.0
    min_stop_min: float = 15.0
    max_switch_per_hour: int = 2
    seed: int = 0


class AirStationEnv:
    def __init__(self, scenarios, sim: CL.SimParams = None, rl: RLParams = None,
                 model_path: str = "artifacts/models/power_model.json"):
        self.scenarios = list(scenarios)
        self.sim = sim or CL.SimParams()
        self.rl = rl or RLParams()
        self.model_path = model_path
        self.cache = {}
        self.rng = np.random.default_rng(self.rl.seed)
        self.state_dim = 11

    # ---------------- 内部 ----------------
    def _cap(self, device_flag_avail):
        return device_flag_avail

    def reset(self):
        sc = self.scenarios[self.rng.integers(len(self.scenarios))]
        self.sc = sc
        self.dem = CL.demand_series(sc)
        self.n = sc.n_steps
        self.k = 0
        self.p = self.sim.p_init_mpa
        self.flows = {"4#": 0.0, "5#": 0.0}
        self.running = {d: False for d in DEVICES}
        self.last_switch = {d: -10**9 for d in DEVICES}
        self.prev_S = 0.0
        self.switch_times = []
        return self._obs()

    def _avail(self):
        avail = set(DEVICES)
        if self.sc.fault and self.k >= self.sc.fault[0]:
            avail.discard(self.sc.fault[1])
        return avail

    def _obs(self):
        d = float(self.dem[self.k])
        dprev = float(self.dem[max(0, self.k - 1)])
        dnext = float(self.dem[min(self.n - 1, self.k + 1)])
        t_since = min((self.k * self.rl.dt_min - max(self.last_switch.values())) / 60.0, 2.0)
        return np.array([
            (self.p - self.sim.p_set_mpa) / 0.05,
            (self.p - self.sim.band_lo_mpa) / (self.sim.band_hi_mpa - self.sim.band_lo_mpa),
            d / (2 * RATED),
            (d - dprev) / RATED,
            self.prev_S / (2 * RATED),
            1.0 if self.running["4#"] else 0.0,
            1.0 if self.running["5#"] else 0.0,
            self.flows["4#"] / RATED,
            self.flows["5#"] / RATED,
            t_since,
            (dnext - d) / RATED,
        ], dtype=np.float32)

    def _n_switches(self, new_flows):
        return sum(1 for d in DEVICES if (new_flows[d] > 0) != self.running[d])

    def _switch_allowed(self, new_flows):
        for d in DEVICES:
            will = new_flows[d] > 0
            if will == self.running[d]:
                continue
            delta = (self.k * self.rl.dt_min - self.last_switch[d])
            if will and delta < self.rl.min_stop_min:
                return False
            if (not will) and delta < self.rl.min_run_min:
                return False
        recent = [t for t in self.switch_times if (self.k * self.rl.dt_min - t) <= 60.0]
        if len(recent) < self.rl.max_switch_per_hour:
            return True
        return self._n_switches(new_flows) == 0

    def _surge_blocked(self, flows):
        for d, f in flows.items():
            if f > 0 and f < q_b4(self.p):
                return True
        return False

    def step(self, a_idx):
        S = float(SUPPLY_GRID[a_idx])
        avail = self._avail()
        flows = PS.allocate(S, self.model_path, self.cache, avail)
        safety_blocked = False
        # 安全过滤: 切换锁定 + B4 喘振边界
        if not self._switch_allowed(flows):
            flows = dict(self.flows); safety_blocked = True
        elif self._surge_blocked({d: flows[d] for d in DEVICES if flows[d] > 0}):
            # 提升到代理边界以上的最小安全供气
            safe_S = min(2 * RATED, max(S, sum(max(0.0, q_b4(self.p)) for d in DEVICES)))
            flows = PS.allocate(safe_S, self.model_path, self.cache, avail); safety_blocked = True

        sw = self._n_switches(flows)
        for d in DEVICES:
            if (flows[d] > 0) != self.running[d]:
                self.last_switch[d] = self.k * self.rl.dt_min
        if sw:
            self.switch_times.append(self.k * self.rl.dt_min)
        self.running = {d: flows[d] > 0 for d in DEVICES}
        self.flows = flows

        q_sup = sum(flows[d] for d in DEVICES)
        q_dem = float(self.dem[self.k])
        q_blow = CL.blowoff_flow(self.p, q_sup, self.sim)
        p_new, vent, unmet, flag = CL.pressure_step_accounted(self.p, q_sup, q_dem, q_blow, self.sim)
        power = PS._power({d: flows[d] for d in DEVICES}, self.model_path, self.cache)
        power = 0.0 if (power is None or np.isnan(power)) else power

        viol = max(0.0, self.sim.band_lo_mpa - p_new) + max(0.0, p_new - self.sim.band_hi_mpa)
        short = max(0.0, q_dem - q_sup)
        dev = (p_new - self.sim.p_set_mpa) / self.rl.band_half
        r = -(self.rl.w_energy * power * (self.rl.dt_min / 60.0)
              + self.rl.w_press * dev * dev
              + self.rl.w_switch * sw
              + self.rl.w_safety * (1.0 if safety_blocked else 0.0))

        self.p = p_new
        self.prev_S = q_sup
        self.k += 1
        done = self.k >= self.n
        info = {"power_kw": power, "supply": q_sup, "demand": q_dem,
                "pressure": p_new, "viol": viol, "short": short, "switch": sw,
                "safety_blocked": safety_blocked, "unmet_m3": unmet,
                "clamp": flag, "blow": q_blow}
        return (self._obs() if not done else np.zeros(self.state_dim, np.float32)), r, done, info


def rollout(env, policy_fn, seed=0):
    """执行一条完整轨迹, 返回逐步记录与汇总指标。"""
    import numpy as np
    obs = env.reset()
    recs = []
    while True:
        a = policy_fn(obs, env)
        obs, r, done, info = env.step(a)
        recs.append(info)
        if done:
            break
    return recs


def summarize(recs, sim: CL.SimParams) -> dict:
    df = pd.DataFrame(recs)
    dt_h = sim.dt_min / 60.0
    E = float(df.power_kw.sum()) * dt_h
    V = float(df.supply.sum()) * sim.dt_min
    comp = float(((df.pressure >= sim.band_lo_mpa) & (df.pressure <= sim.band_hi_mpa)).mean())
    return {"pressure_compliance": round(comp, 4),
            "pressure_std": round(float(df.pressure.std()), 5),
            "energy_kwh": round(E, 1),
            "sec_kwh_per_m3": round(E / V, 6) if V else None,
            "demand_satisfaction": round(1 - float((df.demand - df.supply).clip(lower=0).sum())
                                          / float(df.demand.sum()), 4),
            "unserved_deficit_m3": round(float(df.unmet_m3.sum()), 1),
            "switch_events": int(df.switch.sum()),
            "safety_blocks": int(df.safety_blocked.sum()),
            "unmet_flow_m3": round(float((df.demand - df.supply).clip(lower=0).sum()) * sim.dt_min, 1)}
