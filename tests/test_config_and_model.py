"""Controller configuration guards, the candidate-move grid, and the committed-model loader."""
import os, sys, tempfile

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from controller import ChokeMPC, MPCConfig, _move_grid
from identification import OUTPUTS, load_model
from simulator import OperatingEnvelope

MODEL_CSV = os.path.join(ROOT, "results", "identified_model.csv")


def test_one_point_grid_means_hold():
    """np.linspace(-a, a, 1) is [-a]; a one-point move grid must be 'hold' instead."""
    assert np.array_equal(_move_grid(5.0, 1), [0.0])
    assert 0.0 in _move_grid(5.0, 41), "odd grids must contain the hold move"
    print("  one-point grid is hold, odd grids contain hold  [OK]")


def test_single_move_plan_holds_after_first_move():
    """M = 1 is one free move then hold: every candidate's second move is zero."""
    model = load_model(MODEL_CSV)
    for M, n2 in ((1, 11), (1, 1), (2, 1)):
        cfg = MPCConfig(M=M, n2=n2)
        ctrl = ChokeMPC(model, OperatingEnvelope(), cfg, u0=40.0)
        _, _, du1, du2 = ctrl._candidate_plans()
        assert np.all(du2 == 0.0), f"M={M}, n2={n2}: second move not held"
        assert np.any(du1 == 0.0), f"M={M}, n2={n2}: hold missing from the first move"
    print("  single-move plans hold after move 1  [OK]")


def test_invalid_config_is_rejected():
    """A configuration the controller cannot run raises rather than misbehaving."""
    bad = [dict(P=0), dict(M=0), dict(n1=0), dict(du_max=0.0), dict(w_move=-1.0),
           dict(backoff=-1.0), dict(backoff_frac=0.6), dict(bias_gain=1.5),
           dict(u_min=50.0, u_max=10.0)]
    for fields in bad:
        try:
            MPCConfig(**fields)
        except ValueError:
            continue
        raise AssertionError(f"MPCConfig({fields}) was accepted")
    MPCConfig()  # the defaults are valid
    print(f"  {len(bad)} invalid configurations rejected, defaults accepted  [OK]")


def test_committed_model_loads():
    """The committed model loads as four channels with the study's parameters."""
    model = load_model(MODEL_CSV)
    assert set(model.channels) == set(OUTPUTS)
    for key in OUTPUTS:
        ch = model[key]
        assert ch.tau > 0 and ch.r2 > 0.99, f"{key}: implausible fit {ch}"
    print("  committed model loads, 4 channels, R2 > 0.99  [OK]")


def test_corrupt_model_is_rejected():
    """A truncated or non-finite model file raises instead of being used."""
    with open(MODEL_CSV) as f:
        lines = f.read().splitlines()
    cases = {"missing channel": lines[:-1],
             "non-finite tau": [lines[0], *(l.replace(l.split(",")[4], "nan", 1)
                                           if i == 0 else l for i, l in enumerate(lines[1:]))]}
    for label, body in cases.items():
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("\n".join(body))
        try:
            load_model(f.name)
        except ValueError:
            continue
        finally:
            os.unlink(f.name)
        raise AssertionError(f"{label}: corrupt model was accepted")
    print("  corrupt model files rejected  [OK]")


if __name__ == "__main__":
    for t in (test_one_point_grid_means_hold, test_single_move_plan_holds_after_first_move,
              test_invalid_config_is_rejected, test_committed_model_loads,
              test_corrupt_model_is_rejected):
        print(t.__name__); t()
    print("\nALL CONFIG AND MODEL TESTS PASSED")
