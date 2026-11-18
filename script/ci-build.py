#!/usr/bin/env python3
"""CI build script - cross-platform cargo build wrapper with real-time output"""
from __future__ import annotations

import argparse
import io
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

# Force UTF-8 stdout on Windows (default is cp1252)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def count_crates(source_dir: Path) -> str:
    lock = source_dir / "Cargo.lock"
    if not lock.exists():
        return "?"
    count = sum(
        1 for line in lock.read_text(encoding="utf-8").splitlines()
        if line.startswith("name = ")
    )
    return str(count) if count > 0 else "?"


def run_build(cmd: list[str], cwd: Path, total: str) -> int:
    print(f"$ {' '.join(cmd)}", flush=True)
    env = {**os.environ, "CARGO_TERM_COLOR": "never"}
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,  # line-buffered: deliver each line immediately
    )
    assert proc.stdout is not None
    n = 0
    for line in proc.stdout:
        line = line.rstrip("\n")
        if "Compiling " in line:
            n += 1
            line = line.replace("Compiling ", f"Compiling [{n}/{total}] ", 1)
        print(line, flush=True)
    proc.wait()
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="CI cargo build wrapper")
    parser.add_argument("--target", default=None, help="Cargo target triple")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--source-dir", default=".", help="Cargo project root")
    parser.add_argument("--retries", type=int, default=1, help="Max build attempts")
    parser.add_argument(
        "--zed-rustc-args",
        default="",
        help="Extra rustc args for the zed binary only (switches to `cargo rustc`), "
        "pass as --zed-rustc-args=\"-C ...\"",
    )
    args = parser.parse_args()

    source_dir = Path(args.source_dir).resolve()
    total = count_crates(source_dir)
    print(f"Building {total} crates...", flush=True)

    cmd = ["cargo"]
    # 对齐上游打包脚本（script/bundle-*）：zed 自身 crate 用 opt-level="z"，显著减小体积
    if (source_dir / ".cargo" / "bundle-config.toml").exists():
        cmd.extend(["--config", ".cargo/bundle-config.toml"])
    rustc_args = shlex.split(args.zed_rustc_args)
    if rustc_args:
        cmd.extend(["rustc", "--package", "zed", "--bin", "zed"])
    else:
        cmd.append("build")
    cmd.extend(["--release", f"--jobs={args.jobs}", "--locked"])
    if args.target:
        cmd.extend(["--target", args.target])
    if rustc_args:
        cmd.append("--")
        cmd.extend(rustc_args)

    last_exit = 1
    for attempt in range(1, args.retries + 1):
        if args.retries > 1:
            print(f"\nBuild attempt {attempt}/{args.retries}", flush=True)
        last_exit = run_build(cmd, source_dir, total)
        if last_exit == 0:
            return 0
        print(f"\nBuild failed (exit {last_exit})", flush=True)
        if attempt < args.retries:
            print("Retrying in 15s...", flush=True)
            time.sleep(15)

    return last_exit


if __name__ == "__main__":
    sys.exit(main())
