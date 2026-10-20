"""深度 Q 网络 (DQN) 安全策略 (第二阶段; torch)。

离散动作 = 总供气水平; 状态见 rl_env。训练于 dev 场景, 冻结后于全部场景独立评估,
多随机种子 (>=3) 报告均值/极差。安全过滤在环境侧强制执行 (最短开停机 + B4 代理边界)。
validation_mode=simulation; 仅为内部候选策略, 不接入真实设备。
"""
from __future__ import annotations

import numpy as np

from air_station.stage2 import rl_env as E


def _net(state_dim, n_actions, hidden=128):
    import torch.nn as nn
    return nn.Sequential(nn.Linear(state_dim, hidden), nn.ReLU(),
                         nn.Linear(hidden, hidden), nn.ReLU(),
                         nn.Linear(hidden, n_actions))


class DQNAgent:
    def __init__(self, state_dim, n_actions, seed=0, lr=1e-3, gamma=0.99):
        import torch
        torch.manual_seed(seed)
        torch.set_num_threads(4)
        self.torch = torch
        self.q = _net(state_dim, n_actions)
        self.tq = _net(state_dim, n_actions)
        self.tq.load_state_dict(self.q.state_dict())
        self.opt = torch.optim.Adam(self.q.parameters(), lr=lr)
        self.gamma = gamma
        self.n_actions = n_actions
        self.rng = np.random.default_rng(seed)

    def warm_start(self, states, actions, epochs=40, lr=1e-3, batch=256):
        """行为克隆: 以 MPC 专家动作为监督, 预训练 Q 网络 (RL + MPC 混合)。"""
        import torch
        import torch.nn.functional as F
        X = torch.tensor(np.asarray(states, dtype=np.float32))
        y = torch.tensor(np.asarray(actions, dtype=np.int64))
        opt = torch.optim.Adam(self.q.parameters(), lr=lr)
        n = len(X)
        for ep in range(epochs):
            perm = torch.randperm(n)
            for i in range(0, n, batch):
                idx = perm[i:i + batch]
                logits = self.q(X[idx])
                loss = F.cross_entropy(logits, y[idx])
                opt.zero_grad(); loss.backward(); opt.step()
        self.tq.load_state_dict(self.q.state_dict())
        return {"expert_samples": int(n), "epochs": epochs}

    def act(self, obs, eps=0.0):
        if self.rng.random() < eps:
            return int(self.rng.integers(self.n_actions))
        with self.torch.no_grad():
            return int(self.torch.argmax(self.q(self.torch.tensor(obs).unsqueeze(0))).item())

    def train(self, env, episodes=350, batch=64, buffer_size=60000,
              eps_start=1.0, eps_end=0.05, target_every=800, log_every=0):
        import torch
        import torch.nn as nn
        buf_s, buf_a, buf_r, buf_s2, buf_d = [], [], [], [], []
        total = 0
        losses = []
        for ep in range(episodes):
            obs = env.reset()
            eps = max(eps_end, eps_start - (eps_start - eps_end) * ep / max(1, episodes))
            done = False
            while not done:
                a = self.act(obs, eps)
                obs2, r, done, _ = env.step(a)
                if len(buf_s) < buffer_size:
                    buf_s.append(obs); buf_a.append(a); buf_r.append(r)
                    buf_s2.append(obs2); buf_d.append(float(done))
                else:
                    i = total % buffer_size
                    buf_s[i], buf_a[i], buf_r[i], buf_s2[i], buf_d[i] = obs, a, r, obs2, float(done)
                obs = obs2
                total += 1
                if total % target_every == 0:
                    self.tq.load_state_dict(self.q.state_dict())
                if len(buf_s) >= max(2000, batch):
                    idx = self.rng.integers(0, len(buf_s), batch)
                    s = torch.tensor(np.array([buf_s[i] for i in idx]))
                    a_ = torch.tensor([buf_a[i] for i in idx]).unsqueeze(1)
                    r_ = torch.tensor([buf_r[i] for i in idx], dtype=torch.float32)
                    s2 = torch.tensor(np.array([buf_s2[i] for i in idx]))
                    d_ = torch.tensor([buf_d[i] for i in idx], dtype=torch.float32)
                    qv = self.q(s).gather(1, a_).squeeze(1)
                    with torch.no_grad():
                        q2 = self.tq(s2).max(1).values
                        tgt = r_ + self.gamma * (1 - d_) * q2
                    loss = nn.functional.smooth_l1_loss(qv, tgt)
                    self.opt.zero_grad(); loss.backward(); self.opt.step()
                    losses.append(float(loss.item()))
            if log_every and (ep + 1) % log_every == 0:
                print(f"  ep {ep+1}/{episodes} loss={np.mean(losses[-500:]):.3f}")
        return {"episodes": episodes, "final_loss": round(float(np.mean(losses[-500:])) if losses else 0.0, 4)}

    def greedy(self):
        def fn(obs, env):
            return self.act(obs, 0.0)
        return fn


def train_agents(env_factory, seeds=(0, 1, 2), episodes=350, log_every=0):
    agents = {}
    for sd in seeds:
        env = env_factory(sd)
        ag = DQNAgent(env.state_dim, E.N_ACTIONS, seed=sd)
        info = ag.train(env, episodes=episodes, log_every=log_every)
        agents[sd] = (ag, info)
    return agents
