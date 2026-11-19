#!/usr/bin/env python3
"""Peak RSS for a 2,000-route registration + 200 matches, three processes each.

Run:
    python3 benchmark/memcheck.py
Child mode is internal.  ru_maxrss is the process high-water mark in KiB.
"""
import argparse
import json
import resource
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench import load_impl, synthetic_rules  # noqa: E402


def child(impl, n):
    module, tmp = load_impl(impl)
    router = module.Router()
    for index, rule in enumerate(synthetic_rules(n)):
        router.add(rule, "GET", index)
    for _ in range(200):
        router.match({"REQUEST_METHOD": "GET",
                      "PATH_INFO": "/api/v%d/resource%d/42" % ((n - 1) % 3, n - 1)})
    print(json.dumps({
        "impl": impl,
        "n_routes": n,
        "maxrss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--child", action="store_true")
    ap.add_argument("--impl", choices=("baseline", "optimized"))
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "memcheck.json"))
    args = ap.parse_args()
    if args.child:
        child(args.impl, args.n)
        return
    result = {"n_routes": args.n, "reps": 3, "impls": {}}
    for impl in ("baseline", "optimized"):
        runs = []
        for _ in range(3):
            proc = subprocess.run([sys.executable, __file__, "--child", "--impl", impl,
                                   "--n", str(args.n)], capture_output=True, text=True, check=True)
            runs.append(json.loads(proc.stdout.strip().splitlines()[-1])["maxrss_kb"])
        result["impls"][impl] = {"maxrss_kb": runs, "median_kb": sorted(runs)[1]}
    base = result["impls"]["baseline"]["median_kb"]
    opt = result["impls"]["optimized"]["median_kb"]
    result["ratio_opt_over_base"] = opt / base
    Path(args.out).write_text(json.dumps(result, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()