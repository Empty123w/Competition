# B 组运行与复现手册（RUNBOOK）

## 1. 获取代码
仓库 `yang466/Competition`，分支 `feat/b-int-a6`（B 集成分支）。当前工作树即为完整 B 侧成果（含 `src/air_station/`、`configs/`、`analysis/`、`docs/b/`）。

## 2. 安装依赖
```bash
python3 -m pip install --break-system-packages pandas numpy pyyaml scikit-learn lightgbm joblib openpyxl
```

## 3. 数据与模型
- 原始 SCADA：`data/{设备运行参数.csv,管网运行数据.xlsx,用气负荷数据.csv,运行事件记录.xlsx,...}`（本地工作树提供）。
- 处理后：`data/processed/*.parquet`（`station_timeseries/device_timeseries/events/maintenance/forecast_features/health_features`）。
- A6 交接：`artifacts/handoff_b/*_calibrated.parquet`；B 侧 test 预测：`artifacts/handoff_b_test/`。
- 模型：`artifacts/models/{power_model.json,pressure_model.json,surge_boundary.json,forecast_*}`。

## 4. 调用调度接口
```bash
python3 -c "from air_station.optimization import b_api; print(b_api.API_VERSION, b_api.DISPATCH_MODES)"
python3 analysis/scripts/c_call_examples.py     # 三模式 + 异常/回退样例
```

## 5. 喘振评估
```bash
python3 -c "
from air_station.optimization import b_api, contracts as C
from air_station.optimization.contracts import DeviceSnapshot, StationSnapshot, DeviceAction, DispatchResult
snap=StationSnapshot('2026-09-11T00:00:00',0.60,[DeviceSnapshot('4#',True,60.0,None,0.60)]) 
r=DispatchResult(C.STATUS_SUCCESS,'2026-09-11T00:00:00','t'); r.device_actions=[DeviceAction('4#','adjust',50.0,0.19,True)]
print(b_api.assess_surge_risk(snap, r).to_dict())"
```

## 6. 场景演示（B7）
```bash
python3 analysis/scripts/b7_scenarios.py       # -> analysis/reports/b/b7/
```

## 7. 压力仿真
```bash
python3 -u analysis/scripts/sim_closed_loop_study.py   # 5 场景 × 4 策略 + 敏感性
python3 -c "from air_station.simulation.closed_loop import simulate,SCENARIOS; print(simulate('load_balanced',SCENARIOS['low'])['metrics'])"
```

## 8. 回测与汇总
```bash
# 新数据段测试(如需)
python3 analysis/scripts/multikpi_harness.py --window test --start 0 --n 3000 --cands b1_rule,b3_b4,bal,lrb_t10
python3 analysis/scripts/b5_unified_summary.py     # 全历史统一汇总
python3 analysis/scripts/b5_load_ratio_all.py      # 加载率 D1/D2/D3
```
> 不要重复运行已完成的 dev(12284)/valid(1916) 大规模回测。

## 9. 测试
```bash
python3 -m pytest tests/optimization tests/simulation tests/safety -q
python3 -m pytest tests/data tests/models tests/baseline tests/b5 -q
python3 -m pytest tests/optimization/test_b01_b20_acceptance.py -v
```

## 10. 查看结果
- 交付包：`analysis/reports/b/delivery/`（README/ALGORITHMS_AND_API/DEMO_SCENARIOS/RUNBOOK/OPEN_ITEMS）。
- 报告：`analysis/reports/b/{B5_UNIFIED_SUMMARY,B_KPI_FINAL_ACCEPTANCE,SIM_CLOSED_LOOP_STUDY,MAIN_STRATEGY_RECOMMENDATION}.md`。
- 文档：`docs/b/01..08`。

## 11. 常见错误定位
| 现象 | 原因/处理 |
|---|---|
| `MODEL_UNAVAILABLE` | `artifacts/models/power_model.json` 缺失 → `performance.train_performance()` |
| `STALE_FORECAST` | 传入预测 `as_of` 早于快照 → 检查时间对齐（Asia/Shanghai, tz-naive） |
| `INFEASIBLE_CAPACITY` | 需求+备用超容量 → 正常；查看 `reason_codes` |
| CSV 读出乱码 | 原始 csv 用 `encoding='utf-8-sig'`（部分 GBK） |
| 平衡残差非 0 | 检查仿真 `pressure_prev`/`vent_m3`/`unmet_m3` 是否最新（已修复） |
| 测试跳过 | `artifacts/models` 缺失时 conftest 会自动训练 |
