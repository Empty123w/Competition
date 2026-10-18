#!/usr/bin/env python3
"""通用数据体检工具 (Data Profiler).

扫描工作空间中的原始数据文件, 输出:
  - reports/data_profile.json      机器可读的完整画像
  - reports/数据体检报告.md         人可读的体检报告
  - reports/数据字段字典.md         字段级字典

设计目标: 对未知格式的主办方数据做无偏见盘点, 不依赖具体业务假设.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace"))
REPORT_DIR = WORKSPACE / "reports"
EXCLUDE_DIRS = {".git", "node_modules", "scripts", "reports", "docs", "__pycache__",
                "dist", "build", ".venv", "venv", "target"}
DATA_EXT = {".csv", ".tsv", ".txt", ".xlsx", ".xls", ".json", ".parquet", ".feather"}

# 列名 -> 单位推断 (仅作提示, 不改变原始数据)
UNIT_HINTS = [
    (r"(温度|temp|temperat)", "℃"),
    (r"(压力|press)", "kPa/bar (需看量程)"),
    (r"(流量|flow|风量|排量)", "m³/min 或 m³/h (需确认)"),
    (r"(功率|power|kw)", "kW"),
    (r"(电流|current|amp)", "A"),
    (r"(电压|voltage|volt)", "V"),
    (r"(转速|speed|rpm)", "r/min"),
    (r"(效率|efficien)", "%"),
    (r"(比功率|specific.?power)", "kW/(m³/min)"),
    (r"(电耗|kwh|耗电|energy)", "kWh"),
    (r"(振动|vibration)", "mm/s 或 μm"),
    (r"(湿度|humid)", "%RH"),
    (r"(露点|dew)", "℃"),
    (r"(时间|time|date|日期)", "时间戳"),
]

# 列名 -> 可能时间列
TIME_HINTS = re.compile(r"(time|date|日期|时间|timestamp|ts$|datetim)", re.I)
# 列名 -> 可能设备编号列
ID_HINTS = re.compile(r"(id$|编号|设备|device|机器|machine|机号|asset|tag|方锅|seq|slave)", re.I)


def find_data_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.startswith("~$") or fn.startswith("."):
                continue
            ext = Path(fn).suffix.lower()
            if ext in DATA_EXT:
                files.append(Path(dirpath) / fn)
    return sorted(files)


def infer_unit(col: str) -> str:
    low = col.lower()
    for pat, unit in UNIT_HINTS:
        if re.search(pat, low):
            return unit
    return ""


def read_any(path: Path) -> dict[str, pd.DataFrame]:
    """返回 {逻辑表名: DataFrame}."""
    ext = path.suffix.lower()
    try:
        if ext == ".csv":
            for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
                try:
                    return {path.stem: pd.read_csv(path, encoding=enc, low_memory=False)}
                except UnicodeDecodeError:
                    continue
            return {path.stem: pd.read_csv(path, encoding="latin-1", low_memory=False)}
        if ext == ".tsv":
            return {path.stem: pd.read_csv(path, sep="\t", low_memory=False)}
        if ext == ".txt":
            try:
                df = pd.read_csv(path, sep=None, engine="python", low_memory=False)
                if df.shape[1] > 1:
                    return {path.stem: df}
            except Exception:
                pass
            return {path.stem: pd.read_csv(path, sep="\t", low_memory=False)}
        if ext in (".xlsx", ".xls"):
            try:
                sheets = pd.read_excel(path, sheet_name=None)
                return {f"{path.stem}::{k}": v for k, v in sheets.items()}
            except Exception:
                # 文件名伪装: 实为文本分隔文件(常见 GBK TSV)
                for enc in ("gbk", "gb18030", "utf-8-sig", "utf-8"):
                    try:
                        df = pd.read_csv(path, sep="\t", encoding=enc, low_memory=False)
                        if df.shape[1] >= 2:
                            return {f"{path.stem}::[实为TSV/{enc}]": df}
                    except Exception:
                        continue
                raise
        if ext == ".json":
            obj = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(obj, list):
                return {path.stem: pd.json_normalize(obj)}
            if isinstance(obj, dict):
                out = {}
                for k, v in obj.items():
                    if isinstance(v, list):
                        out[f"{path.stem}::{k}"] = pd.json_normalize(v)
                if not out:
                    out[path.stem] = pd.json_normalize(obj)
                return out
        if ext in (".parquet", ".feather"):
            return {path.stem: pd.read_parquet(path)}
    except Exception as e:  # noqa
        return {f"{path.stem}::[读取失败]": pd.DataFrame({"_error": [str(e)]})}
    return {}


def _to_datetime(series: pd.Series) -> pd.Series | None:
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    if not (pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)
            or pd.api.types.is_numeric_dtype(series)):
        return None
    sample = series.dropna().astype(str).head(200)
    if sample.empty:
        return None
    # 数字时间戳: 判断量级
    if pd.api.types.is_numeric_dtype(series):
        mx = pd.to_numeric(series, errors="coerce").max()
        if pd.isna(mx):
            return None
        unit = None
        if mx > 1e17:
            unit = "ns"
        elif mx > 1e14:
            unit = "us"
        elif mx > 1e11:
            unit = "ms"
        elif mx > 1e8:
            unit = "s"
        if unit is None:
            return None
        try:
            return pd.to_datetime(series, unit=unit, errors="coerce")
        except Exception:
            return None
    # 字符串: 尝试解析, 要求成功率 > 0.8
    parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    ok = parsed.notna().sum() / max(sample.shape[0], 1)
    if ok > 0.8:
        return parsed
    parsed2 = pd.to_datetime(series, errors="coerce")
    if parsed2.notna().sum() / max(sample.shape[0], 1) > 0.8:
        return parsed2
    return None


def detect_frequency(ts: pd.Series) -> dict:
    s = ts.dropna().sort_values()
    if len(s) < 3:
        return {"样本数": len(s)}
    diffs = s.diff().dropna().dt.total_seconds()
    diffs = diffs[diffs > 0]
    if diffs.empty:
        return {"样本数": len(s)}
    med = float(diffs.median())
    mode = diffs.round(3).mode()
    return {
        "样本数": int(len(s)),
        "中位间隔秒": med,
        "众数间隔秒": float(mode.iloc[0]) if not mode.empty else None,
        "最小间隔秒": float(diffs.min()),
        "最大间隔秒": float(diffs.max()),
        "推断频率": _freq_label(med),
    }


def _freq_label(sec: float) -> str:
    if sec <= 0:
        return "未知"
    if sec < 1:
        return f"{1/sec:.0f} Hz (约{sec:.3f}s)"
    if sec < 60:
        return f"约 {sec:.1f} 秒/点"
    if sec < 3600:
        return f"约 {sec/60:.1f} 分钟/点"
    if sec < 86400:
        return f"约 {sec/3600:.1f} 小时/点"
    return f"约 {sec/86400:.1f} 天/点"


def col_outliers(s: pd.Series) -> dict:
    x = pd.to_numeric(s, errors="coerce").dropna()
    if len(x) < 8:
        return {}
    q1, q3 = x.quantile(0.25), x.quantile(0.75)
    iqr = q3 - q1
    if iqr == 0:
        return {"异常值(IQR)": 0, "常量": True}
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    n_out = int(((x < lo) | (x > hi)).sum())
    return {"异常值(IQR)": n_out, "异常占比": round(n_out / len(x), 5),
            "下界": float(lo), "上界": float(hi)}


def detect_id_columns(df: pd.DataFrame) -> list[str]:
    ids = []
    for c in df.columns:
        if ID_HINTS.search(str(c)):
            nun = df[c].nunique(dropna=True)
            if 1 < nun <= max(2000, len(df) * 0.5):
                ids.append(c)
    return ids


def _hashable(df: pd.DataFrame) -> pd.DataFrame:
    """把不可哈希的单元格(如 list/dict)转成字符串, 便于去重/统计."""
    out = df.copy()
    for c in out.columns:
        if out[c].map(lambda v: isinstance(v, (list, dict, set))).any():
            out[c] = out[c].map(lambda v: json.dumps(v, ensure_ascii=False, sort_keys=True)
                                 if isinstance(v, (list, dict, set)) else v)
    return out


def profile_table(table_name: str, df: pd.DataFrame) -> dict:
    df = _hashable(df)
    info: dict = {
        "表名": table_name,
        "行数": int(df.shape[0]),
        "列数": int(df.shape[1]),
        "列名": [str(c) for c in df.columns],
        "重复行数": int(df.duplicated().sum()),
        "全空列": [str(c) for c in df.columns if df[c].isna().all()],
        "字段": {},
    }
    time_cols, id_cols = [], []
    for c in df.columns:
        s = df[c]
        col: dict = {
            "dtype": str(s.dtype),
            "非空数": int(s.notna().sum()),
            "缺失数": int(s.isna().sum()),
            "缺失率": round(float(s.isna().mean()), 5),
            "唯一值数": int(s.nunique(dropna=True)),
            "单位(推断)": infer_unit(str(c)),
        }
        if pd.api.types.is_numeric_dtype(s):
            x = pd.to_numeric(s, errors="coerce")
            col.update({
                "min": _safe(x.min()), "max": _safe(x.max()),
                "mean": _safe(x.mean()), "std": _safe(x.std()),
                "p50": _safe(x.median()),
            })
            col.update(col_outliers(s))
        else:
            vc = s.astype(str).value_counts(dropna=True).head(5)
            col["Top5取值"] = {str(k): int(v) for k, v in vc.items()}
        # 时间列探测
        if df.shape[0] > 0 and (TIME_HINTS.search(str(c)) or not pd.api.types.is_numeric_dtype(s)):
            ts = _to_datetime(s)
            if ts is not None and ts.notna().sum() >= max(3, df.shape[0] * 0.5):
                col["是时间列"] = True
                col["时间范围"] = [str(ts.min()), str(ts.max())]
                col["采样频率"] = detect_frequency(ts)
                time_cols.append(str(c))
        info["字段"][str(c)] = col
        if ID_HINTS.search(str(c)):
            id_cols.append(str(c))
    info["时间列"] = time_cols
    info["候选设备编号列"] = id_cols
    info["候选设备编号列的取值数"] = {c: int(df[c].nunique(dropna=True)) for c in id_cols}
    return info


def _safe(v):
    try:
        if pd.isna(v):
            return None
        return float(v)
    except Exception:
        return None


def file_hash(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    files = find_data_files(WORKSPACE)
    profile: dict = {
        "生成时间": datetime.now().isoformat(timespec="seconds"),
        "工作空间": str(WORKSPACE),
        "文件数": len(files),
        "文件": [],
    }
    for path in files:
        rel = str(path.relative_to(WORKSPACE))
        entry = {
            "路径": rel,
            "大小字节": path.stat().st_size,
            "md5_12": file_hash(path),
            "表": [],
        }
        tables = read_any(path)
        for tname, df in tables.items():
            entry["表"].append(profile_table(tname, df))
        profile["文件"].append(entry)

    (REPORT_DIR / "data_profile.json").write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 扫描 {len(files)} 个文件, 写入 reports/data_profile.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
