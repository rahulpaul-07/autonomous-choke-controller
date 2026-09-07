"""HTTP service around the choke controller.

The study in `src/` is a batch analysis. This exposes the three calls a
production system would actually make:

    GET  /envelope   the six safe limits, the feasible choke window, the max safe rate
    POST /target     "I want R bbl/hr" -> what is safely achievable, and what stops you
    POST /decide     one control interval: measurements in, next choke position out
    POST /simulate   run the closed loop and return the trace with a compliance audit

Run it:
    pip install -r requirements.txt
    uvicorn service.api:app --reload --port 8000
    open http://localhost:8000/docs
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
while not (ROOT / "src").is_dir() and ROOT.parent != ROOT:
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

from controller import (  # noqa: E402
    ChokeMPC,
    MPCConfig,
    SteadyStateTargetOptimiser,
    run_closed_loop,
)
from identification import identify, run_step_test  # noqa: E402
from simulator import OperatingEnvelope, WellSimulator, max_safe_rate  # noqa: E402

TAGS = ("WHP", "FLP", "BHP")

app = FastAPI(
    title="Autonomous choke controller",
    version="1.0.0",
    description=(
        "Constrained model-predictive control of a production choke on a single "
        "naturally flowing oil well. Targets are honoured only where they are safe; "
        "where they are not, the service reports the achievable rate and names the "
        "binding constraint rather than chasing the setpoint."
    ),
)

_MODEL: list = []
_SESSIONS: dict[str, ChokeMPC] = {}


def model():
    """Identify the control model once, on first use (a ~7 s step test)."""
    if not _MODEL:
        _MODEL.append(identify(run_step_test()))
    return _MODEL[0]


def limits(env: OperatingEnvelope) -> dict[str, dict[str, float]]:
    return {t: {"min": getattr(env, f"{t}_min"), "max": getattr(env, f"{t}_max")} for t in TAGS}


# --- schemas ----------------------------------------------------------------
class TargetRequest(BaseModel):
    target_rate: float = Field(..., gt=0, le=1000, description="Requested oil rate [bbl/hr]")


class TargetResponse(BaseModel):
    requested_rate: float
    achievable_rate: float
    steady_state_choke_pct: float
    mode: str
    active_constraints: list[str]
    capped: bool


class Measurement(BaseModel):
    oil_rate: float = Field(..., description="Oil flow rate Q [bbl/hr]")
    whp: float = Field(..., description="Wellhead pressure [psi]")
    flp: float = Field(..., description="Flowline pressure [psi]")
    bhp: float = Field(..., description="Bottom-hole pressure [psi]")
    choke_pct: float = Field(..., ge=0, le=100, description="Measured choke position [%]")


class DecideRequest(BaseModel):
    measurement: Measurement
    target_rate: float = Field(..., gt=0, le=1000)
    session_id: str | None = Field(
        None,
        description=(
            "Omit on the first call and keep the id returned. The controller is stateful — "
            "it carries the disturbance estimate and the bad-data history between intervals."
        ),
    )


class DecideResponse(BaseModel):
    session_id: str
    choke_command_pct: float
    choke_move_pct: float
    mode: str
    achievable_rate: float
    active_constraints: list[str]
    candidates_feasible: int
    candidates_total: int
    bad_tags: list[str]
    compute_ms: float


class SimulateRequest(BaseModel):
    target_rate: float = Field(100.0, gt=0, le=1000)
    initial_choke_pct: float = Field(30.0, ge=0, le=100)
    hours: int = Field(90, ge=5, le=500)
    seed: int = Field(0, ge=0, le=100000)
    noise: bool = True
    target_step_at_hour: int | None = Field(None, ge=1)
    target_rate_after_step: float | None = Field(None, gt=0, le=1000)
    include_trace: bool = False


class Violation(BaseModel):
    constraint: str
    intervals: int


class SimulateResponse(BaseModel):
    settled_rate: float
    achievable_rate: float
    steady_state_offset: float
    total_choke_travel_pct: float
    max_choke_move_pct: float
    violating_intervals: int
    worst_excursion_psi: float
    violations: list[Violation]
    modes: list[str]
    wall_clock_ms: float
    trace: list[dict[str, Any]] | None


# --- routes -----------------------------------------------------------------
@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "model_identified": bool(_MODEL), "open_sessions": len(_SESSIONS)}


@app.get("/envelope", tags=["well"])
def envelope() -> dict:
    """The safe operating limits, the feasible choke window and the maximum safe rate."""
    env = OperatingEnvelope.load()
    cap = max_safe_rate(env)
    return {
        "source": env.source,
        "limits": limits(env),
        "feasible_choke_window_pct": {"min": cap["u_min_feasible"], "max": cap["u_max_feasible"]},
        "feasible_rate_window_bbl_hr": {"min": cap["Q_min_feasible"], "max": cap["Q"]},
        "max_safe_rate": {
            "Q_bbl_hr": cap["Q"],
            "choke_pct": cap["u"],
            "binding_constraint": cap["binding"],
            "at_that_point": {"WHP": cap["WHP"], "FLP": cap["FLP"], "BHP": cap["BHP"]},
        },
    }


@app.post("/target", response_model=TargetResponse, tags=["control"])
def target(req: TargetRequest) -> TargetResponse:
    """Ask the steady-state target optimiser what is safely achievable."""
    env, cfg = OperatingEnvelope.load(), MPCConfig()
    u_ss, q_ach, mode, active = SteadyStateTargetOptimiser(model(), env, cfg).solve(req.target_rate)
    return TargetResponse(
        requested_rate=req.target_rate,
        achievable_rate=round(float(q_ach), 3),
        steady_state_choke_pct=round(float(u_ss), 3),
        mode=str(mode),
        active_constraints=list(active),
        capped=bool(q_ach < req.target_rate - cfg.ss_tol),
    )


@app.post("/decide", response_model=DecideResponse, tags=["control"])
def decide(req: DecideRequest) -> DecideResponse:
    """One control interval: current measurements in, next choke position out."""
    m = req.measurement
    session_id = req.session_id or uuid.uuid4().hex[:12]

    if session_id not in _SESSIONS:
        _SESSIONS[session_id] = ChokeMPC(
            model(), OperatingEnvelope.load(), MPCConfig(), u0=m.choke_pct
        )
    ctrl = _SESSIONS[session_id]

    measurement = {
        "OilRate_bbl_hr": m.oil_rate,
        "WHP_psi": m.whp,
        "FLP_psi": m.flp,
        "BHP_psi": m.bhp,
    }

    started = time.perf_counter()
    try:
        u_cmd = ctrl.compute(measurement, req.target_rate, choke_position=m.choke_pct)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"control computation failed: {exc}") from exc
    compute_ms = (time.perf_counter() - started) * 1000

    st = ctrl.log[-1]
    return DecideResponse(
        session_id=session_id,
        choke_command_pct=round(float(u_cmd), 3),
        choke_move_pct=round(float(st.du), 3),
        mode=str(st.mode),
        achievable_rate=round(float(st.Q_target_achievable), 3),
        active_constraints=list(st.active_constraints),
        candidates_feasible=int(st.n_candidates_feasible),
        candidates_total=int(st.n_candidates_total),
        bad_tags=[t for t in str(st.bad_tags).split(",") if t and t != "nan"],
        compute_ms=round(compute_ms, 3),
    )


@app.delete("/decide/{session_id}", tags=["control"])
def end_session(session_id: str) -> dict:
    """Discard a controller session."""
    if _SESSIONS.pop(session_id, None) is None:
        raise HTTPException(status_code=404, detail="unknown session_id")
    return {"closed": session_id}


@app.post("/simulate", response_model=SimulateResponse, tags=["control"])
def simulate(req: SimulateRequest) -> SimulateResponse:
    """Run the closed loop end to end and audit it on the true plant state."""
    if req.target_step_at_hour and req.target_rate_after_step is None:
        raise HTTPException(422, "target_rate_after_step is required when target_step_at_hour is set")

    env, cfg, mdl = OperatingEnvelope.load(), MPCConfig(), model()
    if req.target_step_at_hour:
        after, at = req.target_rate_after_step, req.target_step_at_hour
        schedule = lambda k: req.target_rate if k < at else after  # noqa: E731
    else:
        schedule = req.target_rate

    started = time.perf_counter()
    try:
        sim = WellSimulator(u0=req.initial_choke_pct, noise=req.noise, seed=req.seed)
        ctrl = ChokeMPC(mdl, env, cfg, u0=req.initial_choke_pct)
        df = run_closed_loop(sim, ctrl, schedule, req.hours)
    except Exception as exc:
        raise HTTPException(500, f"closed-loop run failed: {exc}") from exc
    elapsed_ms = (time.perf_counter() - started) * 1000

    final_target = req.target_rate_after_step if req.target_step_at_hour else req.target_rate
    _, q_ach, _, _ = SteadyStateTargetOptimiser(mdl, env, cfg).solve(final_target)

    counts: dict[str, int] = {}
    violating = 0
    for r in df.itertuples():
        names = env.violations(r.WHP_true, r.FLP_true, r.BHP_true)
        if names:
            violating += 1
            for n in names:
                counts[n] = counts.get(n, 0) + 1

    worst = 0.0
    for tag in TAGS:
        lo, hi = getattr(env, f"{tag}_min"), getattr(env, f"{tag}_max")
        vals = df[f"{tag}_true"].to_numpy(dtype=float)
        worst = max(worst, float(np.max(np.maximum(lo - vals, vals - hi))), 0.0)

    settled = float(df.Q_true.tail(30).mean())
    return SimulateResponse(
        settled_rate=round(settled, 3),
        achievable_rate=round(float(q_ach), 3),
        steady_state_offset=round(settled - float(q_ach), 3),
        total_choke_travel_pct=round(float(df.dChoke.abs().sum()), 2),
        max_choke_move_pct=round(float(df.dChoke.abs().max()), 2),
        violating_intervals=violating,
        worst_excursion_psi=round(worst, 4),
        violations=[Violation(constraint=k, intervals=v) for k, v in sorted(counts.items())],
        modes=[str(m) for m in pd.unique(df["mode"].astype(str))],
        wall_clock_ms=round(elapsed_ms, 1),
        trace=df.round(4).to_dict(orient="records") if req.include_trace else None,
    )
