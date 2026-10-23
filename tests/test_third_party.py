import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from gen_third_party import pick_license, pick_url  # noqa: E402

NOTICES = ROOT / "THIRD_PARTY_LICENSES.md"


def test_third_party_lists_models_and_htmx() -> None:
    text = NOTICES.read_text(encoding="utf-8")
    assert "Apache-2.0" in text
    assert "EmbeddingGemma" in text
    assert "non-commercial" in text
    assert "htmx" in text
    assert "BSD 2-Clause" in text
    assert "| fastapi " in text
    assert "ogphotos |" not in text


def test_third_party_credits_bundled_uv() -> None:
    version = (ROOT / "packaging" / "uv-version.txt").read_text(encoding="utf-8").strip()
    text = NOTICES.read_text(encoding="utf-8")
    assert f"- uv {version} (`bin/`), MIT OR Apache-2.0, https://github.com/astral-sh/uv" in text


def test_pick_license_prefers_expression_then_short_license_then_classifiers() -> None:
    assert pick_license({"license_expression": "MIT", "license": "other"}) == "MIT"
    assert pick_license({"license": "BSD-3-Clause"}) == "BSD-3-Clause"
    long_text = {
        "license": "x" * 81,
        "classifiers": [
            "License :: OSI Approved :: MIT License",
            "License :: OSI Approved :: Apache Software License",
            "Programming Language :: Python",
        ],
    }
    assert pick_license(long_text) == "MIT License, Apache Software License"
    assert pick_license({"license": "line1\nline2"}) == "UNKNOWN"
    assert pick_license({}) == "UNKNOWN"


def test_pick_url_preference_order() -> None:
    info = {"project_urls": {"Docs": "d", "SOURCE": "s", "homepage": "h"}, "home_page": "hp"}
    assert pick_url(info, "pkg") == "h"
    assert pick_url({"project_urls": {"Docs": "d"}, "home_page": "hp"}, "pkg") == "hp"
    assert pick_url({}, "pkg") == "https://pypi.org/project/pkg/"
