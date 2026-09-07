"""Compare two directories of result tables cell by cell.

The README claims that deleting `results/` and re-running the study regenerates
every table identically, with the sole exception of wall-clock timing columns,
which are machine-dependent. This script turns that claim into something CI can
fail on.

    python tools/check_reproducibility.py results_committed results

Exit code 0 if every compared cell matches, 1 otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Columns whose values are wall-clock measurements and therefore machine-dependent.
TIMING_COLUMN = re.compile(r"(\bms\b|_ms|ms_|_sec|_seconds|runtime|elapsed|wall|exec_time|per_interval)", re.I)

# Absolute tolerance. 0.0 means bit-for-bit on the decimal representation written to CSV.
TOLERANCE = 0.0


def compare_frame(name: str, a: pd.DataFrame, b: pd.DataFrame) -> tuple[int, int, list[str]]:
    problems: list[str] = []

    if list(a.columns) != list(b.columns):
        problems.append(f"{name}: column names differ\n  committed: {list(a.columns)}\n  regenerated: {list(b.columns)}")
        return 0, 0, problems
    if len(a) != len(b):
        problems.append(f"{name}: row count differs ({len(a)} committed vs {len(b)} regenerated)")
        return 0, 0, problems

    numeric = text = 0
    for col in a.columns:
        if TIMING_COLUMN.search(str(col)):
            continue

        left, right = a[col], b[col]

        if pd.api.types.is_numeric_dtype(left) and pd.api.types.is_numeric_dtype(right):
            lv = left.to_numpy(dtype=float)
            rv = right.to_numpy(dtype=float)
            both_nan = np.isnan(lv) & np.isnan(rv)
            close = np.isclose(lv, rv, rtol=0.0, atol=TOLERANCE, equal_nan=True)
            bad = ~(close | both_nan)
            numeric += lv.size
            if bad.any():
                idx = int(np.argmax(bad))
                problems.append(
                    f"{name}[{col}]: {int(bad.sum())} of {lv.size} cells differ; "
                    f"first at row {idx}: {lv[idx]!r} -> {rv[idx]!r}"
                )
        else:
            # Missing values must compare equal. Under pandas' Arrow-backed string
            # dtype, NA != NA, so fill with a sentinel before comparing rather than
            # relying on astype(str) alone.
            lv = left.fillna("\x00<NA>").astype(str).to_numpy()
            rv = right.fillna("\x00<NA>").astype(str).to_numpy()
            mismatch = lv != rv
            text += mismatch.size
            if mismatch.any():
                idx = int(np.argmax(mismatch))
                problems.append(
                    f"{name}[{col}]: {int(mismatch.sum())} of {mismatch.size} cells differ; "
                    f"first at row {idx}: {left.iloc[idx]!r} -> {right.iloc[idx]!r}"
                )

    return numeric, text, problems


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2

    committed, regenerated = Path(argv[1]), Path(argv[2])
    for d in (committed, regenerated):
        if not d.is_dir():
            print(f"not a directory: {d}")
            return 2

    committed_files = sorted(p.name for p in committed.glob("*.csv"))
    regenerated_files = sorted(p.name for p in regenerated.glob("*.csv"))

    if not committed_files:
        print(f"no CSV tables found in {committed}")
        return 2

    problems: list[str] = []
    missing = set(committed_files) - set(regenerated_files)
    extra = set(regenerated_files) - set(committed_files)
    if missing:
        problems.append(f"tables not regenerated: {sorted(missing)}")
    if extra:
        problems.append(f"unexpected new tables: {sorted(extra)}")

    total_numeric = total_text = 0
    for name in committed_files:
        if name in missing:
            continue
        a = pd.read_csv(committed / name)
        b = pd.read_csv(regenerated / name)
        n_num, n_txt, errs = compare_frame(name, a, b)
        total_numeric += n_num
        total_text += n_txt
        problems.extend(errs)

    print(f"tables compared : {len(committed_files) - len(missing)}")
    print(f"numeric cells compared : {total_numeric:,}")
    print(f"text cells compared    : {total_text:,}")
    print(f"timing columns skipped as machine-dependent : {TIMING_COLUMN.pattern}")

    if problems:
        print("\nREPRODUCIBILITY CHECK FAILED\n")
        for p in problems:
            print("  " + p)
        return 1

    print("\nreproducibility check passed: every compared cell is identical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
