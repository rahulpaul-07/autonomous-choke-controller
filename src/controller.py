"""
controller.py
================================================================================
Autonomous production choke controller.

Two-layer architecture, mirroring industrial APC practice (Honeywell Profit
Controller / RMPCT, Aspen DMCplus):

  Layer 1 - Steady-State Target Optimiser (SSTO)
      Each control interval, find the choke opening whose *predicted steady
      state* lies inside the whole operating envelope and whose oil rate is as
      close as possible to the operator's target.  If the requested target is
      unreachable the SSTO returns the maximum achievable safe rate, so an
      infeasible target degrades gracefully instead of causing integral
      wind-up or constraint chattering.

  Layer 2 - Dynamic MPC
      Receding-horizon optimisation over the identified Hammerstein model.
      A grid of candidate two-move plans is rolled forward over the full
      prediction horizon (vectorised over all candidates).  Any candidate whose
      predicted trajectory leaves the operating envelope is REJECTED outright.
      Among the survivors, the plan minimising tracking error + move
      suppression is chosen and only its FIRST move is implemented.

  Offset-free tracking
      A DMC-style constant output-disturbance estimator corrects the model
      prediction with the measured/predicted mismatch, so plant-model mismatch
      and unmeasured drift produce no steady-state offset.  The same bias is
      fed into the SSTO so the achievable-rate calculation stays honest.

  Infeasible initial conditions
      If no candidate is feasible (e.g. at start-up, when the well sits outside
      its envelope before the choke has been opened), the controller switches
      to RECOVERY and selects the move minimising predicted constraint
      violation - i.e. it returns the well to the envelope as fast as the
      ramp-rate limit allows.
================================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from identification import OUTPUTS, PlantModel
from simulator import OperatingEnvelope

Q_KEY, WHP_KEY, FLP_KEY, BHP_KEY = OUTPUTS


@dataclass
class MPCConfig:
    """Tuning parameters for the choke MPC."""

    Ts: float = 1.0             # control interval                            [h]
    P: int = 40                 # prediction horizon (~3x slowest tau)    [steps]
    M: int = 2                  # free moves in the candidate plan        [steps]
    du_max: float = 5.0         # ramp-rate limit per interval                [%]
    u_min: float = 0.0
    u_max: float = 100.0

    w_track: float = 1.0        # weight on (Q - Q_sp)^2 over the horizon
    w_move: float = 15.0        # move suppression on du^2
    n1: int = 41                # first-move grid resolution   (0.25 % steps)
    n2: int = 11                # second-move grid resolution

    backoff: float = 5.0        # constraint back-off [psi] for robustness
    backoff_frac: float = 0.0   # if > 0, back off by this FRACTION of each
                                # variable's span instead of a fixed psi value.
                                # A fixed psi margin is span-blind: 5 psi is
                                # 1.1 % of BHP's range but 8.1 % of FLP's, so it
                                # is over-conservative on the narrow variables.
                                # Left at 0 (fixed 5 psi) for the submitted
                                # results; see studies.run_limit_sensitivity.
    ss_tol: float = 0.5         # |Q_target - Q_achievable| considered on-target
    bias_gain: float = 0.15     # disturbance-estimator filter gain

    # --- measurement validation -------------------------------------------
    validate_data: bool = True  # enable the bad-data layer
    freeze_count: int = 5       # identical readings before a tag is called stale
    rate_margin: float = 2.5    # allowed rate of change, x the physical maximum
    hold_after: int = 3         # consecutive bad intervals before freezing the choke


def backoffs(cfg: "MPCConfig", env: OperatingEnvelope) -> dict:
    """Per-variable constraint back-off [psi]."""
    if cfg.backoff_frac > 0.0:
        return {"WHP": cfg.backoff_frac * (env.WHP_max - env.WHP_min),
                "FLP": cfg.backoff_frac * (env.FLP_max - env.FLP_min),
                "BHP": cfg.backoff_frac * (env.BHP_max - env.BHP_min)}
    return {"WHP": cfg.backoff, "FLP": cfg.backoff, "BHP": cfg.backoff}


@dataclass
class ControllerStatus:
    """Diagnostics published by the controller every interval."""

    u: float
    du: float
    Q_target_requested: float
    Q_target_achievable: float
    u_ss: float
    mode: str                       # TRACKING | CONSTRAINED | RECOVERY
    active_constraints: list = field(default_factory=list)
    n_candidates_total: int = 0
    n_candidates_feasible: int = 0
    cost: float = np.nan
    bad_tags: str = ""


# ------------------------------------------------------------------------------
# Measurement validation - the bad-data layer
# ------------------------------------------------------------------------------


# Physically plausible instrument ranges. A reading outside these can only be a
# transmitter fault, never a real process state.
SENSOR_RANGE = {
    Q_KEY:   (0.0, 400.0),
    WHP_KEY: (0.0, 600.0),
    FLP_KEY: (0.0, 500.0),
    BHP_KEY: (1500.0, 4500.0),
}


class DataValidator:
    """
    Rejects implausible measurements before they can reach the controller.

    In practice a frozen or spiking transmitter is the most common cause of an
    APC trip - or worse, of an APC that keeps driving on bad data. Three checks
    are applied to every tag, every interval:

      1. RANGE   - outside the instrument's physical span
      2. RATE    - changed by more than the process could physically move in one
                   interval (choke gain x ramp limit x margin, plus noise)
      3. FREEZE  - bit-identical for ``freeze_count`` consecutive intervals,
                   which a noisy analogue instrument never is

    A tag that fails any check is marked BAD. The controller then substitutes
    its own model prediction for that tag and suspends the bias update, so a bad
    sensor cannot poison the disturbance estimate.
    """

    def __init__(self, model: PlantModel, cfg: MPCConfig) -> None:
        """Derive the per-tag rate-of-change limits from the model gains."""
        self.cfg = cfg
        self.max_rate = {}
        for key in OUTPUTS:
            ch = model[key]
            # largest steady-state move one full ramp-limited choke step can cause
            g = max(abs(ch.a1 + 2 * ch.a2 * u) for u in (0.0, 50.0, 100.0))
            self.max_rate[key] = cfg.rate_margin * g * cfg.du_max + 1.0
        self.last: dict = {}
        self.repeat = {k: 0 for k in OUTPUTS}
        self.bad_streak = 0

    def check(self, meas: dict) -> dict:
        """Return ``{tag: (is_good, reason)}`` for this measurement set."""
        status = {}
        for key in OUTPUTS:
            v = float(meas[key])
            lo, hi = SENSOR_RANGE[key]
            good, reason = True, ""

            if not np.isfinite(v) or v < lo or v > hi:
                good, reason = False, "RANGE"
            elif key in self.last and abs(v - self.last[key]) > self.max_rate[key]:
                good, reason = False, "RATE"
            else:
                if key in self.last and v == self.last[key]:
                    self.repeat[key] += 1
                else:
                    self.repeat[key] = 0
                if self.repeat[key] >= self.cfg.freeze_count:
                    good, reason = False, "FROZEN"

            status[key] = (good, reason)
            if good:
                self.last[key] = v
        return status


# ------------------------------------------------------------------------------
# Layer 1 - steady-state target optimiser
# ------------------------------------------------------------------------------


class SteadyStateTargetOptimiser:
    """Finds the feasible steady state closest to the operator's target."""

    def __init__(self, model: PlantModel, env: OperatingEnvelope,
                 cfg: MPCConfig) -> None:
        """Pre-compute the steady-state gain curves the optimiser scans."""
        self.model, self.env, self.cfg = model, env, cfg
        self.u_grid = np.arange(cfg.u_min, cfg.u_max + 0.05, 0.05)
        ss = {k: model[k].gain_curve(self.u_grid) for k in OUTPUTS}
        self.Q, self.WHP = ss[Q_KEY], ss[WHP_KEY]
        self.FLP, self.BHP = ss[FLP_KEY], ss[BHP_KEY]

    def _feasible_mask(self, bias: dict) -> np.ndarray:
        e, b = self.env, backoffs(self.cfg, self.env)
        return (
            (self.WHP + bias[WHP_KEY] >= e.WHP_min + b["WHP"])
            & (self.WHP + bias[WHP_KEY] <= e.WHP_max - b["WHP"])
            & (self.FLP + bias[FLP_KEY] >= e.FLP_min + b["FLP"])
            & (self.FLP + bias[FLP_KEY] <= e.FLP_max - b["FLP"])
            & (self.BHP + bias[BHP_KEY] >= e.BHP_min + b["BHP"])
            & (self.BHP + bias[BHP_KEY] <= e.BHP_max - b["BHP"])
        )

    def solve(self, Q_target: float, bias: dict | None = None):
        """
        Returns ``(u_ss, Q_achievable, mode, active_constraints)``, where all
        rates are expressed in *plant* units (model prediction + bias).
        """
        bias = bias or {k: 0.0 for k in OUTPUTS}
        mask = self._feasible_mask(bias)
        if not mask.any():
            # No feasible steady state under the current bias estimate: fall
            # back to the least-violating opening so RECOVERY has a direction.
            e, b = self.env, backoffs(self.cfg, self.env)
            viol = (
                np.maximum(e.WHP_min + b["WHP"] - (self.WHP + bias[WHP_KEY]), 0) ** 2
                + np.maximum((self.WHP + bias[WHP_KEY]) - (e.WHP_max - b["WHP"]), 0) ** 2
                + np.maximum(e.FLP_min + b["FLP"] - (self.FLP + bias[FLP_KEY]), 0) ** 2
                + np.maximum((self.FLP + bias[FLP_KEY]) - (e.FLP_max - b["FLP"]), 0) ** 2
                + np.maximum(e.BHP_min + b["BHP"] - (self.BHP + bias[BHP_KEY]), 0) ** 2
                + np.maximum((self.BHP + bias[BHP_KEY]) - (e.BHP_max - b["BHP"]), 0) ** 2
            )
            j = int(np.argmin(viol))
            return float(self.u_grid[j]), float(self.Q[j] + bias[Q_KEY]), "RECOVERY", []

        idx = np.where(mask)[0]
        Q_plant = self.Q + bias[Q_KEY]
        Q_lo, Q_hi = float(Q_plant[idx].min()), float(Q_plant[idx].max())
        Q_clipped = float(np.clip(Q_target, Q_lo, Q_hi))
        j = idx[int(np.argmin(np.abs(Q_plant[idx] - Q_clipped)))]

        u_ss, Q_ach = float(self.u_grid[j]), float(Q_plant[j])
        mode = "TRACKING" if abs(Q_target - Q_ach) <= self.cfg.ss_tol else "CONSTRAINED"

        e, b = self.env, backoffs(self.cfg, self.env)
        active = []
        for tag, y, key in (("WHP", self.WHP[j] + bias[WHP_KEY], "WHP"),
                            ("FLP", self.FLP[j] + bias[FLP_KEY], "FLP"),
                            ("BHP", self.BHP[j] + bias[BHP_KEY], "BHP")):
            lo, hi, m = getattr(e, f"{tag}_min"), getattr(e, f"{tag}_max"), b[key]
            if y <= lo + 3.0 * m: active.append(f"{tag}_min")
            if y >= hi - 3.0 * m: active.append(f"{tag}_max")
        return u_ss, Q_ach, mode, active

    def envelope_limits(self, bias: dict | None = None) -> dict:
        """Feasible steady-state rate / choke window under the current bias."""
        bias = bias or {k: 0.0 for k in OUTPUTS}
        mask = self._feasible_mask(bias)
        if not mask.any():
            return {}
        idx = np.where(mask)[0]
        Qp = self.Q + bias[Q_KEY]
        return {
            "Q_min": float(Qp[idx].min()),
            "Q_max": float(Qp[idx].max()),
            "u_min": float(self.u_grid[idx].min()),
            "u_max": float(self.u_grid[idx].max()),
            "u_at_Qmax": float(self.u_grid[idx][int(np.argmax(Qp[idx]))]),
        }


# ------------------------------------------------------------------------------
# Layer 2 - dynamic MPC
# ------------------------------------------------------------------------------


class ChokeMPC:
    """Constrained receding-horizon controller on the production choke."""

    def __init__(
        self,
        model: PlantModel,
        env: OperatingEnvelope | None = None,
        cfg: MPCConfig | None = None,
        u0: float = 30.0,
        keep_diagnostics: bool = False,
    ) -> None:
        """Build the controller around an identified model and an envelope."""
        self.model = model
        self.env = env or OperatingEnvelope.load()
        self.cfg = cfg or MPCConfig()
        self.ssto = SteadyStateTargetOptimiser(self.model, self.env, self.cfg)

        self.u = float(np.clip(u0, self.cfg.u_min, self.cfg.u_max))
        self.u_tracking_error = 0.0
        self.y_hat = {k: np.nan for k in OUTPUTS}
        self.bias = {k: 0.0 for k in OUTPUTS}
        self._initialised = False
        self.validator = DataValidator(model, self.cfg)
        self.log: list[ControllerStatus] = []

        # Optional record of what the controller predicted and what it rejected,
        # so the decision itself can be plotted rather than just its outcome.
        self.keep_diagnostics = keep_diagnostics
        self.diagnostics: list[dict] = []

        # Pre-compute the discrete pole of every channel
        self._a = {k: float(np.exp(-self.cfg.Ts / model[k].tau)) for k in OUTPUTS}

    # -- vectorised prediction ------------------------------------------------

    def _rollout(self, U: np.ndarray, y0: dict) -> dict:
        """
        Roll the identified model forward for every candidate plan at once.

        ``U`` has shape (n_candidates, P).  Returns a dict of (n_candidates, P)
        trajectories already corrected by the output-disturbance estimate.
        """
        n, P = U.shape
        traj = {}
        for key in OUTPUTS:
            ch = self.model[key]
            yss = ch.a0 + ch.a1 * U + ch.a2 * U**2       # (n, P) static map
            a = self._a[key]
            y = np.empty((n, P))
            prev = np.full(n, y0[key], dtype=float)
            for k in range(P):
                prev = a * prev + (1.0 - a) * yss[:, k]
                y[:, k] = prev
            traj[key] = y + self.bias[key]
        return traj

    def _rollout_2move(self, u1: np.ndarray, u2: np.ndarray, y0: dict,
                       P: int) -> dict:
        """
        Exact closed-form roll-out for the two-move-then-hold plan structure.

        With u = u1 for one interval and u2 held thereafter, the first-order
        recursion  y[k] = a*y[k-1] + (1-a)*s(u[k])  solves in closed form:

            y[0] = a*y_init + (1-a)*s(u1)
            y[k] = a^k * y[0] + s(u2) * (1 - a^k)          for k >= 1

        which removes the P-step Python loop. Verified against the general
        :meth:`_rollout` to floating-point tolerance in ``tests/``.
        """
        traj = {}
        k = np.arange(P)
        for key in OUTPUTS:
            ch = self.model[key]
            a = self._a[key]
            s1 = ch.a0 + ch.a1 * u1 + ch.a2 * u1**2
            s2 = ch.a0 + ch.a1 * u2 + ch.a2 * u2**2
            y_first = a * y0[key] + (1.0 - a) * s1              # (n,)
            a_pow = a ** k                                       # (P,)
            traj[key] = (np.outer(y_first, a_pow)
                         + np.outer(s2, 1.0 - a_pow)
                         + self.bias[key])
        return traj

    def _violation(self, traj: dict) -> np.ndarray:
        """Weighted squared constraint violation per candidate (vector)."""
        e, b = self.env, backoffs(self.cfg, self.env)
        v = np.zeros(traj[Q_KEY].shape[0])
        for key, lo, hi in (
            (WHP_KEY, e.WHP_min + b["WHP"], e.WHP_max - b["WHP"]),
            (FLP_KEY, e.FLP_min + b["FLP"], e.FLP_max - b["FLP"]),
            (BHP_KEY, e.BHP_min + b["BHP"], e.BHP_max - b["BHP"]),
        ):
            y = traj[key]
            v += np.sum(np.maximum(lo - y, 0.0) ** 2, axis=1)
            v += np.sum(np.maximum(y - hi, 0.0) ** 2, axis=1)
        return v

    def _candidate_plans(self):
        """
        Build the candidate move grid.

        Returns ``(u1, u2, du1, du2)`` - the two choke positions of each
        candidate plan and the moves actually realisable after clipping.
        Every move in the plan is returned so that move suppression can be
        applied to the whole plan - penalising only the first move makes it
        free to defer action to move 2, which stalls the receding horizon.
        """
        cfg = self.cfg
        g1 = np.linspace(-cfg.du_max, cfg.du_max, cfg.n1)
        g2 = np.linspace(-cfg.du_max, cfg.du_max, cfg.n2)
        d1, d2 = np.meshgrid(g1, g2, indexing="ij")
        d1, d2 = d1.ravel(), d2.ravel()

        u1 = np.clip(self.u + d1, cfg.u_min, cfg.u_max)
        u2 = np.clip(u1 + d2, cfg.u_min, cfg.u_max)

        return u1, u2, u1 - self.u, u2 - u1

    # -- main control law -----------------------------------------------------

    def compute(self, measurement: dict, Q_target: float,
                choke_position: float | None = None) -> float:
        """
        Execute one control interval.

        The problem statement specifies exactly what the controller receives each
        interval: *"Current Oil Flow Rate (Q), Wellhead Pressure, Flowline
        Pressure, Bottom Hole Pressure, Current Choke Position"*.

        ``measurement``    : dict with keys OilRate_bbl_hr, WHP_psi, FLP_psi, BHP_psi
        ``Q_target``       : requested oil production rate [bbl/hr]
        ``choke_position`` : the MEASURED choke position [%]. Supplying it makes
                             the controller position-feedback rather than
                             open-loop on its own command history, so an actuator
                             that fails to track (sticking valve, manual
                             override, a bumpless transfer from operator control)
                             cannot silently desynchronise the controller.

        Returns the new choke position command [%].
        """
        cfg = self.cfg

        # ---- read back the actual valve position ---------------------------
        if choke_position is not None:
            u_meas = float(np.clip(choke_position, cfg.u_min, cfg.u_max))
            self.u_tracking_error = u_meas - self.u
            self.u = u_meas

        # ---- measurement validation ----------------------------------------
        if cfg.validate_data and self._initialised:
            status = self.validator.check(measurement)
        else:
            status = {k: (True, "") for k in OUTPUTS}
        bad = [f"{k.split('_')[0]}:{r}" for k, (ok, r) in status.items() if not ok]
        self.validator.bad_streak = self.validator.bad_streak + 1 if bad else 0

        # ---- state estimation / output-disturbance update -------------------
        if not self._initialised:
            self.y_hat = {k: float(measurement[k]) for k in OUTPUTS}
            self.bias = {k: 0.0 for k in OUTPUTS}
            self._initialised = True
        else:
            for k in OUTPUTS:
                if not status[k][0]:
                    # bad reading: keep the previous bias, run on the model alone
                    continue
                innov = float(measurement[k]) - (self.y_hat[k] + self.bias[k])
                self.bias[k] += cfg.bias_gain * innov

        y0 = dict(self.y_hat)

        # ---- safe degradation on sustained bad data -------------------------
        # Losing a constrained measurement for several intervals means we can no
        # longer verify the envelope. The safe action is to stop moving.
        if bad and self.validator.bad_streak >= cfg.hold_after:
            for k in OUTPUTS:
                self.y_hat[k] = self.model[k].advance(self.y_hat[k], self.u, cfg.Ts)
            self.log.append(
                ControllerStatus(
                    u=self.u, du=0.0, Q_target_requested=float(Q_target),
                    Q_target_achievable=float("nan"), u_ss=self.u,
                    mode="HOLD_BAD_DATA", active_constraints=[],
                    n_candidates_total=0, n_candidates_feasible=0,
                    cost=float("nan"), bad_tags=",".join(bad),
                )
            )
            return self.u

        # ---- Layer 1: steady-state target optimisation ----------------------
        u_ss, Q_sp, mode, active = self.ssto.solve(Q_target, self.bias)

        # ---- Layer 2: constrained dynamic optimisation ----------------------
        u1, u2, du1, du2 = self._candidate_plans()
        traj = self._rollout_2move(u1, u2, y0, cfg.P)
        viol = self._violation(traj)

        err = traj[Q_KEY] - Q_sp
        # Move suppression is applied to EVERY move in the plan, otherwise the
        # optimiser can leave du1 = 0 and let du2 do the work at zero cost -
        # which stalls the receding horizon and creates steady-state offset.
        cost = cfg.w_track * np.sum(err**2, axis=1) + cfg.w_move * (du1**2 + du2**2)

        feasible = viol <= 1e-9
        n_feas = int(feasible.sum())

        if n_feas > 0:
            j = int(np.where(feasible)[0][np.argmin(cost[feasible])])
            best_cost = float(cost[j])
        else:
            # RECOVERY: nothing is fully feasible -> minimise violation first,
            # break ties on tracking cost.
            order = np.lexsort((cost, np.round(viol, 9)))
            j = int(order[0])
            best_cost = float(cost[j])
            mode = "RECOVERY"

        du_apply = float(du1[j])

        # ---- record the decision, for the prediction/decision plots ---------
        if self.keep_diagnostics:
            self.diagnostics.append({
                "t": len(self.log) * cfg.Ts,
                "du_grid": du1.copy(),                    # every candidate first move
                "cost": cost.copy(),                      # its cost
                "feasible": feasible.copy(),              # did it survive screening?
                "chosen": j,                              # which one we applied
                "u_now": self.u,
                "Q_pred": traj[Q_KEY][j].copy(),          # the trajectory we committed to
                "BHP_pred": traj[BHP_KEY][j].copy(),
                "WHP_pred": traj[WHP_KEY][j].copy(),
                "Q_sp": Q_sp,
            })

        self.u = float(np.clip(self.u + du_apply, cfg.u_min, cfg.u_max))

        # ---- advance the internal model one interval ------------------------
        for k in OUTPUTS:
            self.y_hat[k] = self.model[k].advance(self.y_hat[k], self.u, cfg.Ts)

        self.log.append(
            ControllerStatus(
                u=self.u,
                du=du_apply,
                Q_target_requested=float(Q_target),
                Q_target_achievable=float(Q_sp),
                u_ss=float(u_ss),
                mode=mode,
                active_constraints=active,
                n_candidates_total=int(u1.size),
                n_candidates_feasible=n_feas,
                cost=best_cost,
                bad_tags=",".join(bad),
            )
        )
        return self.u


# ------------------------------------------------------------------------------
# Closed-loop driver
# ------------------------------------------------------------------------------


def run_closed_loop(sim, ctrl: ChokeMPC, target_schedule, n_steps: int,
                    sensor_fault=None):
    """
    Run ``n_steps`` control intervals of the closed loop.

    ``target_schedule`` is a scalar or a callable ``f(k) -> Q_target``.
    ``sensor_fault``    is an optional callable ``f(k, meas) -> meas`` that
                        corrupts the measurement the CONTROLLER sees, while the
                        true plant state is logged untouched. Used to test
                        transmitter failures.

    Returns a tidy DataFrame of plant, controller and constraint information.
    """
    import pandas as pd

    if not callable(target_schedule):
        _c = float(target_schedule)
        target_schedule = lambda k: _c  # noqa: E731

    meas = dict(zip(OUTPUTS, sim.measure()))
    rows = []
    for k in range(n_steps):
        tgt = float(target_schedule(k))
        meas_seen = dict(meas) if sensor_fault is None else sensor_fault(k, dict(meas))
        # the controller reads back the actual valve position, as the brief specifies
        u_cmd = ctrl.compute(meas_seen, tgt, choke_position=sim.u)
        Q, WHP, FLP, BHP = sim.step(u_cmd)
        meas = {Q_KEY: Q, WHP_KEY: WHP, FLP_KEY: FLP, BHP_KEY: BHP}
        st = ctrl.log[-1]
        rows.append(
            {
                "Time_hr": (k + 1) * sim.Ts,
                # noise-free plant state, used for constraint auditing
                "Q_true": sim.Q, "WHP_true": sim.WHP,
                "FLP_true": sim.FLP, "BHP_true": sim.BHP,
                "Q_target": tgt,
                "Q_achievable": st.Q_target_achievable,
                "OilRate_bbl_hr": Q,
                "WHP_psi": WHP,
                "FLP_psi": FLP,
                "BHP_psi": BHP,
                "Q_seen": meas_seen[Q_KEY], "WHP_seen": meas_seen[WHP_KEY],
                "FLP_seen": meas_seen[FLP_KEY], "BHP_seen": meas_seen[BHP_KEY],
                "Choke_pct": sim.u,
                "Choke_cmd": u_cmd,
                "dChoke": st.du,
                "u_tracking_error": ctrl.u_tracking_error,
                "mode": st.mode,
                "active_constraints": ",".join(st.active_constraints),
                "u_ss": st.u_ss,
                "n_feasible": st.n_candidates_feasible,
                "n_candidates": st.n_candidates_total,
                "bad_tags": st.bad_tags,
                "WHT_F": getattr(sim, "WHT", float("nan")),
                "AP_psi": getattr(sim, "AP", float("nan")),
                "Pr_psi": sim.p.Pr,
                "water_cut": sim.p.water_cut,
            }
        )
    return pd.DataFrame(rows)
