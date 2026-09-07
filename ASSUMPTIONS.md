# Assumption register

Every assumption behind the simulator, the control model and the controller,
with the reason it was made, what it buys, and where it breaks. Assumptions
marked **[BRIEF]** are imposed by the problem statement; the rest are ours.

---

### A1 — Single naturally flowing well, one production choke **[BRIEF]**

**Reason** The problem statement fixes this: *"Single well only, naturally flowing
well, single production choke, no gas lift optimization, no ESP optimization."*

**Impact** One manipulated variable, so the controller is single-input. The
steady-state target optimiser reduces to a 1-D scan over choke opening rather
than a general LP.

**Limitation** No well-to-well interaction and no artificial-lift trade-off. A
real pad shares a flowline and a separator, so the max-rate calculation would
become a shared-constraint problem across wells.

---

### A2 — Linear inflow performance: `Q = PI·(Pr − BHP)`

**Reason** Not assumed — **measured**. Plotting BHP against Q for the five steady
states in the supplied reference dataset gives a straight line; the fit yields
`PI = 0.2313 bbl/hr/psi` and `Pr = 3569 psi`, and predicts the held-out points to
within a few psi.

**Impact** Drawdown is exactly `Q/PI`, so `BHP = Pr − Q/PI` and the maximum rate
follows in one line: `Q_max = (Pr − BHP_min)·PI = 166.3 bbl/hr`. This is what
makes the production ceiling explainable rather than a number from a solver.

**Limitation** A linear IPR holds only above the bubble point. Below it, free gas
in the reservoir curves the relationship (Vogel), and the ceiling would be
optimistic. Nothing in the supplied data spans that region, so we cannot check it.

---

### A3 — Tubing pressure loss is quadratic in rate and *falls* as rate rises

**Reason** Fitted from the same steady states. Over this window the gas-lightening
of the fluid column dominates wall friction, so a faster well carries a lighter
column: `ΔP_tub = 3297.5 − 4.847·Q + 0.005535·Q²`.

**Impact** Reproduces the observed WHP behaviour without a multiphase flow
correlation. It is the left-hand (unstable-looking but here monotonic) branch of
a normal VLP curve.

**Limitation** Calibrated on the rate range the reference data actually covers,
**90–159 bbl/hr**, and used over the wider feasible envelope of 62–166 — see the
extrapolation note at the end. Extrapolated far enough the quadratic turns and
friction would dominate; the fitted minimum sits at 438 bbl/hr, well outside
anything we simulate.

---

### A4 — The choke behaves as a square-root orifice: `Q = CdA(u)·√(WHP − FLP)`

**Reason** Standard subcritical choke behaviour, and it is what the data shows:
computing `Q/√ΔP` at each steady state gives a smooth, monotone function of
opening, fitted as a quadratic `CdA(u)`.

**Impact** Gives the simulator a physically correct coupling between rate and the
pressure drop across the valve, rather than a lookup table.

**Limitation** Assumes subcritical flow throughout. If the choke went critical
(sonic), rate would stop responding to downstream pressure and the model would
over-predict the effect of FLP. More importantly, `CdA(u)` was fitted from a
reference dataset spanning only **30–65 % opening** — see the extrapolation note
at the end of this document.

---

### A5 — Flowline pressure is a calibrated function of choke opening

**Reason** The brief rules out facility-network modelling (*"no facility network
interactions"*), and the reference data shows FLP tracking the choke closely. It
is fitted as `FLP = 212.5 − 0.692·u − 0.00299·u²`.

**Impact** Closes the four-equation system without needing a separator or
gathering-network model.

**Limitation** This is the **weakest physical link in the simulator** and we say
so. A real flowline pressure rises with throughput; here it falls, because it is
standing in for the whole downstream system's response to the choke. It is an
empirical boundary condition, not a first-principles one.

---

### A6 — One first-order lag per output, with its own time constant

**Reason** Chosen after the step test, not before. Each output settles on a clean
exponential with a different speed: Q 4.9 h, WHP 8.6 h, FLP 5.8 h, BHP 12.4 h.
A single shared time constant would misrepresent BHP by more than a factor of two.

**Impact** Five parameters per channel, so the MPC can roll a 40-step forecast in
about a millisecond. It also sets the prediction horizon: P must cover the
slowest constrained variable.

**Limitation** First order cannot represent inverse response, resonance or
slugging. If the well slugged, the dynamics would be oscillatory and this model
would not see it coming.

---

### A7 — Static nonlinearity + linear dynamics (Hammerstein), not a fully linear model

**Reason** The measured gains change materially across the operating range — the
oil-rate gain falls from **2.33 to 1.40 bbl/hr per %** between 20 % and 70 %
opening, i.e. by 40 % across the range the step test actually covers. A single
linearised gain would be wrong at one end of the envelope. (Gains beyond 70 % are
extrapolation and are not quoted.)

**Impact** Keeps the dynamics linear (so prediction stays a one-line recursion)
while the steady-state map stays accurate everywhere. Cross-validation on the
independent reference dataset gives R² = 0.983–0.993.

**Limitation** Assumes the nonlinearity is *static* — that the shape of the
response is the same everywhere and only its size changes. True enough here;
not true of a well that changes flow regime.

---

### A8 — Dead time is negligible at a one-hour control interval

**Reason** Not assumed — **tested**. Transport delay θ was left as a free
parameter in the identification, bounded to [0, 5] h. It converged to
**0.00 h for Q, WHP and BHP**, and ≈ 0.24 h for FLP on the reference data.

**Impact** Confirms that a one-hour interval is comfortably slower than the
well's transport delays, so no Smith predictor or delay compensation is needed
and the prediction is a plain lag.

**Limitation** This is a *result of the sampling choice*, not a property of the
well. At a one-minute interval the flowline transport delay would be several
samples and would have to be modelled explicitly.

---

### A9 — Constant reservoir properties, GOR and water cut **[BRIEF]**

**Reason** Imposed by the problem statement.

**Impact** The identified model stays valid for the whole run; no online
re-identification is needed.

**Limitation** False on any real well. We therefore **broke this assumption on
purpose** and tested it: 48 psi of unmodelled depletion and a 12 % water-cut step
(`figures/stress_tests.png`). The controller tracked the declining capacity and
reported when the target became unreachable, with zero violations.

---

### A10 — The safe operating limits (our values)

**Reason** The brief names WHP, FLP and BHP as active constraints but **never
gives numeric ranges**, and the organisers did not supply them. Ours bracket the
region the reference dataset exercises, with a physical failure mode attached to
each limit. All six live in `config/operating_limits.json`.

**Impact** Sets the feasible choke window (15.5–70.65 %) and the maximum safe rate
(166.2 bbl/hr).

**Limitation** They are engineering placeholders, and every headline number is
conditional on them. This is why we ran the limit-sensitivity study: across 200
randomised envelopes the controller holds zero violations and reaches 96.9 % of
each envelope's own ceiling, with the binding constraint moving between WHP, BHP
and FLP. The limits are an input, not an assumption baked into the design.

---

### A11 — Measurement noise is zero-mean and Gaussian

**Reason** A reasonable default for field instrumentation, with σ set to roughly
**1 % of each variable's operating range** (measured: 0.87 % on Q, 0.93 % on WHP,
1.28 % on FLP, 0.98 % on BHP).

**Impact** Justifies the first-order disturbance filter and the constraint
back-off, and lets us report results over repeated noise realisations rather than
a single lucky run.

**Limitation** Real instruments drift, quantise and fail. We tested those cases
separately — frozen transmitter, spikes, and a two-tag IO-card bias — rather than
pretending the Gaussian assumption covers them.

---

### A12 — The actuator tracks the command within the ramp limit

**Reason** The brief specifies the ±5 %/interval limit as a controller constraint;
the simulator enforces it plant-side as well, so an over-aggressive controller
cannot cheat.

**Impact** The commanded and actual positions agree, so the ramp-limit test is
meaningful.

**Limitation** A real valve sticks, has backlash and has a finite stroke time.
Because the controller **reads the measured position back** every interval rather
than trusting its own command history, a valve that fails to track is detected
rather than silently accumulated — but the controller cannot *fix* a stuck valve,
only stop moving and flag it.

---

### A13 — WHT and annulus pressure are monitored, not constrained **[BRIEF]**

**Reason** The brief states both are *"not active constraints in this challenge
but should be recognized as part of a complete production operating envelope."*

**Impact** They are computed and logged but never restrict the controller.

**Limitation** Worth knowing what that costs: both move in the **opposite**
direction to the three constrained pressures as the choke opens. If annulus
pressure had a limit anywhere below ≈ 318 psi it would bind *before* BHP — at
300 psi the safe capacity drops from 166 to 144 bbl/hr. Promoting either to a
hard constraint is a one-line change, since both are already computed.


---

## A note on extrapolation — where the simulator is working outside its data

This is the most important caveat in the document, so it is stated separately
rather than buried in one assumption.

The simulator's coefficients were calibrated against the supplied reference
dataset, which spans **choke 30–65 %** and **oil rate 90–159 bbl/hr**. The
feasible operating envelope is wider than that:

| | calibrated on | used over | extrapolation |
|---|---|---|---|
| Choke opening | 30 – 65 % | 15.5 – 70.65 % | −14.5 pp low, +5.65 pp high |
| Oil rate | 90 – 159 bbl/hr | 62 – 166 bbl/hr | −28 low, +7 high |

**Why the upper end is defensible.** The maximum safe rate — the headline result
— sits at 70.65 % choke, only 5.65 percentage points beyond the highest
calibrated point, and the extrapolation is along a smooth monotone fit with no
turning point nearby. The identified control model independently reproduces the
same behaviour there, and the cross-validation on the reference data holds to
R² = 0.983–0.993.

**Why the lower end is weaker.** Below 30 % choke the simulator is extrapolating
substantially. That region matters only for Scenario A's start-up (18 %) and the
RECOVERY demonstration (10 %), neither of which drives a headline number. A real
commissioning step test would extend down to 10 % before trusting the model
there.

**What we would do about it with more data.** Extend the step test below 20 % and
above 70 %, and re-fit. The identification pipeline is a single command
(`python src/scenarios.py`), so this costs nothing but data.
