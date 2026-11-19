#!/usr/bin/env python3
"""Differential test: upstream eager-compile Router vs lazy-compile Router.

The baseline module is reconstructed from patch.diff: src/bottle.py is copied to
a temp directory and the patch is reverse-applied with `git apply -R`.  That
yields the exact upstream file (verified by the caller in README), so this test
compares the published source against the code it replaced rather than against
a hand-written approximation.

Every scenario is a distinct sequence of add/match operations.  A "sample" is a
distinct (router history, request) pair; the count is the size of a sha256 set,
not a loop count.  Results are serialized with sorted keys and compared
byte-for-byte.
"""
import hashlib
import importlib.util
import json
import random
import shutil
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "bottle.py"
PATCH = ROOT / "patch.diff"
CORPUS = ROOT / "benchmark" / "corpus_real.json"
SEED = 20261119
UPSTREAM_SHA256 = "0a491959da988c681cc599c7698be5bf1d6d1ef5b38c40f1afa9e9c4bf9bc968"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_baseline():
    tmp = Path(tempfile.mkdtemp(prefix="bottle-diff-base-"))
    shutil.copy(SRC, tmp / "bottle.py")
    proc = subprocess.run(["git", "apply", "-R", "-p1", str(PATCH)],
                          cwd=tmp, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit("reverse patch failed: " + proc.stderr)
    digest = hashlib.sha256((tmp / "bottle.py").read_bytes()).hexdigest()
    if digest != UPSTREAM_SHA256:
        raise SystemExit("reverse patch produced %s, expected upstream %s" % (digest, UPSTREAM_SHA256))
    return load_module("bottle_diff_baseline", tmp / "bottle.py"), tmp


def execute(module, spec):
    router = module.Router(strict=spec.get("strict", False))
    output = []
    for op in spec["ops"]:
        if op[0] == "filter":
            _, name, pattern = op
            router.add_filter(name, lambda conf, p=pattern: (p, None, None))
        elif op[0] == "add":
            _, rule, method, target, name = op
            try:
                router.add(rule, method, target, name)
                output.append({"add": "ok"})
            except Exception as exc:  # add-time syntax errors must agree too
                output.append({"add": "error", "type": type(exc).__name__,
                               "msg": str(exc)})
        elif op[0] == "match":
            _, method, path = op
            try:
                target, args = router.match({"REQUEST_METHOD": method, "PATH_INFO": path})
                output.append({"match": "ok", "target": target, "args": args})
            except Exception as exc:
                output.append({"match": "error", "type": type(exc).__name__,
                               "status": getattr(exc, "status_code", None),
                               "body": str(getattr(exc, "body", ""))})
        else:
            raise AssertionError(op)
    return json.dumps(output, sort_keys=True, ensure_ascii=False, default=repr)


def concrete(rule, i):
    """Turn a Bottle rule into one concrete request path."""
    if "\\:" in rule:
        rule = rule.replace("\\:", ":")
    import re
    def sub(match):
        name = match.group(1)
        if name.endswith(":int"):
            return str(7 + i)
        if name.endswith(":path"):
            return "seg%d/leaf" % i
        if name.endswith(":hex"):
            return "deadbeef"
        return "v%d" % i
    return re.sub(r"<([^>]*)>", sub, rule)


KINDS = ("static", "dyn", "int", "path", "anon", "colon", "unicode", "custom")


def make_rule(kind, i):
    if kind == "static":
        return "/s%d" % i
    if kind == "dyn":
        return "/d%d/<id>" % i
    if kind == "int":
        return "/n%d/<num:int>" % i
    if kind == "path":
        return "/p%d/<rest:path>" % i
    if kind == "anon":
        return "/a%d/<>" % i
    if kind == "colon":
        return "/c%d/act\\:run" % i
    if kind == "unicode":
        return "/u%d/<name>" % i
    if kind == "custom":
        return "/f%d/<hex:hex>" % i
    raise AssertionError(kind)


def scenario(rng, index):
    """A distinct add/match history.  index is folded into every target."""
    n = rng.choice([1, 2, 3, 5, 8, 12, 20, 30, 40])
    ops = [("filter", "hex", "[0-9a-f]+")]
    added = []
    for i in range(n):
        kind = rng.choice(KINDS)
        method = rng.choice(["GET", "GET", "GET", "POST", "ANY", "HEAD"])
        rule = make_rule(kind, i)
        target = "t%d-%d" % (index, i)
        ops.append(("add", rule, method, target, None))
        added.append((rule, method, target))
    # One overwrite: exercises the same-flatpat replacement branch.
    if added and rng.random() < 0.5:
        rule, method, _ = rng.choice(added)
        ops.append(("add", rule, method, "t%d-overwrite" % index, None))
    # A generic route added last: must not steal earlier matches.
    if rng.random() < 0.4:
        ops.append(("add", "/<:>", "GET", "t%d-generic" % index, None))
    for _ in range(3):
        if not added:
            ops.append(("match", "GET", "/none/%d" % index))
            continue
        rule, method, _ = rng.choice(added)
        request_method = "GET" if method in ("GET", "ANY") else method
        ops.append(("match", request_method, concrete(rule, index)))
    ops.append(("match", "GET", "/definitely/missing/%d" % index))
    ops.append(("match", "POST", "/definitely/missing/%d" % index))
    return {"ops": ops, "strict": rng.random() < 0.1}


def boundary_scenarios():
    out = []
    for count in (98, 99, 100, 197, 198, 199):
        ops = []
        for i in range(count):
            ops.append(("add", "/b%d/<id>" % i, "GET", "b%d" % i, None))
        ops.append(("match", "GET", "/b%d/x" % (count - 1)))
        ops.append(("add", "/b%d/<id>" % count, "GET", "b%d" % count, None))
        ops.append(("match", "GET", "/b%d/x" % count))
        ops.append(("match", "GET", "/b0/x"))
        out.append({"ops": ops, "strict": False})
    return out


def real_scenario():
    data = json.loads(CORPUS.read_text(encoding="utf-8"))
    ops = []
    for index, entry in enumerate(data["entries"]):
        ops.append(("add", entry["rule"], "GET", "real%d" % index, None))
    for entry in data["entries"]:
        ops.append(("match", "GET", entry["url"]))
    ops.append(("match", "GET", "/not/a/real/path"))
    ops.append(("match", "POST", data["entries"][0]["url"]))
    ops.append(("match", "HEAD", data["entries"][-1]["url"]))
    return {"ops": ops, "strict": False}


def main():
    baseline, tmp = load_baseline()
    optimized = load_module("bottle_diff_opt", SRC)
    rng = random.Random(SEED)
    scenarios = [scenario(rng, i) for i in range(400)]
    scenarios += boundary_scenarios()
    scenarios.append(real_scenario())

    seen = set()
    comparisons = 0
    mismatches = []
    for index, spec in enumerate(scenarios):
        a = execute(baseline, spec).encode()
        b = execute(optimized, spec).encode()
        comparisons += 1
        if a != b:
            mismatches.append(index)
        history = hashlib.sha256(json.dumps(spec["ops"], sort_keys=True).encode()).hexdigest()
        for op in spec["ops"]:
            if op[0] == "match":
                seen.add(hashlib.sha256((history + "\x00" + json.dumps(op)).encode()).hexdigest())
    result = {
        "seed": SEED,
        "scenarios": len(scenarios),
        "distinct_samples": len(seen),
        "comparisons": comparisons,
        "mismatches": len(mismatches),
        "mismatch_scenarios": mismatches[:10],
        "real_corpus_routes": json.loads(CORPUS.read_text(encoding="utf-8"))["count"],
        "baseline": "src/bottle.py + patch.diff reverse-applied in a temp dir",
        "baseline_sha256": UPSTREAM_SHA256,
        "comparison": "sorted-key JSON, byte-for-byte",
    }
    print(json.dumps(result, sort_keys=True))
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if mismatches else 0


if __name__ == "__main__":
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sys.exit(main())