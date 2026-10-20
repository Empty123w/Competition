# 用户指令记忆

本文件记录了用户的指令、偏好和教导，用于在未来的交互中提供参考。

## 格式

### 用户指令条目

[用户指令摘要]
- Date: [YYYY-MM-DD]
- Context: [提及的场景或时间]
- Instructions:
  - [用户教导或指示的内容，逐行描述]

### 项目知识条目

[项目知识摘要]
- Date: [YYYY-MM-DD]
- Context: Agent 在执行 [具体任务描述] 时发现
- Category: [运维部署|构建方法|测试方法|排错调试|工作流协作|环境配置]
- Instructions:
  - [具体的知识点，逐行描述]

## 去重策略

- 添加新条目前，检查是否存在相似或相同的指令
- 若发现重复，跳过新条目或与已有条目合并
- 合并时，更新上下文或日期信息
- 这有助于避免冗余条目，保持记忆文件整洁

## 条目

[B 模块分支与协作边界]
- Date: 2026-10-18
- Context: 用户指派成员 B 开发调度基线、优化、安全与回测模块时
- Instructions:
  - 只在 `feat/b-optimization` 分支工作，不修改 `main`
  - 不覆盖或修改 A/C/D 的代码与数据（`data/`、`artifacts/`、`configs/` 对 B 只读）
  - 不重建已有数据处理或预测模型
  - 未经用户确认，不执行 Git push、合并或其他远程仓库修改操作
  - 完成每个阶段后先汇报结果，等待确认再进入下一阶段

[本环境 Python 与依赖安装方式]
- Date: 2026-10-18
- Context: Agent 在 B0 阶段核查环境与运行测试时发现
- Category: 环境配置
- Instructions:
  - 环境中不存在 `python` 命令，使用 `python3`（版本 3.11.2）
  - 安装项目依赖：`python3 -m pip install --break-system-packages -e ".[dev]"`（含 pytest、python-docx）
  - 当前实际安装版本：pandas 3.0.6、numpy 2.4.6、pyarrow 26.0.0、lightgbm 4.7.0

[项目测试方法]
- Date: 2026-10-18
- Context: Agent 在执行 B0/B1/RUL 测试核查时发现
- Category: 测试方法
- Instructions:
  - A 模块测试：`python3 -m pytest tests/data tests/models -q`（原基准 47 passed；含 RUL 后 54 passed）
  - RUL 测试：`python3 -m pytest tests/models/test_rul.py -q -rs`（7 passed）
  - B1 基线测试：`python3 -m pytest tests/baseline -q`（24 passed）
  - 全量回归：`python3 -m pytest tests/data tests/models tests/baseline -q`（含 RUL 后基准 78 passed）
  - 出现大量 skip 说明 Parquet/模型/依赖不完整，不能当作通过

[分支历史关系与跨分支同步方式]
- Date: 2026-10-18
- Context: Agent 在增量核验 A 新增 RUL 模块时发现
- Category: 工作流协作
- Instructions:
  - `feat/b-optimization` 与 `origin/main` 历史不相关（无公共祖先），禁止直接 merge
  - 同步 A 的新文件时使用选择性提取：`git restore --source=<ref> --worktree -- <path>`，不合并历史、不覆盖 B 成果
  - 本环境远程仅有 `origin` = `yang466/Competition`；`Wxyun113/Competition` 未配置为远程
  - RUL 产物（rul_sequences.parquet、rul_*.pkl、rul_metrics.json）需在 `.gitignore` 加精确例外才能跟踪
  - `origin` URL 内嵌令牌为**只读**（fetch/ls-remote 可用，push 报 Invalid username or token）
  - 推送改用已配置凭据助手：`git push https://github.com/yang466/Competition.git <branch>:refs/heads/<branch>`

[B 组第二阶段环境与运行命令]
- Date: 2026-10-20
- Context: Agent 在完成 B 组第二阶段（ML/DL/RL/MPC 自主优化）时发现
- Category: 环境配置/构建方法
- Instructions:
  - 本环境预装 numpy/pandas/sklearn/lightgbm/joblib；**未预装 torch**，深度学习实验前需安装：
    `pip install --break-system-packages --quiet torch --index-url https://download.pytorch.org/whl/cpu`（CPU 版，约数分钟）
  - 第二阶段运行命令：
    - 喘振预警：`python3 analysis/scripts/stage2_surge_eval.py`（GPU 无关；含 GRU/LSTM 约 5 分钟）
    - 压力 MPC：`python3 analysis/scripts/stage2_pressure_eval.py`（含全场景 MPC 搜索，约 1-5 分钟）
    - 安全约束 RL：`python3 analysis/scripts/stage2_rl_eval.py --seeds 0 1 2 --episodes 120 --expert-episodes 25`（3 种子约 25-35 分钟）
    - KPI 汇总：`python3 analysis/scripts/stage2_kpi_summary.py`
  - stage2 产物写入 `analysis/reports/b/stage2/{surge,pressure,rl}/`；stage2 代码在 `src/air_station/stage2/`
  - 计时类测试（如 `tests/optimization/test_pressure_mpc.py::test_runtime_under_one_second`）在 CPU 被后台训练占满时会误报失败，需在负载解除后复跑
