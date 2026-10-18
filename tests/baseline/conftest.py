"""B1 测试 fixtures。"""
from __future__ import annotations

import pytest

from tests.baseline import _helpers


@pytest.fixture
def base_config() -> dict:
    return _helpers.load_base_config()


@pytest.fixture
def constraints(base_config):
    return _helpers.constraints_from(base_config)
