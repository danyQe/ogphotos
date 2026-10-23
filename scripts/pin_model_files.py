"""Print pinned `ModelFile(...)` lines for a Hugging Face repo at a fixed revision.

Usage:
    uv run python scripts/pin_model_files.py REPO_ID REVISION [--base-url URL]

Sizes and sha256 come from the Hub's LFS metadata; small non-LFS files are
downloaded and hashed. Set HF_TOKEN for gated repos. `.gitattributes` and
`README.md` are skipped. Paste the output into app/models_registry.py."""

import argparse
import hashlib
import os
import urllib.request

from huggingface_hub import HfApi

SKIP = {".gitattributes", "README.md"}


def hash_remote(url: str, token: str | None) -> str:
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    h = hashlib.sha256()
    with urllib.request.urlopen(req) as resp:  # noqa: S310 - fixed https URL
        while chunk := resp.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo_id")
    ap.add_argument("revision")
    ap.add_argument("--base-url", help="mirror base URL replacing the Hugging Face resolve URL")
    args = ap.parse_args()

    token = os.environ.get("HF_TOKEN")
    info = HfApi(token=token).model_info(args.repo_id, revision=args.revision, files_metadata=True)
    for sib in sorted(info.siblings or [], key=lambda s: s.rfilename):
        path = sib.rfilename
        if path in SKIP:
            continue
        if args.base_url:
            url = f"{args.base_url.rstrip('/')}/{path.replace('/', '__')}"
        else:
            url = f"https://huggingface.co/{args.repo_id}/resolve/{args.revision}/{path}"
        if sib.lfs is not None:
            digest, size = sib.lfs.sha256, sib.lfs.size
        else:
            digest, size = hash_remote(url, token), sib.size
        print(f'    ModelFile("{path}", "{url}", "{digest}", {size}),')


if __name__ == "__main__":
    main()
