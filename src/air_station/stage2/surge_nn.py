"""喘振预警序列模型 (GRU / LSTM, torch; 第二阶段)。

复用 surge_warn.window_features 的逐秒特征, 以长度 L 的滑窗作为序列输入,
输出 "未来 H 秒内是否发生事件" 的概率。与 LightGBM 使用同一特征定义与同一评估协议,
保证公平对照; dev 训练, valid 选阈值, test 冻结评估。

validation_mode=simulation。
"""
from __future__ import annotations

from typing import Optional

import numpy as np

_STATE: dict = {}


def available() -> bool:
    try:
        import torch  # noqa
        return True
    except Exception:
        return False


def _build_seq(feats_feats: np.ndarray, L: int) -> np.ndarray:
    n, F = feats_feats.shape
    out = np.zeros((n, L, F), dtype=np.float32)
    for i in range(n):
        lo = max(0, i - L + 1)
        seg = feats_feats[lo:i + 1]
        out[i, L - seg.shape[0]:] = seg
    return out


def _seqs(series_list, hz, window_s, L):
    from air_station.stage2 import surge_warn as SW
    Xs, Ys = [], []
    for s in series_list:
        feats = SW.window_features(s.frame(noisy=True), hz, window_s)[SW.FEATURE_COLS].to_numpy(np.float32)
        Xs.append(_build_seq(feats, L))
        Ys.append(SW.hazard_label(s.t, s.events, _HORIZON))
    return np.concatenate(Xs), np.concatenate(Ys)


_HORIZON = 60.0


def _train(arch, series_train, hz, window_s, horizon_s, L=20, epochs=14, hidden=32, seed=0):
    import torch
    import torch.nn as nn
    global _HORIZON
    _HORIZON = horizon_s
    torch.manual_seed(seed)
    torch.set_num_threads(4)
    X, y = _seqs(series_train, hz, window_s, L)
    mu = X.reshape(-1, X.shape[-1]).mean(0)
    sd = X.reshape(-1, X.shape[-1]).std(0) + 1e-6
    Xn = (X - mu) / sd
    F = X.shape[-1]

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            rnn = nn.GRU if arch == "gru" else nn.LSTM
            self.rnn = rnn(F, hidden, batch_first=True)
            self.head = nn.Sequential(nn.Linear(hidden, 32), nn.ReLU(), nn.Linear(32, 1))

        def forward(self, x):
            o, _ = self.rnn(x)
            return self.head(o[:, -1, :]).squeeze(-1)

    net = Net()
    opt = torch.optim.Adam(net.parameters(), lr=2e-3)
    lossf = nn.BCEWithLogitsLoss()
    Xt = torch.tensor(Xn); yt = torch.tensor(y.astype(np.float32))
    n = len(Xt); bs = 512
    for ep in range(epochs):
        perm = torch.randperm(n)
        net.train()
        for i in range(0, n, bs):
            idx = perm[i:i + bs]
            opt.zero_grad()
            out = net(Xt[idx])
            loss = lossf(out, yt[idx])
            loss.backward(); opt.step()
    net.eval()
    _STATE.update(net=net, mu=mu, sd=sd, L=L, F=F, arch=arch)
    return net


def predict_proba(feats, hz: float, window_s: int) -> np.ndarray:
    import torch
    st = _STATE
    features = feats.iloc[:, :] if hasattr(feats, "iloc") else feats
    from air_station.stage2.surge_warn import FEATURE_COLS
    arr = np.asarray(feats[FEATURE_COLS], dtype=np.float32) if hasattr(feats, "columns") else np.asarray(feats, np.float32)
    seq = _build_seq(arr, st["L"])
    seq = (seq - st["mu"]) / st["sd"]
    net = st["net"]
    out = []
    with torch.no_grad():
        for i in range(0, len(seq), 4096):
            logit = net(torch.tensor(seq[i:i + 4096]))
            out.append(torch.sigmoid(logit).numpy())
    return np.concatenate(out)


def train_eval(splits, hz, window_s, horizon_s, arch, thr_grid, eval_predictor, match_w, lead_target):
    _train(arch, splits["dev"], hz, window_s, horizon_s)
    fn = lambda feats: predict_proba(feats, hz, window_s)
    best = None
    for thr in thr_grid:
        m = eval_predictor(fn, splits["valid"], hz, window_s, horizon_s, thr, match_w, lead_target)
        tl = m["time_level"]; el = m["event_level"]
        key = (1 if (tl["accuracy"] or 0) >= 0.90 else 0, el["lead_ge30_coverage"] or 0.0,
               tl["accuracy"] or 0.0)
        if best is None or key > best[0]:
            best = (key, thr, m)
    thr = best[1]
    test = eval_predictor(fn, splits["test"], hz, window_s, horizon_s, thr, match_w, lead_target)
    return thr, best[2], test
