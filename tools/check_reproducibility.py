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

# Agreement tolerance.
#
# On one machine the tables regenerate bit-for-bit. Across machines they do not
# quite: the nonlinear least squares in identification.py converges to a very
# slightly different point when numpy links a different BLAS, and that difference
# propagates into every table downstream. Measured Windows against the Linux CI
# runner, the worst relative disagreement is 6.2e-8 - eight orders of magnitude
# below the two decimal places these results are ever quoted to.
#
# So this asserts agreement to 1e-6 relative, about 16x tighter than the largest
# difference actually observed, rather than a bit-equality it cannot honestly
# expect. ATOL covers quantities that are legitimately zero (the identified
# transport delay theta comes out at ~5.8e-18, where a relative test is meaningless).
RTOL = 1e-6
ATOL = 1e-9

WORST = [0.0]   # largest relative disagreement seen, for the summary line


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
            close = np.isclose(lv, rv, rtol=RTOL, atol=ATOL, equal_nan=True)
            bad = ~(close | both_nan)
            numeric += lv.size

            # Record how far apart the two runs actually are, so the summary
            # states the observed agreement rather than only that it passed.
            with np.errstate(invalid='ignore', divide='ignore'):
                scale = np.maximum(np.abs(lv), np.abs(rv))
                rel = np.where(scale > ATOL, np.abs(lv - rv) / scale, 0.0)
            rel = rel[np.isfinite(rel)]
            if rel.size:
                WORST[0] = max(WORST[0], float(rel.max()))

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
    print(f"largest relative disagreement observed : {WORST[0]:.2e}  (tolerance {RTOL:.0e})")

    if problems:
        print("\nREPRODUCIBILITY CHECK FAILED\n")
        for p in problems:
            print("  " + p)
        return 1

    if WORST[0] == 0.0:
        print("\nreproducibility check passed: every compared cell is bit-for-bit identical")
    else:
        print(f"\nreproducibility check passed: every compared cell agrees to "
              f"{WORST[0]:.1e} relative or better")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
