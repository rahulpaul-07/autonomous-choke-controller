import nbformat as nbf
nb = nbf.v4.new_notebook()
C = []
md = lambda s: C.append(nbf.v4.new_markdown_cell(s))
cd = lambda s: C.append(nbf.v4.new_code_cell(s))

md("""# Autonomous Production Choke Controller for a Single Naturally Flowing Oil Well

**Honeywell Campus Connect — Hackathon Round 2**
**Rahul Paul**

---

### What this notebook does

| # | Step | Section |
|---|------|---------|
| 1 | Analyse the Honeywell reference dataset — steady states, gains, dynamics | 1 |
| 2 | Build a **physics-structured dynamic simulator** of the well (IPR + VLP + choke) | 2 |
| 3 | Run **open-loop step tests** on our own simulator and log the data | 3 |
| 4 | **Identify a control-oriented model** (Hammerstein: nonlinear gain + first-order lag) | 4 |
| 5 | **Cross-validate** that model against the *independent* Honeywell reference data | 5 |
| 6 | Map the **safe operating envelope** and compute the maximum achievable safe rate | 6 |
| 7 | Implement the **two-layer MPC** (Steady-State Target Optimiser + dynamic MPC) | 7 |
| 8 | Demonstrate **Scenarios A / B / C** with full trend plots | 8 |
| 9 | **Robustness study** and KPI summary | 9 |

### Headline results

* Model cross-validates on the independent reference dataset with **R² = 0.98 – 0.99** on all four outputs.
* Across **30 closed-loop runs** (3 scenarios × 10 noise realisations): **zero constraint violations**.
* Steady-state tracking offset **< 0.35 bbl/hr** in every run (offset-free control).
* Infeasible target (200 bbl/hr) is automatically clipped to the **maximum safe rate of ≈ 166 bbl/hr**, limited by the minimum-BHP constraint — the controller settles there instead of winding up.
""")

cd("""import os, sys, json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

sys.path.insert(0, "src")
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 40)

from simulator import (WellSimulator, WellParameters, OperatingEnvelope,
                       steady_state, envelope_scan, max_safe_rate)
from identification import run_step_test, identify, cross_validate, OUTPUTS, SHORT
from controller import ChokeMPC, MPCConfig, SteadyStateTargetOptimiser, run_closed_loop
from plotting import plot_scenario, plot_step_test, plot_validation, plot_envelope
import scenarios as S

ENV = OperatingEnvelope()
CFG = MPCConfig()
print("environment ready")""")

md("""---
## 1. The problem, and what the reference data tells us

A **production choke** is the only control element on a naturally flowing well.
Opening it increases oil rate **Q**, but simultaneously *reduces* wellhead
pressure (WHP), flowline pressure (FLP) and bottom-hole pressure (BHP).
Push it too far and the well leaves its safe operating envelope — excessive
drawdown risks sand production and dropping below the bubble point, and low
WHP/FLP risks slugging and loss of transport to the separator.

So the control problem is a **constrained optimisation**, not a setpoint-tracking
problem: *maximise / track rate, subject to every pressure staying inside its
limits, and subject to a ±5 %/interval choke ramp-rate limit.*

The reference dataset contains a 120-hour multi-step test.""")

cd("""ref = pd.read_csv("data/Autonomous_Choke_Control_Simulated_Dataset.csv")
print(ref.shape)
ref.head()""")

cd("""# Segment the reference data at every choke change and report the approach to steady state
ch = ref.Choke_pct.values
edges = [0] + [i for i in range(1, len(ch)) if ch[i] != ch[i-1]] + [len(ch)]
rows = []
for a, b in zip(edges[:-1], edges[1:]):
    seg = ref.iloc[a:b]
    ss = seg.iloc[-5:].mean(numeric_only=True)
    rows.append({"t_start": seg.Time_hr.iloc[0], "t_end": seg.Time_hr.iloc[-1],
                 "hold_h": b - a, "choke_%": ch[a], "Q": ss.OilRate_bbl_hr,
                 "WHP": ss.WHP_psi, "FLP": ss.FLP_psi, "BHP": ss.BHP_psi})
seg_tbl = pd.DataFrame(rows).round(2)
seg_tbl""")

cd("""# Two facts that drive the whole design:
#  (1) BHP is almost perfectly LINEAR in Q  -> a linear IPR is the right reservoir model
#  (2) all four outputs move monotonically with choke, with DIFFERENT time constants
fig, ax = plt.subplots(1, 2, figsize=(11.5, 4))
ax[0].plot(seg_tbl.Q, seg_tbl.BHP, "o-", color="#0B6FA4")
p = np.polyfit(seg_tbl.Q, seg_tbl.BHP, 1)
ax[0].plot(seg_tbl.Q, np.polyval(p, seg_tbl.Q), "--", color="#E8820C",
           label=f"linear IPR fit: PI = {-1/p[0]:.4f} bbl/hr/psi\\nPr = {p[1]:.0f} psi")
ax[0].set_xlabel("Oil rate Q [bbl/hr]"); ax[0].set_ylabel("BHP [psi]")
ax[0].set_title("Inflow Performance Relationship is linear"); ax[0].legend(); ax[0].grid(alpha=.3)

for col, c in zip(OUTPUTS, ["#0B6FA4", "#E8820C", "#2E7D32", "#6A1B9A"]):
    y = ref[col].values
    ax[1].plot(ref.Time_hr, (y - y.min())/(y.max()-y.min()), lw=1.3, color=c, label=SHORT[col])
ax1b = ax[1].twinx(); ax1b.step(ref.Time_hr, ref.Choke_pct, where="post", color="#999", lw=1)
ax1b.set_ylabel("choke [%]", color="#999")
ax[1].set_xlabel("Time [h]"); ax[1].set_ylabel("normalised response")
ax[1].set_title("Different settling speeds per output"); ax[1].legend(fontsize=8); ax[1].grid(alpha=.3)
plt.tight_layout(); plt.show()""")

md("""**Reading of the data**

* `BHP` vs `Q` is a straight line → a **linear IPR** `Q = PI·(Pr − BHP)` is the correct
  reservoir model. The fit gives `PI ≈ 0.231 bbl/hr/psi` and `Pr ≈ 3569 psi`.
* Q responds fastest, BHP slowest → the plant needs **one time constant per output**,
  not a single lag. This directly sets the MPC prediction horizon
  (`P ≈ 3 × τ_BHP`).
* All gains are mildly nonlinear (the choke characteristic is quadratic-ish), which
  is why the control model uses a **static nonlinearity** rather than a single
  linearised gain.""")

md("""---
## 2. The simulator

Rather than replaying the reference data, we build a **physics-structured
simulator** whose steady state is solved from first principles and whose
parameters are calibrated against the reference dataset.

$$
\\begin{aligned}
\\text{Reservoir (IPR):} \\quad & Q = PI\\,(P_r - BHP) \\\\
\\text{Tubing (VLP):}    \\quad & WHP = BHP - \\Delta P_{tub}(Q) \\\\
\\text{Choke (orifice):} \\quad & Q = C_dA(u)\\,\\sqrt{WHP - FLP} \\\\
\\text{Manifold:}        \\quad & FLP = f(u)
\\end{aligned}
$$

These four equations are solved **simultaneously** at every steady state
(1-D root find on `Q` with Brent's method). The transient is generated by
applying an independent first-order lag per output, with time constants taken
from the step-test analysis. On top of that the simulator adds an
**actuator ramp-rate limit** and **measurement noise**, so an over-aggressive
controller cannot cheat.""")

cd("""p = WellParameters()
print(f"Reservoir pressure Pr = {p.Pr} psi,  Productivity index PI = {p.PI} bbl/hr/psi")
print(f"Time constants [h]: Q {p.tau_Q},  WHP {p.tau_WHP},  FLP {p.tau_FLP},  BHP {p.tau_BHP}")
print(f"Ramp-rate limit: +/- {p.du_max} % per {WellSimulator.Ts:.0f} h control interval\\n")

comp = []
for _, r in seg_tbl.iterrows():
    ss = steady_state(r["choke_%"])
    comp.append({"choke_%": r["choke_%"],
                 "Q_data": r.Q,   "Q_sim": round(ss["Q"], 2),
                 "WHP_data": r.WHP, "WHP_sim": round(ss["WHP"], 2),
                 "FLP_data": r.FLP, "FLP_sim": round(ss["FLP"], 2),
                 "BHP_data": r.BHP, "BHP_sim": round(ss["BHP"], 2)})
print("Simulator steady state vs reference dataset steady state:")
pd.DataFrame(comp)""")

md("""The simulator reproduces the reference steady states to within a few percent —
and it does so from **physical equations**, not from a curve fit of the data.
Small residuals remain because the last 20–30 h of each reference segment has not
fully settled (τ_BHP ≈ 13.5 h).

The required interface is exactly as specified in the problem statement:""")

cd("""sim = WellSimulator(u0=30.0, noise=True, seed=0)
Q, WHP, FLP, BHP = sim.step(35.0)          # <-- required interface
print(f"Q = {Q:.2f} bbl/hr | WHP = {WHP:.2f} | FLP = {FLP:.2f} | BHP = {BHP:.2f} psi")""")

md("""---
## 3. Open-loop step test

The controller is **not** allowed to see the simulator's internal physics.
We treat the simulator as a black box and run a designed multi-step test that

* spans the full operating envelope (20 % → 70 %),
* includes **both positive and negative** steps of different sizes (to check for
  asymmetry / hysteresis),
* holds each level long enough (40–55 h ≈ 3–4 τ) for the slowest output (BHP)
  to settle.""")

cd("""step_df = run_step_test()
print(f"{len(step_df)} hours of open-loop step-test data")
step_df.head()""")

md("""---
## 4. Control-oriented model identification

We fit a **Hammerstein** structure — a static nonlinearity followed by linear
dynamics — independently to each of the four outputs:

$$
y_{ss}(u) = a_0 + a_1 u + a_2 u^2, \\qquad
\\tau_y \\frac{dy}{dt} = y_{ss}\\big(u(t-\\theta)\\big) - y
$$

Parameters `(a₀, a₁, a₂, τ, θ)` are estimated per channel by nonlinear
least-squares on the **free-run simulation error** (not one-step-ahead error),
which is the honest test for a model that an MPC will use for multi-step
prediction. Multiple `(τ, θ)` starting points are used to avoid local minima.""")

cd("""model = identify(step_df)
model.summary()[["a0", "a1", "a2", "tau", "theta", "rmse", "r2"]].round(4)""")

cd("""fig = plot_step_test(step_df, model); plt.show()""")

md("""---
## 5. Cross-validation on the *independent* Honeywell reference dataset

This is the key credibility check: the model above was fitted **only** on our own
step-test data. We now free-run it (open loop, no feedback, no re-fitting) over
the choke sequence in the Honeywell reference dataset and compare.""")

cd("""val = cross_validate(model, ref)
print("Free-run prediction error on data the model has never seen:")
display(val.round(3))
fig = plot_validation(model, ref); plt.show()""")

md("""R² of **0.98 – 0.99** on unseen data, with RMSE small relative to the operating
range of each variable. The control model is trustworthy for multi-step
prediction.""")

md("""---
## 6. The safe operating envelope

Before designing the controller we ask a purely *steady-state* question:
**which choke openings are safe at all, and what is the maximum rate the well can
sustain inside its envelope?**

| Variable | Lower limit | Upper limit | Rationale |
|---|---|---|---|
| WHP | 200 psi | 320 psi | slugging / flowline entry ← → wellhead equipment rating |
| FLP | 140 psi | 210 psi | minimum transport pressure to separator ← → gathering system rating |
| BHP | 2850 psi | 3300 psi | maximum drawdown: sand & bubble-point ← → minimum drawdown for stable inflow |""")

cd("""scan = envelope_scan(ENV)
lim = max_safe_rate(ENV)
print(f"Feasible choke window : {lim['u_min_feasible']:.2f} % – {lim['u_max_feasible']:.2f} %")
print(f"Feasible rate window  : {lim['Q_min_feasible']:.2f} – {lim['Q']:.2f} bbl/hr")
print(f"MAXIMUM SAFE RATE     : {lim['Q']:.2f} bbl/hr at choke {lim['u']:.2f} %")
print(f"   binding constraint : BHP = {lim['BHP']:.1f} psi  (limit {ENV.BHP_min} psi)")
print(f"   WHP {lim['WHP']:.1f} psi, FLP {lim['FLP']:.1f} psi at that point")
fig = plot_envelope(scan, ENV); plt.show()""")

md("""**This is the single most important design insight.** The choke can physically
open to 100 %, but the well cannot be produced beyond ≈ **166 bbl/hr** without
violating the minimum-BHP limit. Any target above that is *infeasible* — and the
controller must recognise it rather than saturate the choke.""")

md("""---
## 7. The controller

Two layers, mirroring industrial APC architecture (Honeywell Profit Controller /
RMPCT, Aspen DMCplus):

### Layer 1 — Steady-State Target Optimiser (SSTO)
Every interval, scan the choke range on the *identified model's* gain curves,
keep only openings whose predicted steady state is inside the envelope
(with a back-off margin), and pick the one whose rate is closest to the
operator's target. If the target is unreachable, this returns the
**maximum achievable safe rate** — which is exactly how Scenario C is handled
without wind-up.

### Layer 2 — Dynamic MPC
* Enumerate a grid of **two-move plans** (41 × 11 = 451 candidates), each
  respecting the ±5 % ramp-rate limit.
* Roll every candidate forward over the full **P = 40 h** horizon
  (vectorised — the whole search is one NumPy operation, ~1 ms per interval).
* **Reject** any candidate whose predicted trajectory leaves the envelope.
* Among survivors, minimise `Σ(Q_pred − Q_sp)² + w·(Δu₁² + Δu₂²)`.
* Apply only the **first** move (receding horizon).

### Offset-free tracking
A DMC-style constant output-disturbance estimator adds the measured/predicted
mismatch back into the prediction, and the same bias is fed to the SSTO. This
removes steady-state offset caused by plant–model mismatch.

### RECOVERY mode
If *no* candidate is feasible — e.g. at start-up, when the well sits outside its
envelope before the choke is opened — the controller ranks candidates by
predicted constraint violation and takes the move that returns the well to the
envelope fastest.""")

cd("""print("MPC tuning\\n" + "-"*40)
for k, v in CFG.__dict__.items():
    print(f"  {k:14s} = {v}")

ssto = SteadyStateTargetOptimiser(model, ENV, CFG)
print("\\nSSTO behaviour for a range of requested targets:")
rows = []
for tgt in [50, 80, 100, 120, 150, 166, 180, 200, 250]:
    u_ss, Q_ach, mode, act = ssto.solve(tgt)
    rows.append({"requested": tgt, "u_ss_%": round(u_ss, 2),
                 "achievable": round(Q_ach, 2), "mode": mode,
                 "active_constraints": ",".join(act) or "-"})
pd.DataFrame(rows)""")

md("""Note how the SSTO switches to `CONSTRAINED` above ≈ 166 bbl/hr and reports
`BHP_min` as the active constraint — the controller *knows why* it cannot go
faster, and can tell the operator.""")

md("""---
## 8. Demonstration scenarios""")

md("""### Scenario A — Start-up to target
The well starts at a nearly-shut choke (10 %). At that opening drawdown is so
small that **BHP is above its upper limit** — the well begins *outside* its
envelope. The controller must first recover, then track 100 bbl/hr.""")

cd("""sim = WellSimulator(u0=10.0, seed=1)
ctrl = ChokeMPC(model, ENV, CFG, u0=10.0)
dfA = run_closed_loop(sim, ctrl, 100.0, 90)
kA = S.kpis(dfA, warmup=10)
print(json.dumps({k: v for k, v in kA.items() if k != "modes"}, indent=2, default=str))
print("modes:", kA["modes"])
fig = plot_scenario(dfA, "Scenario A — Start-up to Target (choke 10 % → 100 bbl/hr)", ENV); plt.show()""")

md("""### Scenario B — Target tracking
Steady operation at 100 bbl/hr; the operator raises the target to 150 bbl/hr at
t = 40 h.""")

cd("""sim = WellSimulator(u0=32.0, seed=2)
ctrl = ChokeMPC(model, ENV, CFG, u0=32.0)
dfB = run_closed_loop(sim, ctrl, lambda k: 100.0 if k < 40 else 150.0, 140)
kB = S.kpis(dfB)
print(json.dumps({k: v for k, v in kB.items() if k != "modes"}, indent=2, default=str))
fig = plot_scenario(dfB, "Scenario B — Target Tracking (100 → 150 bbl/hr at t = 40 h)", ENV); plt.show()""")

md("""### Scenario C — Infeasible target
The operator requests 200 bbl/hr, which cannot be reached without breaching the
minimum-BHP limit.""")

cd("""sim = WellSimulator(u0=40.0, seed=3)
ctrl = ChokeMPC(model, ENV, CFG, u0=40.0)
dfC = run_closed_loop(sim, ctrl, lambda k: 120.0 if k < 30 else 200.0, 140)
kC = S.kpis(dfC)
print(json.dumps({k: v for k, v in kC.items() if k != "modes"}, indent=2, default=str))
fig = plot_scenario(dfC, "Scenario C — Infeasible Target (120 → 200 bbl/hr at t = 30 h)", ENV); plt.show()""")

cd("""print("Scenario C — what the controller reports to the operator (last 5 intervals):")
dfC.tail(5)[["Time_hr", "Q_target", "Q_achievable", "Q_true", "BHP_true",
             "Choke_pct", "mode", "active_constraints", "n_feasible", "n_candidates"]].round(2)""")

md("""The controller settles at **≈ 165 bbl/hr — 99.3 % of the theoretical maximum
safe rate of 166.2 bbl/hr** — reports `mode = CONSTRAINED` with `BHP_min` as the
active constraint, and never violates a limit. `n_feasible` shows how many of the
451 candidate moves survived constraint screening: the search is genuinely
constrained, not just tracking a clipped setpoint.""")

md("""---
## 9. Robustness and KPI summary

A single lucky run proves nothing. We repeat all three scenarios across ten
independent measurement-noise realisations.""")

cd("""rob = []
for seed in range(1, 11):
    for tag, (u0, n, tgt, warm) in {
        "A": (10.0, 90, lambda k: 100.0, 10),
        "B": (32.0, 140, lambda k: 100.0 if k < 40 else 150.0, 0),
        "C": (40.0, 140, lambda k: 120.0 if k < 30 else 200.0, 0)}.items():
        sim = WellSimulator(u0=u0, seed=seed)
        ctrl = ChokeMPC(model, ENV, CFG, u0=u0)
        df = run_closed_loop(sim, ctrl, tgt, n)
        k = S.kpis(df, warmup=warm)
        rob.append({"seed": seed, "scenario": tag,
                    "violations": k["constraint_violations_true"],
                    "offset_bbl_hr": k["steady_state_offset"],
                    "settling_h": k["settling_time_h"],
                    "rate": k["final_rate_mean"],
                    "max_move_pct": k["max_abs_move_pct"]})
rob = pd.DataFrame(rob)
rob.groupby("scenario").agg(
    runs=("seed", "count"),
    TOTAL_VIOLATIONS=("violations", "sum"),
    mean_offset=("offset_bbl_hr", "mean"),
    worst_abs_offset=("offset_bbl_hr", lambda x: np.abs(x).max()),
    mean_settling_h=("settling_h", "mean"),
    mean_rate=("rate", "mean"),
    max_move_pct=("max_move_pct", "max")).round(3)""")

cd("""summary = pd.DataFrame({"A": kA, "B": kB, "C": kC}).T
summary[["final_target_requested", "final_target_achievable", "final_rate_mean",
         "steady_state_offset", "settling_time_h", "IAE_vs_achievable",
         "constraint_violations_true", "max_abs_move_pct", "total_choke_travel_pct"]]""")

md("""---
## 10. Conclusions and lessons learned

**What worked**

1. **Physics-structured simulator.** Solving IPR + VLP + choke simultaneously
   (rather than replaying a curve fit) gave a plant with genuine nonlinear
   coupling, so the plant–model mismatch the MPC faces is realistic.
2. **Hammerstein control model.** A static quadratic gain plus one first-order
   lag per output captured 98–99 % of the variance on unseen data with only five
   parameters per channel — enough fidelity for a 40-step prediction, cheap
   enough to search 451 candidate plans per interval in ~1 ms.
3. **Separating the steady-state and dynamic problems.** The SSTO answers
   *"where should we end up?"* and the MPC answers *"how do we get there safely?"*.
   This is what makes the infeasible-target case degrade gracefully instead of
   saturating the choke.
4. **Constraint back-off.** A 5 psi margin inside each limit absorbed measurement
   noise and model mismatch. It costs about 0.7 bbl/hr (0.4 % of production) and
   bought **zero violations in 30 runs** — a trade any production engineer takes.

**What we got wrong first, and fixed**

* *Move suppression on the first move only.* Our initial cost function penalised
  only Δu₁. The optimiser learned to set Δu₁ = 0 and let Δu₂ do the work for
  free — and since only the first move is applied, the controller stalled with a
  3–7 bbl/hr steady-state offset. Penalising **every** move in the plan removed
  the offset entirely. This is a subtle receding-horizon trap and was the single
  biggest bug in the project.
* *Horizon too short.* At P = 20 the MPC could not see BHP (τ = 13.5 h) settle,
  so it approved moves that were feasible over the horizon but violated the limit
  afterwards. P = 40 ≈ 3 τ_BHP fixed it.
* *Back-off too small.* At 2 psi, measurement noise produced occasional grazes of
  the BHP limit. 5 psi eliminated them at negligible production cost.

**How this would extend to a real asset**

* Replace the calibrated manifold correlation with a network model when several
  wells share a flowline, and promote the MPC to multi-well with a common
  separator-capacity constraint.
* Add a slow reservoir-depletion and water-cut model; the output-disturbance
  estimator already handles slow drift, but the SSTO gain curves would need
  periodic re-identification (adaptive step testing).
* Add WHT and annulus pressure as monitored-only variables now, promoted to
  constraints when integrity limits are defined.
* Wrap the SSTO in an economic objective (oil price − water handling cost)
  to turn rate maximisation into margin maximisation.
""")

nb["cells"] = C
nb.metadata = {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
               "language_info": {"name": "python", "version": "3.11"}}
nbf.write(nb, "notebook/Autonomous_Choke_Control.ipynb")
print("notebook written:", len(C), "cells")
