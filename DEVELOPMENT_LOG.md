# Development log

An honest record of how this was built, in the order it happened — including the
two things that were built wrong and had to be diagnosed, and the one conclusion
that turned out to be a misdiagnosis.

It is here because a result without its history is hard to trust. Anyone can
present a controller that works; what tells you whether it was engineered is
whether the author knows where it nearly didn't.

---

## Phase 1 — Read the data before writing any code

The reference dataset (`Autonomous_Choke_Control_Simulated_Dataset.csv`) is 120
hours at five choke levels: 30 → 40 → 55 → 45 → 65 %.

Segmenting at each choke change and taking the last five samples of each segment
gave five steady states. Two things fell out immediately:

* **BHP against Q is a straight line.** That is the signature of a linear inflow
  performance relationship, `Q = PI·(Pr − BHP)`. Fitting it gave
  `PI = 0.2313 bbl/hr/psi` and `Pr = 3569 psi`, and the fit predicted the
  intermediate points to within a few psi. This was *found*, not assumed — it is
  the single most load-bearing fact in the whole project.
* **Every output settles at a different speed.** Fitting a static gain plus one
  first-order lag per channel gave τ of 5.9 / 9.8 / 6.8 / 13.5 h for Q / WHP /
  FLP / BHP, at R² > 0.99. A single shared time constant would have been wrong
  for BHP by more than a factor of two.

**Decision:** the plant is a nonlinear steady-state map plus simple linear
dynamics. That shape drove every later choice.

---

## Phase 2 — Build the simulator from physics, not from a curve fit

The brief said a simulator would be provided. It was not attached, and HirePro
later confirmed in writing that none would be supplied and any equivalent could
be used.

The tempting shortcut was to interpolate the CSV. That was rejected: a lookup
table has no nonlinear coupling, so the controller would face an unrealistically
easy plant and the robustness results would mean nothing.

Instead, four equations solved simultaneously for each choke position:

```
IPR       Q   = PI·(Pr − BHP)
VLP       WHP = BHP − ΔP_tub(Q)
Choke     Q   = CdA(u)·√(WHP − FLP)
Manifold  FLP = f(u)
```

Reduced by substitution to one nonlinear equation in `Q`, solved with Brent's
method. Coefficients calibrated against the five measured steady states.

**Checked, not assumed:** the solved steady states reproduce all five choke
levels in the supplied data to within a few percent — from physics, not from a
fit to those same points.

**Known weakness, recorded at the time:** the manifold relation `FLP = f(u)` is
empirical. A real flowline pressure rises with throughput; here it falls, because
it stands in for the whole downstream system's response to the choke. This is
written up as A5 in `ASSUMPTIONS.md` rather than glossed over.

---

## Phase 3 — Identify the control model on our own experiments

The controller must not see the simulator's equations, or the whole exercise is
circular. So the simulator was treated as a black box and a designed step test
run against it: eight levels, 20 → 70 %, both directions, each held 40–55 h so
the slowest variable settles.

A Hammerstein model — static quadratic gain, one first-order lag — was fitted per
channel by nonlinear least squares on the **free-run** simulation error, not the
one-step-ahead error. One-step-ahead error flatters a model that an MPC will use
for 40-step prediction.

Transport delay θ was left free in [0, 5] h rather than assumed zero. It
converged to 0.00 h, confirming that a one-hour interval is far slower than this
well's transport delays.

**Validation:** the model was then free-run against the Honeywell reference
dataset — data it had never been fitted to — giving R² = 0.983–0.993.

---

## Phase 4 — Map the envelope before designing anything

Sweeping the choke and applying the operating limits answered the question the
controller exists to answer:

* feasible choke window **15.5 – 70.65 %**
* maximum safe rate **166.2 bbl/hr**, binding constraint **BHP_min**

Or in one line: `Q_max = (Pr − BHP_min)·PI = 719 × 0.2313 = 166.3 bbl/hr`.

**The insight that shaped the architecture:** the valve can still open another
30 %. Production is limited by *drawdown*, not by the choke. So the controller's
job is not to track a setpoint — it is to find a constrained optimum.

---

## Phase 5 — Controller v1, and why it was wrong

First attempt: enumerate candidate first moves, roll each forward, reject
infeasible ones, minimise tracking cost.

**Symptom:** near the constraint the choke hunted between 65 % and 77 %, and
Scenario C oscillated instead of settling.

**Diagnosis:** the candidate roll-out let every plan ramp to the steady-state
target at full rate regardless of the first move, so beyond the first few steps
all candidates predicted nearly the same trajectory. The cost was almost flat in
Δu, and a terminal penalty then dominated, producing bang-bang behaviour.

**Fix:** hold the plan's second move to the horizon so each candidate is
genuinely distinct over the full 40 h.

---

## Phase 6 — Controller v2, and the bug that mattered most

**Symptom:** smooth now, but a persistent 3–7 bbl/hr steady-state offset. The
controller settled 2 % short of target and stopped moving.

Time was spent suspecting the disturbance estimator. It was fine.

**Diagnosis:** the cost penalised only the *first* move, `Δu₁`. The optimiser
discovered it could set `Δu₁ = 0` and let `Δu₂` do all the work at zero cost —
and since a receding-horizon controller only ever *applies* the first move, the
controller stood still while believing it had a plan.

**Fix:** penalise every move in the plan. The offset went to zero — measured
worst case now below 0.35 bbl/hr across 30 runs.

**Why it is in the deck:** this is a genuine receding-horizon trap, it is subtle,
and it is the kind of thing you only find by chasing a symptom you did not expect.

---

## Phase 7 — Lock the behaviour before adding anything else

Regression tests written at this point, not at the end:

* the closed-form roll-out must equal the general recursion to < 1e-9
* all three scenarios must hold zero violations, correct rate, ramp respected

Everything after this had a safety net. The fast closed-form roll-out (3.5×
speedup) was only attempted *because* an equivalence test could prove it correct.

---

## Phase 8 — Evidence, not just results

A controller reporting "zero violations" on three scenarios has not been tested.
Four studies were added:

| Study | Question |
|---|---|
| Baselines | Better than *what*? A tuned PI and a cautious operator |
| Monte-Carlo mismatch | What if the model is wrong? 150 randomised models |
| Failure boundary | Where does it break? One parameter at a time |
| Stress tests | What if the brief's own assumptions fail? |

The mismatch study returned **7 of 150 failures in Scenario C** — and that number
was kept rather than tuned away. Every failure shared one cause: an
under-estimated τ_BHP.

---

## Phase 9 — A conclusion that was wrong, and the sweep that caught it

At the end of Phase 6 a lesson had been recorded: *"the prediction horizon must
cover the slowest constrained variable"* — because violations had disappeared
when P went from 20 h to 40 h.

Sweeping P properly from 5 h to 50 h showed that was **not** what fixed it. With
the steady-state target optimiser guaranteeing a feasible endpoint, horizon
length only tightens the worst excursion from 0.77 to 0.23 psi and never causes a
violation on its own. The constraint back-off — changed at the same time — had
done the real work.

**The honest lesson is about method, not horizons: two things were changed at
once and the wrong one was credited.** The original claim was removed from the
deck and replaced with this.

---

## Phase 10 — Trying to spend the safety margin, and putting it back

The limit-sensitivity study showed the controller reaching 96.9 % of each
envelope's ceiling. The missing 3 % traced entirely to the constraint back-off: a
fixed 5 psi margin is 1.1 % of BHP's span but 8.1 % of FLP's, so it looks
over-conservative whenever a narrow variable binds.

The obvious fix — make the back-off proportional to each variable's span —
recovered 2.4 percentage points of production **and lost the zero-violation
guarantee on 18 of 200 envelopes**.

It was put back. The margin is not slack; it is the price of the guarantee. The
proportional variant ships as `MPCConfig.backoff_frac` for anyone who wants to
make that trade knowingly.

---

## Phase 11 — Audit against the literal brief

When the full problem statement became available at readable resolution, the code
was audited line by line against it. Three fidelity gaps were found:

1. **The controller was not using the choke position feedback.** The brief lists
   *"Current Choke Position"* among the five inputs; the controller was tracking
   its own command history. Fixed — it now reads the measured position back, so a
   sticking valve is detected rather than silently accumulated.
2. **Scenario A's "zero violations" had an asterisk.** It started at a 10 % choke,
   where BHP is above its maximum-drawdown limit, and the clean result depended on
   excluding a 10-hour warm-up. Scenario A now starts at 18 %, inside the
   envelope, audited from the first interval. Recovery from outside the envelope
   is demonstrated *separately*, where it reads as a capability rather than a
   caveat.
3. **The step-test / ramp-limit question was open.** Closed by test: repeating the
   identification with the ramp limit enforced moves gains and time constants by
   under 1 %.

`tests/test_spec_compliance.py` was written at this point, asserting the code
against the literal wording of the brief.

---

## Phase 12 — Opening the controller up

Every figure until now showed an *outcome*. The last one shows the *decision*:
the 40 h forecasts committed to, the candidate landscape at a single interval,
and the safe set shrinking from 451 candidates to 207 as the well approaches its
limit.

This is the figure that makes the architecture visible rather than described.

---

## What this process was, and was not

**It was:** incremental, with a verification gate at each step; data-driven before
model-driven; and honest about two implementation bugs and one wrong conclusion.

**It was not:** a formal SDLC. There is no version-control history, no second-
engineer design review, and the tests were written after the code rather than
before. Docstring coverage is good but not complete. These are stated rather than
hidden.

**On tooling:** this project was built with AI assistance, with the engineering
decisions directed and reviewed at each phase. The reasoning, the diagnoses and
the corrections above are real and reproducible — every number in the report is
asserted against the result files by `tests/test_reported_numbers.py`, and the
whole study regenerates from `python src/scenarios.py && python src/studies.py`.
