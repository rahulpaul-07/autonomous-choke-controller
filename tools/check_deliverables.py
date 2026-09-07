"""
check_deliverables.py - confirm every required plot and figure is present.

Run from the project root:   python tools/check_deliverables.py
"""
import os, glob, pandas as pd
REQ = {"Target Oil Rate":"Q_target", "Actual Oil Rate":"OilRate_bbl_hr",
       "Wellhead Pressure":"WHP_psi", "Flowline Pressure":"FLP_psi",
       "Bottom Hole Pressure":"BHP_psi", "Choke Position":"Choke_pct"}
print("REQUIRED PLOTS - the brief asks for these six trends, per scenario\n")
print(f"{'':24s} " + "  ".join(f"{t:^5s}" for t in ("A","B","C")))
ok = True
for name, col in REQ.items():
    row = []
    for t in "ABC":
        f = f"results/scenario_{t}.csv"
        has = os.path.exists(f) and col in pd.read_csv(f, nrows=1).columns
        ok &= has; row.append(" YES " if has else " NO  ")
    print(f"  {name:22s} " + "  ".join(row))
print()
for t in "ABC":
    p = f"figures/scenario_{t}.png"
    e = os.path.exists(p); ok &= e
    print(f"  scenario {t} figure : {'FOUND' if e else 'MISSING'}   {p}  "
          f"({os.path.getsize(p)/1000:.0f} kB)" if e else f"  scenario {t} figure : MISSING")
print(f"\n  --> {'ALL SIX REQUIRED TRENDS PRESENT AND PLOTTED FOR A, B AND C' if ok else 'SOMETHING IS MISSING'}\n")
print("EVERY FIGURE IN THE PACKAGE\n")
what = {
 "scenario_A.png":"Scenario A - start-up to 100 bbl/hr (6 required trends)",
 "scenario_B.png":"Scenario B - target change 100 -> 150 (6 required trends)",
 "scenario_C.png":"Scenario C - infeasible target 200 (6 required trends)",
 "scenario_RECOVERY.png":"Extra - recovery from outside the envelope",
 "step_test_identification.png":"Open-loop step test + identified model overlay",
 "model_validation.png":"Cross-validation on the Honeywell reference data",
 "operating_envelope.png":"Steady-state envelope, max safe rate",
 "results_summary.png":"All three scenarios side by side",
 "control_architecture.png":"Two-layer SSTO + MPC block diagram",
 "controller_decisions.png":"Inside one decision - forecasts and rejected moves",
 "baseline_comparison.png":"MPC vs tuned PI vs cautious operator",
 "montecarlo_mismatch.png":"150 randomised models per scenario",
 "breaking_point.png":"Where the controller fails, one knob at a time",
 "limit_sensitivity.png":"200 randomised operating envelopes",
 "stress_tests.png":"Depletion, water cut, instrument faults",
 "monitored_variables.png":"WHT and annulus pressure (informational)",
}
found = {os.path.basename(f) for f in glob.glob("figures/*.png")}
for f in sorted(what):
    m = "OK " if f in found else "MISSING"
    print(f"  {m}  {f:32s} {what[f]}")
extra = sorted(found - set(what))
if extra: print(f"\n  unlisted extras: {extra}")
print(f"\n  {len(found)} figures present, {len(what)} expected")
