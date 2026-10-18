"""RUL 模拟退化数据生成器。

设计:
  - 4#/5# 每台生成多个退化 episode; 初始 RUL=2000h, 退化到 maintenance_reset_rul 后
    判定为保养, RUL 复位为 2000 并开启**新 episode_id**;
  - 振动/温度随退化指数 d∈[0,1] **指数级上升**;
  - 场景: normal / slow / fast / noisy;
  - 固定随机种子, 输出一致; 所有记录 origin=simulated;
  - 特征列命名与真实 health_features.parquet 对齐, 便于模型直接复用。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SENSOR_COLS = ["vib1_current", "vib2_current", "vib3_current", "discharge_temp_c",
               "oil_tank_c", "current_proxy_a", "unit_flow_power"]


def load_config(path: str = "configs/rul.yaml") -> dict:
    import yaml
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _choose_scenarios(cfg: dict, rng: np.random.RandomState, n: int) -> list[str]:
    names = list(cfg["scenarios"].keys())
    weights = np.array([cfg["scenarios"][k]["weight"] for k in names], dtype=float)
    weights = weights / weights.sum()
    return list(rng.choice(names, size=n, p=weights))


def generate(cfg: dict | None = None) -> pd.DataFrame:
    cfg = cfg or load_config()
    rng = np.random.RandomState(int(cfg["seed"]))
    devices = cfg["devices"]
    init_rul = float(cfg["initial_rul_hours"])
    reset_rul = float(cfg["maintenance_reset_rul_hours"])
    step = float(cfg["step_hours"])
    n_ep = int(cfg["episodes_per_device"])
    sig = cfg["signals"]

    frames = []
    for dev in devices:
        base = cfg["device_baseline"][dev]
        scenarios = _choose_scenarios(cfg, rng, n_ep)
        t_cursor = pd.Timestamp("2024-01-01 00:00:00")
        for k in range(n_ep):
            sc = scenarios[k]
            scfg = cfg["scenarios"][sc]
            duration = float(scfg["duration_hours"])
            k_vib = float(scfg["k_vib"]); k_temp = float(scfg["k_temp"]); noise = float(scfg["noise"])
            n = int(duration / step) + 1
            tt = np.arange(n) * step
            progress = tt / duration
            rul = init_rul - (init_rul - reset_rul) * progress        # 2000 -> 300
            d = (init_rul - rul) / (init_rul - reset_rul)             # 0 -> 1 退化指数

            def jitter(x, rel):
                return x * (1.0 + rng.normal(0.0, rel, size=len(x)))

            vib1 = jitter(base["vib1"] * np.exp(k_vib * d), noise)
            vib2 = jitter(base["vib2"] * np.exp(k_vib * d), noise)
            vib3 = jitter(base["vib3"] * np.exp(k_vib * d), noise)
            discharge = jitter(base["discharge_temp"] + sig["temp_grow_c"] * (np.exp(k_temp * d) - 1.0), noise * 0.5)
            oil = jitter(base["oil_tank"] + sig["oil_grow_c"] * d, noise * 0.5)
            cur = jitter(base["current_proxy"] + sig["current_grow_a"] * d, noise * 0.4)
            ufp = jitter(base["unit_flow_power"] + sig["ufp_grow"] * d, noise * 0.4)

            times = t_cursor + pd.to_timedelta(tt, unit="h")
            t_cursor = times[-1] + pd.Timedelta(hours=step)   # 下一个 episode 在复位之后
            reset_flag = np.zeros(n, dtype=bool)
            if k < n_ep - 1:
                reset_flag[-1] = True                          # episode 末尾 = 保养复位点

            frames.append(pd.DataFrame({
                "time": times,
                "device_id": dev,
                "episode_id": f"{dev}-{k:03d}",
                "scenario": sc,
                "rul_hours": np.clip(rul, 0.0, None).astype("float32"),
                "vib1_current": vib1.astype("float32"),
                "vib2_current": vib2.astype("float32"),
                "vib3_current": vib3.astype("float32"),
                "discharge_temp_c": discharge.astype("float32"),
                "oil_tank_c": oil.astype("float32"),
                "current_proxy_a": cur.astype("float32"),
                "unit_flow_power": ufp.astype("float32"),
                "maintenance_reset": reset_flag,
                "origin": "simulated",
            }))
    df = pd.concat(frames, ignore_index=True)
    return df.sort_values(["device_id", "time"]).reset_index(drop=True)


def generate_and_save(cfg: dict | None = None,
                      out_path: str = "data/simulated/rul_sequences.parquet") -> pd.DataFrame:
    df = generate(cfg)
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)
    return df


if __name__ == "__main__":
    d = generate_and_save()
    print(f"[OK] 模拟 RUL 序列 {d.shape}; episodes={d['episode_id'].nunique()}; "
          f"devices={sorted(d['device_id'].unique())}")
