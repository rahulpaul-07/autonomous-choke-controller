"""
stress_tests.py
================================================================================
Tests that deliberately violate the challenge's own assumptions.

The problem statement says to assume constant reservoir properties, no changing
GOR or water cut, and (implicitly) healthy instrumentation. Those assumptions
are reasonable for scoping the challenge - and every one of them is false on a
real well. This module tests what happens when they fail.

  D1  RESERVOIR DECLINE   reservoir pressure falls steadily through the run
  D2  WATER-CUT STEP      a step increase in water cut cuts the oil rate for
                          the same lifted liquid
  F1  FROZEN TRANSMITTER  the BHP instrument sticks at its last value
  F2  SPIKED READING      the BHP instrument returns isolated wild values
  F3  MIS-CALIBRATED      the BHP instrument steps to a permanent +220 psi bias
  F4  IO-CARD FAILURE     BHP *and* WHP both read high - the realistic worst
                          case, because it removes the constraint redundancy
                          that limits the damage in F1-F3

The fault tests are run on the INFEASIBLE-target scenario on purpose. With a
comfortable rate target the pressure constraints are slack, so a corrupted BHP
reading does no harm and the test proves nothing. It is only when BHP_min is the
active constraint - the controller deciding how far it dares open - that a bad
BHP reading can actually hurt, because the controller is navigating by it.

D1/D2 test the disturbance estimator. F1/F2 test the bad-data layer: the
controller should detect the fault, stop trusting the tag, and - if it persists
- stop moving the choke rather than drive blind on a constrained variable.
================================================================================
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from controller import ChokeMPC, MPCConfig, run_closed_loop
from identification import OUTPUTS
from simulator import OperatingEnvelope, WellParameters, WellSimulator

Q_KEY, WHP_KEY, FLP_KEY, BHP_KEY = OUTPUTS


# ------------------------------------------------------------------------------
# Sensor fault injectors:  f(k, meas) -> meas  (corrupts only what the
# CONTROLLER sees; the logged plant state stays true)
# ------------------------------------------------------------------------------


def frozen_sensor(tag: str = BHP_KEY, start: int = 60, end: int = 100):
    """Fault injector: ``tag`` sticks at its last value between two intervals."""
    state = {}

    def fault(k, meas):
        if k == start:
            state["held"] = meas[tag]
        if start <= k < end and "held" in state:
            meas[tag] = state["held"]
        return meas
    return fault


def spiked_sensor(tag: str = BHP_KEY, at=(70, 71, 95), delta: float = -260.0):
    """Fault injector: isolated wild readings on ``tag`` at the given intervals."""
    def fault(k, meas):
        if k in at:
            meas[tag] = meas[tag] + delta
        return meas
    return fault


def biased_sensor(tags=(BHP_KEY,), start: int = 70, deltas=(+220.0,)):
    """
    One or more transmitters step to a wrong calibration and stay there.

    Reading a pressure HIGH is the dangerous direction: the controller believes
    it has margin to a minimum-pressure limit that it does not have, so it opens
    the choke further than it should.

    Corrupting BHP alone turns out to do little damage, because at the maximum
    safe operating point WHP is only ~5 psi behind BHP - so the WHP constraint,
    computed from a healthy transmitter, stops the controller almost at once.
    That constraint redundancy is a real and useful safety property. The
    realistic worst case is therefore a SHARED failure - one IO card carrying
    both pressure tags - which is what F4 tests.
    """
    def fault(k, meas):
        if k >= start:
            for tag, d in zip(tags, deltas):
                meas[tag] = meas[tag] + d
        return meas
    return fault


# ------------------------------------------------------------------------------
# Test definitions
# ------------------------------------------------------------------------------


def _audit(df, env, warmup=0):
    d = df.iloc[warmup:]
    viol = sum(1 for _, r in d.iterrows()
               if env.violations(r.WHP_true, r.FLP_true, r.BHP_true))
    tail = df.iloc[-25:]
    return {
        "violating_intervals": int(viol),
        "final_rate": float(tail.Q_true.mean()),
        "final_target": float(tail.Q_target.iloc[-1]),
        "rate_error": float(tail.Q_true.mean() - tail.Q_target.iloc[-1]),
        "min_BHP": float(d.BHP_true.min()),
        "min_WHP": float(d.WHP_true.min()),
        "min_FLP": float(d.FLP_true.min()),
        "intervals_flagged_bad": int((df.bad_tags.fillna("") != "").sum()),
        "intervals_held": int((df["mode"] == "HOLD_BAD_DATA").sum()),
        "max_move": float(df.dChoke.abs().max()),
    }


def run_all(model, env: OperatingEnvelope | None = None,
            cfg: MPCConfig | None = None, verbose: bool = True):
    """Run every disturbance and instrument-fault case; return traces + summary."""
    env = env or OperatingEnvelope.load()
    cfg = cfg or MPCConfig()
    out, summary = {}, {}

    # -- D1: reservoir pressure decline ------------------------------------
    # 0.30 psi/h over 160 h = 48 psi. Real decline is far slower; this
    # compresses months of depletion into the test window so the effect is
    # visible and the controller has to work for it.
    p = WellParameters(Pr_decline=0.30)
    sim = WellSimulator(u0=45.0, seed=11, params=p)
    ctrl = ChokeMPC(model, env, cfg, u0=45.0)
    out["D1"] = run_closed_loop(sim, ctrl, 130.0, 160)
    summary["D1_reservoir_decline"] = _audit(out["D1"], env, warmup=5)

    # -- D2: water-cut step -------------------------------------------------
    def wc_step(sim, t):
        if t >= 70:
            sim.p.water_cut = 0.12
    sim = WellSimulator(u0=45.0, seed=12, disturbances=[wc_step])
    ctrl = ChokeMPC(model, env, cfg, u0=45.0)
    out["D2"] = run_closed_loop(sim, ctrl, 120.0, 160)
    summary["D2_water_cut_step"] = _audit(out["D2"], env, warmup=5)

    # BHP_min is the active constraint here, so the controller is navigating
    # by the very measurement we are about to corrupt.
    FAULT_TARGET = lambda k: 120.0 if k < 30 else 200.0          # noqa: E731

    faults = {
        "F1_frozen_BHP":  (frozen_sensor(BHP_KEY, 60, 110), 13),
        "F2_spiked_BHP":  (spiked_sensor(), 14),
        "F3_biased_BHP":  (biased_sensor(), 15),
        "F4_io_card_BHP_and_WHP": (
            biased_sensor((BHP_KEY, WHP_KEY), 70, (+220.0, +45.0)), 16),
    }
    cfg_off = MPCConfig(); cfg_off.validate_data = False

    for name, (make_fault, seed) in faults.items():
        tag = name.split("_")[0]
        # with the bad-data layer ON
        sim = WellSimulator(u0=40.0, seed=seed)
        ctrl = ChokeMPC(model, env, cfg, u0=40.0)
        out[tag] = run_closed_loop(sim, ctrl, FAULT_TARGET, 140,
                                   sensor_fault=make_fault)
        summary[name] = _audit(out[tag], env)

        # and with it OFF, as the control case
        sim = WellSimulator(u0=40.0, seed=seed)
        ctrl = ChokeMPC(model, env, cfg_off, u0=40.0)
        out[f"{tag}_novalidation"] = run_closed_loop(
            sim, ctrl, FAULT_TARGET, 140,
            sensor_fault=faults[name][0].__class__ and make_fault)
        summary[f"{name}_NO_validation"] = _audit(out[f"{tag}_novalidation"], env)

    if verbose:
        print(pd.DataFrame(summary).T.to_string())
    return out, pd.DataFrame(summary).T
