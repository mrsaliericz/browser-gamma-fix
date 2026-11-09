#!/usr/bin/env python3
"""Read-only compatibility report for installed Chromium browser DLLs.

Runs the same analyzers as the tray app against every browser DLL it can find
(stable, beta, dev and canary channels of Chrome and Edge, Brave, Vivaldi) and
reports which strategy each patch component uses. Nothing is written to any
browser process or file.

Usage:
    python tools\\compatibility_report.py                       # standard locations
    python tools\\compatibility_report.py C:\\x\\msedge.dll ...    # plus extra DLLs
    python tools\\compatibility_report.py --only C:\\x\\msedge.dll # only these DLLs

Exit code 0 when every analyzed DLL is supported, 1 otherwise, 2 when no DLL
was found. With GITHUB_STEP_SUMMARY set, a Markdown table is appended there.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import runtime_gamma22 as runtime  # noqa: E402

LOCAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
PROGRAM_FILES = (Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)"))
CHANNEL_DIRECTORIES = [
    ("Chrome", r"Google\Chrome\Application", "chrome.dll"),
    ("Chrome Beta", r"Google\Chrome Beta\Application", "chrome.dll"),
    ("Chrome Dev", r"Google\Chrome Dev\Application", "chrome.dll"),
    ("Edge", r"Microsoft\Edge\Application", "msedge.dll"),
    ("Edge Beta", r"Microsoft\Edge Beta\Application", "msedge.dll"),
    ("Edge Dev", r"Microsoft\Edge Dev\Application", "msedge.dll"),
    ("Brave", r"BraveSoftware\Brave-Browser\Application", "chrome.dll"),
    ("Vivaldi", r"Vivaldi\Application", "vivaldi.dll"),
]
PER_USER = [
    ("Chrome Canary", LOCAL / r"Google\Chrome SxS\Application", "chrome.dll"),
    ("Edge Canary", LOCAL / r"Microsoft\Edge SxS\Application", "msedge.dll"),
    ("Brave (user)", LOCAL / r"BraveSoftware\Brave-Browser\Application", "chrome.dll"),
    ("Chrome (user)", LOCAL / r"Google\Chrome\Application", "chrome.dll"),
]


def discover() -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    roots = [
        (name, base / relative, dll)
        for name, relative, dll in CHANNEL_DIRECTORIES
        for base in PROGRAM_FILES
    ] + PER_USER
    for name, root, dll_name in roots:
        if not root.is_dir():
            continue
        for dll in sorted(root.glob(f"*/{dll_name}")):
            found.append((name, dll))
    return found


def main(argv: list[str]) -> int:
    only = "--only" in argv
    extra = [Path(arg) for arg in argv if arg != "--only"]
    targets = [] if only else discover()
    for dll in extra:
        if dll.is_dir():
            targets.extend((f"{dll.name}", path) for path in sorted(dll.rglob("*.dll"))
                           if path.name.lower() in {"chrome.dll", "msedge.dll", "vivaldi.dll"})
        else:
            targets.append((dll.parent.parent.name or dll.name, dll))
    if not targets:
        print("No Chromium browser DLL found.")
        return 2
    rows = []
    failures = 0
    for name, dll in targets:
        started = time.monotonic()
        try:
            plan = runtime.make_runtime_plan(dll)
            status = "supported"
            detail = runtime.describe_plan(plan)
        except Exception as error:  # report every analyzer failure, keep going
            failures += 1
            status = "UNSUPPORTED"
            detail = f"{type(error).__name__}: {error}"
        elapsed = time.monotonic() - started
        version = dll.parent.name
        print(f"[{status}] {name} {version} ({dll}) in {elapsed:.1f}s\n    {detail}\n", flush=True)
        rows.append((name, version, status, detail))

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write("## Browser Gamma Fix compatibility\n\n")
            stream.write("| Browser | Version | Status | Details |\n|---|---|---|---|\n")
            for name, version, status, detail in rows:
                cleaned = detail.replace("|", "\\|").replace("\n", " ")
                stream.write(f"| {name} | {version} | {status} | {cleaned} |\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
