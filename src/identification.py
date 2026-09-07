"""
identification.py
================================================================================
Open-loop step-test design, execution and control-oriented model identification.

The controller must NOT use the simulator's internal physics. Instead we run
open-loop step tests on the simulator, log the data, and fit a *control-oriented*
Hammerstein model:

        static gain :   y_ss(u) = a0 + a1*u + a2*u^2      (nonlinear, per output)
        dynamics    :   tau_y * dy/dt = y_ss(u(t-theta)) - y

This is the classic "nonlinear steady-state map + linear low-order dynamics"
structure used by industrial APC packages, and it is what the MPC predicts with.
================================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

OUTPUTS = ["OilRate_bbl_hr", "WHP_psi", "FLP_psi", "BHP_psi"]
SHORT = {"OilRate_bbl_hr": "Q", "WHP_psi": "WHP", "FLP_psi": "FLP", "BHP_psi": "BHP"}


# ------------------------------------------------------------------------------
# Step-test design
# ------------------------------------------------------------------------------


def run_step_test(
    steps: list[tuple[float, float]] | None = None,
    u0: float = 20.0,
    noise: bool = True,
    seed: int = 7,
    sim_factory=None,
) -> pd.DataFrame:
    """
    Execute an open-loop multi-step test on the simulator.

    ``steps``       list of ``(choke_pct, hold_hours)``. The default sequence
                    spans the operating envelope in both directions so that the
                    static nonlinearity and the dynamics are both well excited.
    ``sim_factory`` optional callable ``f(u0) -> simulator``. Supply this to run
                    the identical step test against a DIFFERENT simulator - for
                    example the one provided by Honeywell, wrapped in
                    :class:`external_simulator.ExternalSimulator`. Defaults to
                    our own :class:`simulator.WellSimulator`.
    """
    from simulator import WellSimulator

    if steps is None:
        steps = [
            (20.0, 40),   # settle at low rate
            (30.0, 40),   # + 10 %
            (45.0, 45),   # + 15 %
            (60.0, 50),   # + 15 %
            (50.0, 45),   # - 10 %  (check for hysteresis / asymmetry)
            (70.0, 55),   # + 20 %  (near the top of the envelope)
            (35.0, 50),   # - 35 %  (large negative move)
            (55.0, 45),   # + 20 %
        ]

    # The +/-5 %/interval ramp limit is stated under "the CONTROLLER must operate
    # within the following limits". An open-loop step test is a commissioning
    # experiment, not the controller running, and the brief explicitly asks for
    # "choke STEP changes" - so the steps are applied as steps. Identification is
    # insensitive to this either way: see tests/test_step_test_ramp.py, which
    # repeats the identification with the ramp limit enforced and confirms the
    # gains and time constants are unchanged to within a few percent.
    if sim_factory is None:
        sim = WellSimulator(u0=u0, noise=noise, seed=seed, enforce_ramp=False)
    else:
        sim = sim_factory(u0)
    rows = []
    t = 0.0
    for u_cmd, hold in steps:
        for _ in range(int(hold)):
            Q, WHP, FLP, BHP = sim.step(u_cmd)
            t += sim.Ts
            rows.append(
                {
                    "Time_hr": t,
                    "Choke_pct": sim.u,
                    "OilRate_bbl_hr": Q,
                    "WHP_psi": WHP,
                    "FLP_psi": FLP,
                    "BHP_psi": BHP,
                }
            )
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------
# Hammerstein model
# ------------------------------------------------------------------------------


@dataclass
class ChannelModel:
    """Identified model for one output channel."""

    name: str
    a0: float
    a1: float
    a2: float
    tau: float
    theta: float
    rmse: float
    r2: float

    def gain_curve(self, u) -> np.ndarray:
        """Static gain curve y_ss(u) evaluated over an array of choke openings."""
        u = np.asarray(u, dtype=float)
        return self.a0 + self.a1 * u + self.a2 * u**2

    def steady_state(self, u: float) -> float:
        """Steady-state value of this output at choke opening ``u`` [%]."""
        return float(self.a0 + self.a1 * u + self.a2 * u**2)

    def advance(self, y: float, u_delayed: float, Ts: float = 1.0) -> float:
        """One-step prediction using exact ZOH discretisation of the lag."""
        a = np.exp(-Ts / self.tau)
        return a * y + (1.0 - a) * self.steady_state(u_delayed)


@dataclass
class PlantModel:
    """Container for the four identified output channels."""

    channels: dict

    def __getitem__(self, key: str) -> ChannelModel:
        return self.channels[key]

    def summary(self) -> pd.DataFrame:
        """One row per output channel: fitted parameters and fit quality."""
        return pd.DataFrame([asdict(c) for c in self.channels.values()]).set_index("name")

    def steady_state(self, u: float) -> dict:
        """Steady state of all four outputs at choke opening ``u`` [%]."""
        return {k: c.steady_state(u) for k, c in self.channels.items()}

    def predict(self, u_seq, y0: dict, Ts: float = 1.0) -> pd.DataFrame:
        """Free-run (open-loop) prediction of the model over a choke sequence."""
        u_seq = np.asarray(u_seq, dtype=float)
        y = dict(y0)
        rows = []
        for k in range(len(u_seq)):
            for key, ch in self.channels.items():
                y[key] = ch.advance(y[key], u_seq[k], Ts)
            rows.append(dict(y))
        return pd.DataFrame(rows)


def _simulate_channel(p, u, y0, Ts=1.0):
    a0, a1, a2, tau, theta = p
    n = len(u)
    y = np.empty(n)
    y[0] = y0
    a = np.exp(-Ts / max(tau, 1e-3))
    for k in range(1, n):
        kk = k - 1 - theta / Ts
        i0 = int(np.floor(kk))
        fr = kk - i0
        i0c = min(max(i0, 0), n - 1)
        i1c = min(max(i0 + 1, 0), n - 1)
        ud = u[i0c] * (1 - fr) + u[i1c] * fr
        yss = a0 + a1 * ud + a2 * ud**2
        y[k] = a * y[k - 1] + (1 - a) * yss
    return y


def identify(df: pd.DataFrame, Ts: float = 1.0) -> PlantModel:
    """Fit a Hammerstein model to every output channel of a step-test dataset."""
    u = df["Choke_pct"].values.astype(float)
    channels = {}
    for col in OUTPUTS:
        ym = df[col].values.astype(float)

        def residual(p, ym=ym):
            return _simulate_channel(p, u, ym[0], Ts) - ym

        A = np.vstack([np.ones_like(u), u, u**2]).T
        lin = np.linalg.lstsq(A, ym, rcond=None)[0]

        best = None
        for tau0 in (2.0, 5.0, 10.0, 20.0):
            for th0 in (0.0, 1.0, 2.0):
                r = least_squares(
                    residual,
                    list(lin) + [tau0, th0],
                    bounds=([-1e6, -1e6, -1e6, 0.2, 0.0], [1e6, 1e6, 1e6, 80.0, 5.0]),
                )
                if best is None or r.cost < best.cost:
                    best = r
        p = best.x
        e = residual(p)
        channels[col] = ChannelModel(
            name=col,
            a0=float(p[0]), a1=float(p[1]), a2=float(p[2]),
            tau=float(p[3]), theta=float(p[4]),
            rmse=float(np.sqrt(np.mean(e**2))),
            r2=float(1 - np.sum(e**2) / np.sum((ym - ym.mean()) ** 2)),
        )
    return PlantModel(channels)


def cross_validate(model: PlantModel, df: pd.DataFrame, Ts: float = 1.0) -> pd.DataFrame:
    """Score an identified model against an independent dataset (free-run)."""
    u = df["Choke_pct"].values.astype(float)
    rows = []
    for col in OUTPUTS:
        ym = df[col].values.astype(float)
        ch = model[col]
        yp = _simulate_channel([ch.a0, ch.a1, ch.a2, ch.tau, ch.theta], u, ym[0], Ts)
        e = yp - ym
        rows.append(
            {
                "output": SHORT[col],
                "RMSE": float(np.sqrt(np.mean(e**2))),
                "MAE": float(np.mean(np.abs(e))),
                "R2": float(1 - np.sum(e**2) / np.sum((ym - ym.mean()) ** 2)),
                "range": float(ym.max() - ym.min()),
            }
        )
    return pd.DataFrame(rows).set_index("output")
