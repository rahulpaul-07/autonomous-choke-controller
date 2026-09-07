"""
baselines.py
================================================================================
Two honest baselines to quantify what the MPC is actually worth.

A controller that reports "zero violations" in isolation says nothing. The
question a production engineer asks is: *better than what?*

  1. PI CONTROLLER  - what a conventional single-loop scheme does. Tuned
     properly by IMC on the identified FOPDT model, with anti-windup and the
     same ramp-rate limit the MPC obeys. This is a good-faith PI, not a
     straw man: it is given the same model knowledge and the same actuator.
     What it does NOT have is any knowledge of the pressure constraints -
     because a rate PI loop structurally cannot see them.

  2. OPERATOR RULE  - what a cautious human does: pick one choke opening that
     is comfortably inside every limit and leave it there. Modelled as the
     largest opening whose predicted steady state keeps a 50 psi margin on the
     binding constraint (a typical rule-of-thumb back-off).

Both are run through the identical scenarios, plant, noise seeds and ramp
limits as the MPC.
================================================================================
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from identification import OUTPUTS, PlantModel
from simulator import OperatingEnvelope, WellSimulator

Q_KEY, WHP_KEY, FLP_KEY, BHP_KEY = OUTPUTS


# ------------------------------------------------------------------------------
# 1. PI controller
# ------------------------------------------------------------------------------


@dataclass
class PIConfig:
    """Tuning for the PI baseline. Zero gains are filled in by IMC tuning."""
    Kc: float = 0.0             # %/(bbl/hr)   - set by IMC tuning if left at 0
    Ti: float = 0.0             # h            - set by IMC tuning if left at 0
    Ts: float = 1.0
    du_max: float = 5.0
    u_min: float = 0.0
    u_max: float = 100.0
    lam: float = 4.0            # IMC closed-loop time constant [h]


class PIController:
    """
    Velocity-form PI on oil rate, IMC-tuned on the identified model.

    IMC-PI for a first-order process  K/(tau*s+1)  with closed-loop time
    constant lambda:      Kc = tau / (K * lambda),      Ti = tau
    """

    def __init__(self, model: PlantModel, cfg: PIConfig | None = None,
                 u0: float = 30.0, u_ref: float = 45.0) -> None:
        """IMC-tune the loop on the identified model gain at ``u_ref``."""
        self.cfg = cfg or PIConfig()
        ch = model[Q_KEY]
        K = ch.a1 + 2 * ch.a2 * u_ref          # local process gain [bbl/hr per %]
        tau = ch.tau
        if self.cfg.Kc == 0.0:
            self.cfg.Kc = tau / (K * self.cfg.lam)
        if self.cfg.Ti == 0.0:
            self.cfg.Ti = tau
        self.u = float(np.clip(u0, self.cfg.u_min, self.cfg.u_max))
        self.e_prev = None
        self.log = []

    def compute(self, measurement: dict, Q_target: float) -> float:
        """
        Velocity (incremental) form:

            du = Kc * (e[k] - e[k-1])  +  (Kc * Ts / Ti) * e[k]

        The velocity form is inherently anti-windup: when the move is clipped by
        the ramp limit or the valve stop, the un-taken increment is simply never
        accumulated, so there is no integral state to unwind.
        """
        c = self.cfg
        e = float(Q_target) - float(measurement[Q_KEY])
        e_prev = self.e_prev if self.e_prev is not None else e

        du_ideal = c.Kc * (e - e_prev) + (c.Kc * c.Ts / c.Ti) * e
        du = float(np.clip(du_ideal, -c.du_max, c.du_max))
        u_new = float(np.clip(self.u + du, c.u_min, c.u_max))

        self.e_prev = e
        du_actual = u_new - self.u
        self.u = u_new
        self.log.append({"du": du_actual, "e": e,
                         "saturated": abs(du_ideal) > c.du_max + 1e-9})
        return self.u


# ------------------------------------------------------------------------------
# 2. Operator rule
# ------------------------------------------------------------------------------


def operator_choke(model: PlantModel, env: OperatingEnvelope,
                   Q_target: float, margin_frac: float = 0.10) -> float:
    """
    The opening a cautious operator would dial in for this target.

    The rule modelled here is the common one: stay a fixed fraction of each
    variable's operating SPAN away from its limits, then pick the smallest
    opening inside that comfort zone that still meets the target. A single
    absolute psi margin is not realistic, because the same 50 psi is trivial for
    BHP (450 psi span) and crippling for FLP (70 psi span).

    Note this operator is SAFE - conservatism is the whole point. What it costs
    is production, and that is exactly what we are trying to measure.
    """
    u_grid = np.arange(0.0, 100.05, 0.05)
    ss = {k: model[k].gain_curve(u_grid) for k in OUTPUTS}

    spans = {WHP_KEY: env.WHP_max - env.WHP_min,
             FLP_KEY: env.FLP_max - env.FLP_min,
             BHP_KEY: env.BHP_max - env.BHP_min}
    m = {k: margin_frac * v for k, v in spans.items()}

    feasible = (
        (ss[WHP_KEY] >= env.WHP_min) & (ss[WHP_KEY] <= env.WHP_max)
        & (ss[FLP_KEY] >= env.FLP_min) & (ss[FLP_KEY] <= env.FLP_max)
        & (ss[BHP_KEY] >= env.BHP_min) & (ss[BHP_KEY] <= env.BHP_max)
    )
    comfy = feasible & (
        (ss[WHP_KEY] >= env.WHP_min + m[WHP_KEY]) & (ss[WHP_KEY] <= env.WHP_max - m[WHP_KEY])
        & (ss[FLP_KEY] >= env.FLP_min + m[FLP_KEY]) & (ss[FLP_KEY] <= env.FLP_max - m[FLP_KEY])
        & (ss[BHP_KEY] >= env.BHP_min + m[BHP_KEY]) & (ss[BHP_KEY] <= env.BHP_max - m[BHP_KEY])
    )
    zone = comfy if comfy.any() else feasible      # never fall outside the envelope
    idx = np.where(zone)[0]
    reachable = idx[ss[Q_KEY][idx] >= Q_target]
    j = reachable[0] if len(reachable) else idx[int(np.argmax(ss[Q_KEY][idx]))]
    return float(u_grid[j])


# ------------------------------------------------------------------------------
# Runners
# ------------------------------------------------------------------------------


def run_pi(sim: WellSimulator, ctrl: PIController, target_schedule,
           n_steps: int) -> pd.DataFrame:
    """Closed-loop run of the PI baseline, logged like the MPC runs."""
    if not callable(target_schedule):
        _c = float(target_schedule)
        target_schedule = lambda k: _c  # noqa: E731
    meas = dict(zip(OUTPUTS, sim.measure()))
    rows = []
    for k in range(n_steps):
        tgt = float(target_schedule(k))
        u_cmd = ctrl.compute(meas, tgt)
        Q, WHP, FLP, BHP = sim.step(u_cmd)
        meas = {Q_KEY: Q, WHP_KEY: WHP, FLP_KEY: FLP, BHP_KEY: BHP}
        rows.append({"Time_hr": (k + 1) * sim.Ts, "Q_target": tgt,
                     "OilRate_bbl_hr": Q, "WHP_psi": WHP, "FLP_psi": FLP, "BHP_psi": BHP,
                     "Q_true": sim.Q, "WHP_true": sim.WHP, "FLP_true": sim.FLP,
                     "BHP_true": sim.BHP, "Choke_pct": sim.u,
                     "dChoke": ctrl.log[-1]["du"], "mode": "PI"})
    return pd.DataFrame(rows)


def run_operator(sim: WellSimulator, model: PlantModel, env: OperatingEnvelope,
                 target_schedule, n_steps: int, du_max: float = 5.0,
                 margin_frac: float = 0.10) -> pd.DataFrame:
    """Closed-loop run of the fixed-choke operator baseline."""
    if not callable(target_schedule):
        _c = float(target_schedule)
        target_schedule = lambda k: _c  # noqa: E731
    rows = []
    u = sim.u
    for k in range(n_steps):
        tgt = float(target_schedule(k))
        u_set = operator_choke(model, env, tgt, margin_frac)
        du = float(np.clip(u_set - u, -du_max, du_max))
        u = u + du
        Q, WHP, FLP, BHP = sim.step(u)
        rows.append({"Time_hr": (k + 1) * sim.Ts, "Q_target": tgt,
                     "OilRate_bbl_hr": Q, "WHP_psi": WHP, "FLP_psi": FLP, "BHP_psi": BHP,
                     "Q_true": sim.Q, "WHP_true": sim.WHP, "FLP_true": sim.FLP,
                     "BHP_true": sim.BHP, "Choke_pct": sim.u,
                     "dChoke": du, "mode": "OPERATOR"})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------
# Comparison
# ------------------------------------------------------------------------------


def score(df: pd.DataFrame, env: OperatingEnvelope, warmup: int = 0) -> dict:
    """Safety and production metrics for one closed-loop run."""
    d = df.iloc[warmup:]
    viol_rows = sum(1 for _, r in d.iterrows()
                    if env.violations(r.WHP_true, r.FLP_true, r.BHP_true))
    tail = df.iloc[-30:]
    return {
        "violating_intervals": int(viol_rows),
        "violation_pct": 100.0 * viol_rows / max(len(d), 1),
        "mean_rate_last30": float(tail.Q_true.mean()),
        "min_BHP": float(d.BHP_true.min()),
        "min_WHP": float(d.WHP_true.min()),
        "min_FLP": float(d.FLP_true.min()),
        "BHP_shortfall": float(max(env.BHP_min - d.BHP_true.min(), 0.0)),
        "WHP_shortfall": float(max(env.WHP_min - d.WHP_true.min(), 0.0)),
        "total_oil_bbl": float(df.Q_true.sum()),
        "total_choke_travel": float(df.dChoke.abs().sum()),
    }


def compare_all(model: PlantModel, env: OperatingEnvelope, cfg_mpc,
                scen_defs: dict, ChokeMPC, run_closed_loop) -> pd.DataFrame:
    """Run MPC / PI / operator over every scenario and tabulate the comparison."""
    rows, traces = [], {}
    for tag, s in scen_defs.items():
        # --- MPC
        sim = WellSimulator(u0=s["u0"], seed=s["seed"])
        mpc = ChokeMPC(model, env, cfg_mpc, u0=s["u0"])
        d_mpc = run_closed_loop(sim, mpc, s["target"], s["n"])

        # --- PI
        sim = WellSimulator(u0=s["u0"], seed=s["seed"])
        pi = PIController(model, PIConfig(), u0=s["u0"])
        d_pi = run_pi(sim, pi, s["target"], s["n"])

        # --- operator
        sim = WellSimulator(u0=s["u0"], seed=s["seed"])
        d_op = run_operator(sim, model, env, s["target"], s["n"])

        traces[tag] = {"MPC": d_mpc, "PI": d_pi, "OPERATOR": d_op}
        for name, d in traces[tag].items():
            r = score(d, env, warmup=s["warmup"])
            r.update({"scenario": tag, "controller": name})
            rows.append(r)

    df = pd.DataFrame(rows).set_index(["scenario", "controller"])
    return df, traces
