"""Tests for raise_bash_timeout: the floor depends on the command, and only an
unset timeout is ever filled in. No network, no dependencies beyond pytest."""
import importlib.util
import pathlib

import pytest

_HERE = pathlib.Path(__file__).parent
_spec = importlib.util.spec_from_file_location(
    "raise_bash_timeout", _HERE / "raise_bash_timeout.py")
rbt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rbt)


@pytest.mark.parametrize("cmd", [
    "python3 -m pytest -q tests/",
    "pytest",
    "npm test",
    "npm run test -- --watch=false",
    "npx playwright test",
    "npx vitest run",
    "jest --ci",
])
def test_test_runners_get_the_long_floor(cmd):
    assert rbt._floor_for(cmd) == 600_000


@pytest.mark.parametrize("cmd,floor", [
    ("wait-for.sh --timeout 300 'test -f /tmp/out.json'", 315_000),
    ("bash wait-for.sh --timeout 120 --long 'true'", 135_000),
    ("wait-for.sh --sleep 200", 215_000),
    # Capped at the hook's own ceiling.
    ("wait-for.sh --timeout 5000 'true'", rbt.MAX_TIMEOUT_MS),
])
def test_long_wait_for_gets_a_floor_above_the_wait(cmd, floor):
    assert rbt._floor_for(cmd) == floor


@pytest.mark.parametrize("cmd", [
    "wait-for.sh --sleep 20",
    "wait-for.sh --timeout 60 'true'",
    "wait-for.sh 'grep -q DONE /tmp/log'",
    "ls -la",
    "git status",
])
def test_other_commands_get_no_floor(cmd):
    assert rbt._floor_for(cmd) is None
