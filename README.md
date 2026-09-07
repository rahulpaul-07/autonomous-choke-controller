# Autonomous Production Choke Controller

**A constrained MPC that drives the production choke on a naturally flowing oil
well to a requested oil rate — and, when that rate is not safely reachable, says
so, names the limit stopping it, and produces the most it can instead of chasing
the number.**

[![CI](https://github.com/rahulpaul-07/autonomous-choke-controller/actions/workflows/ci.yml/badge.svg)](https://github.com/rahulpaul-07/autonomous-choke-controller/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**[Try it in the browser](https://autonomous-choke-controller-v1.streamlit.app)** ·
[Read the executed notebook](Autonomous_Choke_Control.ipynb) ·
[The 13 assumptions](ASSUMPTIONS.md) ·
[How it was built, including what went wrong](DEVELOPMENT_LOG.md)

Written for Honeywell Campus Connect, hackathon round 2. Rahul Paul.

---

### The problem, in three sentences

A production choke is the one valve between a flowing oil well and the gathering
system. Open it and the well makes more oil; open it too far and wellhead,
flowline or bottom-hole pressure leaves the range the equipment and the reservoir
can tolerate — and those pressure limits, not the valve, are what actually cap
production. This controller picks the choke position every hour on its own, and
the case worth caring about is not tracking a reachable target, it is what
happens when an operator asks for a rate the well cannot safely deliver.

### If you are skimming

* **Two minutes** — the headline table below, then `figures/controller_decisions.png`:
  451 candidate plans per interval, the ones the controller rejected, the one it applied.
* **Ten minutes** — [Part II — Evidence](#part-ii--evidence). Four studies: what it
  beats, what happens when the model is wrong, why each tuning number is what it is,
  and where it breaks.
* **The honest bits** — [Lessons learned](#lessons-learned) has the biggest bug in
  the project and the one conclusion that turned out to be a misdiagnosis.

---

## Headline results

| | |
|---|---|
| Constraint violations across **30 nominal runs** (3 scenarios × 10 noise realisations) | **0** |
| Constraint violations across **150 randomised-model runs**, Scenario B | **0** |
| Same, Scenario C (parked against the BHP limit) | **7 / 150 (4.7 %)**, worst excursion **1.16 psi on a 2850 psi limit** |
| Production vs a cautious operator at the same zero violations | **+5.6 % oil** (164.9 vs 156.1 bbl/hr) |
| A conventional rate PI on the same problem | violates on **69 % of intervals**, BHP 146 psi below its floor |
| Control-model accuracy on the *independent* Honeywell reference dataset | **R² = 0.983 – 0.993** |
| Worst-case steady-state tracking offset | **< 0.35 bbl/hr** (offset-free) |
| Controller execution time per hourly interval | **~1 ms** (451 candidate plans, 40-step horizon) |

Every number in this table is asserted against the result files by
`tests/test_reported_numbers.py`, so a claim cannot drift away from its evidence.

It also survives 48 psi of unmodelled reservoir depletion, a 12 % water-cut step,
a frozen transmitter, and a two-tag IO-card failure — and we report where it
does break: back-off 0 psi, noise ≥ 12× nominal, or τ_BHP under-estimated by
more than ~30 %.

---

## What is in this repository

```
Autonomous_Choke_Control.ipynb      executed notebook — the full study, top to bottom
Honeywell_..._Rahul_Paul.pptx       the deck, as submitted
Honeywell_..._Rahul_Paul.pdf        same deck as PDF
README.md                           this file
ASSUMPTIONS.md                      13 assumptions - reason, impact, limitation for each
DEVELOPMENT_LOG.md                  how it was built, in order, including what went wrong

config/
  operating_limits.json             THE six safe-operating limits, with derivation — edit here only

src/
  simulator.py                      physics-structured well simulator + operating envelope
  external_simulator.py             adapter for any externally supplied simulator
  adopt_simulator.py                one command to re-run everything against it
  identification.py                 step-test design, Hammerstein identification, cross-validation
  controller.py                     SSTO + dynamic MPC + bad-data layer + closed-loop driver
  baselines.py                      IMC-tuned PI and cautious-operator baselines
  robustness.py                     Monte-Carlo mismatch + failure-boundary sweeps
  stress_tests.py                   reservoir decline, water cut, instrument faults
  plotting.py                       every figure
  scenarios.py                      Part I  — the three demonstration scenarios
  studies.py                        Part II — baselines, robustness, stress tests
  make_architecture_figure.py       control-architecture block diagram

tests/
  test_rollout.py                   closed-form roll-out equivalence + scenario regression tests
  test_reported_numbers.py          asserts every headline number in this README against results/
  test_spec_compliance.py           asserts the code against the literal wording of the brief
  test_external_simulator.py        drives the whole pipeline through a foreign simulator
  mock_provided_simulator.py        a deliberately different simulator, used by that test

app/streamlit_app.py                the interactive demo linked at the top of this file
service/api.py                      HTTP service: /envelope, /target, /decide, /simulate

tools/
  check_reproducibility.py          compares regenerated tables to the committed ones, cell by cell
  build_notebook.py  build_deck.py  scripts that generated the notebook and the deck
  check_deliverables.py             submission completeness check

data/
  Autonomous_Choke_Control_Simulated_Dataset.csv    supplied reference dataset

figures/                            16 PNGs used in the notebook and the deck
    controller_decisions.png        what the controller predicted, and what it rejected
results/                            27 tables: per-scenario CSVs, identified model, KPIs, sweeps

Dockerfile                          one image for the study, the demo and the service
.github/workflows/ci.yml            the study, all four test scripts, and the reproducibility check
```

## How to run

```bash
git clone https://github.com/rahulpaul-07/autonomous-choke-controller.git
cd autonomous-choke-controller
pip install -r requirements.txt

python src/scenarios.py     # Part I  — simulator, identification, scenarios A/B/C   (~15 s)
python src/studies.py       # Part II — baselines, robustness, limits, stress, tuning (~2 min)
```

Or open `Autonomous_Choke_Control.ipynb` and run all cells (~20 s — it reads the
canonical tables from `results/`).

**The tests are the point of the repository**, so run them:

```bash
python tests/test_spec_compliance.py    # every literal requirement in the brief
python tests/test_rollout.py            # roll-out equivalence + scenario regression
python tests/test_reported_numbers.py   # every headline number, checked against results/
python tests/test_external_simulator.py # a foreign simulator and foreign limits, end to end
```

`make study`, `make tests` and `make reproduce` wrap all of the above.

**Swapping in a different simulator** takes one command. Anything exposing
`step(choke) -> (Q, WHP, FLP, BHP)` will do:

```bash
python src/adopt_simulator.py your_module:YourWellSim
```

It repeats the designed step test, re-identifies the control model, recomputes the
feasible window and the maximum safe rate, and re-runs Scenarios A/B/C against the
new plant — writing to `figures_external/` and `results_external/`, with no
controller re-tuning by hand.

### Run it as a service, or play with it

```bash
streamlit run app/streamlit_app.py          # interactive demo  -> localhost:8501
uvicorn service.api:app --port 8000         # HTTP service      -> localhost:8000/docs
```

The demo exposes the MPC knobs live and audits each run against the envelope in
front of you. The service wraps the calls a production system would actually make:

| | |
|---|---|
| `GET /envelope` | the six limits, the feasible choke window, the maximum safe rate and what binds it |
| `POST /target` | "I want R bbl/hr" → what is safely achievable, and the constraint stopping you |
| `POST /decide` | one control interval: measurements and choke position in, next choke position out |
| `POST /simulate` | run the closed loop and return the trace with a compliance audit |

`/decide` is the controller itself, one hour at a time, and it is stateful — the
disturbance estimate and the bad-data history carry between intervals. Driving the
real plant through it over HTTP reproduces Scenario C exactly and computes each
move in **0.75 – 1.2 ms**, which is where the timing claim above comes from.

Everything also runs in one container:

```bash
docker build -t choke-controller .
docker run --rm -p 8000:8000 choke-controller
docker run --rm -p 8501:8501 choke-controller \
    streamlit run app/streamlit_app.py --server.address 0.0.0.0
```

### Reproducibility is checked, not claimed

`figures/` and `results/` are committed, and CI deletes them, regenerates everything
from source, and compares the new tables against the committed ones cell by cell
(`tools/check_reproducibility.py`). Of the 62,154 numeric cells across the 27 tables,
62,142 are compared; the 12 that are not are a single wall-clock timing column, skipped
by name rather than quietly tolerated.

On one machine those cells come back bit-for-bit identical. Across machines they do not
quite, and the honest version is more interesting than the tidy one: the nonlinear least
squares in identification.py converges to a fractionally different point when numpy
links a different BLAS, and that difference propagates into every table downstream.
Measured Windows against the Linux CI runner, the largest relative disagreement anywhere
in the 27 tables is **6.2e-8** - eight orders of magnitude below the two decimal places
these results are ever quoted to. So CI asserts agreement to 1e-6 relative, about 16x
tighter than the largest difference observed, and prints the figure it actually measured
on every run.

The same workflow re-runs all four test scripts on Python 3.10 and 3.12 on every
push, so the headline numbers below cannot drift away from the code that produced
them.

---

## Approach in one page

### 1. Simulator (`src/simulator.py`)

Steady state is solved from first principles, not curve-fitted:

| | |
|---|---|
| Reservoir inflow (linear IPR) | `Q = PI · (Pr − BHP)` |
| Tubing lift performance | `WHP = BHP − ΔP_tub(Q)` |
| Production choke (orifice) | `Q = CdA(u) · √(WHP − FLP)` |
| Gathering manifold | `FLP = f(u)` |

The four equations are solved simultaneously by a 1-D root find on `Q`. Parameters
(`PI = 0.2313 bbl/hr/psi`, `Pr = 3569 psi`, choke and tubing coefficients) are
calibrated against the supplied reference dataset. Transients come from one
first-order lag per output, using the time constants seen in the step data.
The simulator also enforces the actuator ramp-rate limit and adds measurement
noise, so an over-aggressive controller cannot cheat.

Required interface:

```python
Q, WHP, FLP, BHP = simulator.step(choke_position)
```

### 2. Control-oriented model (`src/identification.py`)

The controller never sees the simulator's physics. We run a designed 8-level
open-loop step test (20 → 70 %, both directions, 40–55 h holds) and fit a
**Hammerstein** model per output:

```
y_ss(u) = a0 + a1·u + a2·u²          (static nonlinearity)
τ_y · dy/dt = y_ss(u(t−θ)) − y       (linear dynamics)
```

Fitted by nonlinear least squares on the **free-run** simulation error — the
honest test for a model used for 40-step prediction.

| Output | static gain @ 50 % | τ [h] | R² (own data) | R² (reference data) |
|---|---|---|---|---|
| Oil rate Q | +1.77 bbl/hr per % | 4.9 | 0.998 | 0.989 |
| WHP | −1.60 psi per % | 8.6 | 0.998 | 0.983 |
| FLP | −0.97 psi per % | 5.8 | 0.998 | 0.993 |
| BHP | −7.66 psi per % | 12.4 | 0.999 | 0.989 |

The last column is the key number: the model was fitted **only** on our own step
tests, then free-run against the Honeywell reference dataset it had never seen.

### 3. Operating envelope

| Variable | Limits | Rationale |
|---|---|---|
| WHP | 200 – 320 psi | slugging / flowline entry ↔ wellhead equipment rating |
| FLP | 140 – 210 psi | minimum transport pressure ↔ gathering system rating |
| BHP | 2850 – 3300 psi | maximum drawdown (sand, bubble point) ↔ minimum drawdown for stable inflow |

Sweeping the choke through this envelope gives the single most important design
fact: the well is **feasible only for choke ∈ [15.5 %, 70.65 %]**, and the
**maximum safe oil rate is 166.2 bbl/hr**, limited by minimum BHP — not by the
choke, which can still open another 30 %.

### 4. Controller (`src/controller.py`)

**Layer 1 — Steady-State Target Optimiser (SSTO).** Every interval, scan the
choke range on the identified gain curves, keep only openings whose predicted
steady state is inside the envelope (5 psi back-off), and pick the rate closest
to the operator's target. If the target is unreachable this returns the maximum
achievable safe rate and names the binding constraint.

**Layer 2 — Dynamic MPC.** Enumerate 41 × 11 = 451 candidate two-move plans,
each inside the ±5 %/interval ramp limit; roll all of them forward over a
P = 40 h horizon in one vectorised NumPy pass; **reject** any plan whose
predicted trajectory leaves the envelope; among survivors minimise
`Σ(Q − Q_sp)² + w·Σ Δu²`; apply the first move only.

**Offset-free tracking.** A DMC-style constant output-disturbance estimator
corrects both the MPC prediction and the SSTO gain curves.

**RECOVERY mode.** If no candidate is feasible (start-up, upset), rank by
predicted violation and take the move that returns the well to the envelope
fastest.

### 5. Scenario results

| | A — Start-up | B — Target change | C — Infeasible target |
|---|---|---|---|
| Requested target | 100 bbl/hr | 100 → 150 bbl/hr | 120 → 200 bbl/hr |
| Achievable (SSTO) | 100.0 | 150.0 | 165.2 *(capped)* |
| Achieved rate | 100.2 | 150.2 | 164.9 |
| Steady-state offset | +0.23 | +0.17 | −0.29 |
| Settling time | 14 h | 16 h | 16 h |
| **Constraint violations** | **0** | **0** | **0** |
| Max choke move | 5.00 % | 5.00 % | 5.00 % |
| Controller mode | TRACKING | TRACKING | TRACKING → CONSTRAINED |

Scenario A starts the well at an 18 % choke — inside its envelope, at the low end
— and is audited **from the very first interval with no warm-up period excluded**.

Recovery from a state the envelope forbids is demonstrated *separately*
(`figures/scenario_RECOVERY.png`): starting at a near-shut 10 % choke, BHP sits
above its maximum-drawdown limit, the controller enters RECOVERY, is back inside
the envelope in **5 h**, and then tracks 100 bbl/hr with zero further violations.
It is kept out of the three required scenarios so that the headline "zero
violations" needs no asterisk.

*On how violations are counted:* the simulator logs both the true plant state and
the noisy measurement, and compliance is audited on the **true state** — the
stricter test. In Scenario C, which parks the well hard against the minimum-BHP
limit, the noisy BHP *reading* dips a fraction of a psi below the limit on 2 of
140 intervals while the true pressure stays inside. Both counts are reported in
`results/kpi_summary.csv` as `constraint_violations_true` and
`constraint_violations_measured`.

---

## The simulator, and the limits

### 1. The simulator is ours — and that is the assignment

The problem statement says a Python simulator "will be provided" and the FAQ says
students are *not required* to build one. Neither is true. HirePro confirmed in
writing:

> Please use any open-source equivalent simulator for your solution, as we will
> **not** be providing a simulator. Kindly **ignore the line mentioned in the
> Problem Statement** regarding the simulator.

So the simulator is a graded deliverable, not a gap. Ours (`src/simulator.py`,
448 lines, full source included) solves four coupled equations for the steady
state at every step rather than curve-fitting the supplied CSV:

| | |
|---|---|
| Reservoir inflow (linear IPR) | `Q = PI · (Pr − BHP)` |
| Tubing lift performance | `WHP = BHP − ΔP_tub(Q)` |
| Production choke (orifice) | `Q = CdA(u) · √(WHP − FLP)` |
| Gathering manifold | `FLP = f(u)` |

Parameters (`PI = 0.2313 bbl/hr/psi`, `Pr = 3569 psi`, tubing and choke
coefficients) are calibrated against the reference dataset, and the resulting
steady states reproduce all five choke levels in that dataset to within a few
percent — from physics, not from a fit. Transients use one identified
first-order lag per output; the plant also enforces the ±5 %/interval ramp limit
and adds measurement noise, so an over-aggressive controller cannot cheat.

This matters beyond compliance: because the plant is nonlinear and coupled while
the control model is a Hammerstein approximation, the plant–model mismatch the
MPC fights is **real rather than assumed** — which is what makes the robustness
numbers in Part II meaningful.

**The controller is not wedded to this plant.** `src/external_simulator.py`
adapts any object exposing `step(choke) -> (Q, WHP, FLP, BHP)`, and one command
re-runs the entire study against it:

```bash
python src/adopt_simulator.py honeywell_sim:WellSim
```

It repeats the same designed step test, re-identifies the control model,
recomputes the feasible window and max safe rate, and re-runs Scenarios A/B/C —
writing to `figures_external/` and `results_external/`. No controller re-tuning
by hand: the identification and the steady-state target optimiser absorb the
difference, which is the point of separating the control model from the plant.

The adapter copes with a foreign simulator that returns a tuple, list, array or
dict (any reasonable key spelling), and enforces the ±5 %/interval ramp limit on
the plant side whether or not the provided simulator does. This is proved, not
asserted: `tests/mock_provided_simulator.py` is deliberately unlike ours —
different gains, different time constants, a different choke characteristic, a
transport delay, and dict returns — and `tests/test_external_simulator.py`
drives the entire pipeline through it, reaching R² = 0.997–0.999 on
identification and correctly capping an infeasible target.

### 2. The numeric safe operating limits

The brief names WHP, FLP and BHP as active constraints but **never gives their
ranges**, and HirePro directed this question to the organisers,
who did not supply them. Until they do, ours are
engineering placeholders, and they are labelled as such everywhere. All six live in one file — `config/operating_limits.json` — together
with their derivation and a physical rationale for each:

| | Range used | Attached failure mode |
|---|---|---|
| WHP | 200 – 320 psi | slugging / flowline entry ← → wellhead equipment rating |
| FLP | 140 – 210 psi | minimum transport pressure ← → gathering-system rating |
| BHP | 2850 – 3300 psi | maximum drawdown (sand, bubble point) ← → minimum drawdown for stable inflow |

They were chosen to bracket the region the reference dataset actually exercises
(WHP 216–270, FLP 154–189, BHP 2883–3133) with roughly one further step of choke
travel of headroom either side, so the envelope is two-sided and the supplied
data is comfortably feasible.

**To adopt the official numbers, edit those six values and re-run.** Nothing in
the controller, the identification or the studies hard-codes a limit —
`tests/test_external_simulator.py` asserts this by tightening `BHP_min` from
2850 to 2950 psi and checking the max safe rate moves 166.2 → 143.0 bbl/hr with
no code touched. An inverted or empty range is rejected rather than used
silently.

### 3. So we tested whether our limits matter at all

The organisers did not supply the official ranges, so defending six invented
numbers would always be the weakest part of this submission. Instead we tested
whether the *result* depends on them.

**200 randomised operating envelopes** were drawn (each of the six limits varied
independently over a plausible range). For every envelope we computed that
envelope's own maximum safe rate directly from the plant, then asked the
controller to chase a deliberately infeasible target and compared what it
achieved against that ceiling.

| | |
|---|---|
| Envelopes with any constraint violation | **0 of 200** |
| Production achieved, as % of each envelope's *own* max safe rate | mean **96.9 %**, min 93.6 %, max 99.3 % |
| Range of max safe rates across the envelopes | 136.5 – 181.0 bbl/hr |
| Binding constraint | WHP_min **54 %**, BHP_min **27 %**, FLP_min **20 %** |

That last row matters as much as the first: the binding constraint moves between
all three variables depending on the limits, and the steady-state target
optimiser finds whichever one is active. A controller that only ever handles the
BHP limit would fail on 73 % of these envelopes.

**Where the missing 3 % goes — and why we put it back.** A fixed 5 psi back-off
is span-blind: it is 1.1 % of BHP's range but 8.1 % of FLP's, so it looks
over-conservative whenever a narrow variable binds (94.8 % of ceiling when FLP
binds, versus 98.5 % when BHP does). We tried the obvious fix — make the back-off
proportional to each variable's span:

| Back-off | Production | Envelopes violating |
|---|---|---|
| fixed 5 psi *(submitted)* | 96.9 % of ceiling | **0 / 200** |
| span-proportional, 1.1 % | 99.3 % of ceiling | **18 / 200** |

It buys 2.4 percentage points and loses the zero-violation guarantee on 9 % of
envelopes. We put it back. The missing 3 % is not slack — it is what the
guarantee costs. The proportional variant ships as `MPCConfig.backoff_frac` for
anyone who wants to make that trade explicitly.

**What this means for the invented limits.** They are an input, not an
assumption. If a judge asks where 2850 psi came from, the answer is "we chose it,
here is the reasoning, here is the one file to change — and here are 200 other
envelopes where the controller behaves identically."

---

## Part II — Evidence

A controller that reports "zero violations" on three scenarios has not been
tested. Four questions, four studies.

### 1. Better than what?

Both baselines get the same plant, the same noise seeds and the same
±5 %/interval ramp limit. The PI is IMC-tuned in velocity form (inherently
anti-windup) — a good-faith controller, not a straw man. What it cannot have is
knowledge of the pressure constraints: a single rate loop structurally cannot
see them.

| Scenario C (target 200 bbl/hr, infeasible) | Violating intervals | Settled rate | Worst BHP excursion |
|---|---|---|---|
| PI on rate | **97 / 140 (69 %)** | 199.9 bbl/hr | 146 psi below the limit |
| Operator rule (fixed safe choke) | 0 | 156.1 bbl/hr | — |
| **MPC (this work)** | **0** | **164.9 bbl/hr** | — |

Scenarios A and B are honest about the flip side: when the target sits
comfortably inside the envelope, all three work, and the MPC's only edge is
smoother actuation (about a third of the PI's total choke travel). The
architectures separate only when a constraint actually binds — which is exactly
when it matters.

### 2. What if the model is wrong?

150 randomised controllers per scenario, with the internal model corrupted far
beyond anything a real re-identification would leave: incremental gain × U(0.7,
1.3) and τ × U(0.5, 1.5), independently per output channel.

* Scenario B: **0 / 150** violate.
* Scenario C: **7 / 150 (4.7 %)** violate, worst excursion **1.16 psi** on a
  2850 psi limit (0.04 %).

**Every failure has the same signature: τ_BHP under-estimated by more than ~31 %
(factor < 0.69).** The controller believes bottom-hole pressure settles faster
than it does, so it stops backing off while the pressure is still falling. Gain
error alone never causes a failure.

That diagnosis buys a cheap fix. Deliberately *over-estimating* the identified τ
of the slowest constrained variable by 50 % gives 0 / 60 violations for
~0.9 bbl/hr; reaching the same result by blindly widening the back-off to 12 psi
costs 3.9 bbl/hr.

### 3. Why these tuning numbers, and is the controller always feasible?

Every MPC parameter was chosen by measurement. Re-running Scenario C while
varying one knob at a time:

| Knob | Setting | Settled rate | Choke travel | Verdict |
|---|---|---|---|---|
| Control horizon | M = 1 | 165.07 | **103.8** | worse — 2.5× the actuator wear |
| | **M = 2** | 164.89 | **40.8** | **chosen** |
| | M = 3 | 164.89 | 40.8 | identical — the plan holds after move 2 |
| Move suppression | w_move = 0 | 165.04 | **160.8** | 4× the travel for 0.15 bbl/hr |
| | **w_move = 15** | 164.89 | **40.8** | **chosen** |
| | w_move = 100 | 164.89 | 35.8 | marginal gain, slower response |
| First-move grid | 11 → 81 candidates | 164.37 → 164.96 | — | 41 is the knee; finer buys nothing |

**Recursive feasibility.** The property that matters most for a constrained
controller is that it can never paint itself into a corner. Across all three
scenarios — **370 intervals** — the feasible candidate set was never empty
(minimum 153 of 451 survivors, in Scenario C parked against its limit).

The argument, not just the observation: holding the current choke (Δu = 0) is
always in the candidate set, and every plan's tail is a constant-choke hold whose
steady state the SSTO has already verified feasible over the full horizon. So a
feasible plan at one interval guarantees a feasible plan at the next. The only
runs where the set empties are the deliberate RECOVERY demonstrations, which
*start* outside the envelope.

### 4. Where does it break?

| Parameter swept | Result |
|---|---|
| Prediction horizon P | safe from 5 h to 50 h |
| Constraint back-off | **fails at 0 psi** (13 violating intervals); ≥ 1 psi is enough at nominal noise |
| Measurement noise | safe to 8× nominal; **fails at 12×** (4.8 psi excursion) |
| Ramp-rate limit | safe from 0.5 to 10 %/interval — a tighter limit is strictly safer |

### 5. What if the challenge's assumptions fail?

The brief says to assume constant reservoir properties, no changing water cut,
and healthy instrumentation. All three are false on a real well.

| Test | Result |
|---|---|
| **D1** reservoir declines 48 psi (never modelled) | choke opens 47 → 61 % to hold 130 bbl/hr; when the well genuinely cannot make 130 any more the controller switches to CONSTRAINED and settles on the true declining capacity, within 0.6 bbl/hr. 0 violations |
| **D2** water cut steps 0 → 12 % | disturbance estimator absorbs it, 120 bbl/hr recovered with zero offset. 0 violations |
| **F1** frozen BHP transmitter | detected in 5 intervals, choke frozen for 43, 0 violations. Validation disabled: 5 violating intervals |
| **F2** isolated spikes | flagged — but honestly the bias filter alone already handled these |
| **F4** IO card fails, BHP +220 and WHP +45 psi | validation disabled: over-produces to 170.6 bbl/hr and drives BHP **18.9 psi** below its limit for 64 intervals. Validation on: **0 violations** |

**An unplanned finding.** Corrupting BHP alone did far less damage than we
expected. At the maximum safe operating point BHP has 0.3 psi of margin but WHP
has only 5.4 psi — so the WHP constraint, computed from a *healthy* transmitter,
stops the controller almost immediately. That **constraint redundancy** is a real
safety property of a multi-constraint MPC, and it means a fault study that
injects only single-tag faults will overstate how safe the system is. The
dangerous fault is the correlated one, which is why F4 exists.

---

## The monitored variables the brief asks you to recognise

The problem statement lists Wellhead Temperature and Annulus Pressure as
*"Additional Industrial Variables (Informational)"* that are **not** active
constraints here but *"should be recognized as part of a complete production
operating envelope."* The simulator computes both, from physics:

* **WHT** — a Ramey-type heat-loss argument. Fluid leaves the reservoir at
  200 °F and loses heat on the way up, so the *faster* it flows the *hotter* it
  arrives: `WHT = T_amb + (T_res − T_amb)·exp(−a/Q)`.
* **Annulus pressure** — annular thermal pressure buildup. In a packer-completed
  well the A-annulus is sealed, so heating the tubing heats trapped annular
  fluid that cannot expand, and the pressure rises with WHT.

**Why this is worth more than a footnote.** Across the feasible envelope:

| | as the choke opens |
|---|---|
| WHP | 282.5 → 205.4 psi *(falls)* |
| BHP | 3299.9 → 2850.3 psi *(falls)* |
| **WHT** | 119.4 → **159.1 °F** *(rises)* |
| **Annulus pressure** | 177.8 → **316.8 psi** *(rises)* |

The monitored variables move in the **opposite direction** to the constrained
ones. Everything the controller does to gain production relieves the three
pressure constraints' upper ends while steadily *increasing* a well-integrity
risk that nothing in the stated envelope is watching.

Quantified: if annulus pressure had a limit anywhere below ~318 psi it would
become the binding constraint **before** BHP.

| Hypothetical AP limit | Max safe rate | Binding |
|---|---|---|
| 290 psi | 133.5 bbl/hr | **AP** |
| 300 psi | 144.5 bbl/hr | **AP** |
| 310 psi | 156.8 bbl/hr | **AP** |
| 320 psi | 166.2 bbl/hr | BHP |

At a 300 psi annulus limit the well's safe capacity drops from 166 to
144 bbl/hr — a 13 % difference driven entirely by a variable the brief classes
as informational. That is precisely why it says they belong in a complete
operating envelope, and promoting them to hard constraints is a one-line change
once an asset defines the limits.

See `figures/monitored_variables.png`.

---

## Lessons learned

1. **Penalise every move in the plan, not just the first.** Our first cost
   function suppressed only `Δu₁`. The optimiser learned to set `Δu₁ = 0` and let
   `Δu₂` do the work at zero cost — and since a receding-horizon controller only
   ever applies the first move, the controller stalled with a 3–7 bbl/hr
   steady-state offset. Penalising all moves removed it completely. This was the
   single biggest bug in the project.
2. **We misdiagnosed our own controller, and the sweep caught it.** We had
   recorded "the prediction horizon must cover the slowest constrained variable"
   as a lesson, because violations vanished when we lengthened P from 20 h to
   40 h. Sweeping P properly from 5 h to 50 h shows that is **not** what fixed
   it: with the SSTO guaranteeing a feasible endpoint, horizon length only
   tightens the worst excursion from 0.77 to 0.23 psi and never causes a
   violation on its own. The constraint back-off, which we changed at the same
   time, did the real work. The honest lesson is about method — **we changed two
   things at once and credited the wrong one.**
3. **A small constraint back-off buys a large safety margin, cheaply.** At 0 psi:
   13 violating intervals. At 1 psi: zero. We run 5 psi, which costs 1.2 bbl/hr
   (0.7 % of production) and holds up to 8× nominal measurement noise.
4. **Diagnosing the mechanism beats blanket conservatism.** Every mismatch
   failure had one cause — under-estimated τ_BHP. A targeted fix costs
   ~0.9 bbl/hr; blind conservatism buys the same result for 3.9 bbl/hr.
5. **Separating the steady-state and dynamic problems is what makes infeasibility
   safe.** Handling "target too high" inside a single MPC cost function causes
   wind-up. A dedicated steady-state optimiser turns it into a clean, explainable
   answer: here is the most you can safely have, and here is the limit stopping you.
6. **Constraint redundancy is real, and it changes what a fault test means.** See
   the F4 finding above: a fault study injecting only single-tag faults will
   overstate how safe the system is.

## Assumptions

* Single naturally flowing well, single production choke; no gas lift, no ESP.
* Constant reservoir properties, constant GOR and water cut.
* No facility-network interaction — the manifold is a calibrated back-pressure boundary.
* Choke opening `u` [%] is the only manipulated variable; control interval `Ts = 1 h`.
* WHT and annulus pressure are monitored but are not active constraints in this
  challenge — see below for why that matters more than it sounds.
* **The simulator extrapolates at both ends of the envelope.** It is calibrated on
  the supplied data (choke 30–65 %, rate 90–159 bbl/hr) but used over 15.5–70.65 %
  and 62–166 bbl/hr. The maximum safe rate sits only 5.65 percentage points beyond
  the calibrated range; the low end extrapolates further and affects only start-up.
  Stated in full in `ASSUMPTIONS.md`.
* **The safe operating limits are our own** — see "[The simulator, and the limits](#the-simulator-and-the-limits)" above. Every result in this report is conditional on them, and
  all six are editable in `config/operating_limits.json`.
