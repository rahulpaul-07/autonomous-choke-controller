"""
Every headline number in README.md and in the presentation is asserted here
against the result files, so a claim cannot drift away from its evidence.

Run after `python src/scenarios.py && python src/studies.py`:

    python tests/test_reported_numbers.py
"""
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
sys.path.insert(0, os.path.join(ROOT, "src"))

FAILURES = []


def check(label, got, claimed, tol=0.02):
    got = float(got)
    ok = abs(got - claimed) <= tol * max(abs(claimed), 1.0)
    print(f"  {'OK  ' if ok else 'FAIL'}  {label:52s} got {got:>10.2f}   claimed {claimed}")
    if not ok:
        FAILURES.append(label)


def main():
    b = pd.read_csv(os.path.join(RES, "baseline_comparison.csv")).set_index(
        ["scenario", "controller"])
    mc = pd.read_csv(os.path.join(RES, "montecarlo_mismatch.csv"))
    sw = pd.read_csv(os.path.join(RES, "breaking_point.csv"))
    st = pd.read_csv(os.path.join(RES, "stress_tests.csv"), index_col=0)
    val = pd.read_csv(os.path.join(RES, "model_validation.csv"))
    lim = pd.read_csv(os.path.join(RES, "limit_sensitivity.csv"))
    kpi = pd.read_csv(os.path.join(RES, "kpi_summary.csv"), index_col=0)

    print("Scenario results")
    from simulator import OperatingEnvelope, max_safe_rate
    cap = max_safe_rate(OperatingEnvelope.load())["Q"]
    check("scenario C as % of the max safe rate",
          100 * kpi.loc["C", "final_rate_mean"] / cap, 99.2, 0.005)
    check("recovery: hours to re-enter the envelope",
          pd.read_csv(os.path.join(RES, "scenario_RECOVERY.csv")).pipe(
              lambda d: next(i for i, r in d.iterrows()
                             if not OperatingEnvelope.load().violations(
                                 r.WHP_true, r.FLP_true, r.BHP_true))), 5, 0.01)
    for tag, rate in (("A", 100.2), ("B", 150.2), ("C", 164.9)):
        check(f"scenario {tag}: settled rate", kpi.loc[tag, "final_rate_mean"], rate, 0.01)
        check(f"scenario {tag}: constraint violations",
              kpi.loc[tag, "constraint_violations_true"], 0, 0.001)

    print("\nModel cross-validation on the Honeywell reference data")
    check("minimum R2 across the four outputs", val.R2.min(), 0.983, 0.005)
    check("maximum R2 across the four outputs", val.R2.max(), 0.993, 0.005)

    print("\nBaselines (Scenario C)")
    check("PI violating intervals", b.loc[("C", "PI"), "violating_intervals"], 97, 0.02)
    check("PI violation percentage", b.loc[("C", "PI"), "violation_pct"], 69.29, 0.02)
    check("PI worst BHP shortfall [psi]", b.loc[("C", "PI"), "BHP_shortfall"], 145.7, 0.02)
    check("operator violating intervals",
          b.loc[("C", "OPERATOR"), "violating_intervals"], 0, 0.001)
    check("operator settled rate", b.loc[("C", "OPERATOR"), "mean_rate_last30"], 156.1, 0.01)
    check("MPC settled rate", b.loc[("C", "MPC"), "mean_rate_last30"], 164.9, 0.01)
    gain = (b.loc[("C", "MPC"), "mean_rate_last30"]
            - b.loc[("C", "OPERATOR"), "mean_rate_last30"])
    check("MPC gain over operator [bbl/hr]", gain, 8.79, 0.02)
    check("MPC gain over operator [%]",
          100 * gain / b.loc[("C", "OPERATOR"), "mean_rate_last30"], 5.6, 0.03)

    print("\nMonte-Carlo plant-model mismatch")
    for sc, want in (("B", 0), ("C", 7)):
        d = mc[mc.scenario == sc]
        check(f"scenario {sc}: randomised models violating",
              (d.violating_intervals > 0).sum(), want, 0.001)
    check("worst excursion [psi]", mc.worst_excursion_psi.max(), 1.159, 0.02)
    bad = mc[mc.violating_intervals > 0]
    check("largest tau_BHP factor among failures", bad.tau_BHP.max(), 0.69, 0.03)

    print("\nFailure boundary")
    bo = sw[sw.parameter.str.contains("back-off")]
    check("violating intervals at 0 psi back-off",
          bo[bo.value == 0].violating_intervals.iloc[0], 13, 0.01)
    check("violating intervals at 1 psi back-off",
          bo[bo.value == 1].violating_intervals.iloc[0], 0, 0.001)
    nz = sw[sw.parameter.str.contains("noise")]
    check("violating intervals at 8x noise",
          nz[nz.value == 8].violating_intervals.iloc[0], 0, 0.001)
    check("violating intervals at 12x noise",
          nz[nz.value == 12].violating_intervals.iloc[0], 58, 0.01)

    print("\nDisturbances and instrument faults")
    for row, want in (("D1_reservoir_decline", 0), ("D2_water_cut_step", 0),
                      ("F1_frozen_BHP", 0), ("F4_io_card_BHP_and_WHP", 0)):
        check(f"{row}: violating intervals", st.loc[row, "violating_intervals"], want, 0.001)
    check("F1 with validation DISABLED: violating intervals",
          st.loc["F1_frozen_BHP_NO_validation", "violating_intervals"], 5, 0.01)
    check("F4 with validation DISABLED: violating intervals",
          st.loc["F4_io_card_BHP_and_WHP_NO_validation", "violating_intervals"], 64, 0.02)
    check("F4 with validation DISABLED: minimum BHP [psi]",
          st.loc["F4_io_card_BHP_and_WHP_NO_validation", "min_BHP"], 2831.07, 0.001)

    print("\nTuning choices and recursive feasibility")
    tune = pd.read_csv(os.path.join(RES, "tuning_justification.csv"))
    feas = pd.read_csv(os.path.join(RES, "recursive_feasibility.csv"))
    m1 = tune[(tune.knob == "control horizon M") & (tune.value == 1)].iloc[0]
    m2 = tune[(tune.knob == "control horizon M") & (tune.value == 2)].iloc[0]
    check("M=1 total choke travel", m1.total_choke_travel, 103.8, 0.02)
    check("M=2 total choke travel", m2.total_choke_travel, 40.8, 0.02)
    w0 = tune[(tune.knob == "move suppression w_move") & (tune.value == 0)].iloc[0]
    check("w_move=0 total choke travel", w0.total_choke_travel, 160.8, 0.02)
    check("intervals with an empty feasible set", feas.empty_set_intervals.sum(), 0, 0.001)
    check("total intervals checked for feasibility", feas.intervals.sum(), 370, 0.001)
    check("smallest feasible set seen", feas.min_feasible_candidates.min(), 153, 0.02)

    print("\nLimit sensitivity (200 randomised operating envelopes)")
    fixed = lim[lim.backoff_mode.str.startswith("fixed")]
    prop = lim[lim.backoff_mode.str.startswith("span")]
    check("envelopes tested", len(fixed), 200, 0.001)
    check("envelopes violating a limit", (fixed.violating_intervals > 0).sum(), 0, 0.001)
    check("mean % of each envelope's own ceiling", fixed.pct_of_max_safe.mean(), 96.9, 0.01)
    check("worst % of ceiling", fixed.pct_of_max_safe.min(), 93.6, 0.01)
    for tag, want in (("WHP_min", 107), ("BHP_min", 54), ("FLP_min", 39)):
        check(f"envelopes where {tag} binds",
              (fixed.binding_constraint == tag).sum(), want, 0.02)
    check("span-proportional back-off: % of ceiling", prop.pct_of_max_safe.mean(), 99.3, 0.01)
    check("span-proportional back-off: envelopes violating",
          (prop.violating_intervals > 0).sum(), 18, 0.02)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} CLAIM(S) NO LONGER MATCH THE EVIDENCE:")
        for f in FAILURES:
            print("   -", f)
        raise SystemExit(1)
    print("ALL REPORTED NUMBERS MATCH THE RESULT FILES")


if __name__ == "__main__":
    main()
