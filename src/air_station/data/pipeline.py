"""数据流水线: 加载 -> 校验 -> 清洗 -> 持久化 -> 质量报告。

针对低内存环境, 对两张大型表(设备状态监测/设备运行参数)采用**分块流式**处理,
逐块 melt 并追加写入 parquet, 避免一次性构建千万行长表。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.data import cleaners, loaders, validators

TIME_COL = "time"
CHUNK = 20000
DEVICE_PREFIX = re.compile(r"^([45])#(.+)$")

# 需要分块流式处理的大表
BIG_TABLES = {"设备状态监测", "设备运行参数"}


def _numeric_dtype(columns) -> dict:
    return {c: "float32" for c in columns if c != TIME_COL}


def _iter_chunks(raw_dir: Path, name: str):
    filename, kind = loaders.TABLES[name]
    path = raw_dir / filename
    if kind == "csv_utf8":
        enc = "utf-8-sig"
    elif kind == "csv_gbk":
        enc = "gbk"
    else:
        enc = None
    if kind.startswith("csv"):
        header = pd.read_csv(path, nrows=0, encoding=enc)
        dtype = _numeric_dtype(header.columns)
        reader = pd.read_csv(path, encoding=enc, dtype=dtype,
                             parse_dates=[TIME_COL], chunksize=CHUNK, low_memory=True)
        for ch in reader:
            yield ch
    else:
        df = loaders._read_xlsx(path)
        df[TIME_COL] = pd.to_datetime(df[TIME_COL], errors="coerce")
        for i in range(0, len(df), CHUNK):
            yield df.iloc[i:i + CHUNK].copy()


def _clean_chunk(name: str, ch: pd.DataFrame, config: dict) -> pd.DataFrame:
    drop_cols = config.get("drop_columns", [])
    sentinels = config.get("sentinel_values", {})
    thr = config.get("running_power_threshold_kw", 1.0)
    ch = cleaners.drop_invalid(ch, drop_cols)
    ch = cleaners.mask_sentinels(ch, sentinels)
    if name == "设备运行参数":
        ch = cleaners.add_running_flags(ch, thr)
    return ch


def _long_chunk(name: str, ch: pd.DataFrame) -> pd.DataFrame:
    cols = [c for c in ch.columns if DEVICE_PREFIX.match(str(c))]
    if not cols:
        return pd.DataFrame()
    sub = ch[[TIME_COL] + cols].melt(id_vars=TIME_COL, var_name="col", value_name="value")
    parsed = sub["col"].str.extract(DEVICE_PREFIX.pattern)
    sub["device_number"] = parsed[0].astype("int8")
    sub["indicator"] = parsed[1].astype("category")
    sub["value"] = pd.to_numeric(sub["value"], errors="coerce").astype("float32")
    return sub[[TIME_COL, "device_number", "indicator", "value"]]


def _stream_write(path: Path, chunks_iter, transform) -> int:
    import pyarrow as pa
    import pyarrow.parquet as pq

    writer = None
    n = 0
    for chunk in chunks_iter:
        out = transform(chunk)
        if out is None or len(out) == 0:
            continue
        tbl = pa.Table.from_pandas(out, preserve_index=False)
        if writer is None:
            writer = pq.ParquetWriter(path, tbl.schema, compression="snappy")
        writer.write_table(tbl)
        n += len(out)
    if writer is not None:
        writer.close()
    return n


def _time_only(path: Path, name: str) -> pd.Series:
    filename, kind = loaders.TABLES[name]
    p = path / filename
    if kind == "csv_gbk":
        return pd.to_datetime(pd.read_csv(p, usecols=[TIME_COL], encoding="gbk")[TIME_COL])
    if kind == "csv_utf8":
        return pd.to_datetime(pd.read_csv(p, usecols=[TIME_COL], encoding="utf-8-sig")[TIME_COL])
    df = loaders._read_xlsx(p)
    return pd.to_datetime(df[TIME_COL])


def run(raw_dir: str = "data",
        processed_dir: str = "data/processed",
        report_dir: str = "artifacts/metrics",
        config_path: str = "configs/data_quality.yaml") -> dict:
    raw = Path(raw_dir)
    out_dir = Path(processed_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    config = cleaners.load_config(config_path)

    written: list[str] = []
    long_rows = 0

    # 1) 大表分块流式: 同时落盘宽表与长表
    for name in BIG_TABLES:
        wide_path = out_dir / f"{name}.parquet"
        long_path = out_dir / f"long_{name}.parquet"

        def _clean_only(ch, _n=name):
            return _clean_chunk(_n, ch, config)

        # 宽表: 需与长表一致, 用同一清洗
        _stream_write(wide_path, _iter_chunks(raw, name), _clean_only)
        written.append(str(wide_path))
        long_rows += _stream_write(long_path, _iter_chunks(raw, name),
                                   lambda ch, _n=name: _long_chunk(_n, _clean_chunk(_n, ch, config)))
        written.append(str(long_path))

    # 2) 小表整表处理
    small: dict[str, pd.DataFrame] = {}
    for name in loaders.TABLES:
        if name in BIG_TABLES:
            continue
        df = loaders.load_table(raw, name)
        df = _clean_chunk(name, df, config)
        small[name] = df
        path = out_dir / f"{name}.parquet"
        cleaners._write_frame(df, path)
        written.append(str(path))
        ldf = cleaners.to_long({name: df}).get(name)
        if ldf is not None and len(ldf):
            lp = out_dir / f"long_{name}.parquet"
            cleaners._write_frame(ldf, lp)
            written.append(str(lp))
            long_rows += len(ldf)

    # 3) 站级宽表
    station = cleaners.build_station(small)
    cleaners._write_frame(station, out_dir / "station.parquet")
    written.append(str(out_dir / "station.parquet"))

    # 4) 校验报告(仅读时间列 + 形状)
    times = {n: _time_only(raw, n) for n in loaders.TABLES}
    base = times[list(times)[0]]
    alignment = {"aligned": True, "start": str(base.min()), "end": str(base.max()), "tables": {}}
    for n, t in times.items():
        ok = len(t) == len(base) and bool((t.reset_index(drop=True) == base.reset_index(drop=True)).all())
        steps = t.diff().dt.total_seconds().dropna()
        alignment["tables"][n] = {
            "rows": int(len(t)), "aligned": ok,
            "duplicated_ts": int(t.duplicated().sum()),
            "freq_mode_s": float(steps.mode().iloc[0]) if not steps.empty else None,
        }
        alignment["aligned"] &= ok

    report = {
        "alignment": alignment,
        "processed_files": written,
        "long_rows": int(long_rows),
        "station_rows": int(len(station)),
    }
    out = Path(report_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "data_pipeline_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="数据流水线")
    ap.add_argument("--raw-dir", default="data")
    ap.add_argument("--processed-dir", default="data/processed")
    ap.add_argument("--report-dir", default="artifacts/metrics")
    ap.add_argument("--config", default="configs/data_quality.yaml")
    args = ap.parse_args()
    rep = run(args.raw_dir, args.processed_dir, args.report_dir, args.config)
    print(f"[OK] 对齐={rep['alignment']['aligned']} 长表行数={rep['long_rows']} "
          f"站级行数={rep['station_rows']} 产出文件={len(rep['processed_files'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
