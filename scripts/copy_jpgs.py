"""Copy all JPG files from a folder tree into a single flat folder.

Usage:
    python copy_jpgs.py <source_dir> <dest_dir>

Name collisions are resolved by appending _1, _2, ... to the filename.
"""

import argparse
import shutil
import sys
from pathlib import Path

EXTENSIONS = {".mp4", ".mkv"}


def unique_path(dest_dir: Path, name: str) -> Path:
    target = dest_dir / name
    stem, suffix = target.stem, target.suffix
    i = 1
    while target.exists():
        target = dest_dir / f"{stem}_{i}{suffix}"
        i += 1
    return target


def copy_jpgs(source: Path, dest: Path) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve()
    count = 0
    for path in source.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
            continue
        # Skip files already inside the destination (if dest is under source)
        if dest_resolved in path.resolve().parents:
            continue
        target = unique_path(dest, path.name)
        shutil.copy2(path, target)  # copy2 keeps timestamps/metadata
        count += 1
        print(f"{path} -> {target}")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy all JPG files recursively into one folder.")
    parser.add_argument("source", type=Path, help="Root folder to search")
    parser.add_argument("dest", type=Path, help="Folder to copy JPGs into")
    args = parser.parse_args()

    if not args.source.is_dir():
        sys.exit(f"Source folder not found: {args.source}")

    count = copy_jpgs(args.source, args.dest)
    print(f"\nCopied {count} file(s) to {args.dest}")


if __name__ == "__main__":
    main()
