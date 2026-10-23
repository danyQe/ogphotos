"""Sanity checks for the GitHub Pages site and its workflow."""

import re
from html.parser import HTMLParser
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "site" / "index.html").read_text(encoding="utf-8")


def test_required_content() -> None:
    assert (
        "OGphotos — clean up your Google Takeout export. Duplicates, similar shots, faces. "
        "100% offline." in HTML
    )
    assert "Not affiliated with Google. Google Photos is a trademark of Google LLC." in HTML
    for name in (
        "OGphotos-Setup-&lt;ver&gt;.exe",
        "OGphotos-&lt;ver&gt;.dmg",
        "ogphotos-&lt;ver&gt;-linux.tar.gz",
    ):
        assert name in HTML
    assert "https://github.com/danyQe/ogphotos/releases/latest" in HTML


def test_no_insecure_or_third_party_assets() -> None:
    assert "http://" not in HTML
    assert not re.search(r"<script[^>]+src=", HTML, re.I)
    assert not re.search(r"<link[^>]+rel=[\"']stylesheet", HTML, re.I)
    assert not re.search(r"@import|url\(\s*[\"']?https?:", HTML, re.I)
    for url in re.findall(r"https://[^\s\"'<>)]+", HTML):
        assert url.startswith(("https://github.com/", "https://api.github.com/")), url


def test_html_tags_balanced() -> None:
    void = {"meta", "link", "br", "img", "hr", "input"}

    class Checker(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.stack: list[str] = []

        def handle_starttag(self, tag: str, attrs: list) -> None:
            if tag not in void:
                self.stack.append(tag)

        def handle_endtag(self, tag: str) -> None:
            assert self.stack
            assert self.stack.pop() == tag

    checker = Checker()
    checker.feed(HTML)
    assert checker.stack == []


def test_pages_workflow() -> None:
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8"))
    steps = [s for job in wf["jobs"].values() for s in job["steps"]]
    upload = next(s for s in steps if s.get("uses", "").startswith("actions/upload-pages-artifact"))
    assert upload["with"]["path"] == "site"
