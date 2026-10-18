"""原始数据加载器。

处理主办方数据的已知特性:
  - `用气负荷数据.csv` 为 GBK 编码;
  - 其余 CSV 为 UTF-8(带 BOM);
  - 4 个 `.xlsx` 为标准 xlsx, 各含单 sheet;
  - 时间列统一解析为 datetime。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

# 逻辑表名 -> (文件名, 读取方式)
TABLES: dict[str, tuple[str, str]] = {
    "设备运行参数": ("设备运行参数.csv", "csv_utf8"),
    "设备状态监测": ("设备状态监测.csv", "csv_utf8"),
    "用气负荷数据": ("用气负荷数据.csv", "csv_gbk"),
    "管网运行数据": ("管网运行数据.xlsx", "xlsx"),
    "环境参数": ("环境参数.xlsx", "xlsx"),
    "运行事件记录": ("运行事件记录.xlsx", "xlsx"),
    "维护保养记录": ("维护保养记录.xlsx", "xlsx"),
}

TIME_COL = "time"


def _downcast(df: pd.DataFrame) -> pd.DataFrame:
    """降采样数值精度以控制内存 (float64 -> float32)。"""
    for c in df.columns:
        if df[c].dtype == "float64":
            df[c] = df[c].astype("float32")
        elif df[c].dtype == "int64":
            df[c] = pd.to_numeric(df[c], downcast="integer")
    return df


def _read_csv(path: Path, encodings: tuple[str, ...]) -> pd.DataFrame:
    last_err: Exception | None = None
    for enc in encodings:
        try:
            return pd.read_csv(path, encoding=enc, low_memory=False)
        except UnicodeDecodeError as e:  # noqa
            last_err = e
    raise last_err if last_err else RuntimeError(f"无法读取 {path}")


def _read_xlsx(path: Path) -> pd.DataFrame:
    """标准 xlsx, 取第一个 sheet; 兼容误命名为 xlsx 的文本。"""
    import zipfile

    if zipfile.is_zipfile(path):
        df = pd.read_excel(path, sheet_name=0)
        return df
    # 兼容早期版本: 实为 GBK 制表符文本
    return pd.read_csv(path, sep="\t", encoding="gbk", low_memory=False)


def load_table(raw_dir: str | Path, logical_name: str) -> pd.DataFrame:
    raw_dir = Path(raw_dir)
    filename, kind = TABLES[logical_name]
    path = raw_dir / filename
    if not path.exists():
        raise FileNotFoundError(f"缺少数据文件: {path}")
    if kind == "csv_utf8":
        df = _read_csv(path, ("utf-8-sig", "utf-8", "gbk"))
    elif kind == "csv_gbk":
        df = _read_csv(path, ("gbk", "gb18030", "utf-8-sig"))
    else:
        df = _read_xlsx(path)
    if TIME_COL in df.columns:
        df[TIME_COL] = pd.to_datetime(df[TIME_COL], errors="coerce")
    return df


def load_raw(raw_dir: str | Path = "data") -> dict[str, pd.DataFrame]:
    """加载全部时序表, 返回 {逻辑表名: DataFrame}。"""
    return {name: load_table(raw_dir, name) for name in TABLES}


def load_device_specs(raw_dir: str | Path = "data") -> list[dict]:
    """加载设备档案 JSON。"""
    import json

    path = Path(raw_dir) / "设备档案.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("设备档案", [])


def load_metric_dict(raw_dir: str | Path = "data") -> dict:
    """加载指标释义 JSON。"""
    import json

    path = Path(raw_dir) / "指标释义.json"
    return json.loads(path.read_text(encoding="utf-8"))
