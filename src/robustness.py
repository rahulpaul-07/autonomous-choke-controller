"""
robustness.py
================================================================================
Two studies that a controller claiming "zero violations" has to survive.

  1. MONTE-CARLO PLANT-MODEL MISMATCH
     The MPC's internal model is deliberately corrupted - incremental gain
     scaled by U(0.7, 1.3) and time constant by U(0.5, 1.5), independently per
     output channel - while the plant stays as it is. This is far worse
     mismatch than a real re-identification would leave behind. We then count
     constraint violations across hundreds of randomised controllers.

  2. WHERE DOES IT ACTUALLY BREAK?
     Every controller has a boundary. We sweep the prediction horizon, the
     constraint back-off, the measurement noise and the ramp-rate limit until
     violations appear, and report the boundary honestly. A design that has
     never been pushed to failure has not been characterised.
================================================================================
"""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd

from controller import ChokeMPC, MPCConfig, run_closed_loop
from identification import OUTPUTS, PlantModel
from simulator import OperatingEnvelope, WellParameters, WellSimulator

Q_KEY, WHP_KEY, FLP_KEY, BHP_KEY = OUTPUTS


# ------------------------------------------------------------------------------
# Model perturbation
# ------------------------------------------------------------------------------


def perturb_model(model: PlantModel, gain_factors: dict, tau_factors: dict,
                  u_ref: float = 45.0) -> PlantModel:
    """
    Return a copy of ``model`` with corrupted gains and time constants.

    The gain is scaled about a reference operating point:

        y'(u) = y(u_ref) + g * ( y(u) - y(u_ref) )

    so the *sensitivity* changes but the absolute level does not. Scaling the
    raw coefficients instead would shift BHP by hundreds of psi, which is a bias
    error - and bias is exactly what the disturbance estimator is designed to
    remove, so it would not be a fair test of the gain mismatch.
    """
    m = copy.deepcopy(model)
    for key in OUTPUTS:
        ch, g = m[key], gain_factors[key]
        a0, a1, a2 = ch.a0, ch.a1, ch.a2
        y_ref = a0 + a1 * u_ref + a2 * u_ref**2
        # y_ref + g*(a0 + a1 u + a2 u^2 - y_ref)
        ch.a0 = y_ref + g * (a0 - y_ref)
        ch.a1 = g * a1
        ch.a2 = g * a2
        ch.tau = max(ch.tau * tau_factors[key], 0.3)
    return m


def audit(df: pd.DataFrame, env: OperatingEnvelope, warmup: int = 0) -> dict:
    """Violation count, worst excursion and settled rate for one run."""
    d = df.iloc[warmup:]
    viol = sum(1 for _, r in d.iterrows()
               if env.violations(r.WHP_true, r.FLP_true, r.BHP_true))
    worst = max(
        max(env.WHP_min - d.WHP_true.min(), 0.0),
        max(env.FLP_min - d.FLP_true.min(), 0.0),
        max(env.BHP_min - d.BHP_true.min(), 0.0),
        max(d.WHP_true.max() - env.WHP_max, 0.0),
        max(d.FLP_true.max() - env.FLP_max, 0.0),
        max(d.BHP_true.max() - env.BHP_max, 0.0),
    )
    tail = df.iloc[-30:]
    return {
        "violating_intervals": int(viol),
        "worst_excursion_psi": float(worst),
        "rate_last30": float(tail.Q_true.mean()),
        "offset": float(tail.Q_true.mean() - tail.Q_achievable.mean()),
        "max_move": float(df.dChoke.abs().max()),
    }


# ------------------------------------------------------------------------------
# Study 1 - Monte Carlo mismatch
# ------------------------------------------------------------------------------


SCEN = {
    "B": dict(u0=32.0, n=140, seed=2, warmup=0,
              target=lambda k: 100.0 if k < 40 else 150.0),
    "C": dict(u0=40.0, n=140, seed=3, warmup=0,
              target=lambda k: 120.0 if k < 30 else 200.0),
}


def monte_carlo(model: PlantModel, env: OperatingEnvelope, cfg: MPCConfig,
                n_trials: int = 200, gain_range=(0.7, 1.3),
                tau_range=(0.5, 1.5), seed: int = 0,
                scenarios=("B", "C")) -> pd.DataFrame:
    """Run ``n_trials`` randomised-model controllers through each scenario."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_trials):
        gf = {k: float(rng.uniform(*gain_range)) for k in OUTPUTS}
        tf = {k: float(rng.uniform(*tau_range)) for k in OUTPUTS}
        pm = perturb_model(model, gf, tf)
        for tag in scenarios:
            s = SCEN[tag]
            sim = WellSimulator(u0=s["u0"], seed=1000 + i)
            ctrl = ChokeMPC(pm, env, cfg, u0=s["u0"])
            df = run_closed_loop(sim, ctrl, s["target"], s["n"])
            r = audit(df, env, s["warmup"])
            r.update({"trial": i, "scenario": tag,
                      "gain_Q": gf[Q_KEY], "gain_BHP": gf[BHP_KEY],
                      "tau_Q": tf[Q_KEY], "tau_BHP": tf[BHP_KEY],
                      "worst_gain_err": max(abs(v - 1) for v in gf.values()),
                      "worst_tau_err": max(abs(v - 1) for v in tf.values())})
            rows.append(r)
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------
# Study 2 - find the breaking point
# ------------------------------------------------------------------------------


def sweep(model: PlantModel, env: OperatingEnvelope, base_cfg: MPCConfig,
          scenario: str = "C") -> pd.DataFrame:
    """Vary one design parameter at a time until the controller fails."""
    s = SCEN[scenario]
    rows = []

    def run(cfg, noise_mult=1.0, tag="", value=None):
        p = WellParameters()
        p.noise_Q *= noise_mult; p.noise_WHP *= noise_mult
        p.noise_FLP *= noise_mult; p.noise_BHP *= noise_mult
        p.du_max = cfg.du_max
        sim = WellSimulator(u0=s["u0"], seed=s["seed"], params=p)
        ctrl = ChokeMPC(model, env, cfg, u0=s["u0"])
        df = run_closed_loop(sim, ctrl, s["target"], s["n"])
        r = audit(df, env, s["warmup"])
        r.update({"parameter": tag, "value": value})
        rows.append(r)

    # a) prediction horizon - the lesson about covering the slowest tau
    for P in (5, 10, 15, 20, 25, 30, 40, 50):
        c = copy.copy(base_cfg); c.P = P
        run(c, tag="prediction horizon P [h]", value=P)

    # b) constraint back-off
    for b in (0.0, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0):
        c = copy.copy(base_cfg); c.backoff = b
        run(c, tag="constraint back-off [psi]", value=b)

    # c) measurement noise multiplier
    for nm in (1, 2, 3, 5, 8, 12):
        run(copy.copy(base_cfg), noise_mult=nm, tag="noise multiplier [x]", value=nm)

    # d) ramp-rate limit
    for du in (0.5, 1.0, 2.0, 3.0, 5.0, 10.0):
        c = copy.copy(base_cfg); c.du_max = du
        run(c, tag="ramp-rate limit [%/interval]", value=du)

    return pd.DataFrame(rows)
