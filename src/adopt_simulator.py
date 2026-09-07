"""
adopt_simulator.py
================================================================================
One command to re-run this entire project against a DIFFERENT simulator.

    python src/adopt_simulator.py some_module:SomeSimulator

The organisers confirmed no simulator would be provided and that any open-source
equivalent may be used, so ``src/simulator.py`` is ours. This script exists to
show the controller is not wedded to it: point it at any other plant exposing
``step(choke) -> (Q, WHP, FLP, BHP)`` and the whole study re-runs.

It will:
    1. wrap the provided simulator in the adapter and sanity-check its interface
    2. run the same designed open-loop step test we used on our own simulator
    3. re-identify the Hammerstein control model from that data
    4. report the feasible operating window and maximum safe rate implied by the
       limits in config/operating_limits.json
    5. run demonstration Scenarios A, B and C closed-loop
    6. write figures to figures_external/ and tables to results_external/

Nothing in the controller is re-tuned by hand. If the provided simulator behaves
differently from ours, the identification and the steady-state target optimiser
absorb the difference automatically - which is the whole point of separating the
control model from the plant.
================================================================================
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from controller import ChokeMPC, MPCConfig, SteadyStateTargetOptimiser, run_closed_loop
from external_simulator import ExternalSimulator, load_simulator
from identification import OUTPUTS, cross_validate, identify, run_step_test
from plotting import plot_scenario, plot_step_test
from simulator import OperatingEnvelope

FIG = os.path.join(ROOT, "figures_external")
RES = os.path.join(ROOT, "results_external")


def sanity_check(make_sim, cfg: MPCConfig) -> ExternalSimulator:
    """Confirm the provided simulator honours the documented interface."""
    print("1. Checking the provided simulator's interface")
    sim = ExternalSimulator(make_sim(), u0=40.0, Ts=cfg.Ts, du_max=cfg.du_max)
    out = sim.step(45.0)
    assert len(out) == 4, "step() must return four values"
    names = ("Q [bbl/hr]", "WHP [psi]", "FLP [psi]", "BHP [psi]")
    print("   step(45.0) -> " + ",  ".join(f"{n} = {v:.2f}" for n, v in zip(names, out)))
    if not all(np.isfinite(out)):
        raise ValueError("the provided simulator returned a non-finite value")
    print("   interface OK\n")
    return sim


def main(spec: str, save: bool = True):
    """Re-run the whole study against the simulator named by ``spec``."""
    for d in (FIG, RES):
        os.makedirs(d, exist_ok=True)

    cfg = MPCConfig()
    env = OperatingEnvelope.load()
    make_sim = load_simulator(spec)

    probe = sanity_check(make_sim, cfg)

    # -- 2. step test on THEIR simulator ------------------------------------
    print("2. Running the open-loop step test on the provided simulator")
    factory = lambda u0: ExternalSimulator(make_sim(), u0=u0, Ts=cfg.Ts,
                                           du_max=cfg.du_max, enforce_ramp=False)
    step_df = run_step_test(sim_factory=factory)
    print(f"   {len(step_df)} hours of step-test data\n")

    # -- 3. re-identify ------------------------------------------------------
    print("3. Identifying the control model from that data")
    model = identify(step_df)
    print(model.summary()[["a0", "a1", "a2", "tau", "theta", "rmse", "r2"]].round(4)
          .to_string())
    print()

    # -- 4. operating window under the configured limits --------------------
    print("4. Feasible operating window implied by config/operating_limits.json")
    print(env.describe())
    ssto = SteadyStateTargetOptimiser(model, env, cfg)
    lim = ssto.envelope_limits()
    if not lim:
        print("\n   !! No feasible steady state exists for this simulator under these\n"
              "      limits. Either the limits or the simulator differ from ours -\n"
              "      edit config/operating_limits.json and re-run.")
        return
    print(f"\n   feasible choke window : {lim['u_min']:.2f} – {lim['u_max']:.2f} %")
    print(f"   feasible rate window  : {lim['Q_min']:.2f} – {lim['Q_max']:.2f} bbl/hr")
    print(f"   MAXIMUM SAFE RATE     : {lim['Q_max']:.2f} bbl/hr "
          f"at choke {lim['u_at_Qmax']:.2f} %\n")

    # -- 5. scenarios --------------------------------------------------------
    print("5. Closed-loop demonstration scenarios")
    Qmax = lim["Q_max"]
    scen = {
        "A": dict(title="Scenario A — Start-up to Target",
                  u0=lim["u_min"] + 3.0, n=90, warmup=0,
                  target=lambda k: 0.60 * Qmax),
        "B": dict(title="Scenario B — Target Tracking",
                  u0=lim["u_min"] + 0.35 * (lim["u_max"] - lim["u_min"]), n=140, warmup=0,
                  target=lambda k: 0.60 * Qmax if k < 40 else 0.90 * Qmax),
        "C": dict(title="Scenario C — Infeasible Target",
                  u0=lim["u_min"] + 0.35 * (lim["u_max"] - lim["u_min"]), n=140, warmup=0,
                  target=lambda k: 0.72 * Qmax if k < 30 else 1.20 * Qmax),
    }

    rows = []
    for tag, s in scen.items():
        sim = ExternalSimulator(make_sim(), u0=s["u0"], Ts=cfg.Ts, du_max=cfg.du_max)
        ctrl = ChokeMPC(model, env, cfg, u0=s["u0"])
        df = run_closed_loop(sim, ctrl, s["target"], s["n"])
        d = df.iloc[s["warmup"]:]
        viol = sum(1 for _, r in d.iterrows()
                   if env.violations(r.WHP_psi, r.FLP_psi, r.BHP_psi))
        tail = df.iloc[-30:]
        rows.append({"scenario": tag,
                     "requested_target": float(tail.Q_target.iloc[-1]),
                     "achievable_target": float(tail.Q_achievable.mean()),
                     "achieved_rate": float(tail.OilRate_bbl_hr.mean()),
                     "violating_intervals_measured": int(viol),
                     "max_abs_move_pct": float(df.dChoke.abs().max()),
                     "modes": ",".join(sorted(df["mode"].unique()))})
        print(f"   {tag}: target {rows[-1]['requested_target']:.1f} -> achievable "
              f"{rows[-1]['achievable_target']:.1f}, achieved "
              f"{rows[-1]['achieved_rate']:.1f} bbl/hr, {viol} violating intervals, "
              f"modes {rows[-1]['modes']}")
        if save:
            df.to_csv(os.path.join(RES, f"scenario_{tag}.csv"), index=False)
            plot_scenario(df, s["title"] + "  (provided simulator)", env,
                          path=os.path.join(FIG, f"scenario_{tag}.png"))

    summary = pd.DataFrame(rows).set_index("scenario")
    if save:
        step_df.to_csv(os.path.join(RES, "open_loop_step_test.csv"), index=False)
        model.summary().to_csv(os.path.join(RES, "identified_model.csv"))
        summary.to_csv(os.path.join(RES, "scenario_summary.csv"))
        plot_step_test(step_df, model,
                       path=os.path.join(FIG, "step_test_identification.png"))

    print("\n" + ExternalSimulator.audit_note())
    print(f"\nFigures -> {os.path.relpath(FIG, ROOT)}/   tables -> "
          f"{os.path.relpath(RES, ROOT)}/")
    return model, summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", help="'module:Callable' for the simulator to drive, "
                                 "e.g. mock_provided_simulator:MockProvidedSimulator")
    a = ap.parse_args()
    main(a.spec)
