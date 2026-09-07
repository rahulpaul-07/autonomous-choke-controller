"""Interactive demo of the autonomous choke controller.

Drive the well yourself: pick a target oil rate, a starting choke position and a
noise seed, retune the MPC, and watch what the controller does about it. The
operating envelope is drawn on every trace and every run is audited against it,
so a violation is something you can see rather than something you are told.

    pip install -r requirements.txt
    streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

# --- make the project importable however the app is launched ----------------
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

st.set_page_config(page_title="Autonomous choke controller", layout="wide")


@st.cache_resource(show_spinner="Running the step test and identifying the control model...")
def build_model():
    """The designed 8-level open-loop step test, then Hammerstein identification."""
    return identify(run_step_test())


@st.cache_data(show_spinner=False)
def envelope_ceiling(limits: tuple) -> dict:
    """Maximum safe rate for the current limit set. Keyed on limits so it recomputes."""
    env = OperatingEnvelope(**dict(limits))
    return max_safe_rate(env)


@st.cache_data(show_spinner="Running the closed loop...")
def run(u0, target, target2, change_at, hours, seed, noise, cfg_fields):
    cfg = MPCConfig(**cfg_fields)
    env = OperatingEnvelope.load()
    sim = WellSimulator(u0=u0, noise=noise, seed=int(seed))
    ctrl = ChokeMPC(model, env, cfg, u0=u0)
    schedule = (lambda k: target if k < change_at else target2) if change_at else target
    return run_closed_loop(sim, ctrl, schedule, int(hours))


# --- sidebar ----------------------------------------------------------------
PRESETS = {
    "A — start-up to target": dict(u0=18.0, seed=1, target=100.0, at=0, target2=100.0, hours=90),
    "B — target change": dict(u0=32.0, seed=2, target=100.0, at=40, target2=150.0, hours=140),
    "C — infeasible target": dict(u0=40.0, seed=3, target=120.0, at=30, target2=200.0, hours=140),
    "Recovery — start outside the envelope": dict(u0=10.0, seed=1, target=100.0, at=0, target2=100.0, hours=90),
}

st.sidebar.title("Well and target")
preset_name = st.sidebar.selectbox("Scenario", list(PRESETS), index=2)
p = PRESETS[preset_name]

u0 = st.sidebar.slider("Initial choke opening [%]", 5.0, 90.0, p["u0"], 0.5)
target = st.sidebar.slider("Target oil rate [bbl/hr]", 40.0, 260.0, p["target"], 1.0)

step_change = st.sidebar.checkbox("Step the target part-way through", value=p["at"] > 0)
if step_change:
    change_at = st.sidebar.slider("Step at hour", 1, 120, int(p["at"] or 30))
    target2 = st.sidebar.slider("New target [bbl/hr]", 40.0, 260.0, p["target2"], 1.0)
else:
    change_at, target2 = 0, target

hours = st.sidebar.slider("Run length [h]", 40, 240, p["hours"], 10)
seed = st.sidebar.number_input("Noise seed", 0, 9999, p["seed"])
noise = st.sidebar.checkbox("Measurement noise on", value=True)

st.sidebar.title("MPC tuning")
st.sidebar.caption("Read off MPCConfig itself, so every real knob appears here.")

TUNABLE = ("P", "M", "w_move", "backoff", "backoff_frac", "n1", "n2", "bias_gain", "validate_data")
defaults = MPCConfig()
cfg_fields: dict = {}
for field in TUNABLE:
    value = getattr(defaults, field)
    label = field.replace("_", " ")
    if isinstance(value, bool):
        cfg_fields[field] = st.sidebar.checkbox(label, value=value)
    elif isinstance(value, int):
        cfg_fields[field] = int(st.sidebar.number_input(label, value=value, step=1, min_value=1))
    else:
        cfg_fields[field] = float(st.sidebar.number_input(label, value=float(value), format="%.4f"))

env = OperatingEnvelope.load()
model = build_model()
cfg = MPCConfig(**cfg_fields)

# --- header -----------------------------------------------------------------
st.title("Autonomous production choke controller")
st.markdown(
    "A constrained MPC drives the choke on a naturally flowing oil well to a requested "
    "oil rate while keeping wellhead, flowline and bottom-hole pressure inside a safe "
    "operating envelope. When the target is not reachable safely it says so, names the "
    "binding limit, and produces the most it can instead of chasing the number."
)

requested_now = target2 if step_change else target
u_ss, q_achievable, ssto_mode, active = SteadyStateTargetOptimiser(model, env, cfg).solve(requested_now)
ceiling = envelope_ceiling(tuple((t, getattr(env, t)) for t in
                                 ("WHP_min", "WHP_max", "FLP_min", "FLP_max", "BHP_min", "BHP_max")))

c1, c2, c3, c4 = st.columns(4)
c1.metric("Requested", f"{requested_now:.1f} bbl/hr")
c2.metric("Safely achievable", f"{q_achievable:.1f} bbl/hr", delta=f"{q_achievable - requested_now:+.1f}")
c3.metric("Target optimiser", str(ssto_mode))
c4.metric("Binding constraint", ", ".join(active) if active else "none")

if requested_now > ceiling["Q"] + 0.5:
    st.warning(
        f"This target is above the well's maximum safe rate of {ceiling['Q']:.1f} bbl/hr "
        f"(choke {ceiling['u']:.1f} %, limited by {ceiling['binding']}). The controller will "
        "cap rather than violate — which is the whole point of the scenario."
    )

df = run(u0, target, target2, change_at, hours, seed, noise, cfg_fields)

# --- compliance audit, on the true plant state ------------------------------
violated = [env.violations(r.WHP_true, r.FLP_true, r.BHP_true) for r in df.itertuples()]
n_violating = sum(1 for v in violated if v)

worst = 0.0
for tag in TAGS:
    lo, hi = getattr(env, f"{tag}_min"), getattr(env, f"{tag}_max")
    vals = df[f"{tag}_true"].to_numpy(dtype=float)
    worst = max(worst, float(np.max(np.maximum(lo - vals, vals - hi))), 0.0)

settled = float(df.Q_true.tail(30).mean())
travel = float(df.dChoke.abs().sum())

m1, m2, m3, m4 = st.columns(4)
m1.metric("Settled rate", f"{settled:.2f} bbl/hr")
m2.metric("Offset from achievable", f"{settled - q_achievable:+.2f} bbl/hr")
m3.metric("Constraint violations", str(n_violating), delta_color="inverse",
          delta=None if not n_violating else f"worst {worst:.2f} psi outside")
m4.metric("Total choke travel", f"{travel:.1f} %")

if n_violating:
    names = sorted({c for v in violated for c in v})
    st.error(f"Envelope exceeded on {n_violating} of {len(df)} intervals ({', '.join(names)}).")
else:
    st.success(
        "Zero constraint violations, audited on the true plant state rather than the "
        "noisy measurement — the stricter test."
    )

# --- plot -------------------------------------------------------------------
PANELS = [("Q_true", "Oil rate [bbl/hr]", None),
          ("WHP_true", "WHP [psi]", "WHP"),
          ("FLP_true", "FLP [psi]", "FLP"),
          ("BHP_true", "BHP [psi]", "BHP")]

fig, axes = plt.subplots(len(PANELS) + 1, 1, figsize=(11, 2.05 * (len(PANELS) + 1)), sharex=True)
t = df.Time_hr

for ax, (col, label, tag) in zip(axes, PANELS):
    ax.plot(t, df[col], lw=1.6, color="#0B6FA4")
    if tag:
        lo, hi = getattr(env, f"{tag}_min"), getattr(env, f"{tag}_max")
        ax.axhspan(lo, hi, color="#2E7D32", alpha=0.07, zorder=0)
        ax.axhline(lo, color="#C62828", lw=1.0, ls="--")
        ax.axhline(hi, color="#C62828", lw=1.0, ls="--")
    else:
        ax.plot(t, df.Q_target, lw=1.1, ls="--", color="#333", label="requested")
        ax.plot(t, df.Q_achievable, lw=1.2, ls=":", color="#E8820C", label="achievable (SSTO)")
        ax.legend(loc="lower right", fontsize=8, frameon=False)
    ax.set_ylabel(label, fontsize=9)
    ax.grid(alpha=0.25, lw=0.5)

axes[-1].step(t, df.Choke_pct, where="post", lw=1.6, color="#6A1B9A")
axes[-1].set_ylabel("Choke [%]", fontsize=9)
axes[-1].set_xlabel("Time [h]", fontsize=9)
axes[-1].grid(alpha=0.25, lw=0.5)
fig.tight_layout()
st.pyplot(fig, width="stretch")

st.caption("Controller mode over the run: " + " → ".join(pd.unique(df["mode"].astype(str))))

# --- what the controller reports to the operator ----------------------------
st.subheader("What the controller reports to the operator")
st.dataframe(
    df.tail(8)[["Time_hr", "Q_target", "Q_achievable", "Q_true", "BHP_true", "Choke_pct",
                "mode", "active_constraints", "n_feasible", "n_candidates"]].round(2),
    width="stretch", hide_index=True,
)
st.caption(
    "`n_feasible` is how many of the candidate move-plans survived constraint screening "
    "this interval. If the controller were tracking a setpoint that had simply been clipped "
    "in advance, that number would never move."
)

with st.expander("Full closed-loop trace"):
    st.dataframe(df, width="stretch", height=340)
    st.download_button("Download this run as CSV", df.to_csv(index=False).encode(),
                       file_name="closed_loop_run.csv", mime="text/csv")

st.caption(
    f"Operating limits loaded from {env.source}: WHP {env.WHP_min:.0f}–{env.WHP_max:.0f}, "
    f"FLP {env.FLP_min:.0f}–{env.FLP_max:.0f}, BHP {env.BHP_min:.0f}–{env.BHP_max:.0f} psi. "
    "They are engineering placeholders — edit config/operating_limits.json and every number "
    "on this page moves with them."
)
