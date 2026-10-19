"""EW 验证 v3 指标测试: 匹配窗与误报窗统一 / 首秒激活计数 / unknown 屏蔽。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
_s = importlib.util.spec_from_file_location("ew_v3", ROOT / "analysis" / "scripts" / "surge_ew_validation_v3.py")
EW = importlib.util.module_from_spec(_s); _s.loader.exec_module(EW)


def test_pre_event_warning_not_counted_as_false_alarm():
    # 事件 [100,120]; 报警起始于 90 (在匹配窗内) 与 200 (窗外)
    events = [(100, 120)]
    n = 300
    alarm = np.zeros(n, bool)
    alarm[90:130] = True      # 覆盖 90 (预警) 与事件窗
    alarm[200:210] = True     # 窗外 -> 真误报
    m = EW._metrics(events, alarm, np.zeros(n, bool), n)
    assert m["false_alarms"]["event_level_count"] == 1     # 仅 200 那一次
    assert m["false_alarms"]["onsets_in_match_window"] == 1
    assert m["event_level"]["n_detected"] == 1
    assert m["event_level"]["leads_s"] == [10]             # 100-90=10


def test_onset_at_first_second_counted():
    alarm = np.array([True, True, False, True])
    ons = EW._onsets(alarm)
    assert ons[0] == 0 and len(ons) == 2    # 第0秒起始 + 第3秒起始


def test_alarm_active_from_start_without_event_is_false_alarm():
    events = []
    n = 100
    alarm = np.ones(n, bool)     # 全程报警, 无事件
    m = EW._metrics(events, alarm, np.zeros(n, bool), n)
    assert m["false_alarms"]["event_level_count"] == 1   # 起始(第0秒)一次
    assert m["alarm_active_seconds"] == n


def test_unknown_steps_masked_from_confusion():
    # 事件真值 below 覆盖 0..9; 其中 0..4 为 unknown(报警 False)
    events = [(0, 9)]
    n = 20
    alarm = np.zeros(n, bool)
    alarm[5:10] = True                 # 已知段检出
    unknown = np.zeros(n, bool); unknown[0:5] = True
    m = EW._metrics(events, alarm, unknown, n)
    # known 段: TP=5 (5..9), FN=0 -> recall=1.0 (unknown 步不当作 FN)
    assert m["time_level"]["TP"] == 5 and m["time_level"]["FN"] == 0
    assert m["unknown_seconds"] == 5
    assert m["time_level"]["recall"] == 1.0


def test_time_level_fp_and_active_duration_reported():
    events = [(50, 60)]
    n = 200
    alarm = np.zeros(n, bool)
    alarm[10:15] = True     # 窗外 FP 步
    alarm[50:61] = True     # 事件内 TP
    m = EW._metrics(events, alarm, np.zeros(n, bool), n)
    assert m["time_level"]["FP"] == 5
    assert m["time_level"]["TP"] == 11
    assert m["alarm_active_seconds"] == 16
