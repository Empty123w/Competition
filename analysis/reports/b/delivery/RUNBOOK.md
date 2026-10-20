# RUNBOOK（复现与运行）

## 环境
Python 3.11；`pandas numpy pyyaml scikit-learn lightgbm joblib openpyxl`。模型/配置：`artifacts/models/power_model.json`、`configs/{baseline,optimization,safety,rolling_forecast,rolling_forecast_test}.yaml`。

## 1. 测试（回归）
```bash
python3 -m pytest tests/optimization -q
python3 -m pytest tests/data tests/models tests/baseline tests/safety tests/b5 -q
```

## 2. A6 test 滚动校准预测（隔离，不覆盖 A 交付）
```bash
python3 -u analysis/scripts/a6_test_replay.py \
  2>&1 | tee analysis/reports/phaseA6_replay_test/test_replay.log
# 产物: artifacts/handoff_b_test/forecast_test_dispatch_calibrated.parquet (+scoring, manifest)
# 4 折(08-21/28, 09-04, 09-11), 时域 15/30/45/60/75, 覆盖 89–91%
```

## 3. B5 分段回测（状态连续，15min 网格）
```bash
python3 analysis/scripts/multikpi_harness.py --window dev   --start 0 --n 13000 --cands b1_rule,b3_b4,bal,lrb,lrb_t5,lrb_t10
python3 analysis/scripts/multikpi_harness.py --window valid --start 0 --n 2000  --cands b1_rule,b3_b4,bal,lrb,lrb_t5,lrb_t10
python3 analysis/scripts/multikpi_harness.py --window test  --start 0 --n 3000  --cands b1_rule,b3_b4,bal,lrb_t10
```

## 4. 全历史统一汇总
```bash
python3 analysis/scripts/b5_unified_summary.py     # -> b5_unified_summary.json
python3 analysis/scripts/b5_load_ratio_all.py      # -> delivery/load_ratio_all.json
```

## 5. C 调用样例与专项
```bash
python3 analysis/scripts/c_call_examples.py        # -> delivery/c_call_examples.json
python3 analysis/scripts/kpi_latency.py            # 时延
python3 analysis/scripts/kpi_supply_service.py     # 供气/缺口
python3 analysis/scripts/load_ratio_study.py       # D1/D2/D3 (dev/valid)
python3 analysis/scripts/pressure_study.py         # 压力可辨识性
```

## 6. 长任务与进度
- 长回测用 `nohup python3 -u … > log 2>&1 &`，断点写 `multikpi_<window>_ckpt.csv` 与 `<window>_...log`（每 250 步）。
- 若环境不支持持久任务：保留上述命令与已生成产物；从 `_ckpt.csv`/`fold_complete.json`（`artifacts/models/rolling_forecast_test/`）可断点续跑。

## 7. 纪律
不修改冻结 B1/B4；不覆盖 A 的 dev/valid 交付（test 走独立 `artifacts/handoff_b_test/`）；不删历史账本；本地提交；不推送。
