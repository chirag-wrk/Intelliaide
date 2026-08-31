"""
benchmark_drain3_strategies.py
================================
Benchmarks all 4 combinations of Drain3 strategy × file parallelism:

  pf_seq  — per-FILE  Drain3,  files processed sequentially
  pf_par  — per-FILE  Drain3,  files processed in parallel  (ProcessPool across files)
  pc_seq  — per-CHUNK Drain3,  files processed sequentially (chunks parallelised inside each file)
  pc_par  — per-CHUNK Drain3,  files processed in parallel  (ProcessPool across files;
                                                              chunks sequential within each worker
                                                              to avoid nested process pools)

A single input file is split into N equal virtual "files" (--split) to simulate
a real must-gather workload with multiple pod log files.

Usage:
    python benchmark_drain3_strategies.py <file1> [<file2> ...] [options]

    Pass one or more actual log files. Each file is treated as a separate unit,
    which is what pf_par / pc_par actually parallelise across.

Options:
    --chunk-size INT        Lines per chunk for per-chunk strategies (default: 10000)
    --level CHAR            Log level: E, W, I, UNKNOWN, all (default: all)
    --output-dir PATH       Directory to write result files (default: ./benchmark_out)
    --runs INT              Number of repetitions per strategy (default: 1)
    --workers INT           Max worker processes for file-level and chunk-level pools (default: auto)
    --no-output             Skip writing result files (timing only)

Examples:
    python benchmark_drain3_strategies.py kubelet.log crio.log current.log
    python benchmark_drain3_strategies.py kubelet.log --level E --chunk-size 5000
"""

import argparse
import multiprocessing
import sys
import time
import json
import os
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Import shared primitives from ml_log_classification
# ---------------------------------------------------------------------------
_ML_DIR = Path(__file__).resolve().parent
if str(_ML_DIR) not in sys.path:
    sys.path.insert(0, str(_ML_DIR))

try:
    from ml_log_classification import (
        get_config,
        create_drain3_miner,
        extract_drain3_results,
        process_lines_with_drain3,
        chunk_file_reader,
        process_single_chunk,
        merge_chunk_results,
        build_cluster_to_lines,
        classify_templates_for_level,
        classify_templates_by_frequency,
        FrequencyConfig,
        PipelineConfig,
        DRAIN3_AVAILABLE,
    )
except ImportError as e:
    print(f"[ERROR] Cannot import from ml_log_classification.py: {e}")
    sys.exit(1)

if not DRAIN3_AVAILABLE:
    print("[ERROR] drain3 is not installed. Run: pip install drain3")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_and_categorize_file(file_path: str, chunk_size: int, config: PipelineConfig) -> Dict[str, Dict]:
    """Read file in chunks, categorise lines by level. Returns dict[level -> {preprocessed, original}]."""
    results = []
    for chunk in chunk_file_reader(file_path, chunk_size):
        results.append(process_single_chunk(chunk, config))
    return merge_chunk_results(results)


def write_lines(path: Path, lines: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", errors="replace") as f:
        f.write("\n".join(lines))


def stats_summary(template_list: List[Dict]) -> Dict:
    """Return concise stats for a set of templates."""
    if not template_list:
        return {"templates": 0, "total_lines": 0}
    total_lines = sum(t.get("size", 1) for t in template_list)
    sizes = [t.get("size", 1) for t in template_list]
    return {
        "templates": len(template_list),
        "total_lines": total_lines,
        "min_cluster_size": min(sizes),
        "max_cluster_size": max(sizes),
        "avg_cluster_size": round(total_lines / len(sizes), 1),
    }


# ---------------------------------------------------------------------------
# Post-Drain3 classification helper  (mirrors production pipeline)
# ---------------------------------------------------------------------------

def post_drain3_classify(
    level: str,
    template_list: List[Dict],
    cluster_to_lines: Dict,
    orig_lines: List[str],
    event_ids: List,
    config: PipelineConfig,
) -> Tuple[Dict, float]:
    """Run the two production classification passes that follow Drain3 and
    return (classification_result, classify_ms).

    Pass 1 — classify_templates_for_level:
        Shifts "error-like" and config-change templates from W/I/UNKNOWN
        into Error and ConfigChanges buckets.  (No-op for level 'E'.)

    Pass 2 — classify_templates_by_frequency  (on the Error bucket):
        Pareto analysis that splits errors into rare vs high-frequency tiers.
    """
    t0 = time.perf_counter()

    # Pass 1: level-based reclassification
    classified = classify_templates_for_level(
        level, template_list, cluster_to_lines, orig_lines, event_ids
    )

    # Pass 2: frequency split on the Error templates (E-level or shifted errors)
    error_templates   = classified["error"]["templates"] if level != "E" else template_list
    error_c2l         = classified["error"]["cluster_to_lines"] if level != "E" else cluster_to_lines
    freq_result = classify_templates_by_frequency(
        error_templates, error_c2l, config.frequency
    )

    classify_ms = (time.perf_counter() - t0) * 1000
    return {
        "level_classified": classified,
        "freq_split": freq_result,
        "error_templates": len(error_templates),
        "rare_templates":  len(freq_result["rare"]["templates"]),
        "hf_templates":    len(freq_result["high_freq"]["templates"]),
    }, classify_ms


# ---------------------------------------------------------------------------
# STRATEGY A: One Drain3 model per FILE  (current implementation)
# ---------------------------------------------------------------------------

def strategy_per_file(
    preprocessed_lines: List[str],
    original_lines: List[str],
    config: PipelineConfig,
    level: str = "E",
) -> Dict:
    """
    All lines go into a single Drain3 miner, followed by production
    post-Drain3 classification passes.
    """
    t0 = time.perf_counter()

    cluster_sizes, template_list, event_ids = process_lines_with_drain3(
        preprocessed_lines, config
    )
    drain3_ms = (time.perf_counter() - t0) * 1000

    cluster_to_lines = build_cluster_to_lines(original_lines, event_ids)
    _, classify_ms = post_drain3_classify(
        level, template_list, cluster_to_lines, original_lines, event_ids, config
    )

    elapsed_ms = (time.perf_counter() - t0) * 1000

    return {
        "strategy": "per_file",
        "elapsed_ms": elapsed_ms,
        "drain3_ms": round(drain3_ms, 1),
        "classify_ms": round(classify_ms, 1),
        "template_list": template_list,
        "cluster_sizes": cluster_sizes,
        "event_ids": event_ids,
        "cluster_to_lines": cluster_to_lines,
        "stats": stats_summary(template_list),
    }


# ---------------------------------------------------------------------------
# STRATEGY B: One Drain3 model per CHUNK  (parallelised with ProcessPoolExecutor)
#
# ProcessPoolExecutor is used (not ThreadPoolExecutor) because Drain3 is
# CPU-bound (prefix-tree traversal + string matching). Each worker process
# has its own GIL so all cores can be utilised simultaneously.
#
# The worker (_drain3_chunk_worker) must be a module-level function so
# Python's multiprocessing pickler can serialise it to child processes.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# FILE-LEVEL PARALLEL WORKERS
#
# These two functions are module-level so Python's multiprocessing pickler
# can serialise them to child processes (same reason as _drain3_chunk_worker).
#
# _file_level_worker_pf  — per-FILE  Drain3: one model for all lines of a file
# _file_level_worker_pc  — per-CHUNK Drain3: split file into chunks, run each
#                          chunk's Drain3 sequentially (NOT spawning nested
#                          ProcessPools, which would deadlock or thrash).
# ---------------------------------------------------------------------------

def _file_level_worker_pf(args: Tuple) -> Dict:
    """Worker: per-FILE Drain3 + post-Drain3 classification on one file's lines."""
    import sys, time
    from pathlib import Path
    _dir = Path(__file__).resolve().parent
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))
    from ml_log_classification import (
        process_lines_with_drain3, build_cluster_to_lines,
        classify_templates_for_level, classify_templates_by_frequency,
        PipelineConfig, Drain3Config,
    )

    idx, pre_lines, orig_lines, level, d3_settings = args
    cfg = PipelineConfig()
    cfg.drain3 = Drain3Config(
        batch_size=d3_settings["batch_size"],
        similarity_threshold=d3_settings["similarity_threshold"],
        depth=d3_settings["depth"],
        max_children=d3_settings["max_children"],
    )
    t0 = time.perf_counter()
    _, template_list, event_ids = process_lines_with_drain3(pre_lines, cfg)
    cluster_to_lines = build_cluster_to_lines(orig_lines, event_ids)
    # Post-Drain3 classification passes
    classified = classify_templates_for_level(level, template_list, cluster_to_lines, orig_lines, event_ids)
    error_tmpls = classified["error"]["templates"] if level != "E" else template_list
    error_c2l   = classified["error"]["cluster_to_lines"] if level != "E" else cluster_to_lines
    classify_templates_by_frequency(error_tmpls, error_c2l, cfg.frequency)

    elapsed_ms = (time.perf_counter() - t0) * 1000
    total_lines = sum(t.get("size", 1) for t in template_list) if template_list else 0
    return {"idx": idx, "elapsed_ms": elapsed_ms, "total_lines": total_lines}


def _file_level_worker_pc(args: Tuple) -> Dict:
    """Worker: per-CHUNK Drain3 (sequential chunks) + post-Drain3 classification."""
    import sys, time
    from collections import defaultdict
    from pathlib import Path
    _dir = Path(__file__).resolve().parent
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))
    from ml_log_classification import (
        process_lines_with_drain3, build_cluster_to_lines,
        classify_templates_for_level, classify_templates_by_frequency,
        PipelineConfig, Drain3Config,
    )

    idx, pre_lines, orig_lines, level, chunk_size, d3_settings = args
    cfg = PipelineConfig()
    cfg.drain3 = Drain3Config(
        batch_size=d3_settings["batch_size"],
        similarity_threshold=d3_settings["similarity_threshold"],
        depth=d3_settings["depth"],
        max_children=d3_settings["max_children"],
    )
    t0 = time.perf_counter()

    # MAP: one Drain3 per chunk, sequential within this worker
    chunk_results = []
    for ci, i in enumerate(range(0, len(pre_lines), chunk_size)):
        c_pre = pre_lines[i:i + chunk_size]
        c_orig = orig_lines[i:i + chunk_size]
        if not c_pre:
            continue
        _, tmpl_list, event_ids = process_lines_with_drain3(c_pre, cfg)
        chunk_results.append({"idx": ci, "template_list": tmpl_list,
                               "event_ids": event_ids, "orig_lines": c_orig})

    # REDUCE: merge by template string
    tstr_to_cid: Dict = {}
    canonical: list = []
    canonical_c2l: Dict = defaultdict(list)
    global_event_ids: list = []
    for cr in sorted(chunk_results, key=lambda r: r["idx"]):
        local_to_can: Dict = {}
        for t in cr["template_list"]:
            tstr = t["template"]
            if tstr not in tstr_to_cid:
                cid = len(canonical)
                tstr_to_cid[tstr] = cid
                canonical.append({"id": cid, "template": tstr, "size": 0})
            cid = tstr_to_cid[tstr]
            canonical[cid]["size"] += t.get("size", 1)
            local_to_can[t["id"]] = cid
        for ol, eid in zip(cr["orig_lines"], cr["event_ids"]):
            can_id = local_to_can.get(eid)
            global_event_ids.append(can_id)
            if can_id is not None:
                canonical_c2l[can_id].append(ol)

    # Post-Drain3 classification passes
    c2l = dict(canonical_c2l)
    classified = classify_templates_for_level(level, canonical, c2l, orig_lines, global_event_ids)
    error_tmpls = classified["error"]["templates"] if level != "E" else canonical
    error_c2l   = classified["error"]["cluster_to_lines"] if level != "E" else c2l
    classify_templates_by_frequency(error_tmpls, error_c2l, cfg.frequency)

    elapsed_ms = (time.perf_counter() - t0) * 1000
    total_lines = sum(t.get("size", 1) for t in canonical) if canonical else 0
    return {"idx": idx, "elapsed_ms": elapsed_ms, "total_lines": total_lines}


def _drain3_chunk_worker(args: Tuple) -> Dict:
    """
    Module-level worker executed in a child process.

    Receives a tuple (idx, pre_lines, orig_lines, drain3_settings) so no
    PipelineConfig object needs to cross the process boundary (avoids pickle
    issues with complex dataclass hierarchies).
    """
    import sys
    from pathlib import Path
    _dir = Path(__file__).resolve().parent
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))

    from ML_LOG_CLASSIFICATION import process_lines_with_drain3, PipelineConfig, Drain3Config

    idx, pre_lines, orig_lines, d3_settings = args

    # Reconstruct a minimal config from the serialisable settings dict
    cfg = PipelineConfig()
    cfg.drain3 = Drain3Config(
        batch_size=d3_settings["batch_size"],
        similarity_threshold=d3_settings["similarity_threshold"],
        depth=d3_settings["depth"],
        max_children=d3_settings["max_children"],
    )

    cluster_sizes, template_list, event_ids = process_lines_with_drain3(pre_lines, cfg)
    return {
        "idx": idx,
        "template_list": template_list,
        "cluster_sizes": cluster_sizes,
        "event_ids": event_ids,
        "orig_lines": orig_lines,
    }


def strategy_per_chunk(
    preprocessed_lines: List[str],
    original_lines: List[str],
    chunk_size: int,
    config: PipelineConfig,
    max_workers: Optional[int] = None,
    level: str = "E",
) -> Dict:
    """
    Each chunk gets its own fresh Drain3 miner (parallel MAP), followed by
    a sequential REDUCE and production post-Drain3 classification passes.
    """
    t0 = time.perf_counter()

    # Serialise only what child processes need (avoids pickling PipelineConfig)
    d3_settings = {
        "batch_size": config.drain3.batch_size,
        "similarity_threshold": config.drain3.similarity_threshold,
        "depth": config.drain3.depth,
        "max_children": config.drain3.max_children,
    }

    # Build chunk pairs
    work_items: List[Tuple] = []
    for idx, i in enumerate(range(0, len(preprocessed_lines), chunk_size)):
        c_pre = preprocessed_lines[i:i + chunk_size]
        c_orig = original_lines[i:i + chunk_size]
        if c_pre:
            work_items.append((idx, c_pre, c_orig, d3_settings))

    num_workers = max_workers or min(multiprocessing.cpu_count(), len(work_items), 8)

    # --- MAP phase: parallel Drain3 per chunk ---
    per_chunk_results: List[Dict] = [None] * len(work_items)  # preserve order
    map_ms = 0.0
    t_map = time.perf_counter()

    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        future_to_pos = {
            executor.submit(_drain3_chunk_worker, item): pos
            for pos, item in enumerate(work_items)
        }
        for future in as_completed(future_to_pos):
            pos = future_to_pos[future]
            try:
                per_chunk_results[pos] = future.result()
            except Exception as exc:
                print(f"  [per_chunk] Chunk {work_items[pos][0]} failed: {exc}")

    map_ms = (time.perf_counter() - t_map) * 1000
    per_chunk_results = [r for r in per_chunk_results if r is not None]

    # --- REDUCE / RECONCILE phase (single-threaded, fast) ---
    t_reduce = time.perf_counter()

    template_str_to_canonical: Dict[str, int] = {}
    canonical_templates: List[Dict] = []
    canonical_cluster_to_lines: Dict[int, List[str]] = defaultdict(list)
    global_event_ids: List[Optional[int]] = []

    # Sort by original chunk index so global_event_ids order matches input lines
    for chunk_result in sorted(per_chunk_results, key=lambda r: r["idx"]):
        local_to_canonical: Dict[int, int] = {}
        for t in chunk_result["template_list"]:
            tstr = t["template"]
            if tstr not in template_str_to_canonical:
                cid = len(canonical_templates)
                template_str_to_canonical[tstr] = cid
                canonical_templates.append({"id": cid, "template": tstr, "size": 0})
            cid = template_str_to_canonical[tstr]
            canonical_templates[cid]["size"] += t.get("size", 1)
            local_to_canonical[t["id"]] = cid

        for orig_line, local_eid in zip(chunk_result["orig_lines"], chunk_result["event_ids"]):
            if local_eid is None or local_eid not in local_to_canonical:
                global_event_ids.append(None)
            else:
                can_id = local_to_canonical[local_eid]
                canonical_cluster_to_lines[can_id].append(orig_line)
                global_event_ids.append(can_id)

    reduce_ms = (time.perf_counter() - t_reduce) * 1000
    drain3_ms = (time.perf_counter() - t0) * 1000
    canonical_cluster_sizes = {t["id"]: t["size"] for t in canonical_templates}
    canonical_c2l = dict(canonical_cluster_to_lines)

    # --- Post-Drain3 classification (mirrors production) ---
    _, classify_ms = post_drain3_classify(
        level, canonical_templates, canonical_c2l,
        original_lines, global_event_ids, config
    )

    elapsed_ms = (time.perf_counter() - t0) * 1000

    return {
        "strategy": "per_chunk",
        "chunks_processed": len(per_chunk_results),
        "workers_used": num_workers,
        "map_ms": round(map_ms, 1),
        "reduce_ms": round(reduce_ms, 1),
        "drain3_ms": round(drain3_ms, 1),
        "classify_ms": round(classify_ms, 1),
        "elapsed_ms": elapsed_ms,
        "template_list": canonical_templates,
        "cluster_sizes": canonical_cluster_sizes,
        "event_ids": global_event_ids,
        "cluster_to_lines": canonical_c2l,
        "stats": stats_summary(canonical_templates),
    }


# ---------------------------------------------------------------------------
# Multi-file helpers  (split + 4 runner functions)
# ---------------------------------------------------------------------------

def _read_files_data(
    file_paths: List[Path],
    level: str,
    chunk_size: int,
    config: PipelineConfig,
) -> Tuple[List[Tuple[List[str], List[str]]], float]:
    """Read and categorise each file independently.
    Returns (files_data, total_read_ms) where files_data is a list of
    (pre_lines, orig_lines) tuples — one entry per file that has lines for `level`."""
    t0 = time.perf_counter()
    files_data = []
    for fp in file_paths:
        by_level = read_and_categorize_file(str(fp), chunk_size, config)
        ld = by_level.get(level, {})
        if isinstance(ld, dict) and "preprocessed" in ld:
            pre, orig = ld["preprocessed"], ld["original"]
        elif isinstance(ld, list):
            pre, orig = ld, ld
        else:
            pre, orig = [], []
        if pre:
            files_data.append((pre, orig))
    read_ms = (time.perf_counter() - t0) * 1000
    return files_data, read_ms


def _d3_settings(config: PipelineConfig) -> Dict:
    return {
        "batch_size":            config.drain3.batch_size,
        "similarity_threshold":  config.drain3.similarity_threshold,
        "depth":                 config.drain3.depth,
        "max_children":          config.drain3.max_children,
    }


def run_pf_sequential(
    files_data: List[Tuple[List[str], List[str]]],
    config: PipelineConfig,
    level: str = "E",
) -> Dict:
    """Strategy pf_seq: per-FILE Drain3 + classification, files sequential."""
    t0 = time.perf_counter()
    total_lines = 0
    drain3_ms_total = 0.0
    classify_ms_total = 0.0
    for pre, orig in files_data:
        res = strategy_per_file(pre, orig, config, level)
        total_lines += res["stats"]["total_lines"]
        drain3_ms_total += res.get("drain3_ms", 0.0)
        classify_ms_total += res.get("classify_ms", 0.0)
    return {
        "elapsed_ms":  (time.perf_counter() - t0) * 1000,
        "total_lines": total_lines,
        "drain3_ms":   round(drain3_ms_total, 1),
        "classify_ms": round(classify_ms_total, 1),
    }


def run_pf_parallel(
    files_data: List[Tuple[List[str], List[str]]],
    config: PipelineConfig,
    level: str = "E",
    max_workers: Optional[int] = None,
) -> Dict:
    """Strategy pf_par: per-FILE Drain3 + classification, files in parallel."""
    d3 = _d3_settings(config)
    args = [(i, pre, orig, level, d3) for i, (pre, orig) in enumerate(files_data)]
    nw = max_workers or min(multiprocessing.cpu_count(), len(files_data), 8)
    t0 = time.perf_counter()
    total_lines = 0
    with ProcessPoolExecutor(max_workers=nw) as pool:
        for res in pool.map(_file_level_worker_pf, args):
            total_lines += res["total_lines"]
    return {
        "elapsed_ms":  (time.perf_counter() - t0) * 1000,
        "total_lines": total_lines,
        "drain3_ms":   None,  # wall-clock parallel time; breakdown not meaningful
        "classify_ms": None,
    }


def run_pc_sequential(
    files_data: List[Tuple[List[str], List[str]]],
    config: PipelineConfig,
    chunk_size: int,
    level: str = "E",
    inner_workers: Optional[int] = None,
) -> Dict:
    """Strategy pc_seq: per-CHUNK Drain3 + classification, files sequential."""
    t0 = time.perf_counter()
    total_lines = 0
    drain3_ms_total = 0.0
    classify_ms_total = 0.0
    for pre, orig in files_data:
        res = strategy_per_chunk(pre, orig, chunk_size, config, inner_workers, level)
        total_lines += res["stats"]["total_lines"]
        drain3_ms_total += res.get("drain3_ms", 0.0)
        classify_ms_total += res.get("classify_ms", 0.0)
    return {
        "elapsed_ms":  (time.perf_counter() - t0) * 1000,
        "total_lines": total_lines,
        "drain3_ms":   round(drain3_ms_total, 1),
        "classify_ms": round(classify_ms_total, 1),
    }


def run_pc_parallel(
    files_data: List[Tuple[List[str], List[str]]],
    config: PipelineConfig,
    chunk_size: int,
    level: str = "E",
    max_workers: Optional[int] = None,
) -> Dict:
    """Strategy pc_par: per-CHUNK Drain3 + classification, files in parallel.
    Chunks sequential within each worker to avoid nested ProcessPools."""
    d3 = _d3_settings(config)
    args = [(i, pre, orig, level, chunk_size, d3) for i, (pre, orig) in enumerate(files_data)]
    nw = max_workers or min(multiprocessing.cpu_count(), len(files_data), 8)
    t0 = time.perf_counter()
    total_lines = 0
    with ProcessPoolExecutor(max_workers=nw) as pool:
        for res in pool.map(_file_level_worker_pc, args):
            total_lines += res["total_lines"]
    return {
        "elapsed_ms":  (time.perf_counter() - t0) * 1000,
        "total_lines": total_lines,
        "drain3_ms":   None,
        "classify_ms": None,
    }


# ---------------------------------------------------------------------------
# Comparison report
# ---------------------------------------------------------------------------

def print_comparison(results: Dict[str, Dict], level: str) -> None:
    """Print a side-by-side table for all 4 strategies."""
    labels = ["pf_seq", "pf_par", "pc_seq", "pc_par"]
    times  = {k: results[k]["elapsed_ms"] for k in labels}
    fastest = min(times, key=times.get)

    col = 13
    print("\n" + "=" * 80)
    print(f"  BENCHMARK RESULTS  —  Level: {level}")
    print("=" * 80)
    print(f"  {'Strategy':<20}  " + "  ".join(f"{l:>{col}}" for l in labels))
    print(f"  {'-'*20}  " + "  ".join("-" * col for _ in labels))
    print(f"  {'Total time (ms)':<20}  " +
          "  ".join(f"{times[l]:>{col}.1f}" for l in labels))
    print(f"  {'Total lines':<20}  " +
          "  ".join(f"{results[l]['total_lines']:>{col}}" for l in labels))
    print("=" * 80)
    print(f"  FASTEST: {fastest}  ({times[fastest]:.1f} ms)")
    for k in labels:
        if k != fastest:
            pct = (times[k] - times[fastest]) / times[fastest] * 100
            print(f"    {k:<10} is {pct:+.1f}% vs fastest")
    print("=" * 80)


def save_results(
    out_dir: Path,
    level: str,
    results: Dict[str, Dict],
    file_size_mb: float = 0.0,
) -> None:
    """Write stats JSON and .xlsx comparison for all 4 strategies."""
    out_dir.mkdir(parents=True, exist_ok=True)

    stats_path = out_dir / f"{level}_benchmark_stats.json"
    stats_path.write_text(
        json.dumps({k: {"elapsed_ms": v["elapsed_ms"], "total_lines": v["total_lines"]}
                    for k, v in results.items()}, indent=2),
        encoding="utf-8",
    )
    print(f"  Saved: {stats_path}")

    _save_excel(out_dir, level, results, file_size_mb)


# Each entry: (row_label, stats_key, fmt)
_SUMMARY_ROWS = [
    ("Total time (ms)", "elapsed_ms", "ms"),
]


def _fmt_val(v, fmt: str):
    """Format a raw value for display."""
    if v is None:
        return "—"
    if fmt == "ms":
        return round(float(v), 1)
    if fmt == "int":
        return int(v)
    if fmt == "float":
        return round(float(v), 1)
    return v


def _build_summary_rows(level: str, results: Dict[str, Dict], file_size_mb: float) -> List[List]:
    """Build rows: Level | Metric | pf_seq | pf_par | pc_seq | pc_par | Total lines | File size (MB) | Fastest."""
    strategy_order = ["pf_seq", "pf_par", "pc_seq", "pc_par"]
    total_lines = results.get("pf_seq", {}).get("total_lines", "—")
    rows = []
    for label, key, fmt in _SUMMARY_ROWS:
        vals = {s: _fmt_val(results[s].get(key), fmt) for s in strategy_order}
        numeric = {s: float(v) for s, v in vals.items() if v != "—"}
        fastest = min(numeric, key=numeric.get) if numeric else "—"
        rows.append([level, label] + [vals[s] for s in strategy_order] + [total_lines, round(file_size_mb, 2), fastest])
    return rows



def _save_excel(
    out_dir: Path,
    level: str,
    results: Dict[str, Dict],
    file_size_mb: float = 0.0,
) -> None:
    """Append a new row to the single combined benchmark_results.xlsx.
    Columns: Level | Metric | pf_seq | pf_par | pc_seq | pc_par | File size (MB) | Fastest."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    summary_rows = _build_summary_rows(level, results, file_size_mb)
    # Level | Metric | pf_seq | pf_par | pc_seq | pc_par | Total lines | File size (MB) | Fastest
    NCOLS = 9
    COL_HEADERS = ["Level", "Metric", "pf_seq", "pf_par", "pc_seq", "pc_par", "Total lines", "File size (MB)", "Fastest"]

    xlsx_path = out_dir / "benchmark_results.xlsx"
    if xlsx_path.exists():
        wb = openpyxl.load_workbook(str(xlsx_path))
    else:
        wb = openpyxl.Workbook()
        wb.active.title = "Summary"

    # ── Shared styles ─────────────────────────────────────────────────────────
    header_fill  = PatternFill("solid", fgColor="1F497D")  # dark blue
    pf_seq_fill  = PatternFill("solid", fgColor="DCE6F1")  # light blue
    pf_par_fill  = PatternFill("solid", fgColor="BDD7EE")  # medium blue
    pc_seq_fill  = PatternFill("solid", fgColor="E2EFDA")  # light green
    pc_par_fill  = PatternFill("solid", fgColor="C6EFCE")  # medium green
    # col_fills index matches column index (1-based): Level=None, Metric=None, pf_seq, pf_par, pc_seq, pc_par, size, fastest
    col_fills = [None, None, pf_seq_fill, pf_par_fill, pc_seq_fill, pc_par_fill, None, None, None]
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"),  bottom=Side(style="thin"),
    )

    def style_row(ws, row_idx: int, is_header: bool = False) -> None:
        for ci in range(1, NCOLS + 1):
            cell = ws.cell(row=row_idx, column=ci)
            cell.border = thin_border
            cell.alignment = Alignment(horizontal="center" if ci == 1 else
                                       "left" if ci == 2 else "center")
            if is_header:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = header_fill
                cell.alignment = Alignment(horizontal="center")
            elif col_fills[ci - 1]:
                cell.fill = col_fills[ci - 1]

    ws = wb["Summary"]

    # Write column header once (only if sheet is empty)
    if ws.max_row == 1 and ws.cell(1, 1).value is None:
        ws.append(COL_HEADERS)
        style_row(ws, 1, is_header=True)
        for ci, width in enumerate([8, 20, 13, 13, 13, 13, 14, 16, 14], start=1):
            ws.column_dimensions[get_column_letter(ci)].width = width
        ws.freeze_panes = "A2"

    for row in summary_rows:
        ws.append(row)
        style_row(ws, ws.max_row)

    wb.save(str(xlsx_path))
    total_data_rows = ws.max_row - 1  # subtract header
    print(f"  Saved: {xlsx_path}  ({total_data_rows} total rows)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Benchmark 4 strategies: per-file/per-chunk × sequential/parallel files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Multiple real files (recommended — each file processed as its own unit)
  python benchmark_drain3_strategies.py kubelet.log crio.log current.log

  # Single file only
  python benchmark_drain3_strategies.py kubelet.log
""",
    )
    parser.add_argument("log_files", nargs="+",
                        help="One or more log file paths to benchmark together")
    parser.add_argument("--chunk-size", type=int, default=10_000,
                        help="Lines per chunk for per-chunk strategies (default: 10000)")
    parser.add_argument("--level", default="all",
                        choices=["E", "W", "I", "UNKNOWN", "all"],
                        help="Log level to benchmark, or 'all' (default: all)")
    parser.add_argument("--output-dir", default="./benchmark_out",
                        help="Directory for result files (default: ./benchmark_out)")
    parser.add_argument("--runs", type=int, default=1,
                        help="Repetitions per strategy for averaging (default: 1)")
    parser.add_argument("--workers", type=int, default=None,
                        help="Max workers for file-level and chunk-level pools (default: auto)")
    parser.add_argument("--no-output", action="store_true",
                        help="Skip writing result files")
    args = parser.parse_args()

    ALL_LEVELS = ["E", "W", "I", "UNKNOWN"]
    levels_to_run = ALL_LEVELS if args.level == "all" else [args.level]

    # Validate all paths up front
    file_paths: List[Path] = []
    for raw in args.log_files:
        p = Path(raw).resolve()
        if not p.exists():
            print(f"[ERROR] File not found: {p}")
            sys.exit(1)
        file_paths.append(p)

    total_size_mb = sum(p.stat().st_size for p in file_paths) / (1024 * 1024)
    auto_workers = min(multiprocessing.cpu_count(), len(file_paths), 8)
    print(f"\nFiles    : {len(file_paths)}")
    for p in file_paths:
        print(f"           {p.name}  ({p.stat().st_size / (1024*1024):.1f} MB)")
    print(f"Total sz : {total_size_mb:.2f} MB")
    print(f"Levels   : {', '.join(levels_to_run)}")
    print(f"Chunk sz : {args.chunk_size:,} lines")
    print(f"Workers  : {args.workers or f'auto ({auto_workers})'}")
    print(f"Runs     : {args.runs}")

    config = get_config()
    config.chunk_size = args.chunk_size

    out_dir = Path(args.output_dir)
    skipped = []

    STRATEGY_LABELS = {
        "pf_seq": "per-FILE  Drain3, sequential files",
        "pf_par": "per-FILE  Drain3, parallel files  ",
        "pc_seq": "per-CHUNK Drain3, sequential files",
        "pc_par": "per-CHUNK Drain3, parallel files  ",
    }

    for level_idx, level in enumerate(levels_to_run, start=1):
        print(f"\n{'='*80}")
        print(f"  LEVEL {level}  ({level_idx}/{len(levels_to_run)})")
        print(f"{'='*80}")

        # ---- Read all files for this level (timed — I/O is part of real cost) ----
        print(f"  Reading {len(file_paths)} file(s) for level '{level}' ...")
        files_data, read_ms = _read_files_data(file_paths, level, args.chunk_size, config)

        if not files_data:
            print(f"  [SKIP] No lines found for level '{level}' in any file.")
            skipped.append(level)
            continue

        total_lines = sum(len(pre) for pre, _ in files_data)
        print(f"  Read in {read_ms:.0f} ms  |  {len(files_data)} file(s) with data  "
              f"|  {total_lines:,} lines total")

        results: Dict[str, Dict] = {}

        run_fns = [
            ("pf_seq", lambda fd, lv=level: run_pf_sequential(fd, config, lv)),
            ("pf_par", lambda fd, lv=level: run_pf_parallel(fd, config, lv, args.workers)),
            ("pc_seq", lambda fd, lv=level: run_pc_sequential(fd, config, args.chunk_size, lv, args.workers)),
            ("pc_par", lambda fd, lv=level: run_pc_parallel(fd, config, args.chunk_size, lv, args.workers)),
        ]

        for s_idx, (strat, fn) in enumerate(run_fns, start=1):
            print(f"\n  [{s_idx}/4] {STRATEGY_LABELS[strat]} ...")
            times = []
            last_r = None
            for run in range(args.runs):
                last_r = fn(files_data)
                times.append(last_r["elapsed_ms"])
                print(f"      Run {run+1}/{args.runs}: {last_r['elapsed_ms']:.0f} ms  |  "
                      f"{last_r['total_lines']:,} lines")
            results[strat] = {
                "elapsed_ms":  sum(times) / len(times) + read_ms,
                "total_lines": last_r["total_lines"],
            }

        print_comparison(results, level)

        if not args.no_output:
            print(f"\n  Saving results ...")
            save_results(out_dir, level, results, total_size_mb)

    if skipped:
        print(f"\n[INFO] Skipped levels with no data: {', '.join(skipped)}")
    if args.no_output:
        print("\n[INFO] File output skipped (--no-output)")


if __name__ == "__main__":
    main()
