#!/usr/bin/env python3
"""Fail closed when a directory intended for sharing appears to contain private data."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


TEXT_SUFFIXES = {
    ".md", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx", ".html",
    ".css", ".yaml", ".yml", ".toml", ".ini", ".json", ".svg",
}
RAW_DATA_SUFFIXES = {".xlsx", ".xls", ".csv", ".tsv", ".pdf", ".sqlite", ".db"}
IGNORE_NAMES = {"privacy_audit.py", ".DS_Store"}
PATTERNS = {
    "email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    "mainland_phone": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "long_identifier": re.compile(r"(?<!\d)\d{15,19}(?!\d)"),
    "mac_home_path": re.compile(r"/Users/[^/\s]+/"),
    "linux_home_path": re.compile(r"/home/[^/\s]+/"),
    "windows_home_path": re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+\\"),
    "cloud_share_url": re.compile(r"https?://[^\s\"']+(?:share|sites|drive|onedrive|dropbox)[^\s\"']*", re.I),
}


def iter_files(root: Path):
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*")):
        if path.is_file() and ".git" not in path.parts and "__pycache__" not in path.parts:
            yield path


def scan(root: Path, max_bytes: int) -> dict:
    findings: list[dict] = []
    files = list(iter_files(root))
    for path in files:
        rel = str(path.relative_to(root)) if root.is_dir() else path.name
        if path.name in IGNORE_NAMES:
            continue
        size = path.stat().st_size
        if size > max_bytes:
            findings.append({"severity": "block", "file": rel, "kind": "large_file", "detail": size})
        if path.suffix.lower() in RAW_DATA_SUFFIXES:
            findings.append({"severity": "block", "file": rel, "kind": "possible_raw_data", "detail": path.suffix.lower()})
        if path.suffix.lower() not in TEXT_SUFFIXES or size > 5_000_000:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                findings.append({"severity": "review", "file": rel, "kind": "unreadable_text", "detail": "encoding"})
                continue
        for kind, pattern in PATTERNS.items():
            matches = list(pattern.finditer(content))
            if matches:
                lines = sorted({content.count("\n", 0, match.start()) + 1 for match in matches[:10]})
                findings.append({"severity": "block", "file": rel, "kind": kind, "detail": {"count": len(matches), "lines": lines}})
    return {
        "root": str(root),
        "files_scanned": len(files),
        "blocking_findings": sum(item["severity"] == "block" for item in findings),
        "review_findings": sum(item["severity"] == "review" for item in findings),
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan a shareable skill or report directory for common privacy leaks.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--report", type=Path, help="Optional JSON report path outside the scanned directory.")
    parser.add_argument("--max-bytes", type=int, default=2_000_000)
    args = parser.parse_args()
    root = args.path.expanduser().resolve()
    if not root.exists():
        parser.error(f"path does not exist: {root}")
    report = scan(root, args.max_bytes)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 1 if report["blocking_findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
