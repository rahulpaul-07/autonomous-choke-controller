"""plotting.py - figure generation for the choke control study."""
from __future__ import annotations

import matplotlib

# Fall back to a headless backend ONLY when no interactive / inline backend is
# already active, so the same module works both as a script and inside Jupyter.
_BACKEND = matplotlib.get_backend().lower()
if not any(k in _BACKEND for k in ("inline", "ipympl", "nbagg", "widget",
                                   "qt", "tk", "macosx", "gtk")):
    matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from simulator import OperatingEnvelope

plt.rcParams.update({
    "figure.dpi": 130, "savefig.dpi": 160, "font.size": 9,
    "axes.grid": True, "grid.alpha": 0.3, "axes.spines.top": False,
    "axes.spines.right": False, "figure.facecolor": "white",
    "axes.titlesize": 10, "axes.titleweight": "bold", "legend.frameon": False,
})

C_ACT, C_TGT, C_ACH, C_LIM, C_CHK = "#0B6FA4", "#111111", "#E8820C", "#C62828", "#2E7D32"


def _limit_band(ax, lo, hi, label="safe envelope"):
    ax.axhspan(lo, hi, color="#2E7D32", alpha=0.06, zorder=0)
    ax.axhline(lo, color=C_LIM, ls="--", lw=1.1, zorder=1)
    ax.axhline(hi, color=C_LIM, ls="--", lw=1.1, zorder=1, label=label)


def plot_scenario(df: pd.DataFrame, title: str, env: OperatingEnvelope | None = None,
                  path: str | None = None, annotate: str | None = None):
    """Six-panel trend plot required by the deliverables list."""
    env = env or OperatingEnvelope.load()
    fig, ax = plt.subplots(3, 2, figsize=(11.5, 8.2), sharex=True)
    t = df.Time_hr

    a = ax[0, 0]
    a.plot(t, df.Q_target, color=C_TGT, ls="--", lw=1.6, label="Target rate (requested)")
    if "Q_achievable" in df:
        a.plot(t, df.Q_achievable, color=C_ACH, ls=":", lw=2.0, label="Achievable target (SSTO)")
    a.plot(t, df.OilRate_bbl_hr, color=C_ACT, lw=1.5, label="Actual rate (measured)")
    a.set_ylabel("Oil rate  [bbl/hr]"); a.set_title("Oil Production Rate — target vs actual")
    a.legend(loc="best", fontsize=7.5)

    a = ax[0, 1]
    a.plot(t, df.Choke_pct, color=C_CHK, lw=1.6)
    a.set_ylabel("Choke opening  [%]"); a.set_title("Production Choke Position (MV)")
    a.set_ylim(0, 100)

    a = ax[1, 0]
    _limit_band(a, env.WHP_min, env.WHP_max)
    a.plot(t, df.WHP_psi, color=C_ACT, lw=1.4)
    a.set_ylabel("WHP  [psi]"); a.set_title("Wellhead Pressure (constrained CV)")
    a.legend(loc="best", fontsize=7.5)

    a = ax[1, 1]
    _limit_band(a, env.FLP_min, env.FLP_max)
    a.plot(t, df.FLP_psi, color=C_ACT, lw=1.4)
    a.set_ylabel("FLP  [psi]"); a.set_title("Flowline Pressure (constrained CV)")
    a.legend(loc="best", fontsize=7.5)

    a = ax[2, 0]
    _limit_band(a, env.BHP_min, env.BHP_max)
    a.plot(t, df.BHP_psi, color=C_ACT, lw=1.4)
    a.set_ylabel("BHP  [psi]"); a.set_title("Bottom Hole Pressure (constrained CV)")
    a.set_xlabel("Time  [h]"); a.legend(loc="best", fontsize=7.5)

    a = ax[2, 1]
    a.step(t, df.dChoke, where="post", color="#6A1B9A", lw=1.2)
    a.axhline(5, color=C_LIM, ls="--", lw=1.1)
    a.axhline(-5, color=C_LIM, ls="--", lw=1.1, label="ramp limit ±5 %/interval")
    a.set_ylabel("Δ Choke  [%/interval]"); a.set_title("Choke Move Size vs Ramp-Rate Limit")
    a.set_xlabel("Time  [h]"); a.set_ylim(-6.5, 6.5); a.legend(loc="best", fontsize=7.5)

    # shade the controller mode along the top of the rate plot
    if "mode" in df:
        colors = {"TRACKING": "#2E7D32", "CONSTRAINED": "#E8820C", "RECOVERY": "#C62828"}
        y0, y1 = ax[0, 0].get_ylim()
        h = (y1 - y0) * 0.035
        for mode, c in colors.items():
            mask = (df["mode"] == mode).values
            if mask.any():
                ax[0, 0].fill_between(t, y1 - h, y1, where=mask, color=c, alpha=0.55,
                                      step="post", lw=0, label=f"mode: {mode}")
        ax[0, 0].legend(loc="lower right", fontsize=7, ncol=2)

    fig.suptitle(title, fontsize=12.5, fontweight="bold", y=0.995)
    if annotate:
        fig.text(0.5, 0.005, annotate, ha="center", fontsize=8, style="italic", color="#444")
    fig.tight_layout(rect=[0, 0.02, 1, 0.985])
    if path:
        fig.savefig(path, bbox_inches="tight")
    return fig


def plot_step_test(df: pd.DataFrame, model, path: str | None = None):
    """Open-loop step test with the identified model overlaid."""
    from identification import OUTPUTS, SHORT, _simulate_channel
    u = df.Choke_pct.values.astype(float)
    fig, ax = plt.subplots(5, 1, figsize=(11, 10), sharex=True,
                           gridspec_kw={"height_ratios": [0.8, 1, 1, 1, 1]})
    ax[0].step(df.Time_hr, u, where="post", color=C_CHK, lw=1.6)
    ax[0].set_ylabel("Choke [%]"); ax[0].set_title("Open-loop step test — excitation sequence")

    units = {"OilRate_bbl_hr": "bbl/hr", "WHP_psi": "psi", "FLP_psi": "psi", "BHP_psi": "psi"}
    for i, col in enumerate(OUTPUTS):
        ym = df[col].values.astype(float)
        ch = model[col]
        yp = _simulate_channel([ch.a0, ch.a1, ch.a2, ch.tau, ch.theta], u, ym[0])
        a = ax[i + 1]
        a.plot(df.Time_hr, ym, color="#999", lw=1.0, label="simulator (plant)")
        a.plot(df.Time_hr, yp, color=C_ACT, lw=1.5, label="identified model (free-run)")
        a.set_ylabel(f"{SHORT[col]} [{units[col]}]")
        a.set_title(f"{SHORT[col]}:  τ = {ch.tau:.1f} h,  θ = {ch.theta:.2f} h,  "
                    f"R² = {ch.r2:.4f},  RMSE = {ch.rmse:.2f}")
        a.legend(loc="best", fontsize=7.5)
    ax[-1].set_xlabel("Time  [h]")
    fig.tight_layout()
    if path:
        fig.savefig(path, bbox_inches="tight")
    return fig


def plot_validation(model, ref: pd.DataFrame, path: str | None = None):
    """Free-run validation of the identified model on the reference dataset."""
    from identification import OUTPUTS, SHORT, _simulate_channel
    u = ref.Choke_pct.values.astype(float)
    fig, ax = plt.subplots(2, 2, figsize=(11, 6))
    units = {"OilRate_bbl_hr": "bbl/hr", "WHP_psi": "psi", "FLP_psi": "psi", "BHP_psi": "psi"}
    for a, col in zip(ax.ravel(), OUTPUTS):
        ym = ref[col].values.astype(float)
        ch = model[col]
        yp = _simulate_channel([ch.a0, ch.a1, ch.a2, ch.tau, ch.theta], u, ym[0])
        e = yp - ym
        r2 = 1 - np.sum(e**2) / np.sum((ym - ym.mean()) ** 2)
        a.plot(ref.Time_hr, ym, color="#111", lw=1.4, label="Honeywell reference dataset")
        a.plot(ref.Time_hr, yp, color=C_ACH, lw=1.6, ls="--", label="identified model (free-run)")
        a.set_title(f"{SHORT[col]}   R² = {r2:.4f},  RMSE = {np.sqrt(np.mean(e**2)):.2f} {units[col]}")
        a.set_xlabel("Time [h]"); a.set_ylabel(f"{SHORT[col]} [{units[col]}]")
        a.legend(loc="best", fontsize=7.5)
    fig.suptitle("Cross-validation: model identified on our own step tests, "
                 "tested on the independent Honeywell reference data",
                 fontsize=11, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    if path:
        fig.savefig(path, bbox_inches="tight")
    return fig


def plot_envelope(scan: pd.DataFrame, env: OperatingEnvelope | None = None,
                  path: str | None = None):
    """Steady-state operating envelope: where the well can safely be run."""
    env = env or OperatingEnvelope.load()
    fig, ax = plt.subplots(2, 2, figsize=(11, 6.4))
    feas = scan[scan.feasible]
    u_lo, u_hi = feas.Choke_pct.min(), feas.Choke_pct.max()
    Qmax = feas.Q.max()

    specs = [("Q", "Oil rate [bbl/hr]", None, None),
             ("WHP", "WHP [psi]", env.WHP_min, env.WHP_max),
             ("FLP", "FLP [psi]", env.FLP_min, env.FLP_max),
             ("BHP", "BHP [psi]", env.BHP_min, env.BHP_max)]
    for a, (col, lbl, lo, hi) in zip(ax.ravel(), specs):
        a.plot(scan.Choke_pct, scan[col], color=C_ACT, lw=1.8)
        if lo is not None:
            _limit_band(a, lo, hi)
        a.axvspan(u_lo, u_hi, color="#2E7D32", alpha=0.10, zorder=0,
                  label=f"feasible choke {u_lo:.1f}–{u_hi:.1f} %")
        a.set_xlabel("Choke opening [%]"); a.set_ylabel(lbl)
        a.legend(loc="best", fontsize=7.5)
    ax.ravel()[0].axhline(Qmax, color=C_ACH, ls=":", lw=1.8,
                          label=f"max safe rate = {Qmax:.1f} bbl/hr")
    ax.ravel()[0].legend(loc="best", fontsize=7.5)
    ax.ravel()[0].set_title("Production capacity is limited by BHP, not by the choke")
    fig.suptitle("Steady-state operating envelope of the well",
                 fontsize=11.5, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    if path:
        fig.savefig(path, bbox_inches="tight")
    return fig


def plot_results_summary(dfs: dict, env: OperatingEnvelope | None = None,
                         path: str | None = None):
    """Compact 3-scenario comparison used on the results slide."""
    env = env or OperatingEnvelope.load()
    fig, ax = plt.subplots(2, 3, figsize=(13.2, 5.6), sharex="col")
    titles = {
        "A": "A — Start-up  (choke 18 % → 100 bbl/hr)",
        "B": "B — Target change  (100 → 150 bbl/hr)",
        "C": "C — Infeasible target  (→ 200 bbl/hr)",
    }
    for j, tag in enumerate(["A", "B", "C"]):
        df = dfs[tag]
        a = ax[0, j]
        a.plot(df.Time_hr, df.Q_target, color=C_TGT, ls="--", lw=1.5, label="requested target")
        a.plot(df.Time_hr, df.Q_achievable, color=C_ACH, ls=":", lw=2.0, label="achievable (SSTO)")
        a.plot(df.Time_hr, df.OilRate_bbl_hr, color=C_ACT, lw=1.5, label="actual rate")
        a.set_title(titles[tag], fontsize=9.5)
        if j == 0:
            a.set_ylabel("Oil rate  [bbl/hr]")
        a.legend(fontsize=7, loc="lower right")

        a = ax[1, j]
        a.axhspan(env.BHP_min, env.BHP_max, color="#2E7D32", alpha=0.07, zorder=0)
        a.axhline(env.BHP_min, color=C_LIM, ls="--", lw=1.2, label="BHP limits (hard)")
        a.axhline(env.BHP_max, color=C_LIM, ls="--", lw=1.2)
        a.plot(df.Time_hr, df.BHP_psi, color=C_ACT, lw=1.4)
        a.set_xlabel("Time  [h]")
        if j == 0:
            a.set_ylabel("BHP  [psi]")
        a.set_ylim(env.BHP_min - 60, env.BHP_max + 90)
        a.legend(fontsize=7, loc="upper right")

    fig.suptitle("Closed-loop results — the binding constraint (BHP) is respected in every scenario",
                 fontsize=12, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.955])
    if path:
        fig.savefig(path, bbox_inches="tight", facecolor="white")
    return fig


# ==============================================================================
# Extended studies: baselines, robustness, stress tests
# ==============================================================================


def plot_baselines(traces: dict, env: OperatingEnvelope | None = None,
                   path: str | None = None):
    """MPC vs a tuned PI vs a conservative operator, on the hardest scenario."""
    env = env or OperatingEnvelope.load()
    colors = {"MPC": C_ACT, "PI": "#C62828", "OPERATOR": "#E8820C"}
    labels = {"MPC": "MPC (this work)", "PI": "PI on rate (IMC-tuned)",
              "OPERATOR": "Operator rule (fixed safe choke)"}

    fig, ax = plt.subplots(2, 3, figsize=(13.4, 4.9))
    for j, tag in enumerate(["A", "B", "C"]):
        a = ax[0, j]
        d0 = traces[tag]["MPC"]
        a.plot(d0.Time_hr, d0.Q_target, color="#111", ls="--", lw=1.3, label="target")
        for name, d in traces[tag].items():
            a.plot(d.Time_hr, d.Q_true, color=colors[name], lw=1.5, label=labels[name])
        a.set_title({"A": "A — Start-up", "B": "B — Target change",
                     "C": "C — Infeasible target (200 bbl/hr)"}[tag], fontsize=10)
        if j == 0:
            a.set_ylabel("Oil rate  [bbl/hr]")
        a.legend(fontsize=7, loc="best")

        a = ax[1, j]
        a.axhspan(env.BHP_min, env.BHP_max, color="#2E7D32", alpha=0.07, zorder=0)
        a.axhline(env.BHP_min, color=C_LIM, ls="--", lw=1.3, label="BHP limits (hard)")
        a.axhline(env.BHP_max, color=C_LIM, ls="--", lw=1.3)
        for name, d in traces[tag].items():
            a.plot(d.Time_hr, d.BHP_true, color=colors[name], lw=1.4)
        a.set_xlabel("Time  [h]")
        if j == 0:
            a.set_ylabel("BHP  [psi]")
        a.legend(fontsize=7, loc="lower left")
    fig.suptitle("Better than what?  MPC against a properly tuned PI and a cautious operator",
                 fontsize=12.5, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    if path:
        fig.savefig(path, bbox_inches="tight", facecolor="white")
    return fig


def plot_montecarlo(mc: pd.DataFrame, path: str | None = None):
    """Outcome of the randomised plant-model mismatch study."""
    fig, ax = plt.subplots(1, 3, figsize=(13.4, 3.9))
    c = mc[mc.scenario == "C"]
    b = mc[mc.scenario == "B"]

    a = ax[0]
    a.bar(["Scenario B\n(target change)", "Scenario C\n(at the constraint)"],
          [(b.violating_intervals > 0).mean() * 100, (c.violating_intervals > 0).mean() * 100],
          color=[C_GREEN_OK := "#2E7D32", "#E8820C"], width=0.55)
    for i, v in enumerate([(b.violating_intervals > 0).mean() * 100,
                           (c.violating_intervals > 0).mean() * 100]):
        a.text(i, v + 0.15, f"{v:.1f} %", ha="center", fontweight="bold", fontsize=10)
    a.set_ylabel("% of randomised models\nthat violate a limit")
    a.set_title(f"{len(b)} randomised models per scenario\ngain ±30 %, τ ±50 % per channel", fontsize=9.5)
    a.set_ylim(0, max(6, (c.violating_intervals > 0).mean() * 100 * 1.6))

    a = ax[1]
    a.scatter(c.tau_BHP, c.worst_excursion_psi, s=16,
              c=np.where(c.violating_intervals > 0, "#C62828", "#0B6FA4"), alpha=0.75)
    a.axvline(0.69, color="#C62828", ls="--", lw=1.3)
    a.text(0.70, a.get_ylim()[1] * 0.82, " every failure has\n τ_BHP factor < 0.69",
           fontsize=8, color="#C62828", fontweight="bold")
    a.set_xlabel("τ_BHP error factor  (model τ ÷ true τ)")
    a.set_ylabel("worst excursion  [psi]")
    a.set_title("Failures have one signature:\nthe model thinks BHP settles too fast", fontsize=9.5)

    a = ax[2]
    a.hist(c.rate_last30, bins=22, color=C_ACT, alpha=0.85)
    a.axvline(c.rate_last30.mean(), color="#E8820C", lw=2,
              label=f"mean {c.rate_last30.mean():.1f} bbl/hr")
    a.set_xlabel("settled oil rate  [bbl/hr]")
    a.set_ylabel("count")
    a.set_title("Production is barely affected\nby severe model error", fontsize=9.5)
    a.legend(fontsize=8)

    fig.suptitle("Monte-Carlo plant–model mismatch", fontsize=12.5, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    if path:
        fig.savefig(path, bbox_inches="tight", facecolor="white")
    return fig


def plot_breaking_point(sw: pd.DataFrame, path: str | None = None):
    """Where the controller stops working - one parameter at a time."""
    params = list(dict.fromkeys(sw.parameter))
    fig, ax = plt.subplots(1, len(params), figsize=(13.4, 3.6))
    for a, p in zip(np.atleast_1d(ax), params):
        g = sw[sw.parameter == p]
        bad = g.violating_intervals > 0
        a.bar(range(len(g)), g.violating_intervals,
              color=np.where(bad, "#C62828", "#2E7D32"), width=0.6)
        a.set_xticks(range(len(g)))
        a.set_xticklabels([f"{v:g}" for v in g.value], fontsize=8)
        a.set_xlabel(p, fontsize=9)
        a.set_ylim(0, max(float(g.violating_intervals.max()) * 1.25, 1.0))
        if not bad.any():
            a.text(0.5, 0.5, "0 violations\nat every setting", ha="center",
                   va="center", transform=a.transAxes, fontsize=10,
                   color="#2E7D32", fontweight="bold")
        a.set_title("SAFE across the range" if not bad.any()
                    else f"fails at {g.value[bad].min():g}", fontsize=9.5,
                    color="#2E7D32" if not bad.any() else "#C62828")
    np.atleast_1d(ax)[0].set_ylabel("violating intervals")
    fig.suptitle("Where does it break?  One design parameter at a time (Scenario C)",
                 fontsize=12.5, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.90])
    if path:
        fig.savefig(path, bbox_inches="tight", facecolor="white")
    return fig


def plot_stress(out: dict, env: OperatingEnvelope | None = None,
                path: str | None = None):
    """Disturbances and instrument faults - assumptions deliberately broken."""
    env = env or OperatingEnvelope.load()
    fig, ax = plt.subplots(2, 4, figsize=(14.2, 5.0))

    # -- D1 reservoir decline
    d = out["D1"]
    a = ax[0, 0]
    a.plot(d.Time_hr, d.Q_target, "--", color="#111", lw=1.3, label="target")
    a.plot(d.Time_hr, d.Q_achievable, ":", color=C_ACH, lw=1.8, label="achievable (SSTO)")
    a.plot(d.Time_hr, d.Q_true, color=C_ACT, lw=1.5, label="actual")
    a.set_title("D1  Reservoir decline\n(0.3 psi/h, 48 psi over the run)", fontsize=9.5)
    a.set_ylabel("Oil rate  [bbl/hr]"); a.legend(fontsize=7)
    a = ax[1, 0]
    a.plot(d.Time_hr, d.Choke_pct, color=C_CHK, lw=1.6, label="choke")
    a2 = a.twinx(); a2.plot(d.Time_hr, d.Pr_psi, color="#6A1B9A", lw=1.4)
    a2.set_ylabel("reservoir pressure [psi]", color="#6A1B9A", fontsize=8)
    a.set_ylabel("Choke  [%]"); a.set_xlabel("Time [h]"); a.legend(fontsize=7, loc="upper left")

    # -- D2 water cut
    d = out["D2"]
    a = ax[0, 1]
    a.plot(d.Time_hr, d.Q_target, "--", color="#111", lw=1.3)
    a.plot(d.Time_hr, d.Q_true, color=C_ACT, lw=1.5)
    a.axvline(70, color="#C62828", ls=":", lw=1.5)
    a.text(72, a.get_ylim()[0] + 1, " water cut\n 0 → 12 %", fontsize=8, color="#C62828")
    a.set_title("D2  Water-cut step\n(unmodelled fluid change)", fontsize=9.5)
    a = ax[1, 1]
    a.plot(d.Time_hr, d.Choke_pct, color=C_CHK, lw=1.6)
    a.axvline(70, color="#C62828", ls=":", lw=1.5)
    a.set_xlabel("Time [h]")

    # -- F1 frozen / F4 IO-card failure, with and without the bad-data layer
    for col, tag, title in ((2, "F1", "F1  Frozen BHP transmitter\n(sticks for 50 h)"),
                            (3, "F4", "F4  IO card fails: BHP +220,\nWHP +45 psi high")):
        d_on, d_off = out[tag], out[f"{tag}_novalidation"]
        a = ax[0, col]
        a.plot(d_off.Time_hr, d_off.BHP_true, color="#C62828", lw=1.4,
               label="validation OFF")
        a.plot(d_on.Time_hr, d_on.BHP_true, color=C_ACT, lw=1.5, label="validation ON")
        a.axhline(env.BHP_min, color=C_LIM, ls="--", lw=1.3, label="BHP limit")
        a.set_title(title, fontsize=9.5)
        lo = min(d_off.BHP_true.min(), d_on.BHP_true.min(), env.BHP_min)
        a.set_ylim(lo - 4, env.BHP_min + 45)
        a.legend(fontsize=7, loc="upper right")
        exc = env.BHP_min - d_off.BHP_true.min()
        if exc > 0:
            a.text(0.03, 0.13, f"validation OFF dips {exc:.1f} psi below the limit",
                   transform=a.transAxes, fontsize=7.5, color="#C62828",
                   fontweight="bold",
                   bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.85))
        if col == 2:
            a.set_ylabel("BHP  [psi]")
        a = ax[1, col]
        a.plot(d_off.Time_hr, d_off.Choke_pct, color="#C62828", lw=1.4)
        a.plot(d_on.Time_hr, d_on.Choke_pct, color=C_ACT, lw=1.5)
        held = (d_on["mode"] == "HOLD_BAD_DATA").values
        if held.any():
            a.fill_between(d_on.Time_hr, 0, 100, where=held, color="#FFC107",
                           alpha=0.30, step="post", lw=0, label="choke frozen (HOLD)")
            a.legend(fontsize=7, loc="lower right")
        a.set_ylim(30, 85); a.set_xlabel("Time [h]")

    fig.suptitle("Breaking the challenge's own assumptions: disturbances and instrument faults",
                 fontsize=12.5, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    if path:
        fig.savefig(path, bbox_inches="tight", facecolor="white")
    return fig


def plot_limit_sensitivity(df: pd.DataFrame, base: OperatingEnvelope | None = None,
                           df_prop: pd.DataFrame | None = None,
                           path: str | None = None):
    """Does the result depend on the operating limits we chose?  (It does not.)"""
    base = base or OperatingEnvelope.load()
    fig, ax = plt.subplots(1, 3, figsize=(13.4, 4.0))

    a = ax[0]
    colours = {"BHP_min": C_ACT, "WHP_min": "#E8820C", "FLP_min": "#6A1B9A",
               "BHP_max": "#00838F", "WHP_max": "#AD1457", "FLP_max": "#558B2F"}
    for tag, g in df.groupby("binding_constraint"):
        a.scatter(g.max_safe_rate, g.achieved_rate, s=16, alpha=0.8,
                  color=colours.get(tag, "#999"), label=f"{tag} binding  (n={len(g)})")
    lo = min(df.max_safe_rate.min(), df.achieved_rate.min()) - 4
    hi = max(df.max_safe_rate.max(), df.achieved_rate.max()) + 4
    a.plot([lo, hi], [lo, hi], "--", color="#111", lw=1.2, label="theoretical ceiling")
    a.set_xlabel("that envelope's own max safe rate  [bbl/hr]")
    a.set_ylabel("rate the controller achieved  [bbl/hr]")
    a.set_title("Whatever the limits, it finds the ceiling", fontsize=10)
    a.legend(fontsize=7, loc="upper left")

    a = ax[1]
    nf = int((df.violating_intervals > 0).sum())
    a.hist(df.pct_of_max_safe, bins=24, color=C_ACT, alpha=0.85,
           label=f"fixed 5 psi back-off — mean {df.pct_of_max_safe.mean():.1f} %\n"
                 f"{nf} of {len(df)} envelopes violate")
    if df_prop is not None:
        npr = int((df_prop.violating_intervals > 0).sum())
        a.hist(df_prop.pct_of_max_safe, bins=24, color="#C62828", alpha=0.55,
               label=f"span-proportional — mean {df_prop.pct_of_max_safe.mean():.1f} %\n"
                     f"{npr} of {len(df_prop)} envelopes violate")
    a.set_xlabel("achieved, as % of that envelope's max safe rate")
    a.set_ylabel("envelopes")
    a.set_title("The missing 3 % is what the guarantee costs", fontsize=10)
    a.legend(fontsize=7, loc="upper left")

    a = ax[2]
    counts = df.binding_constraint.value_counts()
    a.bar(range(len(counts)), counts.values,
          color=[colours.get(k, "#999") for k in counts.index], width=0.6)
    a.set_xticks(range(len(counts)))
    a.set_xticklabels(counts.index, fontsize=8, rotation=20)
    a.set_ylabel("envelopes")
    a.set_title("Which limit actually binds\n(it is not always BHP)", fontsize=10)
    nviol = int((df.violating_intervals > 0).sum())
    a.text(0.5, 0.88, f"constraint violations:\n{nviol} of {len(df)} envelopes",
           transform=a.transAxes, ha="center", fontsize=10, fontweight="bold",
           color="#2E7D32" if nviol == 0 else "#C62828",
           bbox=dict(boxstyle="round,pad=0.4", fc="#F1F8F1" if nviol == 0 else "#FDECEC",
                     ec="#2E7D32" if nviol == 0 else "#C62828"))

    fig.suptitle("200 randomised operating envelopes — the limits are an input, not an assumption",
                 fontsize=12, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.92])
    if path:
        fig.savefig(path, bbox_inches="tight", facecolor="white")
    return fig


def plot_monitored(traces: dict, env: OperatingEnvelope | None = None,
                   path: str | None = None):
    """
    Wellhead temperature and annulus pressure - monitored, not constrained.

    The brief names both as variables that "should be recognised as part of a
    complete production operating envelope". The reason shows up immediately:
    they move in the OPPOSITE direction to the three constrained pressures.
    """
    from simulator import max_safe_rate, steady_state_table
    env = env or OperatingEnvelope.load()
    t = steady_state_table()
    cap = max_safe_rate(env)
    feas = ((t["WHP"] >= env.WHP_min) & (t["WHP"] <= env.WHP_max)
            & (t["FLP"] >= env.FLP_min) & (t["FLP"] <= env.FLP_max)
            & (t["BHP"] >= env.BHP_min) & (t["BHP"] <= env.BHP_max))

    fig, ax = plt.subplots(1, 3, figsize=(13.4, 4.0))

    a = ax[0]
    a.axvspan(t["u"][feas].min(), t["u"][feas].max(), color="#2E7D32", alpha=0.10,
              label="feasible choke window")
    a.plot(t["u"], t["BHP"] / 10.0, color=C_ACT, lw=1.8, label="BHP ÷ 10  (constrained)")
    a.plot(t["u"], t["WHP"], color="#0288D1", lw=1.8, label="WHP  (constrained)")
    a.plot(t["u"], t["AP"], color="#C62828", lw=2.0, ls="--", label="annulus pressure  (monitored)")
    a.plot(t["u"], t["WHT"], color="#E8820C", lw=2.0, ls="--", label="WHT [°F]  (monitored)")
    a.set_xlabel("Choke opening  [%]"); a.set_ylabel("psi   /   °F")
    a.set_title("They move the OTHER way", fontsize=10)
    a.legend(fontsize=7, loc="center right")

    a = ax[1]
    for tag, c in (("A", "#2E7D32"), ("B", C_ACT), ("C", "#E8820C")):
        d = traces[tag]
        a.plot(d.Time_hr, d.AP_psi, color=c, lw=1.5, label=f"Scenario {tag}")
    a.set_xlabel("Time  [h]"); a.set_ylabel("annulus pressure  [psi]")
    a.set_title("Annulus pressure rises as the controller\npushes for rate", fontsize=10)
    a.legend(fontsize=8)

    a = ax[2]
    lims = [290, 300, 310, 320]
    rates = []
    for L in lims:
        j = np.where(feas & (t["AP"] <= L))[0]
        rates.append(t["Q"][j].max() if len(j) else 0.0)
    bars = a.bar([str(L) for L in lims], rates, width=0.6,
                 color=["#C62828" if r < cap["Q"] - 0.5 else "#2E7D32" for r in rates])
    a.axhline(cap["Q"], color="#111", ls="--", lw=1.4,
              label=f"{cap['Q']:.1f} bbl/hr on the three pressures alone")
    for b, r in zip(bars, rates):
        a.text(b.get_x() + b.get_width() / 2, r + 1.5, f"{r:.0f}", ha="center",
               fontsize=8.5, fontweight="bold")
    a.set_xlabel("hypothetical annulus-pressure limit  [psi]")
    a.set_ylabel("max safe rate  [bbl/hr]")
    a.set_title("If AP had a limit it would bind FIRST\n(below ~318 psi)", fontsize=10)
    a.set_ylim(0, cap["Q"] * 1.18); a.legend(fontsize=7.5, loc="lower right")

    fig.suptitle("Monitored variables: why the brief says they belong in a complete operating envelope",
                 fontsize=12, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.92])
    if path:
        fig.savefig(path, bbox_inches="tight", facecolor="white")
    return fig


def plot_controller_decisions(df: pd.DataFrame, diagnostics: list,
                              env: OperatingEnvelope | None = None,
                              launch_at=(30, 34, 38, 44, 52, 66),
                              inspect_at: int = 35,
                              path: str | None = None):
    """
    What the controller PREDICTED and what it REJECTED - not just what happened.

    Every other figure in this project shows an outcome. This one opens the
    controller up at the moment it decides:

      * the trajectories it committed to, drawn from the interval it committed
        to them, against what the plant actually did afterwards
      * the full candidate landscape at one interval - cost against move size,
        which candidates survived constraint screening, and which one was applied
      * how the feasible set shrinks as the well approaches its limit
    """
    env = env or OperatingEnvelope.load()
    fig, ax = plt.subplots(2, 2, figsize=(13.4, 5.5))
    P = len(diagnostics[0]["Q_pred"])

    # -- 1. predicted vs actual oil rate ------------------------------------
    a = ax[0, 0]
    a.plot(df.Time_hr, df.Q_target, "--", color="#111", lw=1.2, label="requested target")
    a.plot(df.Time_hr, df.Q_true, color=C_ACT, lw=2.0, label="what actually happened")
    for n, k in enumerate(launch_at):
        if k >= len(diagnostics):
            continue
        e = diagnostics[k]
        t = e["t"] + np.arange(P)
        a.plot(t, e["Q_pred"], color=C_ACH, lw=1.0, alpha=0.85,
               label="predicted at each decision" if n == 0 else None)
        a.plot(e["t"], e["Q_pred"][0], "o", color=C_ACH, ms=3.5)
    a.set_ylabel("Oil rate  [bbl/hr]")
    a.set_title("Each thin line is a 40 h forecast the controller committed to",
                fontsize=10)
    a.legend(fontsize=7.5, loc="lower right")

    # -- 2. predicted vs actual BHP, against the limit ----------------------
    a = ax[0, 1]
    a.axhline(env.BHP_min, color=C_LIM, ls="--", lw=1.4, label="BHP limit (hard)")
    a.plot(df.Time_hr, df.BHP_true, color=C_ACT, lw=2.0, label="actual BHP")
    for n, k in enumerate(launch_at):
        if k >= len(diagnostics):
            continue
        e = diagnostics[k]
        a.plot(e["t"] + np.arange(P), e["BHP_pred"], color=C_ACH, lw=1.0, alpha=0.85,
               label="predicted BHP" if n == 0 else None)
    a.set_ylabel("BHP  [psi]")
    a.set_ylim(env.BHP_min - 20, max(df.BHP_true.max(), 3100) + 20)
    a.set_title("The constraint is enforced on the FORECAST, not on the measurement",
                fontsize=10)
    a.legend(fontsize=7.5, loc="upper right")

    # -- 3. the candidate landscape at one interval -------------------------
    e = diagnostics[min(inspect_at, len(diagnostics) - 1)]
    a = ax[1, 0]
    ok, bad = e["feasible"], ~e["feasible"]
    a.scatter(e["du_grid"][bad], e["cost"][bad], s=13, color="#C62828", alpha=0.55,
              label=f"rejected — forecast breaches a limit  ({int(bad.sum())})")
    a.scatter(e["du_grid"][ok], e["cost"][ok], s=13, color="#2E7D32", alpha=0.65,
              label=f"feasible  ({int(ok.sum())})")
    a.plot(e["du_grid"][e["chosen"]], e["cost"][e["chosen"]], "*", ms=20,
           color="#FF8F00", mec="#111", mew=0.6, label="applied (cheapest feasible)")
    a.set_xlabel("candidate first move  Δu  [%]")
    a.set_ylabel("cost   Σ(Q−Q_sp)² + w·ΣΔu²")
    a.set_yscale("log")
    a.set_title(f"Every candidate at t = {e['t']:.0f} h, and why one was chosen", fontsize=10)
    a.legend(fontsize=7.5, loc="upper center", markerscale=0.55)

    # -- 4. how the feasible set shrinks ------------------------------------
    a = ax[1, 1]
    t = np.array([d["t"] for d in diagnostics])
    nf = np.array([int(d["feasible"].sum()) for d in diagnostics])
    a.fill_between(t, 0, nf, color=C_ACT, alpha=0.30)
    a.plot(t, nf, color=C_ACT, lw=1.6)
    a.axhline(len(diagnostics[0]["du_grid"]), color="#777", ls=":", lw=1.2,
              label=f"{len(diagnostics[0]['du_grid'])} candidates generated")
    a.set_xlabel("Time  [h]"); a.set_ylabel("candidates surviving screening")
    a.set_title("The safe set shrinks as the well nears its limit", fontsize=10)
    a.set_ylim(0, len(diagnostics[0]["du_grid"]) * 1.12)
    a.legend(fontsize=7.5, loc="lower left")
    ax[1, 0].set_xlabel("candidate first move  Δu  [%]")

    fig.suptitle("Inside the controller: the forecast it acts on, and the moves it throws away",
                 fontsize=12.5, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    if path:
        fig.savefig(path, bbox_inches="tight", facecolor="white")
    return fig
