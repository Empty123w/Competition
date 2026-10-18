"""原始数据加载器 (阶段 A1)。

职责:
  - 显式指定或检测编码;
  - 将 `time` 转换为 datetime64;
  - 不自动填补缺失值, 不修改原始文件;
  - 提供统一入口 load_all_sources()。

只读: 所有函数均不写回原始数据。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

TIME_COL = "time"

# 逻辑表名 -> (文件名, 读取方式)
#   csv_utf8: UTF-8(带 BOM) CSV
#   csv_gbk : GBK CSV
#   xlsx    : 标准 xlsx (兼容历史误命名为 xlsx 的 GBK TSV)
TABLES: dict[str, tuple[str, str]] = {
    "设备运行参数": ("设备运行参数.csv", "csv_utf8"),
    "设备状态监测": ("设备状态监测.csv", "csv_utf8"),
    "用气负荷数据": ("用气负荷数据.csv", "csv_gbk"),
    "管网运行数据": ("管网运行数据.xlsx", "xlsx"),
    "环境参数": ("环境参数.xlsx", "xlsx"),
    "运行事件记录": ("运行事件记录.xlsx", "xlsx"),
    "维护保养记录": ("维护保养记录.xlsx", "xlsx"),
}

JSON_FILES = {"设备档案": "设备档案.json", "指标释义": "指标释义.json"}

REQUIRED_FILES: list[str] = [v[0] for v in TABLES.values()] + list(JSON_FILES.values())


# --------------------------------------------------------------------------- #
# 编码
# --------------------------------------------------------------------------- #
def detect_encoding(path: str | Path, sample_bytes: int = 1 << 16) -> str:
    """按优先级尝试解码, 返回可用编码。"""
    raw = Path(path).read_bytes()[:sample_bytes]
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1"):
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


def _to_datetime(df: pd.DataFrame, time_col: str = TIME_COL) -> pd.DataFrame:
    if time_col in df.columns:
        df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
    return df


# --------------------------------------------------------------------------- #
# 基础读取
# --------------------------------------------------------------------------- #
def load_csv(path: str | Path, encoding: str | None = None,
             parse_time: bool = True) -> pd.DataFrame:
    """读取 CSV; 不填充缺失; 解析 time 列。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"[load_csv] 文件不存在: {path}")
    enc = encoding or detect_encoding(path)
    try:
        df = pd.read_csv(path, encoding=enc, low_memory=False)
    except Exception as e:  # noqa
        raise ValueError(f"[load_csv] 读取失败 {path.name} (encoding={enc}): {e}") from e
    if parse_time:
        df = _to_datetime(df)
    return df


def load_excel(path: str | Path, sheet_name: int | str = 0,
               parse_time: bool = True) -> pd.DataFrame:
    """读取 xlsx; 若实为误命名的 GBK TSV 则回退读取。"""
    import zipfile

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"[load_excel] 文件不存在: {path}")
    if zipfile.is_zipfile(path):
        df = pd.read_excel(path, sheet_name=sheet_name)
    else:
        enc = detect_encoding(path)
        df = pd.read_csv(path, sep="\t", encoding=enc, low_memory=False)
    if parse_time:
        df = _to_datetime(df)
    return df


def load_json(path: str | Path):
    """读取 JSON 文件。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"[load_json] 文件不存在: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 兼容旧接口
# --------------------------------------------------------------------------- #
def _read_xlsx(path: str | Path) -> pd.DataFrame:
    return load_excel(path, parse_time=False)


def load_table(raw_dir: str | Path, logical_name: str) -> pd.DataFrame:
    raw_dir = Path(raw_dir)
    filename, kind = TABLES[logical_name]
    path = raw_dir / filename
    if kind == "csv_utf8":
        return load_csv(path, encoding="utf-8-sig")
    if kind == "csv_gbk":
        return load_csv(path, encoding="gbk")
    return load_excel(path)


def load_raw(raw_dir: str | Path = "data") -> dict[str, pd.DataFrame]:
    """加载全部时序表, 返回 {逻辑表名: DataFrame}。"""
    return {name: load_table(raw_dir, name) for name in TABLES}


def load_device_specs(raw_dir: str | Path = "data") -> list[dict]:
    data = load_json(Path(raw_dir) / "设备档案.json")
    return data.get("设备档案", [])


def load_metric_dict(raw_dir: str | Path = "data") -> dict:
    return load_json(Path(raw_dir) / "指标释义.json")


# --------------------------------------------------------------------------- #
# 统一入口
# --------------------------------------------------------------------------- #
def load_all_sources(raw_dir: str | Path = "data") -> dict:
    """读取 CSV/XLSX/JSON 全部数据源。

    返回:
      {
        "raw_dir": str,
        "tables": {逻辑表名: DataFrame},
        "device_specs": [...],
        "metric_dict": {...},
        "encodings": {文件名: 编码},
        "load_errors": {文件名: 错误信息},
      }
    """
    raw_dir = Path(raw_dir)
    tables: dict[str, pd.DataFrame] = {}
    encodings: dict[str, str] = {}
    load_errors: dict[str, str] = {}

    for name, (filename, kind) in TABLES.items():
        path = raw_dir / filename
        try:
            if kind == "csv_gbk":
                tables[name] = load_csv(path, encoding="gbk")
                encodings[filename] = "gbk"
            elif kind == "csv_utf8":
                enc = detect_encoding(path)
                tables[name] = load_csv(path, encoding=enc)
                encodings[filename] = enc
            else:
                enc = "xlsx" if __import__("zipfile").is_zipfile(path) else detect_encoding(path)
                tables[name] = load_excel(path)
                encodings[filename] = enc
        except Exception as e:  # noqa
            load_errors[filename] = str(e)

    device_specs: list[dict] = []
    metric_dict: dict = {}
    for key, filename in JSON_FILES.items():
        path = raw_dir / filename
        try:
            payload = load_json(path)
            if key == "设备档案":
                device_specs = payload.get("设备档案", [])
            else:
                metric_dict = payload
        except Exception as e:  # noqa
            load_errors[filename] = str(e)

    return {
        "raw_dir": str(raw_dir),
        "tables": tables,
        "device_specs": device_specs,
        "metric_dict": metric_dict,
        "encodings": encodings,
        "load_errors": load_errors,
    }
