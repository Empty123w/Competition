"""数据流水线 (阶段 A1 起)。

用法:
  # 阶段 A1: 读取与校验原始数据
  python -m air_station.data.pipeline --stage validate --input data

  # 阶段 A2: 清洗与持久化(分块流式, 适配低内存)
  python -m air_station.data.pipeline --stage clean --input data

  # 全部
  python -m air_station.data.pipeline --stage all
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
BIG_TABLES = {"设备状态监测", "设备运行参数"}


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _load_config(path: str) -> dict:
    import yaml
    p = Path(path)
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# 阶段 A1: 校验
# --------------------------------------------------------------------------- #
def validate_stage(raw_dir: str, report_dir: str, config_path: str,
                   archive_dir: str = "analysis/reports/phaseA1") -> dict:
    config = _load_config(config_path)
    report = validators.validate_raw(raw_dir, config)
    out = Path(report_dir)
    _write_json(out / "raw_validation.json", report)
    _write_json(out / "file_hash_manifest.json", report["file_hashes"])
    _write_json(out / "validation_issues.json",
                {"errors": [i for i in report["issues"] if i["level"] == "error"],
                 "warnings": [i for i in report["issues"] if i["level"] == "warning"]})
    # 可入库的归档副本
    if archive_dir:
        arc = Path(archive_dir)
        _write_json(arc / "raw_validation.json", report)
        _write_json(arc / "file_hash_manifest.json", report["file_hashes"])
        _write_json(arc / "validation_issues.json",
                    {"errors": [i for i in report["issues"] if i["level"] == "error"],
                     "warnings": [i for i in report["issues"] if i["level"] == "warning"]})
    return report


def print_validation(report: dict) -> None:
    print(f"[校验] 原始目录: {report['raw_dir']}")
    print(f"[校验] 已加载表: {len(report['files_loaded'])} 张")
    for name, info in report["per_table"].items():
        print(f"    - {info['file']:<22} 行={info['rows']:>7} "
              f"范围={info['time_min']}~{info['time_max']} 频率={info['freq_mode_s']}s "
              f"重复={info['duplicates']} 乱序={info['out_of_order']}")
    al = report["alignment"]
    print(f"[校验] 跨表对齐: {'通过' if al.get('aligned') else '不一致'} (基准={al.get('base_table')})")
    print(f"[校验] 文件哈希: 已生成 {len(report['file_hashes'])} 条")
    print(f"[校验] 错误 {report['error_count']} 条, 警告 {report['warning_count']} 条, "
          f"结果: {'PASS' if report['passed'] else 'FAIL'}")
    for i in report["issues"]:
        loc = "/".join(x for x in (i["file"], i["field"]) if x)
        print(f"    [{i['level'].upper()}] {i['code']} {loc} :: {i['message']}")


# --------------------------------------------------------------------------- #
# 阶段 A2: 清洗(分块流式)
# --------------------------------------------------------------------------- #
def _numeric_dtype(columns) -> dict:
    return {c: "float32" for c in columns if c != TIME_COL}


def _iter_chunks(raw_dir: Path, name: str):
    filename, kind = loaders.TABLES[name]
    path = raw_dir / filename
    enc = "utf-8-sig" if kind == "csv_utf8" else ("gbk" if kind == "csv_gbk" else None)
    if kind.startswith("csv"):
        header = pd.read_csv(path, nrows=0, encoding=enc)
        dtype = _numeric_dtype(header.columns)
        reader = pd.read_csv(path, encoding=enc, dtype=dtype,
                             parse_dates=[TIME_COL], chunksize=CHUNK, low_memory=True)
        for ch in reader:
            yield ch
    else:
        df = loaders.load_excel(path)
        for i in range(0, len(df), CHUNK):
            yield df.iloc[i:i + CHUNK].copy()


def _clean_chunk(name: str, ch: pd.DataFrame, config: dict) -> pd.DataFrame:
    dq = config.get("data_quality", config)
    drop_cols = [c["name"] for c in dq.get("invalid_columns", [])] if isinstance(
        dq.get("invalid_columns", []), list) else []
    sentinels = {str(s["value"]): s.get("meaning") for s in dq.get("sentinels", [])}
    thr = dq.get("running_state", {}).get("power_threshold_kw", 1.0)
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


def clean_stage(raw_dir: str, processed_dir: str, report_dir: str, config_path: str) -> dict:
    raw = Path(raw_dir)
    out_dir = Path(processed_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    config = _load_config(config_path)

    written: list[str] = []
    long_rows = 0
    for name in BIG_TABLES:
        wide_path = out_dir / f"{name}.parquet"
        long_path = out_dir / f"long_{name}.parquet"
        _stream_write(wide_path, _iter_chunks(raw, name),
                      lambda ch, _n=name: _clean_chunk(_n, ch, config))
        written.append(str(wide_path))
        long_rows += _stream_write(
            long_path, _iter_chunks(raw, name),
            lambda ch, _n=name: _long_chunk(_n, _clean_chunk(_n, ch, config)))
        written.append(str(long_path))

    small: dict[str, pd.DataFrame] = {}
    for name in loaders.TABLES:
        if name in BIG_TABLES:
            continue
        df = loaders.load_table(raw, name)
        df = _clean_chunk(name, df, config)
        small[name] = df
        cleaners._write_frame(df, out_dir / f"{name}.parquet")
        written.append(str(out_dir / f"{name}.parquet"))
        ldf = cleaners.to_long({name: df}).get(name)
        if ldf is not None and len(ldf):
            cleaners._write_frame(ldf, out_dir / f"long_{name}.parquet")
            written.append(str(out_dir / f"long_{name}.parquet"))
            long_rows += len(ldf)

    station = cleaners.build_station(small)
    cleaners._write_frame(station, out_dir / "station.parquet")
    written.append(str(out_dir / "station.parquet"))

    report = {"processed_files": written, "long_rows": int(long_rows),
              "station_rows": int(len(station))}
    _write_json(Path(report_dir) / "data_pipeline_report.json", report)
    return report


# --------------------------------------------------------------------------- #
# 阶段 A2: 清洗与标准化
# --------------------------------------------------------------------------- #
def standardize_stage(raw_dir: str, processed_dir: str, report_dir: str, config_path: str,
                      archive_dir: str = "analysis/reports/phase2") -> dict:
    from air_station.data import validators

    config = _load_config(config_path)
    tables = cleaners.build_standard_tables(raw_dir, processed_dir, config)
    checks = cleaners.run_acceptance_checks(tables, raw_dir)

    outputs = {
        "station_timeseries.parquet": {"rows": int(len(tables["station"])),
                                       "columns": list(tables["station"].columns)},
        "device_timeseries.parquet": {"rows": int(len(tables["device"])),
                                      "columns": list(tables["device"].columns)},
        "events.parquet": {"rows": int(len(tables["events"])),
                           "columns": list(tables["events"].columns)},
        "maintenance.parquet": {"rows": int(len(tables["maintenance"])),
                                "columns": list(tables["maintenance"].columns)},
    }
    manifest = {
        "raw_hashes": validators.hash_manifest(raw_dir, cleaners.REQUIRED_RAW),
        "outputs": outputs,
        "checks": checks,
    }
    _write_json(Path(processed_dir) / "data_manifest.json", manifest)
    _write_json(Path(report_dir) / "data_cleaning_checks.json", checks)
    _write_report(Path(archive_dir) / "data_cleaning_report.md", manifest)
    return manifest


def _write_report(path: Path, manifest: dict) -> None:
    outs = manifest["outputs"]
    checks = manifest["checks"]
    lines = ["# 阶段 2 数据清洗报告", "",
             "由 `python -m air_station.data.pipeline --stage standardize` 生成。", "",
             "## 1. 标准输出表", "",
             "| 文件 | 行数 | 列数 |", "|------|------|------|"]
    for name, info in outs.items():
        lines.append(f"| {name} | {info['rows']:,} | {len(info['columns'])} |")
    lines += ["", "## 2. 验收自检", "", "| 检查项 | 结果 |", "|--------|------|"]
    for k, v in checks.items():
        if k == "all_pass":
            continue
        lines.append(f"| {k} | {'PASS' if v.get('pass') else 'FAIL'} |")
    lines += ["", f"**总计: {'PASS' if checks['all_pass']['pass'] else 'FAIL'}**", "",
              "## 3. 质量标记列", "",
              "- 站级: `environment_interpolated` (温湿度掉点/插值)",
              "- 设备级: `temperature_available`, `maintenance_sentinel_removed`, "
              "`fault_label_quality`, `possible_station_level_event`, `quality_valid`, `quality_reason`",
              "", "## 4. 清洗规则要点", "",
              "- B相电流恒零: 保留原始, 不入特征; 生成 `current_proxy_a`=(A相+C相)/2",
              "- 大气压恒常量: 保留记录, 不入模型",
              "- 中冷/后冷前6天长缺失: 保留为空, 不插值, 标记 `temperature_available=false`",
              "- 环境温湿度=0 掉点: 仅短缺口插值并标记 `environment_interpolated`",
              "- 维护计数器 65535/4294970000: 精准置 NaN, 标记 `maintenance_sentinel_removed`",
              "- 状态: 8/10 用事件记录, 其余异常值用功率>1kW 兜底, 标记 `state_source`",
              "- 故障码26: 保留, 标记 `fault_label_quality=weak`, `possible_station_level_event=true`",
              ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------- #
# 阶段 A3: 特征工程
# --------------------------------------------------------------------------- #
def features_stage(raw_dir: str, processed_dir: str, report_dir: str,
                   forecast_config: str, health_config: str,
                   archive_dir: str = "analysis/reports/phaseA3") -> dict:
    import numpy as np
    import yaml
    from air_station.models.forecasting import features as FF
    from air_station.models.health import features as HF

    fcfg = yaml.safe_load(Path(forecast_config).read_text(encoding="utf-8"))
    hcfg = yaml.safe_load(Path(health_config).read_text(encoding="utf-8"))

    fc = FF.build_forecast_features(processed_dir, fcfg)
    hf = HF.build_health_features(raw_dir, processed_dir, hcfg)

    def _no_inf(df):
        num = df.select_dtypes(include=[np.number])
        return bool(np.isinf(num.to_numpy()).sum() == 0)

    checks = {
        "forecast_no_inf": {"pass": _no_inf(fc)},
        "health_no_inf": {"pass": _no_inf(hf)},
        "forecast_rows": {"pass": len(fc) > 0, "rows": int(len(fc))},
        "health_rows": {"pass": len(hf) > 0, "rows": int(len(hf))},
        "health_has_running_field": {"pass": "running" in hf.columns},
        "health_dev_nan_when_stopped": {
            "pass": bool(hf.loc[~hf["running"].astype(bool),
                                [c for c in hf.columns if c.startswith("dev_")]].isna().all().all())},
    }
    checks["all_pass"] = {"pass": all(v.get("pass") for v in checks.values())}

    _write_json(Path(report_dir) / "feature_checks.json", checks)
    _write_feature_dictionary(FF.FEATURE_SPECS, HF.FEATURE_SPECS, Path(archive_dir))
    manifest = {
        "forecast_features": {"rows": int(len(fc)), "columns": list(fc.columns)},
        "health_features": {"rows": int(len(hf)), "columns": list(hf.columns)},
        "checks": checks,
    }
    _write_json(Path(processed_dir) / "feature_manifest.json", manifest)
    return manifest


def _write_feature_dictionary(forecast_specs: list, health_specs: list, out_dir: Path) -> None:
    import csv
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for scope, specs in (("forecast", forecast_specs), ("health", health_specs)):
        for s in specs:
            rows.append({"scope": scope, "name": s["name"], "unit": s["unit"],
                         "description": s["desc"], "calculation": s["calc"],
                         "missing_strategy": s["missing"]})
    with open(out_dir / "feature_dictionary.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["scope", "name", "unit", "description",
                                          "calculation", "missing_strategy"])
        w.writeheader()
        w.writerows(rows)
    lines = ["# 阶段 A3 特征字典", "", "| scope | name | unit | description | calculation | missing |",
             "|-------|------|------|-------------|-------------|---------|"]
    for r in rows:
        lines.append(f"| {r['scope']} | {r['name']} | {r['unit']} | {r['description']} | "
                     f"{r['calculation']} | {r['missing_strategy']} |")
    (out_dir / "feature_dictionary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="数据流水线 (A1 校验 / A2 标准化 / A3 特征)")
    ap.add_argument("--stage", choices=["validate", "clean", "standardize", "features", "all"],
                    default="validate")
    ap.add_argument("--input", dest="raw_dir", default="data")
    ap.add_argument("--processed-dir", default="data/processed")
    ap.add_argument("--report-dir", default="artifacts/metrics")
    ap.add_argument("--config", default="configs/data_quality.yaml")
    ap.add_argument("--forecast-config", default="configs/forecasting.yaml")
    ap.add_argument("--health-config", default="configs/health.yaml")
    args = ap.parse_args()

    if args.stage in ("validate", "all"):
        report = validate_stage(args.raw_dir, args.report_dir, args.config)
        print_validation(report)
        if not report["passed"]:
            return 1
    if args.stage in ("standardize", "all"):
        manifest = standardize_stage(args.raw_dir, args.processed_dir,
                                     args.report_dir, args.config)
        c = manifest["checks"]
        print(f"[标准化] 站级 {manifest['outputs']['station_timeseries.parquet']['rows']:,} 行, "
              f"设备级 {manifest['outputs']['device_timeseries.parquet']['rows']:,} 行")
        for k, v in c.items():
            if k != "all_pass":
                print(f"    {'PASS' if v.get('pass') else 'FAIL'} {k}")
        print(f"[标准化] 总计: {'PASS' if c['all_pass']['pass'] else 'FAIL'}")
        if not c["all_pass"]["pass"]:
            return 1
    if args.stage in ("features", "all"):
        m = features_stage(args.raw_dir, args.processed_dir, args.report_dir,
                           args.forecast_config, args.health_config)
        c = m["checks"]
        print(f"[特征] forecast {m['forecast_features']['rows']:,} 行 / "
              f"health {m['health_features']['rows']:,} 行")
        for k, v in c.items():
            if k != "all_pass":
                print(f"    {'PASS' if v.get('pass') else 'FAIL'} {k}")
        print(f"[特征] 总计: {'PASS' if c['all_pass']['pass'] else 'FAIL'}")
        if not c["all_pass"]["pass"]:
            return 1
    if args.stage == "clean":
        rep = clean_stage(args.raw_dir, args.processed_dir, args.report_dir, args.config)
        print(f"[清洗] 长表行数={rep['long_rows']} 站级行数={rep['station_rows']} "
              f"产出文件={len(rep['processed_files'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
