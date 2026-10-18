"""原始数据校验 (阶段 A1)。

产出结构化问题列表, 每条问题都带**文件名**与**字段名**, 便于定位。
校验项:
  - 文件存在性
  - 必需字段
  - 时间解析 / 唯一性 / 分钟采样间隔 / 乱序
  - 跨表时间对齐
  - 数值范围
  - 原始文件哈希清单
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.data import loaders

TIME_COL = "time"


def _issue(level: str, code: str, message: str,
           file: str | None = None, field: str | None = None) -> dict:
    return {"level": level, "code": code, "file": file, "field": field, "message": message}


# --------------------------------------------------------------------------- #
# 哈希
# --------------------------------------------------------------------------- #
def file_hash(path: str | Path, algo: str = "md5") -> dict:
    path = Path(path)
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return {"algo": algo, "digest": h.hexdigest(), "size_bytes": path.stat().st_size}


def hash_manifest(raw_dir: str | Path, files: list[str] | None = None) -> dict:
    """生成 {文件名: {md5, sha256, size}} 清单。"""
    raw_dir = Path(raw_dir)
    files = files or sorted(p.name for p in raw_dir.iterdir()
                            if p.is_file() and p.suffix.lower() in
                            {".csv", ".xlsx", ".xls", ".json"})
    manifest: dict[str, dict] = {}
    for name in files:
        path = raw_dir / name
        if not path.exists():
            manifest[name] = {"exists": False}
            continue
        md5 = file_hash(path, "md5")
        sha = file_hash(path, "sha256")
        manifest[name] = {"exists": True, "size_bytes": md5["size_bytes"],
                          "md5": md5["digest"], "sha256": sha["digest"]}
    return manifest


# --------------------------------------------------------------------------- #
# 检查
# --------------------------------------------------------------------------- #
def check_files_exist(raw_dir: str | Path, required: list[str]) -> list[dict]:
    raw_dir = Path(raw_dir)
    issues = []
    for name in required:
        if not (raw_dir / name).exists():
            issues.append(_issue("error", "file_missing", f"必需文件缺失: {name}", file=name))
    return issues


def check_required_fields(tables: dict[str, pd.DataFrame],
                          required_fields: dict[str, list[str]]) -> list[dict]:
    issues = []
    for name, fields in required_fields.items():
        df = tables.get(name)
        if df is None:
            issues.append(_issue("error", "table_missing", f"表未加载: {name}", file=name))
            continue
        for f in fields:
            if f not in df.columns:
                issues.append(_issue("error", "field_missing",
                                     f"必需字段缺失: {f}", file=name, field=f))
    return issues


def check_time_series(tables: dict[str, pd.DataFrame],
                      expected_freq_seconds: int = 60,
                      expected_start: str | None = None,
                      expected_end: str | None = None,
                      expected_rows: int | None = None) -> tuple[list[dict], dict, dict]:
    """返回 (issues, alignment, per_table)。"""
    issues: list[dict] = []
    per_table: dict[str, dict] = {}
    times: dict[str, pd.Series] = {}

    for name, df in tables.items():
        fname = loaders.TABLES.get(name, (name, ""))[0]
        if TIME_COL not in df.columns:
            issues.append(_issue("error", "time_missing", "缺少 time 列", file=fname, field="time"))
            continue
        t = df[TIME_COL]
        nat = int(t.isna().sum())
        if nat:
            issues.append(_issue("error", "time_parse",
                                 f"time 解析失败 {nat} 行", file=fname, field="time"))
        valid = t.dropna()
        diffs = valid.diff().dt.total_seconds().dropna()
        dup = int(t.duplicated().sum())
        if dup:
            issues.append(_issue("error", "time_duplicate",
                                 f"重复时间戳 {dup} 个", file=fname, field="time"))
        out_of_order = int((diffs < 0).sum())
        if out_of_order:
            issues.append(_issue("error", "time_unordered",
                                 f"乱序记录 {out_of_order} 处", file=fname, field="time"))
        non_freq = int((diffs.round(3) != expected_freq_seconds).sum())
        if non_freq:
            issues.append(_issue("warning", "freq_mismatch",
                                 f"非 {expected_freq_seconds}s 间隔 {non_freq} 处", file=fname, field="time"))
        if expected_rows is not None and len(df) != expected_rows:
            issues.append(_issue("warning", "row_count",
                                 f"行数 {len(df)} != 期望 {expected_rows}", file=fname))
        if expected_start and str(valid.min()) < expected_start:
            issues.append(_issue("warning", "range_start",
                                 f"起始时间 {valid.min()} 早于期望 {expected_start}", file=fname, field="time"))
        if expected_end and str(valid.max()) > expected_end:
            issues.append(_issue("warning", "range_end",
                                 f"结束时间 {valid.max()} 晚于期望 {expected_end}", file=fname, field="time"))

        per_table[name] = {
            "file": fname,
            "rows": int(len(df)),
            "time_min": str(valid.min()) if len(valid) else None,
            "time_max": str(valid.max()) if len(valid) else None,
            "nat": nat,
            "duplicates": dup,
            "out_of_order": out_of_order,
            "freq_mode_s": float(diffs.mode().iloc[0]) if not diffs.empty else None,
            "non_freq_steps": non_freq,
        }
        times[name] = valid.reset_index(drop=True)

    # 跨表对齐
    alignment: dict = {"aligned": True, "base_table": None}
    if times:
        base_name = next(iter(times))
        base = times[base_name]
        alignment["base_table"] = base_name
        for name, t in times.items():
            ok = len(t) == len(base) and bool((t == base).all())
            alignment[name] = {"aligned": ok, "rows": int(len(t))}
            if not ok:
                alignment["aligned"] = False
                fname = loaders.TABLES.get(name, (name, ""))[0]
                issues.append(_issue("error", "alignment",
                                     f"{name} 与基准表 {base_name} 未逐行对齐",
                                     file=fname, field="time"))
    return issues, alignment, per_table


def check_numeric_ranges(tables: dict[str, pd.DataFrame],
                         ranges: dict[str, dict]) -> list[dict]:
    """按指标名匹配列, 检查数值是否越界。ranges: {指标: {min,max,unit}}"""
    issues = []
    for name, df in tables.items():
        fname = loaders.TABLES.get(name, (name, ""))[0]
        for indicator, spec in ranges.items():
            cols = [c for c in df.columns if indicator in str(c)]
            for c in cols:
                x = pd.to_numeric(df[c], errors="coerce").dropna()
                if x.empty:
                    continue
                lo, hi = spec.get("min"), spec.get("max")
                bad = 0
                if lo is not None:
                    bad += int((x < lo).sum())
                if hi is not None:
                    bad += int((x > hi).sum())
                if bad:
                    unit = spec.get("unit", "")
                    issues.append(_issue(
                        "warning", "range_value",
                        f"{c} 越界 {bad} 行 (期望 {lo}~{hi} {unit})",
                        file=fname, field=c))
    return issues


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #
def validate_raw(raw_dir: str | Path, config: dict | None = None) -> dict:
    cfg = (config or {}).get("validation", {})
    required_files = cfg.get("required_files", loaders.REQUIRED_FILES)
    required_fields = cfg.get("required_fields", {})
    ranges = cfg.get("numeric_ranges", {})
    tcfg = (config or {}).get("time", {})
    freq = int(tcfg.get("expected_freq_seconds", 60))
    start = tcfg.get("expected_start")
    end = tcfg.get("expected_end")
    rows = tcfg.get("n_rows_expected")

    issues: list[dict] = []
    issues += check_files_exist(raw_dir, required_files)

    sources = loaders.load_all_sources(raw_dir)
    tables = sources["tables"]
    for fname, err in sources["load_errors"].items():
        issues.append(_issue("error", "load_failed", f"读取失败: {err}", file=fname))

    issues += check_required_fields(tables, required_fields)
    t_issues, alignment, per_table = check_time_series(tables, freq, start, end, rows)
    issues += t_issues
    issues += check_numeric_ranges(tables, ranges)

    hashes = hash_manifest(raw_dir, required_files)
    errors = [i for i in issues if i["level"] == "error"]
    warnings = [i for i in issues if i["level"] == "warning"]

    return {
        "raw_dir": str(raw_dir),
        "files_loaded": sorted(tables.keys()),
        "load_errors": sources["load_errors"],
        "encodings": sources["encodings"],
        "file_hashes": hashes,
        "alignment": alignment,
        "per_table": per_table,
        "issues": issues,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "passed": len(errors) == 0,
    }
