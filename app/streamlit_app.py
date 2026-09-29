"""Interactive demo of the autonomous choke controller.

Drive the well yourself: pick a target oil rate, a starting choke position and a
noise seed, retune the MPC, and watch what the controller does about it. The
operating envelope is drawn on every trace and every run is audited against it,
so a violation is something you can see rather than something you are told.

    pip install -r requirements.txt
    streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

# --- make the project importable however the app is launched ----------------
ROOT = Path(__file__).resolve().parent
while not (ROOT / "src").is_dir() and ROOT.parent != ROOT:
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT / "src"))

from controller import (  # noqa: E402
    ChokeMPC,
    MPCConfig,
    SteadyStateTargetOptimiser,
    backoffs,
    run_closed_loop,
)
from identification import OUTPUTS, identify, load_model, run_step_test  # noqa: E402
from simulator import OperatingEnvelope, WellSimulator, max_safe_rate  # noqa: E402

REPO_URL = "https://github.com/rahulpaul-07/autonomous-choke-controller"
FIGURES = ROOT / "figures"
RESULTS = ROOT / "results"
TAGS = ("WHP", "FLP", "BHP")
TAG_KEY = dict(zip(TAGS, OUTPUTS[1:]))
TAG_NAME = {"WHP": "Wellhead pressure", "FLP": "Flowline pressure", "BHP": "Bottom-hole pressure"}

# Chosen for contrast on the dark theme in .streamlit/config.toml.
C_PLANT, C_REQUEST, C_ACHIEVABLE = "#4FB3E8", "#D1D5DB", "#F59E0B"
C_LIMIT, C_BAND, C_CHOKE = "#F87171", "#22C55E", "#A78BFA"

st.set_page_config(
    page_title="Autonomous Choke Controller | Constrained MPC",
    page_icon=":material/tune:",
    layout="wide",
    menu_items={
        "Get help": f"{REPO_URL}#readme",
        "Report a bug": f"{REPO_URL}/issues",
        "About": "Constrained model-predictive control of a production choke. "
                 f"Source and evidence: {REPO_URL}",
    },
)

# Metric values default to a display size that truncates "CONSTRAINED" in a
# four-column row; this keeps every value readable down to tablet width.
st.markdown(
    """<style>
    [data-testid="stMetricValue"] { font-size: 1.55rem; }
    [data-testid="stMetricLabel"] p { font-size: 0.85rem; }
    </style>""",
    unsafe_allow_html=True,
)


# --- cached building blocks --------------------------------------------------
@st.cache_resource(show_spinner="Loading the identified control model...")
def build_model():
    """The model the study identified and committed; re-identified only if it is missing."""
    try:
        return load_model(str(RESULTS / "identified_model.csv"))
    except (OSError, ValueError):
        logging.getLogger(__name__).warning(
            "committed model unavailable; re-identifying from a step test", exc_info=True)
        return identify(run_step_test())


@st.cache_data(show_spinner=False)
def envelope_ceiling(limits: tuple) -> dict:
    """Maximum safe rate on the true plant for this limit set."""
    return max_safe_rate(OperatingEnvelope(**dict(limits)))


@st.cache_data(show_spinner="Running the closed loop...", max_entries=64)
def run(u0: float, target: float, target2: float, change_at: int, hours: int,
        seed: int, noise: bool, cfg_fields: dict) -> pd.DataFrame:
    """One closed-loop run, cached on every input that changes its result."""
    cfg = MPCConfig(**cfg_fields)
    env = OperatingEnvelope.load()
    sim = WellSimulator(u0=u0, noise=noise, seed=seed)
    ctrl = ChokeMPC(build_model(), env, cfg, u0=u0)
    schedule = (lambda k: target if k < change_at else target2) if change_at else target
    return run_closed_loop(sim, ctrl, schedule, hours)


@st.cache_data(show_spinner=False)
def read_result(name: str) -> pd.DataFrame:
    """A committed result table from results/."""
    return pd.read_csv(RESULTS / name)


def limiting_constraint(model, env: OperatingEnvelope, cfg: MPCConfig, u: float) -> str:
    """The limit with the least span-normalised margin at steady state for choke ``u``."""
    b = backoffs(cfg, env)
    margins = {}
    for tag in TAGS:
        y = model[TAG_KEY[tag]].steady_state(u)
        lo, hi = getattr(env, f"{tag}_min"), getattr(env, f"{tag}_max")
        span = hi - lo
        margins[f"{tag}_min"] = (y - (lo + b[tag])) / span
        margins[f"{tag}_max"] = ((hi - b[tag]) - y) / span
    return min(margins, key=margins.get)


# --- charts ------------------------------------------------------------------
def _panel(data: pd.DataFrame, title: str, height: int) -> alt.Chart:
    return alt.Chart(data, title=alt.Title(title, anchor="start", fontSize=13),
                     height=height, width="container")


def trend_charts(df: pd.DataFrame, env: OperatingEnvelope) -> list:
    """Rate, the three constrained pressures and the choke, on one fixed time axis.

    Returned as separate charts rather than one concatenation so each one fills
    the column width exactly at any screen size.
    """
    t_domain = [0, float(df.Time_hr.max())]

    def x_axis(last: bool) -> alt.X:
        return alt.X("Time_hr:Q", title="Time [h]" if last else None,
                     scale=alt.Scale(domain=t_domain, nice=False),
                     axis=alt.Axis(labels=last, ticks=last))

    series = ["Plant", "Requested", "Safely achievable"]
    rate = df.melt("Time_hr", ["Q_true", "Q_target", "Q_achievable"], "series", "value")
    rate["series"] = rate["series"].map(dict(zip(["Q_true", "Q_target", "Q_achievable"], series)))
    charts = [_panel(rate, "Oil rate [bbl/hr]", 290).mark_line(strokeWidth=2).encode(
        x=x_axis(False),
        y=alt.Y("value:Q", title=None, scale=alt.Scale(zero=False)),
        color=alt.Color("series:N", title=None,
                        legend=alt.Legend(orient="top", direction="horizontal"),
                        scale=alt.Scale(domain=series, range=[C_PLANT, C_REQUEST, C_ACHIEVABLE])),
        strokeDash=alt.StrokeDash("series:N", legend=None,
                                  scale=alt.Scale(domain=series, range=[[1, 0], [6, 4], [2, 3]])),
        tooltip=[alt.Tooltip("Time_hr:Q", title="Hour"), alt.Tooltip("series:N", title="Series"),
                 alt.Tooltip("value:Q", title="bbl/hr", format=".1f")],
    )]

    for tag in TAGS:
        lo, hi = getattr(env, f"{tag}_min"), getattr(env, f"{tag}_max")
        vals = df[f"{tag}_true"]
        pad = 0.08 * (hi - lo)
        y_scale = alt.Scale(domain=[min(lo, vals.min()) - pad, max(hi, vals.max()) + pad],
                            nice=False, zero=False)
        band = alt.Chart(pd.DataFrame({"lo": [lo], "hi": [hi]})).mark_rect(
            color=C_BAND, opacity=0.13).encode(y=alt.Y("lo:Q", scale=y_scale), y2="hi:Q")
        rules = alt.Chart(pd.DataFrame({"limit": [lo, hi]})).mark_rule(
            color=C_LIMIT, strokeDash=[5, 4], strokeWidth=1.2).encode(
            y=alt.Y("limit:Q", scale=y_scale),
            tooltip=alt.Tooltip("limit:Q", title=f"{tag} limit [psi]"))
        line = alt.Chart(pd.DataFrame({"Time_hr": df.Time_hr, "value": vals})).mark_line(
            strokeWidth=2, color=C_PLANT).encode(
            x=x_axis(False), y=alt.Y("value:Q", title=None, scale=y_scale),
            tooltip=[alt.Tooltip("Time_hr:Q", title="Hour"),
                     alt.Tooltip("value:Q", title=f"{tag} [psi]", format=".1f")])
        charts.append(alt.layer(band, rules, line).properties(
            title=alt.Title(f"{TAG_NAME[tag]} [psi]: green band is the safe range",
                            anchor="start", fontSize=13),
            height=210, width="container"))

    charts.append(_panel(df, "Choke opening [%]", 230).mark_line(
        interpolate="step-after", strokeWidth=2, color=C_CHOKE).encode(
        x=x_axis(True),
        y=alt.Y("Choke_pct:Q", title=None, scale=alt.Scale(zero=False)),
        tooltip=[alt.Tooltip("Time_hr:Q", title="Hour"),
                 alt.Tooltip("Choke_pct:Q", title="Choke [%]", format=".2f"),
                 alt.Tooltip("mode:N", title="Mode")]))
    return charts


# --- header ------------------------------------------------------------------
st.title("Autonomous production choke controller")
st.markdown(
    "A constrained model-predictive controller that sets the production choke on a "
    "naturally flowing oil well every hour. It drives the well to a requested oil rate "
    "while keeping wellhead, flowline and bottom-hole pressure inside a safe envelope. "
    "When the request is **not** safely reachable, it says so, names the limit in the "
    "way, and produces the most it safely can instead of chasing the number."
)
st.markdown(
    f"[Source code]({REPO_URL}) · "
    f"[Executed notebook]({REPO_URL}/blob/main/Autonomous_Choke_Control.ipynb) · "
    f"[Design assumptions]({REPO_URL}/blob/main/ASSUMPTIONS.md) · "
    f"[Development log]({REPO_URL}/blob/main/DEVELOPMENT_LOG.md)"
)

VIEWS = ("Simulator", "How it works", "Study results")
view = st.segmented_control("View", VIEWS, default=VIEWS[0], label_visibility="collapsed") or VIEWS[0]

env = OperatingEnvelope.load()
model = build_model()


# --- view: simulator ---------------------------------------------------------
PRESETS = {
    "C: infeasible target": dict(
        u0=40.0, seed=3, target=120.0, at=30, target2=200.0, hours=140,
        blurb="Asks for 200 bbl/hr, above what the well can safely make. The controller "
              "caps at the safe maximum instead of violating the bottom-hole limit."),
    "A: start-up to target": dict(
        u0=18.0, seed=1, target=100.0, at=0, target2=100.0, hours=90,
        blurb="Brings the well from a low choke opening to 100 bbl/hr."),
    "B: target change": dict(
        u0=32.0, seed=2, target=100.0, at=40, target2=150.0, hours=140,
        blurb="Tracks 100 bbl/hr, then a reachable step to 150 bbl/hr."),
    "Recovery: start outside the envelope": dict(
        u0=10.0, seed=1, target=100.0, at=0, target2=100.0, hours=90,
        blurb="Starts with pressures outside the envelope and returns them as fast as "
              "the 5 %/h ramp limit allows."),
}


def sidebar_inputs() -> tuple:
    """Every simulator input, bounded to a range the controller is meant to run in."""
    sb = st.sidebar
    sb.header("Scenario")
    name = sb.selectbox("Preset", list(PRESETS), help="Each preset reproduces a study scenario.")
    p = PRESETS[name]
    sb.caption(p["blurb"])

    u0 = sb.slider("Initial choke opening [%]", 5.0, 90.0, p["u0"], 0.5)
    target = sb.slider("Target oil rate [bbl/hr]", 40.0, 260.0, p["target"], 1.0)
    hours = sb.slider("Run length [h]", 40, 240, p["hours"], 10)

    step = sb.toggle("Step the target part-way through", value=p["at"] > 0)
    if step:
        at = sb.slider("Step at hour", 1, hours - 1, min(p["at"] or 30, hours - 1))
        target2 = sb.slider("New target [bbl/hr]", 40.0, 260.0, p["target2"], 1.0)
    else:
        at, target2 = 0, target

    seed = int(sb.number_input("Noise seed", 0, 9999, p["seed"], 1))
    noise = sb.toggle("Measurement noise", value=True)

    d = MPCConfig()
    with sb.expander("Advanced: MPC tuning"):
        cfg = dict(
            P=st.slider("Prediction horizon P [h]", 5, 120, d.P,
                        help="How far ahead each candidate plan is simulated."),
            M=st.radio("Free moves per plan M", (1, 2), index=d.M - 1, horizontal=True,
                       help="Plans are one or two moves, then hold."),
            w_move=st.slider("Move suppression w_move", 0.0, 100.0, d.w_move, 1.0,
                             help="Penalty on choke movement: higher means less actuator wear, "
                                  "slower response."),
            backoff=st.slider("Constraint back-off [psi]", 0.0, 25.0, d.backoff, 0.5,
                              help="Safety margin kept inside every pressure limit."),
            n1=st.slider("First-move grid points n1", 5, 121, d.n1, 2,
                         help="Resolution of the first choke move searched each interval."),
            n2=st.slider("Second-move grid points n2", 1, 41, d.n2, 2),
            bias_gain=st.slider("Disturbance-estimator gain", 0.0, 1.0, d.bias_gain, 0.01,
                                help="How fast plant-model mismatch is corrected. 0 disables "
                                     "offset-free tracking."),
            validate_data=st.toggle("Bad-data validation", value=d.validate_data,
                                    help="Range, rate-of-change and frozen-value checks on "
                                         "every measurement."),
        )
    return u0, target, target2, at, hours, seed, noise, cfg, step


def simulator_view() -> None:
    u0, target, target2, at, hours, seed, noise, cfg_fields, step = sidebar_inputs()
    cfg = MPCConfig(**cfg_fields)

    requested = target2 if step else target
    u_ss, q_ach, ssto_mode, _ = SteadyStateTargetOptimiser(model, env, cfg).solve(requested)
    ceiling = envelope_ceiling(tuple((f"{t}_{s}", getattr(env, f"{t}_{s}"))
                                     for t in TAGS for s in ("min", "max")))
    capped = ssto_mode == "CONSTRAINED"
    limit = limiting_constraint(model, env, cfg, u_ss) if capped else "None"

    st.subheader("What the controller decides")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Requested rate [bbl/hr]", f"{requested:.1f}")
    c2.metric("Safely achievable [bbl/hr]", f"{q_ach:.1f}",
              delta=f"{q_ach - requested:+.1f}" if capped else None)
    c3.metric("Target optimiser", ssto_mode.title(),
              help="Tracking: the request is reachable. Constrained: it is capped at the "
                   "safe maximum. Recovery: no safe steady state exists.")
    c4.metric("Limiting constraint", limit.replace("_", " "),
              help="The pressure limit with the least margin at the chosen operating point.")

    if ssto_mode == "RECOVERY":
        st.warning(
            "**No steady state satisfies every limit with this tuning.** The back-off leaves "
            "no room inside the envelope, so the controller can only minimise how far outside "
            "it runs. Reduce the constraint back-off."
        )
    elif requested > ceiling["Q"] + 0.5:
        b = backoffs(cfg, env)[limit.split("_")[0]]
        st.warning(
            f"**{requested:.0f} bbl/hr is not safely reachable.** The well's maximum safe rate "
            f"is {ceiling['Q']:.1f} bbl/hr at {ceiling['u']:.1f} % choke, limited by "
            f"{ceiling['binding'].replace('_', ' ')}. The controller aims for {q_ach:.1f} bbl/hr, "
            f"keeping a {b:g} psi back-off from that limit, instead of chasing the request."
        )

    df = run(u0, target, target2, at, hours, seed, noise, cfg_fields)

    # Compliance audit, on the true plant state rather than the noisy measurement.
    # A well that STARTS outside the envelope cannot be inside it on hour one, so
    # it is audited from the moment it re-enters, as in the study's recovery case.
    start = WellSimulator(u0=u0, noise=False)
    starts_outside = bool(env.violations(start.WHP, start.FLP, start.BHP))
    violated = [env.violations(r.WHP_true, r.FLP_true, r.BHP_true) for r in df.itertuples()]
    first_inside = next((i for i, v in enumerate(violated) if not v), len(violated))
    audit_from = first_inside if starts_outside else 0
    audited = violated[audit_from:]
    n_violating = sum(1 for v in audited if v)
    worst = 0.0
    for tag in TAGS:
        lo, hi = getattr(env, f"{tag}_min"), getattr(env, f"{tag}_max")
        vals = df[f"{tag}_true"].to_numpy(dtype=float)[audit_from:]
        if vals.size:
            worst = max(worst, float(np.max(np.maximum(lo - vals, vals - hi))))

    tail = min(30, len(df))
    settled = float(df.Q_true.tail(tail).mean())

    st.subheader("What actually happened")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric(f"Mean rate, last {tail} h [bbl/hr]", f"{settled:.2f}")
    m2.metric("Offset from achievable [bbl/hr]", f"{settled - q_ach:+.2f}")
    m3.metric("Intervals outside envelope", f"{n_violating} / {len(audited)}",
              help="Audited from the first hour inside the envelope when the well starts "
                   "outside it." if starts_outside else None)
    m4.metric("Total choke travel [%]", f"{df.dChoke.abs().sum():.1f}",
              help="Sum of absolute choke moves: a proxy for actuator wear.")

    if starts_outside:
        if first_inside < len(violated):
            st.info(f"The well started outside its envelope. The controller brought it back "
                    f"inside in {df.Time_hr.iloc[first_inside]:g} h, at the 5 %/h ramp limit.")
        else:
            st.error("The well started outside its envelope and did not return within the run.")
    if n_violating:
        names = ", ".join(sorted({c.replace("_", " ") for v in audited for c in v}))
        st.error(f"The envelope was exceeded on {n_violating} of {len(audited)} audited intervals "
                 f"({names}), worst by {worst:.2f} psi. Try a larger back-off or the default tuning.")
    elif audited:
        st.success("Zero constraint violations, audited on the true plant state rather than "
                   "the noisy measurement (the stricter test).")

    for chart in trend_charts(df, env):
        st.altair_chart(chart, width="stretch")
    modes = " → ".join(m.title() for m in pd.unique(df["mode"].astype(str)))
    st.caption(f"Controller mode over the run: {modes}. Hover a trace for values.")

    st.subheader("What the controller reports to the operator")
    report = df.tail(8)[["Time_hr", "Q_target", "Q_achievable", "Q_true", "BHP_true",
                         "Choke_pct", "mode", "active_constraints", "n_feasible", "n_candidates"]]
    report = report.assign(
        mode=report["mode"].str.title(),
        active_constraints=report["active_constraints"].str.replace("_", " ").str.replace(",", ", "),
    )
    st.dataframe(
        report, width="stretch", hide_index=True,
        column_config={
            "Time_hr": st.column_config.NumberColumn("Hour", format="%d"),
            "Q_target": st.column_config.NumberColumn("Requested", format="%.1f"),
            "Q_achievable": st.column_config.NumberColumn("Achievable", format="%.1f"),
            "Q_true": st.column_config.NumberColumn("Rate", format="%.2f"),
            "BHP_true": st.column_config.NumberColumn("BHP [psi]", format="%.1f"),
            "Choke_pct": st.column_config.NumberColumn("Choke [%]", format="%.2f"),
            "mode": "Mode",
            "active_constraints": "Near-limit constraints",
            "n_feasible": st.column_config.NumberColumn(
                "Safe plans", help="Candidate move plans that passed constraint screening."),
            "n_candidates": st.column_config.NumberColumn("Plans searched"),
        },
    )
    st.caption(
        "Each interval the controller simulates every candidate choke plan over the horizon, "
        "rejects any that would leave the envelope, and applies the first move of the cheapest "
        "survivor. *Safe plans* shrinks as the well nears a limit. A setpoint that had simply "
        "been clipped in advance would leave it unchanged."
    )

    with st.expander("Full closed-loop trace"):
        st.dataframe(df, width="stretch", height=340)
        st.download_button("Download this run as CSV", df.to_csv(index=False).encode(),
                           file_name="closed_loop_run.csv", mime="text/csv")

    st.caption(
        f"Operating limits from `config/{env.source}`: WHP {env.WHP_min:.0f}–{env.WHP_max:.0f}, "
        f"FLP {env.FLP_min:.0f}–{env.FLP_max:.0f}, BHP {env.BHP_min:.0f}–{env.BHP_max:.0f} psi. "
        "These are engineering placeholders because the brief gave no numbers; every value on "
        "this page is derived from that one file."
    )


# --- view: how it works ------------------------------------------------------
def how_it_works_view() -> None:
    st.image(str(FIGURES / "control_architecture.png"), width="stretch",
             caption="Control architecture: two layers, one manipulated variable.")
    left, right = st.columns(2, gap="large")
    left.markdown(
        "#### 1. Steady-state target optimiser\n"
        "Scans the identified model's steady-state map for the choke opening whose "
        "predicted pressures sit inside every limit (less a back-off) and whose rate is "
        "closest to the request. If the request is out of reach, it returns the highest "
        "safe rate and names the limit. That is what stops integral wind-up and "
        "constraint chattering.\n\n"
        "#### 2. Dynamic MPC\n"
        "Enumerates 41 × 11 = 451 two-move plans within the ±5 %/h ramp limit, rolls "
        "each one forward 40 hours in closed form, **rejects** every plan whose forecast "
        "leaves the envelope, and applies the first move of the cheapest survivor."
    )
    right.markdown(
        "#### 3. Offset-free tracking\n"
        "A constant output-disturbance estimator corrects the model with measured "
        "mismatch, so modelling error and slow drift leave no steady-state offset.\n\n"
        "#### 4. Bad-data layer and recovery\n"
        "Range, rate-of-change and frozen-value checks on every tag. A bad tag is "
        "replaced by the model prediction, and the choke is frozen after three bad "
        "intervals. If no plan is feasible (for example, the well starts outside its "
        "envelope), the controller minimises predicted violation and returns to the "
        "envelope as fast as the ramp limit allows.\n\n"
        "#### Model\n"
        "A Hammerstein model (quadratic steady-state gain and first-order lag per output), "
        "identified from an open-loop step test. The controller never sees the "
        "simulator's physics."
    )
    st.image(str(FIGURES / "controller_decisions.png"), width="stretch",
             caption="One interval of Scenario C opened up: the forecast the controller "
                     "committed to, and the candidate plans it rejected.")


# --- view: study results -----------------------------------------------------
FIGURE_CAPTIONS = {
    "baseline_comparison": "MPC vs an IMC-tuned PI loop and a cautious operator",
    "montecarlo_mismatch": "150 runs with randomised model error",
    "stress_tests": "Reservoir decline, water-cut step and instrument faults",
    "breaking_point": "Where the controller breaks, and by how much",
    "model_validation": "Identified model vs the independent reference dataset",
    "operating_envelope": "The safe operating envelope across the choke range",
    "limit_sensitivity": "Sensitivity to the placeholder operating limits",
    "scenario_C": "Scenario C trend: infeasible target",
    "step_test_identification": "Open-loop step test and identification fit",
}


def study_results_view() -> None:
    base = read_result("baseline_comparison.csv").set_index(["scenario", "controller"])
    mpc, op, pi = (base.loc[("C", c)] for c in ("MPC", "OPERATOR", "PI"))
    gain = 100.0 * (mpc.mean_rate_last30 / op.mean_rate_last30 - 1.0)
    mc = read_result("montecarlo_mismatch.csv")

    st.markdown("Every number below is read from the committed `results/` tables, which CI "
                "regenerates from source and compares cell by cell on every push.")
    k1, k2, k3 = st.columns(3)
    k1.metric("Oil vs a cautious operator, Scenario C", f"{gain:+.1f} %",
              help="Same zero violations, mean rate over the last 30 h.")
    k2.metric("PI loop: intervals outside envelope", f"{pi.violation_pct:.0f} %",
              help="A conventional rate PI cannot see the pressure limits.")
    runs = mc.groupby("scenario").violating_intervals
    bad, total = (runs.apply(lambda v: int((v > 0).sum())), runs.size())
    k3.metric("Model-error runs with any violation",
              f"B {bad['B']}/{total['B']} · C {bad['C']}/{total['C']}",
              help="Monte Carlo: the plant's gains and time constants randomly perturbed "
                   "away from the controller's model.")

    st.markdown("#### Scenario C: three ways to handle an infeasible target")
    table = base.xs("C").reset_index()[["controller", "violating_intervals", "mean_rate_last30",
                                         "min_BHP", "total_choke_travel"]]
    table["controller"] = table["controller"].map(
        {"MPC": "MPC (this work)", "PI": "PI on rate, IMC-tuned", "OPERATOR": "Cautious operator"})
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "controller": "Controller",
        "violating_intervals": st.column_config.NumberColumn("Intervals violating"),
        "mean_rate_last30": st.column_config.NumberColumn("Rate, last 30 h", format="%.1f"),
        "min_BHP": st.column_config.NumberColumn("Min BHP [psi] (limit 2850)", format="%.0f"),
        "total_choke_travel": st.column_config.NumberColumn("Choke travel [%]", format="%.1f"),
    })

    st.markdown("#### Figures")
    fig = st.selectbox("Figure", list(FIGURE_CAPTIONS), format_func=FIGURE_CAPTIONS.get,
                       label_visibility="collapsed")
    st.image(str(FIGURES / f"{fig}.png"), width="stretch")


{"Simulator": simulator_view, "How it works": how_it_works_view,
 "Study results": study_results_view}[view]()
