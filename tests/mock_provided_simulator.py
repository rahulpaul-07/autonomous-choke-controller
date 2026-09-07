"""
A foreign simulator, used to prove that the adapter is genuinely generic and
that the controller does not depend on the plant it was developed against.

It is deliberately UNLIKE ours:
  * different gains, different time constants, a different choke characteristic
  * returns a **dict** with different key spellings, not a tuple
  * does **not** enforce the ramp-rate limit (the adapter must)
  * carries a pure transport delay on the flowline pressure

If this runs through the whole pipeline unchanged, so will any other.
"""
import numpy as np


class MockProvidedSimulator:
    """Interface: ``sim.step(choke_position) -> dict``."""

    def __init__(self, seed: int = 3):
        self.rng = np.random.default_rng(seed)
        self.tau = {"q": 3.2, "whp": 6.5, "flp": 4.4, "bhp": 17.0}
        self.y = {"q": 70.0, "whp": 300.0, "flp": 205.0, "bhp": 3250.0}
        self._flp_buf = [205.0]

    # a different steady-state map from ours, on purpose
    def _ss(self, u):
        u = float(np.clip(u, 0.0, 100.0))
        return {"q":   28.0 + 2.35 * u - 0.0055 * u**2,
                "whp": 312.0 - 1.10 * u - 0.0042 * u**2,
                "flp": 214.0 - 0.55 * u - 0.0051 * u**2,
                "bhp": 3402.0 - 9.10 * u - 0.0125 * u**2}

    def step(self, choke_position):
        ss = self._ss(choke_position)
        self._flp_buf.append(ss["flp"])
        for k in ("q", "whp", "bhp"):
            a = np.exp(-1.0 / self.tau[k])
            self.y[k] = a * self.y[k] + (1 - a) * ss[k]
        a = np.exp(-1.0 / self.tau["flp"])
        self.y["flp"] = a * self.y["flp"] + (1 - a) * self._flp_buf[max(len(self._flp_buf) - 3, 0)]
        n = self.rng.normal
        return {"oil_rate": self.y["q"] + n(0, 0.7),
                "wellhead_pressure": self.y["whp"] + n(0, 0.6),
                "flowline_pressure": self.y["flp"] + n(0, 0.5),
                "bottomhole_pressure": self.y["bhp"] + n(0, 2.6)}
