"""keyAction() in app/static/review.js, run under Node (skipped if Node is missing)."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

REVIEW_JS = Path(__file__).parent.parent / "app" / "static" / "review.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def key_action(key: str, **mods: Any) -> str | None:
    """Load review.js into a VM with no DOM (the UI part bails out) and call keyAction."""
    expr = f"keyAction({json.dumps(key)}, {json.dumps(mods)})"
    script = f"""
const vm = require("vm");
const fs = require("fs");
const ctx = {{}};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync({json.dumps(str(REVIEW_JS))}, "utf8"), ctx);
process.stdout.write(JSON.stringify(vm.runInContext({json.dumps(expr)}, ctx)));
"""
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.parametrize(
    ("key", "mods", "expected"),
    [
        ("k", {}, "keep-best"),
        ("K", {}, "keep-best"),
        ("Enter", {}, "apply"),
        ("Enter", {"tag": "BUTTON"}, None),  # let the focused button act
        ("Enter", {"tag": "A"}, None),
        ("ArrowRight", {}, "next"),
        ("ArrowLeft", {}, "prev"),
        ("1", {}, "toggle-1"),
        ("9", {}, "toggle-9"),
        ("0", {}, None),
        ("k", {"ctrl": True}, None),
        ("k", {"tag": "INPUT"}, None),
        ("x", {}, None),
    ],
)
def test_key_action(key: str, mods: dict[str, Any], expected: str | None) -> None:
    assert key_action(key, **mods) == expected
