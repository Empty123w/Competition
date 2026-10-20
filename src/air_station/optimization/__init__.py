"""优化与调度子包。

阶段 B1 仅提供公共契约 (:mod:`air_station.optimization.contracts`)。
多机优化器 (B3) 将在后续阶段加入本子包, 复用同一契约。
"""
from air_station.optimization import contracts  # noqa: F401

__all__ = ["contracts"]
