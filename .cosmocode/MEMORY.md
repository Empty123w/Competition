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

[B 组第三阶段运行命令与联合闭环]
- Date: 2026-10-21
- Context: Agent 在完成 B 组第三阶段（联合优化与指标验收）时发现
- Category: 构建方法/工作流协作
- Instructions:
  - 联合闭环实现在 `src/air_station/stage2/joint_dispatch.py`（复制阶段一 closed_loop 循环并 import 其物理函数，保证物理一致）；策略：b1/b3/balanced/load_balanced/mpc/joint
  - 阶段三命令：
    - 一致性核查：`python3 analysis/scripts/stage3_pressure_consistency.py`
    - 多策略对照：`python3 analysis/scripts/stage3_joint_eval.py`（约 5-10 分钟）
    - GRU 优化：`python3 analysis/scripts/stage3_surge_optimize.py`（约 5-8 分钟）
    - 指标验收：`python3 analysis/scripts/stage3_acceptance.py`（test 种子 3,4；约 10 分钟）
  - 产物：`analysis/reports/b/stage3/`；评价协议 `.cosmocode/docs/b_stage2/EVAL_PROTOCOL.md`
  - 推荐策略 `joint`：MPC 压力控制 + 加载率均衡分配(spread_balance, tol=0.10) + 低负荷目标，单一运行同时满足能效/加载率/低负荷/压力

[B 组最终指标评价体系运行命令]
- Date: 2026-10-22
- Context: Agent 在建立 B 组最终比赛指标评价体系时发现
- Category: 构建方法/排错调试
- Instructions:
  - 最终回测: `python3 analysis/scripts/b_final_backtest.py --window {dev|valid|test} --basis upper --preset full`（dev 约 20 分钟, valid/test 约 5-6 分钟）
  - 真实压力评价: `python3 analysis/scripts/final_pressure_real.py --window {valid|test|dev} --max-steps N`
  - 指标总表: `python3 analysis/scripts/final_kpi_table.py` → `analysis/reports/b/final/B_FINAL_KPI_TABLE.md`
  - 关键结论: 真实历史 pooled 上 joint SEC −7.73%、D3=1.0、低负荷 −68.4%；压力 KPI 在 valid/test（需求≤容量）达标 0.9998/0.9957，dev 因 13.1% 步需求超 2×额定容量而仅 0.71（物理容量不足，不可伪造）
  - 真实历史需求口径用 A6 真实滚动预测 (upper bound) 与 harness 一致；不得用未来真值

[B 组封版与 B6/C 集成要点]
- Date: 2026-10-25
- Context: Agent 在 B 组封版、B6 集成与推送时发现
- Category: 工作流协作/环境配置
- Instructions:
  - B6 `plan_dispatch` 新增可选 `mode="joint_nompc"`(默认仍 b3); 旧三模式不变; 实现为 lazy import `stage2.joint_dispatch.mpc_decide`(避免循环依赖)
  - C 的 `integration/dispatch_bridge.dispatch_plan(mode=...)` 直接透传 mode, 无需改 C 代码即可调用 joint_nompc(已在 /tmp 一次性 clone 中 dry-run 验证)
  - 样本口径必须区分: 主口径(6策略含 joint_nompc)=10793 步; 宽口径(b1+joint_nompc)=11048; 含 b3_lrb=10789; 不得混写
  - 推送权限: 只能推 Fork `yang466/Competition`; 团队 `Wxyun113/Competition` 推送被拒(403, bot 无权限) -> 只能用 PR
  - 团队 main 的 `optimization/dispatch_api.py` 与本分支改动前逐字节一致 -> 该改动无冲突风险

[前端与后端运行环境（补充）]
- Date: 2026-10-25
- Context: Agent 在安装依赖、运行可视化网页与 C 后端 API 时发现
- Category: 环境配置 / 构建方法
- Instructions:
  - 无 `python` 命令，只有 `python3`（3.11.2）；pip 走 aliyun 镜像且下载大 wheel 极慢。可靠做法：用 `curl` 预下载 wheel 到本地目录，再 `pip3 install --break-system-packages --no-index --find-links=<dir> <pkgs>`；大 wheel 安装前用 zipfile.testzip() 校验。
  - C 后端 API（`air_station.api`）需要 `sqlalchemy`，缺失会导致 uvicorn 启动即报 `ModuleNotFoundError`。
  - C 后端：`PYTHONPATH=src python3 -m uvicorn air_station.api.app:app --host 0.0.0.0 --port 8000`（`/` 能力清单，`/docs` OpenAPI）。
  - 旧版可视化网页：`python3 -m air_station.webapp.app`（默认 0.0.0.0:8080）。新版前端 v2 在 `/v2`，经同源反代 `/capi/*` 调用 C 后端（`CAPI_BASE` 可覆盖，默认 127.0.0.1:8000）。
  - 验收测试：`python3 analysis/scripts/webapp_acceptance.py --base-url http://127.0.0.1:8080`；预测 MAPE 必须复用 `forecasting.evaluate.mape`（min_flow=1.0、分母 max(|y|,1)）。
