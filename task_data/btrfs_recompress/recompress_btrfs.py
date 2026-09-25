#!/usr/bin/env python3
"""Rewrite the data that is already on the btrfs filesystem, then balance it.

The program is deployed by the btrfs_recompress task and started as a transient
unit, so its output reaches the journal and a window on the desktop can follow
it while the rest of the provisioning continues. It runs two steps: it
rewrites every declared mount point with the declared compression, and it then
balances the data chunks that are filled below the declared percentage, which
collects the space the rewrite freed.

The program writes the marker file only after every step succeeded, so the
marker means the one-off work is done and a later run skips it. A failing step
is printed, the remaining steps still run, and the program exits nonzero without
the marker, so a later run tries the work again.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path


def run_step(command: list[str], timeout_seconds: int) -> int:
    """Run one btrfs step and report its command and duration."""

    print(f"run: {' '.join(command)}", flush=True)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, timeout=timeout_seconds, text=True, check=False
        )
    except subprocess.TimeoutExpired:
        print(
            f"/run: timeout {time.monotonic() - started:.3f}s "
            f"{' '.join(command)}",
            flush=True,
        )
        return 1
    print(
        f"/run: {completed.returncode} {time.monotonic() - started:.3f}s "
        f"{' '.join(command)}",
        flush=True,
    )
    return completed.returncode


def parse_arguments() -> argparse.Namespace:
    """Read the command line the task builds from its declared values."""

    parser = argparse.ArgumentParser(
        description="Rewrite the btrfs data with a compression and balance it"
    )
    parser.add_argument("--algorithm", required=True)
    parser.add_argument("--level", required=True, type=int)
    parser.add_argument("--defragment-path", action="append", required=True)
    parser.add_argument("--balance-usage", required=True, type=int)
    parser.add_argument("--minimum-free-gib", required=True, type=int)
    parser.add_argument("--defragment-timeout-seconds", required=True, type=int)
    parser.add_argument("--balance-timeout-seconds", required=True, type=int)
    parser.add_argument("--marker-file", required=True)
    return parser.parse_args()


def free_mib() -> int:
    """The free space of the filesystem the program works on, in MiB.

    The number is printed after every step, because the point of the whole job
    is the room the compression gains, and a user watching the window sees the
    gain of each step instead of waiting for one line after an hour.
    """

    return shutil.disk_usage("/").free // (1024 * 1024)


def main() -> int:
    """Rewrite the data, balance the chunks and record the result."""

    arguments = parse_arguments()
    free_bytes = shutil.disk_usage("/").free
    minimum_free_bytes = arguments.minimum_free_gib * 1024 * 1024 * 1024
    print(
        f"free space before: {free_bytes // (1024 * 1024)} MiB, "
        f"needed at least: {arguments.minimum_free_gib} GiB",
        flush=True,
    )
    if free_bytes < minimum_free_bytes:
        print(
            "error: the machine has no room for a rewrite, so nothing was "
            "written and no marker was left",
            flush=True,
        )
        return 2

    print(
        f"compression: {arguments.algorithm} level {arguments.level}",
        flush=True,
    )
    failures: list[str] = []
    for path in arguments.defragment_path:
        if not Path(path).exists():
            print(f"skip: {path} is not present on this machine", flush=True)
            continue
        print(
            f"rewriting {path}: btrfs prints nothing while it works, so the "
            f"result and the time it took appear when this step ends",
            flush=True,
        )
        code = run_step(
            [
                "btrfs",
                "filesystem",
                "defragment",
                "-r",
                f"-c{arguments.algorithm}",
                "-L",
                str(arguments.level),
                "-f",
                path,
            ],
            arguments.defragment_timeout_seconds,
        )
        if code != 0:
            failures.append(f"rewrite of {path} failed with status {code}")
        print(f"free space after the rewrite of {path}: {free_mib()} MiB", flush=True)

    print(f"balancing the data chunks, free space: {free_mib()} MiB", flush=True)
    code = run_step(
        [
            "btrfs",
            "balance",
            "start",
            f"-dusage={arguments.balance_usage}",
            "--full-balance",
            "/",
        ],
        arguments.balance_timeout_seconds,
    )
    if code != 0:
        failures.append(f"balance failed with status {code}")
    print(f"free space after the balance: {free_mib()} MiB", flush=True)

    free_after_bytes = shutil.disk_usage("/").free
    summary = {
        "free_before_mib": free_bytes // (1024 * 1024),
        "free_after_mib": free_after_bytes // (1024 * 1024),
        "failures": failures,
    }

    if failures:
        print(
            "error: the one-off work did not finish, so no marker was left: "
            + "; ".join(failures),
            flush=True,
        )
        print(json.dumps(summary), flush=True)
        return 1

    marker = Path(arguments.marker_file)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(summary) + "\n", encoding="utf-8")
    print(
        "done: the one-off recompression and balance finished, marker written "
        f"to {marker}",
        flush=True,
    )
    print(json.dumps(summary), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
