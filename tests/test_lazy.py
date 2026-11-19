#!/usr/bin/env python3
"""Focused checks for the lifecycle change (run: python3 tests/test_lazy.py)."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import bottle  # noqa: E402


def test_compile_is_deferred_until_match():
    router = bottle.Router()
    router.add("/a/<id>", "GET", "a")
    router.add("/b/<id>", "GET", "b")
    assert router.dyna_regexes == {}, "add() must not compile routes"
    assert router.match({"REQUEST_METHOD": "GET", "PATH_INFO": "/b/7"}) == ("b", {"id": "7"})
    assert "GET" in router.dyna_regexes and len(router.dyna_regexes["GET"]) == 1


def test_routes_added_after_a_match_are_visible():
    router = bottle.Router()
    router.add("/a/<id>", "GET", "a")
    assert router.match({"REQUEST_METHOD": "GET", "PATH_INFO": "/a/1"})[0] == "a"
    router.add("/b/<id>", "GET", "b")
    assert router.match({"REQUEST_METHOD": "GET", "PATH_INFO": "/b/2"})[0] == "b"


def test_wrong_method_still_reports_405():
    router = bottle.Router()
    router.add("/only-get/<id>", "GET", "target")
    try:
        router.match({"REQUEST_METHOD": "POST", "PATH_INFO": "/only-get/1"})
    except bottle.HTTPError as exc:
        assert exc.status_code == 405, exc.status_code
    else:
        raise AssertionError("expected 405")


def test_invalid_pattern_still_raises_in_add():
    router = bottle.Router()
    try:
        router.add("/x/<:re:(>/", "GET", "bad")
    except Exception:
        return
    raise AssertionError("invalid route pattern must fail in add()")


def test_overwrite_after_match():
    router = bottle.Router()
    router.add("/a/<id>", "GET", "first")
    assert router.match({"REQUEST_METHOD": "GET", "PATH_INFO": "/a/1"})[0] == "first"
    router.add("/a/<id>", "GET", "second")
    assert router.match({"REQUEST_METHOD": "GET", "PATH_INFO": "/a/1"})[0] == "second"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
    print("%d tests passed" % len(tests))