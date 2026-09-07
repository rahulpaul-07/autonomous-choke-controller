"""Equivalence and regression tests for the controller's fast roll-out."""
import os, sys
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from controller import ChokeMPC, MPCConfig
from identification import OUTPUTS, identify, run_step_test
from simulator import OperatingEnvelope


def test_fast_rollout_matches_general():
    """The closed-form roll-out must equal the general recursive one exactly."""
    model = identify(run_step_test())
    ctrl = ChokeMPC(model, OperatingEnvelope(), MPCConfig(), u0=40.0)
    ctrl.y_hat = {k: model[k].steady_state(40.0) for k in OUTPUTS}
    ctrl.bias = {k: 0.3 * (i + 1) for i, k in enumerate(OUTPUTS)}
    ctrl._initialised = True

    rng = np.random.default_rng(0)
    u1 = rng.uniform(0, 100, 37)
    u2 = rng.uniform(0, 100, 37)
    P = MPCConfig().P

    U = np.empty((len(u1), P))
    U[:, 0] = u1
    U[:, 1:] = u2[:, None]

    slow = ctrl._rollout(U, dict(ctrl.y_hat))
    fast = ctrl._rollout_2move(u1, u2, dict(ctrl.y_hat), P)

    for key in OUTPUTS:
        err = np.max(np.abs(slow[key] - fast[key]))
        assert err < 1e-9, f"{key}: max abs difference {err:.3e}"
    print("  fast roll-out matches the general recursion to < 1e-9  [OK]")


def test_closed_loop_regression():
    """Headline scenario results must not drift."""
    from controller import run_closed_loop
    from simulator import WellSimulator

    model = identify(run_step_test())
    env, cfg = OperatingEnvelope(), MPCConfig()
    expected = {"A": (100.0, 18.0, 90), "B": (150.0, 32.0, 140), "C": (165.0, 40.0, 140)}
    targets = {"A": lambda k: 100.0,
               "B": lambda k: 100.0 if k < 40 else 150.0,
               "C": lambda k: 120.0 if k < 30 else 200.0}
    seeds = {"A": 1, "B": 2, "C": 3}

    for tag, (want_rate, u0, n) in expected.items():
        sim = WellSimulator(u0=u0, seed=seeds[tag])
        ctrl = ChokeMPC(model, env, cfg, u0=u0)
        df = run_closed_loop(sim, ctrl, targets[tag], n)
        d = df  # scenario A now starts INSIDE the envelope: audit every interval
        viol = sum(1 for _, r in d.iterrows()
                   if env.violations(r.WHP_true, r.FLP_true, r.BHP_true))
        rate = df.iloc[-30:].Q_true.mean()
        assert viol == 0, f"scenario {tag}: {viol} constraint violations"
        assert abs(rate - want_rate) < 1.5, f"scenario {tag}: rate {rate:.2f}, expected ~{want_rate}"
        assert df.dChoke.abs().max() <= 5.0 + 1e-9, f"scenario {tag}: ramp limit breached"
        print(f"  scenario {tag}: rate {rate:7.2f} bbl/hr, 0 violations, ramp OK  [OK]")


if __name__ == "__main__":
    print("test_fast_rollout_matches_general"); test_fast_rollout_matches_general()
    print("test_closed_loop_regression");       test_closed_loop_regression()
    print("\nALL TESTS PASSED")
