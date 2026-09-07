"""
scenarios.py
================================================================================
End-to-end study: step test -> identification -> validation -> closed-loop
demonstration of Scenarios A, B and C, with figures, result CSVs and a KPI
summary table.

Run with:   python scenarios.py
================================================================================
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from controller import ChokeMPC, MPCConfig, run_closed_loop
from identification import cross_validate, identify, run_step_test
from plotting import plot_envelope, plot_scenario, plot_step_test, plot_validation
from simulator import OperatingEnvelope, WellSimulator, envelope_scan, max_safe_rate

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIG = os.path.join(ROOT, "figures")
RES = os.path.join(ROOT, "results")
DATA = os.path.join(ROOT, "data")
for d in (FIG, RES):
    os.makedirs(d, exist_ok=True)

ENV = OperatingEnvelope.load()
CFG = MPCConfig()


# ------------------------------------------------------------------------------
# KPIs
# ------------------------------------------------------------------------------


def kpis(df: pd.DataFrame, env: OperatingEnvelope = ENV, settle_band: float = 2.0,
         warmup: int = 0) -> dict:
    """Compute the performance / safety KPIs used to score each scenario."""
    d = df.iloc[warmup:]
    tail = df.iloc[-30:]

    viol_true = sum(len(env.violations(r.WHP_true, r.FLP_true, r.BHP_true))
                    for _, r in d.iterrows())
    viol_meas = sum(len(env.violations(r.WHP_psi, r.FLP_psi, r.BHP_psi))
                    for _, r in d.iterrows())

    # settling time to the last target change
    tgt = df.Q_target.values
    changes = np.where(np.diff(tgt) != 0)[0]
    k0 = int(changes[-1] + 1) if len(changes) else 0
    ach = df.Q_achievable.values
    err = np.abs(df.Q_true.values - ach)
    settle = np.nan
    inside = err[k0:] <= settle_band
    for i in range(len(inside)):
        if inside[i:].all():
            settle = float(df.Time_hr.values[k0 + i] - df.Time_hr.values[k0])
            break

    # --- overshoot: how far past the achievable target did it go, as % of the
    #     step it was asked to make?
    tgt = df.Q_target.values
    changes = np.where(np.diff(tgt) != 0)[0]
    k0 = int(changes[-1] + 1) if len(changes) else 0
    q, ach = df.Q_true.values, df.Q_achievable.values
    step = abs(ach[k0] - q[max(k0 - 1, 0)])
    peak = (q[k0:].max() - ach[k0:].mean()) if ach[k0] >= q[max(k0 - 1, 0)] \
        else (ach[k0:].mean() - q[k0:].min())
    overshoot_pct = float(100.0 * max(peak, 0.0) / step) if step > 1.0 else 0.0

    # --- safety margin: closest approach to any limit, as % of that variable's
    #     span, so the three are comparable
    spans = {"WHP": env.WHP_max - env.WHP_min, "FLP": env.FLP_max - env.FLP_min,
             "BHP": env.BHP_max - env.BHP_min}
    margins = {}
    for tag, col in (("WHP", "WHP_true"), ("FLP", "FLP_true"), ("BHP", "BHP_true")):
        lo, hi = getattr(env, f"{tag}_min"), getattr(env, f"{tag}_max")
        margins[tag] = min(d[col].min() - lo, hi - d[col].max()) / spans[tag] * 100.0
    tightest = min(margins, key=margins.get)

    # --- stability: is the choke still hunting once settled? Compare the
    #     standard deviation of the move over the last 30 intervals with the
    #     move size that measurement noise alone would justify.
    settled_moves = df.dChoke.values[-30:]
    return {
        "steps": int(len(df)),
        "overshoot_pct_of_step": round(overshoot_pct, 2),
        "safety_margin_pct_of_span": round(float(margins[tightest]), 2),
        "tightest_constraint": tightest,
        "settled_choke_move_std_pct": round(float(np.std(settled_moves)), 3),
        "settled_choke_move_max_pct": round(float(np.abs(settled_moves).max()), 3),
        "final_target_requested": float(tail.Q_target.iloc[-1]),
        "final_target_achievable": float(tail.Q_achievable.mean()),
        "final_rate_mean": float(tail.Q_true.mean()),
        "steady_state_offset": float(tail.Q_true.mean() - tail.Q_achievable.mean()),
        "IAE_vs_achievable": float(np.mean(np.abs(df.Q_true - df.Q_achievable))),
        "settling_time_h": settle,
        "constraint_violations_true": int(viol_true),
        "constraint_violations_measured": int(viol_meas),
        "max_abs_move_pct": float(df.dChoke.abs().max()),
        "mean_abs_move_pct": float(df.dChoke.abs().mean()),
        "total_choke_travel_pct": float(df.dChoke.abs().sum()),
        "min_BHP": float(d.BHP_true.min()), "min_WHP": float(d.WHP_true.min()),
        "min_FLP": float(d.FLP_true.min()),
        "modes": sorted(df["mode"].unique().tolist()),
    }


# ------------------------------------------------------------------------------
# Main study
# ------------------------------------------------------------------------------


def main(save: bool = True, verbose: bool = True) -> dict:
    """Run the full study: step test, identification, envelope, scenarios A/B/C."""
    out = {}

    # -- 1. open-loop step test + identification ------------------------------
    step_df = run_step_test()
    model = identify(step_df)
    out["model"] = model

    ref_path = os.path.join(DATA, "Autonomous_Choke_Control_Simulated_Dataset.csv")
    ref = pd.read_csv(ref_path)
    val = cross_validate(model, ref)
    out["validation"] = val

    if verbose:
        print("\n=== Identified control-oriented model ===")
        print(model.summary()[["a0", "a1", "a2", "tau", "theta", "rmse", "r2"]].round(4))
        print("\n=== Cross-validation on the Honeywell reference dataset ===")
        print(val.round(3))

    # -- 2. operating envelope ------------------------------------------------
    scan = envelope_scan(ENV)
    limits = max_safe_rate(ENV)
    out["scan"], out["limits"] = scan, limits
    if verbose:
        print("\n=== Steady-state operating envelope ===")
        print(f"  feasible choke window : {limits['u_min_feasible']:.2f} – "
              f"{limits['u_max_feasible']:.2f} %")
        print(f"  max safe oil rate     : {limits['Q']:.2f} bbl/hr  at choke "
              f"{limits['u']:.2f} %   (BHP = {limits['BHP']:.1f} psi, binding)")

    # -- 3. scenarios ---------------------------------------------------------
    scen_defs = {
        "A": dict(
            title="Scenario A — Start-up to Target (choke 18 % → 100 bbl/hr)",
            u0=18.0, n=90, seed=1, target=lambda k: 100.0, warmup=0,
            note="The well is brought up from a low choke opening at the bottom of "
                 "its operating envelope. Zero constraint violations from the very "
                 "first interval — no warm-up period is excluded from the audit.",
        ),
        "B": dict(
            title="Scenario B — Target Tracking (100 → 150 bbl/hr at t = 40 h)",
            u0=32.0, n=140, seed=2,
            target=lambda k: 100.0 if k < 40 else 150.0, warmup=0,
            note="A mid-run target change is absorbed with no constraint "
                 "violation and no steady-state offset. Every choke move stays "
                 "inside the ±5 %/interval ramp-rate limit.",
        ),
        "C": dict(
            title="Scenario C — Infeasible Target (120 → 200 bbl/hr at t = 30 h)",
            u0=40.0, n=140, seed=3,
            target=lambda k: 120.0 if k < 30 else 200.0, warmup=0,
            note="200 bbl/hr is unreachable without breaching the minimum-BHP "
                 "limit. The SSTO detects this, clips the target to the maximum "
                 "achievable safe rate, reports mode = CONSTRAINED, and the well "
                 "settles there instead of winding up against the constraint.",
        ),
    }

    results, summary = {}, {}
    for tag, s in scen_defs.items():
        sim = WellSimulator(u0=s["u0"], seed=s["seed"])
        ctrl = ChokeMPC(model, ENV, CFG, u0=s["u0"])
        df = run_closed_loop(sim, ctrl, s["target"], s["n"])
        results[tag] = df
        summary[tag] = kpis(df, warmup=s["warmup"])
        if save:
            df.to_csv(os.path.join(RES, f"scenario_{tag}.csv"), index=False)
            plot_scenario(df, s["title"], ENV,
                          path=os.path.join(FIG, f"scenario_{tag}.png"),
                          annotate=s["note"])
        if verbose:
            k = summary[tag]
            print(f"\n=== Scenario {tag} ===")
            print(f"  requested target      : {k['final_target_requested']:.1f} bbl/hr")
            print(f"  achievable target     : {k['final_target_achievable']:.2f} bbl/hr")
            print(f"  achieved rate (last 30h): {k['final_rate_mean']:.2f} bbl/hr "
                  f"(offset {k['steady_state_offset']:+.2f})")
            print(f"  settling time         : {k['settling_time_h']} h")
            print(f"  constraint violations : {k['constraint_violations_true']} "
                  f"(true plant state)")
            print(f"  max choke move        : {k['max_abs_move_pct']:.2f} % / interval")
            print(f"  controller modes seen : {k['modes']}")

    # -- 3a. re-run scenario C with decision diagnostics, to plot the controller
    #        itself rather than only its outcome
    sim = WellSimulator(u0=40.0, seed=3)
    ctrl_diag = ChokeMPC(model, ENV, CFG, u0=40.0, keep_diagnostics=True)
    df_diag = run_closed_loop(sim, ctrl_diag, scen_defs["C"]["target"], scen_defs["C"]["n"])
    if save:
        from plotting import plot_controller_decisions
        plot_controller_decisions(df_diag, ctrl_diag.diagnostics, ENV,
                                  path=os.path.join(FIG, "controller_decisions.png"))

    # -- 3b. an EXTRA demonstration: recovery from outside the envelope -------
    # Not one of the three required scenarios. At a near-shut choke the well sits
    # ABOVE its maximum-BHP limit (too little drawdown), so this starts the
    # controller in a state the envelope forbids and asks it to recover.
    sim = WellSimulator(u0=10.0, seed=1)
    ctrl = ChokeMPC(model, ENV, CFG, u0=10.0)
    df_rec = run_closed_loop(sim, ctrl, 100.0, 90)
    first_ok = next((i for i, r in df_rec.reset_index().iterrows()
                     if not ENV.violations(r.WHP_true, r.FLP_true, r.BHP_true)), None)
    results["RECOVERY"] = df_rec
    summary["RECOVERY"] = kpis(df_rec, warmup=int(first_ok or 0))
    summary["RECOVERY"]["hours_to_re_enter_envelope"] = float(first_ok)
    if verbose:
        print(f"\n=== Extra demonstration — recovery from OUTSIDE the envelope ===")
        print(f"  starts at choke 10 % where BHP exceeds its maximum-drawdown limit")
        print(f"  back inside the envelope after : {first_ok} h")
        print(f"  violations after re-entry      : "
              f"{summary['RECOVERY']['constraint_violations_true']}")
        print(f"  settles at                     : "
              f"{summary['RECOVERY']['final_rate_mean']:.2f} bbl/hr")
    if save:
        df_rec.to_csv(os.path.join(RES, "scenario_RECOVERY.csv"), index=False)
        plot_scenario(df_rec, "Extra — Recovery from Outside the Operating Envelope "
                              "(choke 10 % start)", ENV,
                      path=os.path.join(FIG, "scenario_RECOVERY.png"),
                      annotate="Not one of the three required scenarios. At a near-shut choke the well "
                               "sits ABOVE its maximum-BHP limit, so the controller starts in a "
                               "forbidden state, enters RECOVERY and drives back inside the envelope "
                               "at the ramp-rate limit before tracking the target.")

    out["results"], out["summary"] = results, summary

    # -- 4. supporting figures + artefacts ------------------------------------
    if save:
        plot_step_test(step_df, model, path=os.path.join(FIG, "step_test_identification.png"))
        plot_validation(model, ref, path=os.path.join(FIG, "model_validation.png"))
        plot_envelope(scan, ENV, path=os.path.join(FIG, "operating_envelope.png"))
        from plotting import plot_monitored, plot_results_summary
        plot_monitored(results, ENV, path=os.path.join(FIG, "monitored_variables.png"))
        plot_results_summary({t: results[t] for t in "ABC"}, ENV,
                             path=os.path.join(FIG, "results_summary.png"))
        # the architecture block diagram lives in its own module; call it here so
        # that `python src/scenarios.py` regenerates every figure the deck uses
        import make_architecture_figure
        make_architecture_figure.main()
        step_df.to_csv(os.path.join(RES, "open_loop_step_test.csv"), index=False)
        model.summary().to_csv(os.path.join(RES, "identified_model.csv"))
        val.to_csv(os.path.join(RES, "model_validation.csv"))
        pd.DataFrame(summary).T.to_csv(os.path.join(RES, "kpi_summary.csv"))
        with open(os.path.join(RES, "summary.json"), "w") as f:
            json.dump({"limits": limits, "scenarios": summary}, f, indent=2, default=str)

    # -- 5. robustness sweep over measurement-noise realisations --------------
    rob = []
    for seed in range(1, 11):
        for tag, s in scen_defs.items():
            sim = WellSimulator(u0=s["u0"], seed=seed)
            ctrl = ChokeMPC(model, ENV, CFG, u0=s["u0"])
            df = run_closed_loop(sim, ctrl, s["target"], s["n"])
            k = kpis(df, warmup=s["warmup"])
            rob.append({"seed": seed, "scenario": tag,
                        "violations": k["constraint_violations_true"],
                        "offset": k["steady_state_offset"],
                        "rate": k["final_rate_mean"]})
    rob = pd.DataFrame(rob)
    out["robustness"] = rob
    if save:
        rob.to_csv(os.path.join(RES, "robustness_sweep.csv"), index=False)
    if verbose:
        print("\n=== Robustness: 10 measurement-noise realisations x 3 scenarios ===")
        print(rob.groupby("scenario").agg(
            total_violations=("violations", "sum"),
            mean_offset=("offset", "mean"),
            max_abs_offset=("offset", lambda x: np.abs(x).max()),
            mean_rate=("rate", "mean")).round(3))

    return out


if __name__ == "__main__":
    main()
