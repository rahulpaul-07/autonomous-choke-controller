"""
external_simulator.py
================================================================================
Adapter that lets this project be driven by ANY simulator.

The problem statement promised a Python simulator with the interface

    Q, WHP, FLP, BHP = simulator.step(choke_position)

but the organisers confirmed in writing that no simulator would be provided and
that any open-source equivalent may be used, so this project ships its own
(``src/simulator.py``).

That makes the controller's independence from the plant worth demonstrating
rather than assuming. Wrap any other simulator once and every part of this
project - the MPC, the closed-loop driver, the step test, the identification,
the baselines, the robustness sweeps and the stress tests - runs against it
unchanged:

    from external_simulator import ExternalSimulator
    sim = ExternalSimulator(TheirSimulator(), u0=40.0)
    Q, WHP, FLP, BHP = sim.step(45.0)

What the adapter takes care of:

  * **Return shape.** Accepts a 4-tuple, a list, a numpy array, or a dict keyed
    on any reasonable spelling of the four variables.
  * **The ramp-rate limit.** Applied on the plant side regardless of whether the
    provided simulator enforces it, so an over-aggressive controller still
    cannot cheat.
  * **The attributes our driver logs.** A foreign simulator exposes no
    noise-free internal state, so ``Q_true`` etc. mirror the measurements and
    :attr:`has_true_state` is False. Constraint auditing then happens on the
    measurement, which is the stricter reading - see the note in
    :meth:`ExternalSimulator.audit_note`.
================================================================================
"""
from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import numpy as np

_ALIASES = {
    "Q":   ("q", "oil", "oilrate", "oil_rate", "rate", "oilrate_bbl_hr", "q_bbl_hr"),
    "WHP": ("whp", "wellhead", "wellhead_pressure", "whp_psi", "p_wh"),
    "FLP": ("flp", "flowline", "flowline_pressure", "flp_psi", "p_fl"),
    "BHP": ("bhp", "bottomhole", "bottom_hole", "bottomhole_pressure", "bhp_psi", "p_bh"),
}


def _normalise(result) -> tuple[float, float, float, float]:
    """Coerce whatever the provided simulator returns into (Q, WHP, FLP, BHP)."""
    if isinstance(result, dict):
        out = []
        low = {k.lower().replace(" ", "_"): v for k, v in result.items()}
        for tag, names in _ALIASES.items():
            for n in names:
                if n in low:
                    out.append(float(low[n]))
                    break
            else:
                raise KeyError(
                    f"the provided simulator returned a dict with keys "
                    f"{list(result)}, and none of them looks like {tag}. "
                    f"Add the spelling to _ALIASES in external_simulator.py.")
        return tuple(out)

    seq = np.atleast_1d(np.asarray(result, dtype=float)).ravel()
    if seq.size != 4:
        raise ValueError(
            f"expected 4 values (Q, WHP, FLP, BHP) from simulator.step(), got "
            f"{seq.size}. If the provided simulator returns something else, "
            f"pass a custom `unpack` callable to ExternalSimulator.")
    return tuple(float(x) for x in seq)


class ExternalSimulator:
    """Wraps a foreign simulator so the rest of this project can drive it."""

    def __init__(self, sim, u0: float = 30.0, Ts: float = 1.0,
                 du_max: float = 5.0, enforce_ramp: bool = True,
                 unpack=None, step_name: str = "step") -> None:
        """Wrap ``sim`` and take one step to establish a consistent start state."""
        self.sim = sim
        self.Ts = float(Ts)
        self.du_max = float(du_max)
        self.enforce_ramp = bool(enforce_ramp)
        self._unpack = unpack or _normalise
        self._step = getattr(sim, step_name)
        self.has_true_state = False

        self.u = float(np.clip(u0, 0.0, 100.0))
        # a foreign simulator has no notion of "settle at u0", so take one step
        # to establish a consistent starting measurement
        self.Q, self.WHP, self.FLP, self.BHP = self._unpack(self._step(self.u))

        # our driver logs these; a foreign simulator does not expose them
        self.p = SimpleNamespace(Pr=float("nan"), water_cut=0.0, du_max=self.du_max)
        self.t = 0.0
        self.history: list[dict] = []

    # -- the interface the rest of the project expects -----------------------

    def step(self, choke_position: float) -> tuple:
        """Apply a choke position and advance the wrapped simulator one interval."""
        u_cmd = float(np.clip(choke_position, 0.0, 100.0))
        if self.enforce_ramp:
            self.u += float(np.clip(u_cmd - self.u, -self.du_max, self.du_max))
        else:
            self.u = u_cmd

        self.Q, self.WHP, self.FLP, self.BHP = self._unpack(self._step(self.u))
        self.t += self.Ts
        self.history.append({"Time_hr": self.t, "Choke_pct": self.u,
                             "OilRate_bbl_hr": self.Q, "WHP_psi": self.WHP,
                             "FLP_psi": self.FLP, "BHP_psi": self.BHP})
        return self.Q, self.WHP, self.FLP, self.BHP

    def measure(self) -> tuple:
        """Latest (Q, WHP, FLP, BHP) without advancing time."""
        return self.Q, self.WHP, self.FLP, self.BHP

    def dataframe(self) -> "pd.DataFrame":
        """Logged history of the wrapped simulator as a DataFrame."""
        import pandas as pd
        return pd.DataFrame(self.history)

    @staticmethod
    def audit_note() -> str:
        """Caveat to print alongside results from an external simulator."""
        return (
            "This run used an external simulator, which exposes no noise-free\n"
            "internal state. Constraint compliance is therefore audited on the\n"
            "MEASUREMENT rather than the true process value. That is the stricter\n"
            "test: a noisy reading can breach a limit while the true pressure is\n"
            "inside it, so the violation count reported here is an upper bound.")

    # -- convenience ---------------------------------------------------------

    def factory(self, **kw):
        """Return ``f(u0) -> ExternalSimulator`` for :func:`run_step_test`."""
        def make(u0):
            fresh = type(self.sim)() if callable(type(self.sim)) else self.sim
            return ExternalSimulator(fresh, u0=u0, Ts=self.Ts,
                                     du_max=self.du_max, enforce_ramp=False,
                                     unpack=self._unpack, **kw)
        return make


def load_simulator(spec: str):
    """
    Import a simulator from a ``module:Callable`` spec, e.g.

        load_simulator("honeywell_sim:WellSim")
        load_simulator("well_simulator:make_simulator")
    """
    if ":" not in spec:
        raise ValueError("expected 'module:Callable', e.g. 'honeywell_sim:WellSim'")
    mod_name, attr = spec.split(":", 1)
    sys.path.insert(0, ".")
    mod = importlib.import_module(mod_name)
    return getattr(mod, attr)
