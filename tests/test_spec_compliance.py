"""
Asserts the implementation against the literal wording of the problem statement.

Each test quotes the requirement it is checking, so a reviewer can match code to
brief line by line.
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from controller import ChokeMPC, MPCConfig, run_closed_loop
from identification import identify, run_step_test
from simulator import OperatingEnvelope, WellSimulator

ENV, CFG = OperatingEnvelope.load(), MPCConfig()
SCEN = {"A": (18.0, 90, lambda k: 100.0, 1),
        "B": (32.0, 140, lambda k: 100.0 if k < 40 else 150.0, 2),
        "C": (40.0, 140, lambda k: 120.0 if k < 30 else 200.0, 3)}


def _runs(model):
    for tag, (u0, n, tgt, seed) in SCEN.items():
        sim = WellSimulator(u0=u0, seed=seed)
        ctrl = ChokeMPC(model, ENV, CFG, u0=u0)
        yield tag, run_closed_loop(sim, ctrl, tgt, n)


def test_simulator_interface(model):
    """'Q, WHP, FLP, BHP = simulator.step(choke_position)'"""
    out = WellSimulator(u0=40.0).step(45.0)
    assert isinstance(out, tuple) and len(out) == 4, "step() must return exactly 4 values"
    assert all(np.isfinite(out)), "step() returned a non-finite value"
    print("  step(choke) returns exactly (Q, WHP, FLP, BHP)  [OK]")


def test_control_interval(model):
    """'Control Interval (Ts): 1 hour'"""
    assert WellSimulator.Ts == 1.0 and CFG.Ts == 1.0
    for tag, df in _runs(model):
        dt = np.diff(df.Time_hr.values)
        assert np.allclose(dt, 1.0), f"scenario {tag}: control interval is not 1 h"
    print("  controller executes once per hour in every scenario  [OK]")


def test_choke_range(model):
    """'0 % <= Choke Opening <= 100 %'"""
    for tag, df in _runs(model):
        assert df.Choke_pct.min() >= 0.0 and df.Choke_pct.max() <= 100.0, tag
    print("  choke stays within 0-100 % in every scenario  [OK]")


def test_ramp_rate_limit(model):
    """'Maximum Choke Movement = +/-5 % per control interval'"""
    for tag, df in _runs(model):
        worst = df.dChoke.abs().max()
        assert worst <= 5.0 + 1e-9, f"scenario {tag}: choke moved {worst:.3f} %"
        step = np.abs(np.diff(df.Choke_pct.values)).max()
        assert step <= 5.0 + 1e-9, f"scenario {tag}: position jumped {step:.3f} %"
    print("  every choke move is within +/-5 %/interval  [OK]")


def test_active_constraints_never_violated(model):
    """'WHP, FLP and BHP must ALWAYS remain within their safe operating ranges'"""
    for tag, df in _runs(model):
        bad = [(r.Time_hr, ENV.violations(r.WHP_true, r.FLP_true, r.BHP_true))
               for _, r in df.iterrows()
               if ENV.violations(r.WHP_true, r.FLP_true, r.BHP_true)]
        assert not bad, f"scenario {tag}: {len(bad)} violation(s), first at t={bad[0][0]} h {bad[0][1]}"
        print(f"  scenario {tag}: 0 violations across all {len(df)} intervals  [OK]")


def test_controller_receives_choke_position(model):
    """'the controller receives ... Current Choke Position'"""
    sim = WellSimulator(u0=40.0, seed=5)
    ctrl = ChokeMPC(model, ENV, CFG, u0=40.0)
    ctrl.compute(dict(zip(["OilRate_bbl_hr", "WHP_psi", "FLP_psi", "BHP_psi"],
                          sim.measure())), 120.0, choke_position=40.0)
    # lie to the controller about the valve position: it must believe the readback
    ctrl.compute(dict(zip(["OilRate_bbl_hr", "WHP_psi", "FLP_psi", "BHP_psi"],
                          sim.measure())), 120.0, choke_position=55.0)
    assert abs(ctrl.u - 55.0) <= CFG.du_max + 1e-9, \
        "controller ignored the measured choke position"
    assert abs(ctrl.u_tracking_error) > 1.0, "controller did not detect the mismatch"
    print("  controller reads back the measured choke position  [OK]")


def test_infeasible_target_behaviour(model):
    """'If the target rate cannot be achieved safely, operate at the maximum
    achievable production rate without violating constraints.'"""
    from simulator import max_safe_rate
    cap = max_safe_rate(ENV)
    sim = WellSimulator(u0=40.0, seed=3)
    ctrl = ChokeMPC(model, ENV, CFG, u0=40.0)
    df = run_closed_loop(sim, ctrl, lambda k: 120.0 if k < 30 else 200.0, 140)
    tail = df.iloc[-30:]
    assert "CONSTRAINED" in set(df["mode"]), "controller never reported CONSTRAINED"
    assert tail.Q_true.mean() < 200.0, "controller claimed to reach an infeasible target"
    frac = tail.Q_true.mean() / cap["Q"]
    assert frac > 0.97, f"only reached {100*frac:.1f} % of the maximum safe rate"
    print(f"  infeasible target -> settles at {100*frac:.1f} % of the max safe rate, "
          f"reports CONSTRAINED  [OK]")


def test_required_plot_channels(model):
    """'For each scenario provide trends for: Target Oil Rate, Actual Oil Rate,
    WHP, FLP, BHP, Choke Position'"""
    need = ["Q_target", "OilRate_bbl_hr", "WHP_psi", "FLP_psi", "BHP_psi", "Choke_pct"]
    for tag, df in _runs(model):
        missing = [c for c in need if c not in df.columns]
        assert not missing, f"scenario {tag} missing {missing}"
        assert os.path.exists(os.path.join(ROOT, "figures", f"scenario_{tag}.png"))
    print("  all six required trends logged and plotted for A, B and C  [OK]")


def test_identification_insensitive_to_step_test_ramp(model):
    """The +/-5 % limit constrains the CONTROLLER; open-loop step tests apply
    genuine steps. Confirm that choice does not change the identified model."""
    ramped = identify(run_step_test(
        sim_factory=lambda u0: WellSimulator(u0=u0, noise=True, seed=7, enforce_ramp=True)))
    for k in ("OilRate_bbl_hr", "WHP_psi", "FLP_psi", "BHP_psi"):
        g0 = model[k].a1 + 2 * model[k].a2 * 50
        g1 = ramped[k].a1 + 2 * ramped[k].a2 * 50
        assert abs(g1 - g0) / abs(g0) < 0.05, f"{k}: gain moved {100*abs(g1-g0)/abs(g0):.1f} %"
        assert abs(ramped[k].tau - model[k].tau) / model[k].tau < 0.05, f"{k}: tau moved"
    print("  identified gains and time constants shift < 5 % either way  [OK]")


if __name__ == "__main__":
    model = identify(run_step_test())
    for fn in (test_simulator_interface, test_control_interval, test_choke_range,
               test_ramp_rate_limit, test_active_constraints_never_violated,
               test_controller_receives_choke_position, test_infeasible_target_behaviour,
               test_required_plot_channels,
               test_identification_insensitive_to_step_test_ramp):
        print(fn.__name__)
        fn(model)
    print("\nEVERY LITERAL REQUIREMENT IN THE PROBLEM STATEMENT IS SATISFIED")
