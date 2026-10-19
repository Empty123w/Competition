"""多步相对单步新增可执行步的语义不变量测试。

依赖已生成的轨迹文件 analysis/reports/b/multistep_extra_steps_trace.csv
(由 analysis/scripts/multistep_optimization.py 生成); 缺失则跳过。

不变量 (对应"当前步没有跳过检查/没有用未来供气补偿当前缺口"):
  - 每一步 B4 未阻断 (multi_step_blocked=False);
  - 当前步满足其自身预测要求 (multi_step_met_forecast=True);
  - 至少一台机组运行 (multi_step_n>=1);
  - 这些步的 myopic 状态为不可行 (正是多步的覆盖价值来源)。
"""
from pathlib import Path

import pandas as pd
import pytest

TRACE = Path(__file__).resolve().parents[2] / "analysis" / "reports" / "b" / "multistep_extra_steps_trace.csv"


@pytest.fixture(scope="module")
def trace():
    if not TRACE.exists():
        pytest.skip("trace 未生成")
    return pd.read_csv(TRACE)


def test_all_extra_steps_b4_not_blocked(trace):
    assert trace["multi_step_blocked"].sum() == 0


def test_all_extra_steps_met_own_forecast(trace):
    # 当前步必须满足当前步预测要求, 不得依赖未来供气补偿当前缺口
    assert bool(trace["multi_step_met_forecast"].all())


def test_all_extra_steps_have_running_machine(trace):
    assert (trace["multi_step_n"] >= 1).all()


def test_extra_steps_are_myopic_infeasible(trace):
    assert (trace["myopic_status"] == "infeasible").all()
