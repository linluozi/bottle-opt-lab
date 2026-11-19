#!/usr/bin/env python3
"""Measure Bottle Router registration and dispatch, baseline vs lazy-compile.

The baseline implementation is reconstructed from the patched src/bottle.py by
reverse-applying patch.diff to a temporary copy.  No upstream file is vendored,
and the reconstruction is checked byte-for-byte against the diff before use.

Usage:
    python3 benchmark/bench.py --impl baseline  --out benchmark/baseline.json
    python3 benchmark/bench.py --impl optimized --out benchmark/optimized.json
"""
import argparse
import importlib.util
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "bottle.py"
PATCH = ROOT / "patch.diff"
CORPUS = ROOT / "benchmark" / "corpus_real.json"
WARMUP = 3
SAMPLES = 5
MATCH_REPS = 200
SCALES = ("synthetic_100", "synthetic_500", "synthetic_2000", "real_digitalocean")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_impl(impl):
    if impl == "optimized":
        return load_module("bottle_optimized", SRC), None
    tmp = Path(tempfile.mkdtemp(prefix="bottle-baseline-"))
    shutil.copy(SRC, tmp / "bottle.py")
    proc = subprocess.run(
        ["git", "apply", "-R", "-p1", str(PATCH)],
        cwd=tmp, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise SystemExit("reverse patch failed: " + proc.stderr)
    return load_module("bottle_baseline", tmp / "bottle.py"), tmp


def cpu_model():
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def mem_total_kb():
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1])
    except OSError:
        pass
    return None


def synthetic_rules(n):
    return ["/api/v%d/resource%d/<id>" % (i % 3, i) for i in range(n)]


def real_entries():
    data = json.loads(CORPUS.read_text(encoding="utf-8"))
    return data["entries"], data


def stat_block(values):
    ordered = sorted(values)
    q25, _, q75 = statistics.quantiles(ordered, n=4, method="inclusive")
    mean = statistics.mean(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    return {
        "median": statistics.median(values),
        "p25": q25,
        "p75": q75,
        "mean": mean,
        "rel_sd_pct": (sd / mean * 100.0) if mean else 0.0,
        "n": len(values),
    }


def one_sample(module, rules, match_url):
    router = module.Router()
    t0 = time.perf_counter()
    for index, rule in enumerate(rules):
        router.add(rule, "GET", index)
    registration = time.perf_counter() - t0

    env = {"REQUEST_METHOD": "GET", "PATH_INFO": match_url}
    t0 = time.perf_counter()
    router.match(env)
    ready = time.perf_counter() - t0

    t0 = time.perf_counter()
    for _ in range(MATCH_REPS):
        router.match(env)
    match_each = (time.perf_counter() - t0) / MATCH_REPS
    return registration, ready, match_each


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--impl", choices=("baseline", "optimized"), required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--raw-dir", default=str(ROOT / "benchmark" / "raw"))
    args = ap.parse_args()

    module, tmp = load_impl(args.impl)
    entries, corpus_meta = real_entries()
    real_rules = [e["rule"] for e in entries]
    real_dynamic = [e["url"] for e in entries if "<" in e["rule"]]
    match_real = real_dynamic[-1]

    scales = {}
    for name in SCALES:
        if name.startswith("synthetic_"):
            n = int(name.rsplit("_", 1)[1])
            rules = synthetic_rules(n)
            match_url = "/api/v%d/resource%d/42" % ((n - 1) % 3, n - 1)
            kind = "synthetic"
        else:
            rules = real_rules
            match_url = match_real
            kind = "real"
        registration, ready, match_each = [], [], []
        for index in range(WARMUP + SAMPLES):
            reg, rdy, mat = one_sample(module, rules, match_url)
            warmup = index < WARMUP
            raw = {
                "impl": args.impl,
                "scale": name,
                "sample": index,
                "warmup": warmup,
                "n_routes": len(rules),
                "n_dynamic": sum("<" in r for r in rules),
                "registration_s": reg,
                "ready_s": reg + rdy,
                "first_match_s": rdy,
                "match_each_s": mat,
                "match_reps": MATCH_REPS,
                "python": platform.python_version(),
            }
            Path(args.raw_dir).mkdir(parents=True, exist_ok=True)
            (Path(args.raw_dir) / ("%s-%s-%d.json" % (args.impl, name, index))).write_text(
                json.dumps(raw, sort_keys=True), encoding="utf-8")
            if not warmup:
                registration.append(reg)
                ready.append(reg + rdy)
                match_each.append(mat)
        scales[name] = {
            "kind": kind,
            "n_routes": len(rules),
            "n_dynamic": sum("<" in r for r in rules),
            "warmup": WARMUP,
            "samples": SAMPLES,
            "registration_ms": stat_block([v * 1000 for v in registration]),
            "ready_ms": stat_block([v * 1000 for v in ready]),
            "match_us": stat_block([v * 1e6 for v in match_each]),
        }
        print("%-20s reg median %10.3f ms   ready median %10.3f ms   match %8.2f us"
              % (name, scales[name]["registration_ms"]["median"],
                 scales[name]["ready_ms"]["median"],
                 scales[name]["match_us"]["median"]), flush=True)

    upstream = None
    log = ROOT / "benchmark" / "upstream_tests.json"
    if log.exists():
        upstream = json.loads(log.read_text(encoding="utf-8"))
    result = {
        "impl": args.impl,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "command": " ".join(sys.argv),
        "python": sys.version,
        "python_version": platform.python_version(),
        "cpu": cpu_model(),
        "mem_total_kb": mem_total_kb(),
        "seed": "no RNG: route sets and request paths are deterministic index constructions",
        "corpus": {k: corpus_meta[k] for k in ("source", "count", "dynamic_count", "static_count", "sha256_rules")},
        "upstream_tests": upstream,
        "scales": scales,
    }
    Path(args.out).write_text(json.dumps(result, indent=1, sort_keys=True), encoding="utf-8")
    if tmp is not None:
        shutil.rmtree(tmp, ignore_errors=True)
    print("wrote", args.out)


if __name__ == "__main__":
    main()