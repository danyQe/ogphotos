"""Pure helpers in app/static/upload.js, run under Node (skipped if Node is missing)."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

UPLOAD_JS = Path(__file__).parent.parent / "app" / "static" / "upload.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def run_js(expr: str) -> Any:
    """Load upload.js into a VM with no DOM (the UI part bails out) and evaluate expr."""
    script = f"""
const vm = require("vm");
const fs = require("fs");
const ctx = {{ document: {{ getElementById: () => null }} }};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync({json.dumps(str(UPLOAD_JS))}, "utf8"), ctx);
process.stdout.write(JSON.stringify(vm.runInContext({json.dumps(expr)}, ctx)));
"""
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.parametrize(
    ("media", "sidecar", "expected"),
    [
        ("1.jpg", "1.jpg.json", True),
        ("1.jpg", "1.jpg.supplemental-metadata.json", True),
        ("1.jpg", "10.jpg.json", False),  # no dot boundary → not a match
        ("1.jpg", "1.json", False),
        ("a.jpg", "a.jpg.txt", False),
        ("IMG(1).jpg", "IMG.jpg(1).json", True),
        ("IMG-edited.jpg", "IMG.jpg.supplemental-metadata.json", True),
        ("IMG-edited.jpg", "OTHER.jpg.json", False),
        ("IMG(1).jpg", "IMG.jpg.supplemental-metadata(1).json", True),
        ("IMG(1).jpg", "IMG.jpg.json", False),
        ("IMG(1).jpg", "IMG.jpg(11).json", False),
    ],
)
def test_sidecar_matches(media: str, sidecar: str, expected: bool) -> None:
    assert run_js(f"sidecarMatches({json.dumps(media)}, {json.dumps(sidecar)})") is expected


@pytest.mark.parametrize(
    ("sizes", "expected"),
    [
        ([10, 10, 10, 10], [3, 1]),  # count cap
        ([60, 60, 10], [1, 2]),  # byte cap
        ([500, 10], [1, 1]),  # oversized file goes alone
        ([], []),
    ],
)
def test_make_batches(sizes: list[int], expected: list[int]) -> None:
    expr = f"makeBatches({json.dumps(sizes)}.map((size) => ({{ size }})), 3, 100)"
    assert run_js(f"{expr}.map((b) => b.length)") == expected
