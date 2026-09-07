"""
Proves the project can be driven by a simulator it has never seen.

The organisers confirmed no simulator would be provided and that any open-source
equivalent may be used, so ``src/simulator.py`` is ours. These tests run the full
pipeline against ``tests/mock_provided_simulator.py`` - deliberately unlike ours
in gain, dynamics, choke characteristic and return type - to show the controller
is not wedded to the plant it was developed on.
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from controller import ChokeMPC, MPCConfig, SteadyStateTargetOptimiser, run_closed_loop
from external_simulator import ExternalSimulator, _normalise
from identification import identify, run_step_test
from mock_provided_simulator import MockProvidedSimulator
from simulator import OperatingEnvelope


def test_return_shape_normalisation():
    """Tuples, lists, arrays and dicts of any sensible spelling all work."""
    want = (100.0, 250.0, 180.0, 3000.0)
    for form in (want, list(want), np.array(want),
                 {"Q": 100, "WHP": 250, "FLP": 180, "BHP": 3000},
                 {"oil_rate": 100, "wellhead_pressure": 250,
                  "flowline_pressure": 180, "bottomhole_pressure": 3000},
                 {"q_bbl_hr": 100, "whp_psi": 250, "flp_psi": 180, "bhp_psi": 3000}):
        assert _normalise(form) == want
    for bad in ((1, 2, 3), {"a": 1, "b": 2, "c": 3, "d": 4}):
        try:
            _normalise(bad)
        except (ValueError, KeyError):
            continue
        raise AssertionError(f"{bad} should have been rejected")
    print("  return-shape normalisation accepts 6 forms and rejects 2 bad ones  [OK]")


def test_adapter_enforces_ramp_limit():
    """The provided simulator ignores the ramp limit; the adapter must not."""
    sim = ExternalSimulator(MockProvidedSimulator(), u0=40.0, du_max=5.0)
    sim.step(100.0)
    assert abs(sim.u - 45.0) < 1e-9, f"ramp limit not enforced: choke jumped to {sim.u}"
    sim.step(0.0)
    assert abs(sim.u - 40.0) < 1e-9
    print("  adapter enforces the +/-5 %/interval ramp limit on the plant side  [OK]")


def test_full_pipeline_on_foreign_simulator():
    """Step test -> identification -> SSTO -> closed loop, with no code changes."""
    cfg, env = MPCConfig(), OperatingEnvelope.load()
    factory = lambda u0: ExternalSimulator(MockProvidedSimulator(), u0=u0,
                                           du_max=cfg.du_max, enforce_ramp=False)
    model = identify(run_step_test(sim_factory=factory))
    assert model.summary().r2.min() > 0.95, "identification failed on the foreign plant"

    lim = SteadyStateTargetOptimiser(model, env, cfg).envelope_limits()
    assert lim, "no feasible steady state found for the foreign plant"
    Qmax = lim["Q_max"]

    # feasible target -> tracked; infeasible target -> capped, not chased
    u0 = lim["u_min"] + 0.35 * (lim["u_max"] - lim["u_min"])
    for label, target, expect_capped in (("feasible", 0.85 * Qmax, False),
                                         ("infeasible", 1.25 * Qmax, True)):
        sim = ExternalSimulator(MockProvidedSimulator(), u0=u0, du_max=cfg.du_max)
        ctrl = ChokeMPC(model, env, cfg, u0=u0)
        df = run_closed_loop(sim, ctrl, target, 140)
        tail = df.iloc[-30:]
        rate, ach = tail.OilRate_bbl_hr.mean(), tail.Q_achievable.mean()

        assert df.dChoke.abs().max() <= cfg.du_max + 1e-9, "ramp limit breached"
        assert abs(rate - ach) < 2.5, f"{label}: offset {rate - ach:.2f} bbl/hr"
        if expect_capped:
            assert ach < target - 1.0, "infeasible target was not capped"
            assert "CONSTRAINED" in set(df["mode"]), "controller did not report CONSTRAINED"
        else:
            assert abs(ach - target) < 1.5, "feasible target was not tracked"
        print(f"  {label:10s} target {target:6.1f} -> achievable {ach:6.1f}, "
              f"achieved {rate:6.1f} bbl/hr  [OK]")

    print(f"  identification R2 on the foreign plant: "
          f"{model.summary().r2.min():.4f} – {model.summary().r2.max():.4f}  [OK]")


def test_limits_are_config_driven():
    """Editing config/operating_limits.json alone changes the whole analysis."""
    import json
    import tempfile
    base = OperatingEnvelope.load()
    with open(os.path.join(ROOT, "config", "operating_limits.json")) as f:
        cfg = json.load(f)
    cfg["limits"]["BHP_min"]["value"] = 2950.0
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
        tmp = f.name
    tightened = OperatingEnvelope.load(tmp)
    os.unlink(tmp)

    from simulator import max_safe_rate
    q_base = max_safe_rate(base)["Q"]
    q_tight = max_safe_rate(tightened)["Q"]
    assert q_tight < q_base - 10, "tightening BHP_min did not reduce the max safe rate"
    print(f"  BHP_min 2850 -> 2950 psi changes max safe rate "
          f"{q_base:.1f} -> {q_tight:.1f} bbl/hr, no code edited  [OK]")

    try:
        OperatingEnvelope(WHP_min=300.0, WHP_max=200.0).validate()
    except ValueError:
        print("  an inverted limit set is rejected rather than used silently  [OK]")
    else:
        raise AssertionError("inverted limits were accepted")


if __name__ == "__main__":
    for fn in (test_return_shape_normalisation, test_adapter_enforces_ramp_limit,
               test_full_pipeline_on_foreign_simulator, test_limits_are_config_driven):
        print(fn.__name__)
        fn()
    print("\nALL EXTERNAL-SIMULATOR TESTS PASSED")
