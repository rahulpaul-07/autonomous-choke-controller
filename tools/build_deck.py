"""Builds the submission deck on the supplied SIH/Honeywell template."""
import copy, json, os
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root (this script lives in tools/)
TPL = "/sessions/serene-loving-davinci/mnt/uploads/IDEA_Presentation_Format.pptx"
FIG = os.path.join(ROOT, "figures")
OUT = os.path.join(ROOT, "Honeywell_Autonomous_Choke_Controller_Rahul_Paul.pptx")

NAVY, BLUE, ORANGE, GREEN, RED, GREY, DARK = (
    RGBColor(0x0B, 0x2E, 0x4A), RGBColor(0x0B, 0x6F, 0xA4), RGBColor(0xE8, 0x82, 0x0C),
    RGBColor(0x2E, 0x7D, 0x32), RGBColor(0xC6, 0x28, 0x28), RGBColor(0x55, 0x55, 0x55),
    RGBColor(0x1A, 0x1A, 0x1A))

prs = Presentation(TPL)
S = prs.slides

# ------------------------------------------------------------------ helpers
def dup(idx):
    src = S[idx]
    dst = S.add_slide(src.slide_layout)
    for shp in list(dst.shapes):
        shp._element.getparent().remove(shp._element)
    for shp in src.shapes:
        dst.shapes._spTree.append(copy.deepcopy(shp._element))
    return dst

def reorder(order):
    lst = prs.slides._sldIdLst
    ids = list(lst)
    for i in ids:
        lst.remove(i)
    for k in order:
        lst.append(ids[k])

def drop_unreferenced(rIds):
    """Release slide parts that are no longer in the slide-id list."""
    for rId in rIds:
        try:
            prs.part.drop_rel(rId)
        except KeyError:
            pass

def find(slide, name):
    for sh in slide.shapes:
        if sh.name == name:
            return sh
    return None

def kill(slide, name):
    sh = find(slide, name)
    if sh is not None:
        sh._element.getparent().remove(sh._element)

def set_title(slide, text, size=30):
    sh = find(slide, "Title 1") or find(slide, "Subtitle 3")
    tf = sh.text_frame
    tf.clear()
    p = tf.paragraphs[0]
    r = p.add_run(); r.text = text
    r.font.size = Pt(size); r.font.bold = True
    r.font.name = "Times New Roman"; r.font.color.rgb = NAVY
    p.alignment = PP_ALIGN.LEFT
    return sh

def tb(slide, l, t, w, h, fill=None, line=None, radius=False):
    from pptx.enum.shapes import MSO_SHAPE
    if fill is not None or line is not None:
        shp = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE,
            Inches(l), Inches(t), Inches(w), Inches(h))
        if fill is not None:
            shp.fill.solid(); shp.fill.fore_color.rgb = fill
        else:
            shp.fill.background()
        if line is not None:
            shp.line.color.rgb = line; shp.line.width = Pt(1.25)
        else:
            shp.line.fill.background()
        shp.shadow.inherit = False
    box = slide.shapes.add_textbox(Inches(l), Inches(t), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = Inches(0.11); tf.margin_right = Inches(0.11)
    tf.margin_top = Inches(0.06); tf.margin_bottom = Inches(0.04)
    return tf

def para(tf, text, size=12, bold=False, color=DARK, bullet=None, space=4,
         first=False, italic=False, indent=0, font="Arial"):
    p = tf.paragraphs[0] if first else tf.add_paragraph()
    if bullet:
        r0 = p.add_run(); r0.text = bullet + "  "
        r0.font.size = Pt(size); r0.font.bold = True
        r0.font.color.rgb = color; r0.font.name = font
    r = p.add_run(); r.text = text
    r.font.size = Pt(size); r.font.bold = bold; r.font.italic = italic
    r.font.color.rgb = color; r.font.name = font
    p.space_after = Pt(space)
    if indent:
        p.level = indent
    return p

def head(tf, text, size=13, color=BLUE, first=False, space=5):
    return para(tf, text, size=size, bold=True, color=color, first=first, space=space)

def pic(slide, path, l, t, w=None, h=None):
    kw = {}
    if w: kw["width"] = Inches(w)
    if h: kw["height"] = Inches(h)
    return slide.shapes.add_picture(os.path.join(FIG, path), Inches(l), Inches(t), **kw)

def band(slide, l, t, w, h, text, fill, size=11.5, color=RGBColor(0xFF,0xFF,0xFF), bold=True):
    tf = tb(slide, l, t, w, h, fill=fill)
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    r = p.add_run(); r.text = text
    r.font.size = Pt(size); r.font.bold = bold; r.font.color.rgb = color; r.font.name = "Arial"
    p.alignment = PP_ALIGN.CENTER
    return tf

with open(os.path.join(ROOT, "results", "summary.json")) as f:
    SUM = json.load(f)
LIM, SC = SUM["limits"], SUM["scenarios"]

# ---------------------------------------------------------------- structure
# template: 0 instructions | 1 title | 2 idea | 3 technical | 4 feasibility
#           5 artifacts | 6 references
s_model   = dup(2)   # 7  -> PROCESS UNDERSTANDING & MODEL
s_results = dup(2)   # 8  -> RESULTS: scenarios
s_base    = dup(2)   # 9  -> RESULTS: baselines
s_robust  = dup(2)   # 10 -> RESULTS: robustness & failure boundary
s_stress  = dup(2)   # 11 -> RESULTS: broken assumptions
s_limits  = dup(2)   # 12 -> RESULTS: do our assumed limits matter?
s_inside  = dup(2)   # 13 -> CONTROL STRATEGY: inside one decision
_instructions_rId = list(prs.slides._sldIdLst)[0].rId
reorder([1, 2, 7, 3, 13, 8, 9, 10, 12, 11, 4, 5, 6])   # instruction slide (0) dropped
drop_unreferenced([_instructions_rId])

(s_title, s_idea, s_model, s_tech, s_inside, s_results, s_base, s_robust,
 s_limits, s_stress, s_feas, s_art, s_ref) = list(S)

# =========================================================== 1. TITLE
kill(s_title, "TextBox 6")
sub = find(s_title, "Subtitle 3")
sub.left, sub.top = Inches(0.45), Inches(0.22)
sub.width, sub.height = Inches(12.45), Inches(1.42)
tf = sub.text_frame; tf.clear()
tf.word_wrap = True
p = tf.paragraphs[0]
r = p.add_run(); r.text = "AUTONOMOUS PRODUCTION CHOKE CONTROLLER"
r.font.size = Pt(30); r.font.bold = True; r.font.name = "Times New Roman"; r.font.color.rgb = NAVY
p.alignment = PP_ALIGN.CENTER
p2 = tf.add_paragraph()
r2 = p2.add_run(); r2.text = "for a Single Naturally Flowing Oil Well"
r2.font.size = Pt(19); r2.font.bold = False; r2.font.name = "Times New Roman"; r2.font.color.rgb = BLUE
p2.alignment = PP_ALIGN.CENTER

tf = tb(s_title, 0.45, 1.92, 6.55, 4.45, fill=RGBColor(0xF4,0xF8,0xFB), line=BLUE)
for i, (k, v) in enumerate([
    ("Problem Statement ID", "Honeywell Campus Connect – Round 2"),
    ("Problem Statement Title", "Autonomous Production Choke Controller for a\nSingle Naturally Flowing Oil Well"),
    ("Theme", "Advanced Process Control / Industrial Automation\n(Upstream Oil & Gas Production Optimisation)"),
    ("PS Category", "Software"),
    ("Student Name", "Rahul Paul"),
    ("Student ID", "____________________"),
]):
    para(tf, k, size=11, bold=True, color=BLUE, first=(i == 0), space=1)
    para(tf, v, size=13.5, bold=True, color=DARK, space=11)

tf = tb(s_title, 7.25, 1.92, 5.65, 4.45, fill=RGBColor(0x0B,0x2E,0x4A))
para(tf, "HEADLINE RESULTS", size=13, bold=True, color=RGBColor(0xFF,0xFF,0xFF), first=True, space=9)
W = RGBColor(0xFF,0xFF,0xFF); Y = RGBColor(0xFF,0xC1,0x07)
for big, small in [
    ("0", "constraint violations in 30 nominal runs\nand in 150 randomised-model runs (Scenario B)"),
    ("+5.6 % oil", "vs a cautious operator at the same\nzero violations; a rate PI breaches on 69 % of intervals"),
    ("R\u00b2 = 0.98 – 0.99", "model accuracy on the independent\nHoneywell reference dataset"),
    ("survives", "48 psi of unmodelled depletion, a 12 % water-cut\nstep and a two-tag IO-card failure"),
]:
    para(tf, big, size=16, bold=True, color=Y, space=1)
    para(tf, small, size=10, color=W, space=11)

# =========================================================== 2. IDEA
set_title(s_idea, "PROPOSED SOLUTION")
kill(s_idea, "TextBox 8")

tf = tb(s_idea, 0.38, 1.18, 6.35, 2.55, fill=RGBColor(0xF4,0xF8,0xFB), line=BLUE)
head(tf, "THE PROBLEM", first=True)
para(tf, "Operators set production chokes manually and conservatively. Wells run below "
         "capacity, or drift outside safe pressure limits. Manual optimisation does not "
         "scale to hundreds of wells.", size=11.5, space=7)
head(tf, "THE SOLUTION")
para(tf, "A closed-loop controller that recomputes the choke position every hour to track "
         "the operator's oil-rate target while guaranteeing that wellhead, flowline and "
         "bottom-hole pressures never leave their safe envelope — and that respects the "
         "±5 %/interval choke ramp-rate limit at all times.", size=11.5, space=0)

tf = tb(s_idea, 0.38, 3.90, 6.35, 2.85, fill=RGBColor(0xFF,0xFB,0xF3), line=ORANGE)
head(tf, "HOW IT ADDRESSES THE PROBLEM", color=ORANGE, first=True)
for b in [
    "Removes the human from the hourly choke decision — consistent, repeatable, scalable to a whole field.",
    "Safety is structural, not advisory: a move that is predicted to break a limit is rejected before it is made.",
    "When the requested target is impossible, the controller says so, reports which limit is binding, and produces the most it safely can.",
    "Recovers automatically if the well starts outside its envelope after a shut-in or upset — demonstrated separately.",
]:
    para(tf, b, size=11, bullet="▸", color=DARK, space=6)

tf = tb(s_idea, 6.95, 1.18, 5.98, 5.57, fill=RGBColor(0xF6,0xF2,0xFA), line=RGBColor(0x6A,0x1B,0x9A))
head(tf, "INNOVATION AND UNIQUENESS", color=RGBColor(0x6A,0x1B,0x9A), first=True)
for t, d in [
    ("Two-layer APC architecture",
     "A Steady-State Target Optimiser decides where the well should end up; a dynamic MPC "
     "decides how to get there. This is how industrial APC (Honeywell Profit Controller / "
     "RMPCT) is actually built — and it is what makes an infeasible target degrade "
     "gracefully instead of saturating the choke."),
    ("Physics-structured simulator",
     "Steady state is solved from IPR + tubing lift + choke orifice equations simultaneously, "
     "not curve-fitted. Parameters are calibrated to the reference dataset, so plant–model "
     "mismatch is realistic."),
    ("Honest model validation",
     "The control model is identified ONLY from our own step tests, then free-run against "
     "the independent Honeywell reference data: R² = 0.98–0.99 on data it has never seen."),
    ("Explicit candidate rejection",
     "451 candidate move-plans are rolled out over a 40 h horizon every interval; any plan "
     "predicted to leave the envelope is discarded. Constraints are hard, not penalties."),
    ("Offset-free by construction",
     "A DMC-style output-disturbance estimator feeds both layers, so model mismatch and "
     "slow drift produce no steady-state error."),
]:
    para(tf, t, size=11.5, bold=True, color=NAVY, space=1)
    para(tf, d, size=10, color=GREY, space=8)

# =========================================================== 3. MODEL
set_title(s_model, "PROCESS UNDERSTANDING & MODEL")
kill(s_model, "TextBox 8")

tf = tb(s_model, 0.38, 1.10, 6.15, 1.66, fill=RGBColor(0xF4,0xF8,0xFB), line=BLUE)
head(tf, "STEP-TEST DESIGN AND RESULTS", first=True)
para(tf, "8-level open-loop step test on our simulator, 20 → 70 % choke, both directions, "
         "each level held 40–55 h (3–4 τ) so the slowest variable settles.", size=10.5, space=5)
para(tf, "Opening the choke  →  Q ↑   and   WHP ↓, FLP ↓, BHP ↓ .  The oil-rate gain falls "
         "2.33 → 1.40 bbl/hr per % across the tested range, and every output has its own "
         "time constant — a single linear model would not do.", size=9.6, space=2, bold=True, color=NAVY)
para(tf, "Dead time was FITTED, not assumed: θ was left free in [0, 5] h and converged to 0.00 h. "
         "A 1 h interval is far slower than this well's transport delays, so no delay compensation is needed.",
     size=9.0, space=0, color=GREY)

rows = [("Output", "Static gain at 50 %", "τ  [h]", "R²  (own data)", "R²  (reference)"),
        ("Oil rate Q", "+1.77 bbl/hr per %", "4.9", "0.998", "0.989"),
        ("WHP", "−1.60 psi per %", "8.6", "0.998", "0.983"),
        ("FLP", "−0.97 psi per %", "5.8", "0.998", "0.993"),
        ("BHP", "−7.66 psi per %", "12.4", "0.999", "0.989")]
tblshp = s_model.shapes.add_table(5, 5, Inches(0.38), Inches(2.84),
                                  Inches(6.15), Inches(1.48)).table
for c, wdt in enumerate([1.42, 1.72, 0.72, 1.14, 1.15]):
    tblshp.columns[c].width = Inches(wdt)
for i, row in enumerate(rows):
    for j, val in enumerate(row):
        cell = tblshp.cell(i, j)
        cell.text = val
        cell.margin_left = Inches(0.05); cell.margin_right = Inches(0.04)
        cell.margin_top = Inches(0.02); cell.margin_bottom = Inches(0.02)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        pr = cell.text_frame.paragraphs[0]
        pr.alignment = PP_ALIGN.CENTER if j else PP_ALIGN.LEFT
        for r in pr.runs:
            r.font.size = Pt(9.5); r.font.name = "Arial"
            r.font.bold = (i == 0)
            r.font.color.rgb = RGBColor(0xFF,0xFF,0xFF) if i == 0 else DARK
        cell.fill.solid()
        cell.fill.fore_color.rgb = BLUE if i == 0 else (
            RGBColor(0xF6,0xF9,0xFC) if i % 2 else RGBColor(0xFF,0xFF,0xFF))

tf = tb(s_model, 0.38, 4.42, 6.15, 1.72, fill=RGBColor(0xF6,0xF2,0xFA), line=RGBColor(0x6A,0x1B,0x9A))
head(tf, "DYNAMIC MODEL DEVELOPED  —  Hammerstein", color=RGBColor(0x6A,0x1B,0x9A), first=True)
para(tf, "Static nonlinearity:      y_ss(u) = a₀ + a₁·u + a₂·u²", size=12, bold=True, color=NAVY, space=2)
para(tf, "Linear dynamics:          τ_y · dy/dt = y_ss(u(t−θ)) − y", size=12, bold=True, color=NAVY, space=5)
para(tf, "Fitted per output by nonlinear least squares on the FREE-RUN simulation error "
         "(not one-step-ahead) — the honest test for a model an MPC uses for 40-step prediction.",
     size=9.4, color=GREY, space=0)

tf = tb(s_model, 6.72, 1.08, 6.22, 1.84, fill=RGBColor(0xFF,0xFB,0xF3), line=ORANGE)
head(tf, "MODEL ASSUMPTIONS", color=ORANGE, first=True)
for b in ["Single naturally flowing well, single production choke; no gas lift, no ESP.",
          "Constant reservoir properties, constant GOR and water cut.",
          "No facility-network interaction — manifold is a calibrated back-pressure boundary.",
          "Choke opening u [%] is the only manipulated variable;  Ts = 1 h.",
          "WHT and annulus pressure are MONITORED, not constrained — and they move the OPPOSITE way to WHP/FLP/BHP as the choke opens.",
          "If annulus pressure had a limit below ~318 psi it would bind before BHP: at 300 psi the max safe rate falls 166 → 144 bbl/hr.",
          "EXTRAPOLATION: reference data spans choke 30–65 %, the envelope runs 15.5–70.65 %. The max safe rate sits only 5.65 pp beyond calibration."]:
    para(tf, b, size=8.5, bullet="▸", color=DARK, space=1)

pic(s_model, "model_validation.png", 6.72, 3.00, w=6.22)
band(s_model, 6.72, 6.42, 6.22, 0.34,
     "Identified on our own step tests · free-run against the Honeywell reference data it has never seen",
     GREEN, size=9.5)

# --- the two gaps in the problem statement, stated plainly -------------------
tf = tb(s_model, 0.38, 6.22, 6.15, 1.16, fill=RGBColor(0xF1,0xF8,0xF1), line=GREEN)
para(tf, "THE SIMULATOR IS OUR OWN — AND THAT WAS THE ASSIGNMENT", size=9.3,
     bold=True, color=GREEN, first=True, space=2)
para(tf, "HirePro confirmed in writing that no simulator would be provided, that any "
         "open-source equivalent may be used, and that the line in the problem statement "
         "should be ignored. Ours is built from first principles and calibrated to the "
         "supplied reference data — full source in src/simulator.py.",
     size=8.0, color=DARK, space=2)
para(tf, "The safe operating limits were never specified either. Ours are documented "
         "placeholders, all six in config/operating_limits.json — a six-number edit, no code changes.",
     size=8.0, color=DARK, space=0)

# =========================================================== 4. TECHNICAL APPROACH
set_title(s_tech, "TECHNICAL APPROACH  —  CONTROL STRATEGY")
kill(s_tech, "TextBox 8")
pic(s_tech, "control_architecture.png", 1.12, 0.98, w=11.10)

cols = [
    (0.40, "PREDICTION METHODOLOGY", BLUE, RGBColor(0xF4,0xF8,0xFB), [
        "Identified Hammerstein model, not the simulator physics — the controller never sees the plant equations.",
        "Reads back the MEASURED choke position each interval, exactly as the brief specifies, so a sticking valve cannot desynchronise it.",
        "Prediction horizon P = 40 h ≈ 3 × τ_BHP, so the slowest constrained variable is fully seen to settle.",
        "Output-disturbance estimate added to every prediction → offset-free.",
    ]),
    (4.63, "CHOKE MOVE SELECTION LOGIC", ORANGE, RGBColor(0xFF,0xFB,0xF3), [
        "41 × 11 = 451 candidate two-move plans, each inside the ±5 %/interval ramp limit.",
        "All 451 rolled out over the full horizon in one vectorised NumPy pass (~1 ms).",
        "Cost = Σ(Q−Q_sp)² + w·Σ Δu² — move suppression on EVERY move, then apply the first move only.",
        "Tuning was MEASURED, not chosen: M=1 costs 2.5× the choke travel, M=3 adds nothing, w_move=0 costs 4× the travel.",
    ]),
    (8.86, "CONSTRAINT HANDLING", GREEN, RGBColor(0xF1,0xF8,0xF1), [
        "Hard rejection: any plan predicted to leave the WHP / FLP / BHP envelope is discarded outright.",
        "5 psi back-off inside each limit absorbs noise and mismatch — costs 0.4 % of production.",
        "SSTO clips an infeasible target to the max safe rate and names the binding constraint.",
        "RECOVERY mode: if nothing is feasible, take the move that minimises violation.",
    ]),
]
for l, title, c, fillc, bullets in cols:
    tf = tb(s_tech, l, 5.24, 4.07, 1.72, fill=fillc, line=c)
    head(tf, title, color=c, size=11, first=True, space=4)
    for b in bullets:
        para(tf, b, size=9.0, bullet="▸", color=DARK, space=3)

tf = tb(s_tech, 0.40, 7.02, 12.55, 0.34)
para(tf, "Technologies:  Python 3 · NumPy · SciPy (optimisation & root-finding) · pandas · Matplotlib · Jupyter — "
         "no commercial solver required; the whole controller runs in ~1 ms per hourly interval on a laptop.",
     size=10, bold=True, color=NAVY, first=True, space=0)

# =========================================================== 4b. INSIDE A DECISION
set_title(s_inside, "CONTROL STRATEGY  —  INSIDE ONE DECISION")
kill(s_inside, "TextBox 8")
pic(s_inside, "controller_decisions.png", 0.36, 0.96, w=12.62)

for l, title, c, fillc, lines in [
    (0.36, "IT ACTS ON A FORECAST, NOT A MEASUREMENT", BLUE, RGBColor(0xF4,0xF8,0xFB), [
        ("Each thin line is a 40 h forecast committed to at that moment.", False),
        ("The BHP limit is enforced on the PREDICTION — by the time a measurement breaches it, BHP is already 12.4 h from settling.", False)]),
    (4.58, "451 CANDIDATES, SCREENED THEN COSTED", ORANGE, RGBColor(0xFF,0xFB,0xF3), [
        ("Constraint screening comes FIRST, cost second.", True),
        ("At t = 35 h, 98 of 451 moves are rejected because their forecast leaves the envelope; the cheapest of the 353 survivors is applied.", False)]),
    (8.80, "THE SAFE SET SHRINKS AS THE LIMIT NEARS", GREEN, RGBColor(0xF1,0xF8,0xF1), [
        ("451 → 153 surviving, never 0", True),
        ("Early on every move is safe; parked at the BHP limit most are not. Yet the set never empties in 370 intervals — holding the choke is always feasible, so a feasible plan now guarantees one next interval.", False)]),
]:
    tf = tb(s_inside, l, 6.16, 4.07, 1.26, fill=fillc, line=c)
    head(tf, title, color=c, size=10, first=True, space=3)
    for txt, big in lines:
        para(tf, txt, size=9.2 if big else 8.5, bold=big, color=c if big else DARK, space=2)



# =========================================================== 5. RESULTS
set_title(s_results, "RESULTS  —  SCENARIO OUTCOMES & PERFORMANCE")
kill(s_results, "TextBox 8")
pic(s_results, "results_summary.png", 0.40, 1.06, w=8.30)

rows = [("", "A — Start-up", "B — Target change", "C — Infeasible"),
        ("Requested target", "100 bbl/hr", "100 → 150 bbl/hr", "120 → 200 bbl/hr"),
        ("Achievable (SSTO)", "100.0", "150.0", "165.2  (capped)"),
        ("Achieved rate", f"{SC['A']['final_rate_mean']:.1f}", f"{SC['B']['final_rate_mean']:.1f}",
         f"{SC['C']['final_rate_mean']:.1f}"),
        ("Steady-state offset", f"{SC['A']['steady_state_offset']:+.2f}",
         f"{SC['B']['steady_state_offset']:+.2f}", f"{SC['C']['steady_state_offset']:+.2f}"),
        ("Settling time", f"{SC['A']['settling_time_h']:.0f} h",
         f"{SC['B']['settling_time_h']:.0f} h", f"{SC['C']['settling_time_h']:.0f} h"),
        ("Constraint violations", "0", "0", "0"),
        ("Max choke move", "5.00 %", "5.00 %", "5.00 %"),
        ("Controller mode", "TRACKING", "TRACKING", "TRACKING→CONSTRAINED")]
t2 = s_results.shapes.add_table(len(rows), 4, Inches(0.40), Inches(4.60),
                                Inches(8.30), Inches(2.28)).table
for c, wdt in enumerate([2.02, 1.94, 2.14, 2.20]):
    t2.columns[c].width = Inches(wdt)
for i, row in enumerate(rows):
    for j, val in enumerate(row):
        cell = t2.cell(i, j); cell.text = val
        cell.margin_left = Inches(0.05); cell.margin_right = Inches(0.04)
        cell.margin_top = Inches(0.01); cell.margin_bottom = Inches(0.01)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        pr = cell.text_frame.paragraphs[0]
        pr.alignment = PP_ALIGN.CENTER if j else PP_ALIGN.LEFT
        for r in pr.runs:
            r.font.size = Pt(9.2); r.font.name = "Arial"
            r.font.bold = (i == 0 or j == 0 or i == 6)
            r.font.color.rgb = (RGBColor(0xFF,0xFF,0xFF) if i == 0
                                else (GREEN if i == 6 and j else DARK))
        cell.fill.solid()
        cell.fill.fore_color.rgb = (BLUE if i == 0 else
                                    (RGBColor(0xEC,0xF6,0xEC) if i == 6 else
                                     (RGBColor(0xF6,0xF9,0xFC) if i % 2 else RGBColor(0xFF,0xFF,0xFF))))

tf = tb(s_results, 8.90, 1.06, 4.05, 1.72, fill=RGBColor(0xF4,0xF8,0xFB), line=BLUE)
head(tf, "TRACKING PERFORMANCE", first=True)
for b in ["Zero steady-state offset in A and B — the disturbance estimator removes model mismatch.",
          "14–16 h settling (≈ 3 τ_Q) with no overshoot past any limit.",
          "Total choke travel is small: mean move < 0.4 %/interval once on target — gentle on the actuator."]:
    para(tf, b, size=9.6, bullet="▸", space=4)

tf = tb(s_results, 8.90, 2.86, 4.05, 2.10, fill=RGBColor(0xF1,0xF8,0xF1), line=GREEN)
head(tf, "SAFETY PERFORMANCE", color=GREEN, first=True)
para(tf, "0 violations / 30 runs", size=15, bold=True, color=GREEN, space=3)
for b in ["3 scenarios × 10 independent measurement-noise realisations.",
          "Audited on the noise-free plant state, not just the measurement.",
          "Every choke move inside the ±5 %/interval ramp limit, in every run.",
          "Scenario A is audited from the very first interval — no warm-up period is excluded."]:
    para(tf, b, size=9.6, bullet="▸", space=3)

tf = tb(s_results, 8.90, 5.06, 4.05, 1.82, fill=RGBColor(0xFF,0xFB,0xF3), line=ORANGE)
head(tf, "THE INFEASIBLE-TARGET RESULT", color=ORANGE, first=True)
para(tf, "200 bbl/hr is physically unreachable: the minimum-BHP (maximum-drawdown) limit caps "
         "the well at 166.2 bbl/hr.", size=9.8, space=4)
para(tf, "The controller settles at 164.9 bbl/hr — 99.2 % of the theoretical maximum — reports "
         "mode = CONSTRAINED with BHP_min as the active constraint, and never winds up against "
         "the limit.", size=9.8, space=0, bold=True, color=NAVY)

# =========================================================== 6. FEASIBILITY
set_title(s_feas, "FEASIBILITY, VIABILITY & LESSONS LEARNED")
kill(s_feas, "TextBox 8")

tf = tb(s_feas, 0.38, 1.12, 4.10, 2.78, fill=RGBColor(0xF1,0xF8,0xF1), line=GREEN)
head(tf, "FEASIBILITY OF THE IDEA", color=GREEN, first=True)
for b in ["Runs in ~1 ms per hourly control interval — orders of magnitude of headroom on any field controller or edge gateway.",
          "Pure Python / NumPy / SciPy: no commercial solver, no licence cost.",
          "Needs only measurements the well already has: Q, WHP, FLP, BHP and choke position.",
          "Model is re-identified from a routine 300 h step test — a standard commissioning activity.",
          "Maps directly onto existing DCS/APC infrastructure (Honeywell Experion / Profit Controller)."]:
    para(tf, b, size=9.8, bullet="▸", space=5)

tf = tb(s_feas, 4.62, 1.12, 4.10, 2.78, fill=RGBColor(0xFD,0xF0,0xF0), line=RED)
head(tf, "CHALLENGES AND RISKS", color=RED, first=True)
for b in ["Plant–model mismatch as the reservoir depletes and water cut rises — measured: 4.7 % of severely corrupted models graze the BHP limit.",
          "A CORRELATED instrument failure (shared IO card) taking out two constrained tags at once — the single-tag case is largely covered by constraint redundancy.",
          "Measurement noise far above nominal: the controller fails at ~12× the noise we assumed.",
          "Slugging / unmodelled multiphase dynamics faster than the 1 h interval.",
          "Operator trust — an autonomous controller that moves the choke unattended."]:
    para(tf, b, size=9.8, bullet="▸", space=5)

tf = tb(s_feas, 8.86, 1.12, 4.09, 2.78, fill=RGBColor(0xF4,0xF8,0xFB), line=BLUE)
head(tf, "MITIGATION STRATEGIES", first=True)
for b in ["Output-disturbance estimator: verified against 48 psi of unmodelled depletion and a 12 % water-cut step, both with zero violations.",
          "Bias the identified τ of the slowest constrained variable up 50 % — closes the mismatch failure for ~0.9 bbl/hr.",
          "5 psi constraint back-off: holds to 8× nominal noise; 0 psi back-off fails.",
          "Bad-data layer (range / rate-of-change / freeze) with HOLD on sustained faults — turns a 64-interval, 18.9 psi IO-card excursion into zero; back it with independent IO paths for constraint-critical tags.",
          "Controller publishes mode, achievable rate and active constraint every interval; RECOVERY auto-returns the well."]:
    para(tf, b, size=9.5, bullet="▸", space=4)

tf = tb(s_feas, 0.38, 3.96, 12.57, 3.10, fill=RGBColor(0xF6,0xF2,0xFA), line=RGBColor(0x6A,0x1B,0x9A))
head(tf, "LESSONS LEARNED", color=RGBColor(0x6A,0x1B,0x9A), size=13, first=True)
for t, d in [
    ("Penalise every move in the plan, not just the first.",
     "Our first cost function suppressed only du1. The optimiser learned to set du1 = 0 and let du2 do the work for free - "
     "and since a receding-horizon controller only ever applies the first move, the controller stalled with a 3-7 bbl/hr "
     "steady-state offset. Penalising all moves removed it completely. The single biggest bug in the project."),
    ("We misdiagnosed our own controller, and the sweep caught it.",
     "We had recorded 'the horizon must cover the slowest constrained variable' as a lesson, because violations vanished when we "
     "lengthened P from 20 to 40 h. Sweeping P properly from 5 to 50 h shows that is NOT what fixed it - horizon length only "
     "tightens the worst excursion from 0.77 to 0.23 psi and never causes a violation on its own. The back-off, changed at the "
     "same time, did the real work. The lesson is about method: we changed two things at once and credited the wrong one."),
    ("Diagnosing the mechanism beats blanket conservatism.",
     "Every mismatch failure had one cause - under-estimated tau_BHP. A targeted fix (bias the identified tau of the slowest "
     "constrained variable up 50 %) reaches zero violations for ~0.9 bbl/hr; blind conservatism via a 12 psi back-off costs "
     "3.9 bbl/hr for the same result."),
    ("The safety margin is not slack - we tried to spend it and put it back.",
     "A fixed 5 psi back-off looks over-conservative when a narrow variable binds (5 psi is 1.1 % of BHP's span but "
     "8.1 % of FLP's). Making it proportional to each span recovered 2.4 points of production - and lost the "
     "zero-violation guarantee on 9 % of 200 randomised envelopes. We put it back. Knowing the price of a margin is "
     "worth more than shaving it."),
    ("Constraint redundancy is real - and it changes what a fault test means.",
     "A single corrupted pressure transmitter did far less damage than expected, because a second constraint on a healthy "
     "measurement sat only 5 psi behind it. The dangerous fault is the correlated one - a shared IO card. A fault study that "
     "injects only single-tag faults will overstate how safe the system is."),
]:
    para(tf, t, size=9.6, bold=True, color=NAVY, space=1)
    para(tf, d, size=8.2, color=GREY, space=3)

# =========================================================== 6a. BASELINES
set_title(s_base, "RESULTS  —  BETTER THAN WHAT?")
kill(s_base, "TextBox 8")
pic(s_base, "baseline_comparison.png", 0.36, 1.02, w=12.62)

for l, title, c, fillc, lines in [
    (0.36, "PI ON RATE  (IMC-tuned, anti-windup)", RED, RGBColor(0xFD,0xF0,0xF0), [
        ("97 of 140 intervals violate a limit", True),
        ("Reaches 200 bbl/hr — by driving BHP 146 psi below its floor.", False),
        ("Not a tuning failure: a single rate loop structurally cannot see a pressure constraint.", False)]),
    (4.58, "OPERATOR RULE  (fixed safe choke)", ORANGE, RGBColor(0xFF,0xFB,0xF3), [
        ("Safe — 0 violations — but 5.6 % short", True),
        ("Settles at 156.1 bbl/hr where 164.9 was safely available.", False),
        ("Caution is not free: it is 8.8 bbl/hr of production, every hour.", False)]),
    (8.80, "MPC  (this work)", GREEN, RGBColor(0xF1,0xF8,0xF1), [
        ("0 violations AND the highest safe rate", True),
        ("164.9 bbl/hr, 99.2 % of the theoretical maximum.", False),
        ("A third of the PI's total choke travel — far gentler on the actuator.", False)]),
]:
    tf = tb(s_base, l, 5.36, 4.07, 1.58, fill=fillc, line=c)
    head(tf, title, color=c, size=10.5, first=True, space=3)
    for txt, big in lines:
        para(tf, txt, size=11 if big else 9.3, bold=big,
             color=c if big else DARK, space=3)

tf = tb(s_base, 0.36, 7.00, 12.62, 0.36)
para(tf, "Scenarios A and B are honest about this: when the target sits comfortably inside the envelope, all three controllers work. "
         "The architectures only separate when a constraint actually binds — which is precisely when it matters.",
     size=9.8, bold=True, color=NAVY, first=True, space=0)

# =========================================================== 6b. ROBUSTNESS
set_title(s_robust, "RESULTS  —  ROBUSTNESS AND WHERE IT BREAKS")
kill(s_robust, "TextBox 8")
pic(s_robust, "montecarlo_mismatch.png", 0.36, 1.06, w=8.55)
pic(s_robust, "breaking_point.png", 0.36, 4.06, w=8.55)

tf = tb(s_robust, 9.10, 1.06, 3.87, 2.72, fill=RGBColor(0xF4,0xF8,0xFB), line=BLUE)
head(tf, "MODEL DELIBERATELY CORRUPTED", first=True, size=11)
para(tf, "150 randomised models per scenario: incremental gain × U(0.7, 1.3) and "
         "τ × U(0.5, 1.5), independently per output channel — far worse than any "
         "real re-identification would leave.", size=9.3, space=5)
para(tf, "Scenario B:  0 / 150 violate", size=11, bold=True, color=GREEN, space=2)
para(tf, "Scenario C:  7 / 150 (4.7 %) violate,\nworst excursion 1.16 psi on a 2850 psi limit (0.04 %)",
     size=10, bold=True, color=ORANGE, space=4)
para(tf, "We report this rather than hide it — and the failures are not random.",
     size=9.3, italic=True, color=GREY, space=0)

tf = tb(s_robust, 9.10, 3.92, 3.87, 1.72, fill=RGBColor(0xF6,0xF2,0xFA), line=RGBColor(0x6A,0x1B,0x9A))
head(tf, "EVERY FAILURE HAS ONE SIGNATURE", color=RGBColor(0x6A,0x1B,0x9A), first=True, size=11)
para(tf, "τ_BHP under-estimated (factor < 0.69): the controller believes bottom-hole "
         "pressure settles faster than it does, so it stops backing off while the "
         "pressure is still falling. Gain error alone never causes a failure.",
     size=9.3, space=4)
para(tf, "Targeted fix — bias the identified τ of the slowest constrained variable "
         "up 50 % → 0 / 60 violations for ~0.9 bbl/hr. A blind 12 psi back-off buys "
         "the same result for 3.9 bbl/hr.", size=9.3, bold=True, color=NAVY, space=0)

tf = tb(s_robust, 9.10, 5.78, 3.87, 1.62, fill=RGBColor(0xFD,0xF0,0xF0), line=RED)
head(tf, "WHERE IT ACTUALLY BREAKS", color=RED, first=True, size=11)
for b in ["Constraint back-off 0 psi → 13 violating intervals. Any back-off ≥ 1 psi is enough.",
          "Measurement noise: safe to 8× nominal, fails at 12×.",
          "Prediction horizon 5–50 h and ramp limit 0.5–10 %: safe throughout.",
          "A design never pushed to failure has not been characterised."]:
    para(tf, b, size=9.0, bullet="▸", color=DARK, space=2)

# =========================================================== 6bb. LIMITS
set_title(s_limits, "RESULTS  —  DO OUR ASSUMED LIMITS MATTER?")
kill(s_limits, "TextBox 8")
pic(s_limits, "limit_sensitivity.png", 0.36, 1.12, w=12.62)

for l, title, c, fillc, lines in [
    (0.36, "THE PROBLEM WITH OUR OWN NUMBERS", ORANGE, RGBColor(0xFF,0xFB,0xF3), [
        ("The safe ranges are never given in the brief, and the organisers did not supply them.", False),
        ("Ours are documented engineering placeholders — the weakest point in the submission.", False),
        ("So rather than defend six numbers, we tested whether they matter at all.", True)]),
    (4.58, "200 RANDOMISED ENVELOPES", GREEN, RGBColor(0xF1,0xF8,0xF1), [
        ("0 of 200 violate a limit", True),
        ("Max safe rate ranged 136 – 181 bbl/hr; the controller reached 96.9 % of each envelope's OWN ceiling.", False),
        ("The binding limit shifted: WHP 54 %, BHP 27 %, FLP 20 % — it is not just watching BHP.", False)]),
    (8.80, "AND THE MISSING 3 % IS NOT SLACK", BLUE, RGBColor(0xF4,0xF8,0xFB), [
        ("We tried to recover it — and put it back.", True),
        ("A span-proportional back-off reaches 99.3 % of the ceiling, but violates on 18 of 200 envelopes.", False),
        ("We kept the fixed 5 psi. The 3 % is what the zero-violation guarantee costs.", False)]),
]:
    tf = tb(s_limits, l, 5.14, 4.07, 1.86, fill=fillc, line=c)
    head(tf, title, color=c, size=10.5, first=True, space=3)
    for txt, big in lines:
        para(tf, txt, size=9.6 if big else 8.9, bold=big,
             color=c if big else DARK, space=3)

tf = tb(s_limits, 0.36, 7.06, 12.62, 0.34)
para(tf, "The six numbers are an INPUT, not an assumption. Whatever the real limits turn out to be, "
         "they are edited in config/operating_limits.json and everything re-runs — the architecture does not change.",
     size=9.8, bold=True, color=NAVY, first=True, space=0)

# =========================================================== 6c. STRESS TESTS
set_title(s_stress, "RESULTS  —  BREAKING OUR OWN ASSUMPTIONS")
kill(s_stress, "TextBox 8")
pic(s_stress, "stress_tests.png", 0.36, 1.00, w=12.62)

for l, title, c, fillc, lines in [
    (0.36, "D1 · RESERVOIR DECLINE", GREEN, RGBColor(0xF1,0xF8,0xF1), [
        "48 psi of depletion that is never modelled. The controller opens the choke 47 % → 61 % to hold 130 bbl/hr.",
        "When the well genuinely can no longer make 130, it switches to CONSTRAINED and settles on the true declining capacity — within 0.6 bbl/hr of it.",
        "0 violations."]),
    (3.53, "D2 · WATER-CUT STEP", GREEN, RGBColor(0xF1,0xF8,0xF1), [
        "Water cut steps 0 → 12 %; oil rate drops instantly.",
        "The output-disturbance estimator absorbs it and the controller recovers 120 bbl/hr with zero steady-state error.",
        "0 violations."]),
    (6.70, "F1 / F4 · INSTRUMENT FAULTS", ORANGE, RGBColor(0xFF,0xFB,0xF3), [
        "Frozen BHP transmitter: detected in 5 intervals, choke frozen for 43, 0 violations. Validation OFF: 5 violating intervals.",
        "IO card fails (BHP +220, WHP +45 psi): validation OFF over-produces to 170.6 bbl/hr and drives BHP 18.9 psi below its limit for 64 intervals. Validation ON: 0 violations."]),
    (9.87, "AN UNPLANNED FINDING", RGBColor(0x6A,0x1B,0x9A), RGBColor(0xF6,0xF2,0xFA), [
        "Corrupting BHP alone did far less damage than we expected.",
        "At the maximum safe point BHP has 0.3 psi of margin — but WHP has only 5.4 psi. The WHP constraint, computed from a healthy transmitter, stops the controller almost immediately.",
        "That constraint redundancy is a real safety property — and it means a fault study injecting only single-tag faults overstates how safe the system is."]),
]:
    tf = tb(s_stress, l, 5.20, 3.10, 1.84, fill=fillc, line=c)
    head(tf, title, color=c, size=10, first=True, space=3)
    for b in lines:
        para(tf, b, size=8.6, bullet="▸", color=DARK, space=3)

tf = tb(s_stress, 0.36, 7.06, 12.62, 0.34)
para(tf, "The problem statement says to assume constant reservoir properties, no changing water cut, and healthy instrumentation. "
         "All three are false on a real well — so we broke them deliberately and measured what happened.",
     size=9.8, bold=True, color=NAVY, first=True, space=0)

# =========================================================== 7. ARTIFACTS
set_title(s_art, "ARTIFACTS")
kill(s_art, "TextBox 8")

tf = tb(s_art, 0.38, 1.08, 4.35, 3.74, fill=RGBColor(0xF4,0xF8,0xFB), line=BLUE)
head(tf, "CODE SUBMITTED", first=True)
for f, d in [("Autonomous_Choke_Control.ipynb", "executed notebook — runs the whole study end-to-end"),
             ("config/operating_limits.json", "the six safe limits + derivation — the only place they live"),
             ("ASSUMPTIONS.md  ·  DEVELOPMENT_LOG.md", "13 assumptions with limitations · the build phase by phase, including what went wrong"),
             ("src/simulator.py", "physics-structured well simulator + operating envelope"),
             ("src/external_simulator.py", "adapter — drive the study with any other simulator"),
             ("src/identification.py", "step-test design, Hammerstein identification, cross-validation"),
             ("src/controller.py", "SSTO + dynamic MPC + closed-loop driver"),
             ("src/baselines.py", "PI + operator baselines"),
             ("src/robustness.py  ·  stress_tests.py", "mismatch sweeps · disturbances · instrument faults"),
             ("src/scenarios.py / studies.py", "one-command reproduction of every result"),
             ("tests/  (5 suites)", "spec compliance · equivalence · regression · reported numbers · simulator swap")]:
    para(tf, f, size=8.5, bold=True, color=NAVY, space=0, font="Consolas")
    para(tf, d, size=7.4, color=GREY, space=2)

tf = tb(s_art, 0.38, 4.90, 4.35, 2.00, fill=RGBColor(0x1E,0x1E,0x1E))
para(tf, "# the required simulator interface", size=8.2, color=RGBColor(0x8B,0xC3,0x4A), first=True, space=0, font="Consolas")
for ln, c in [("sim  = WellSimulator(u0=40.0)", RGBColor(0xE6,0xE6,0xE6)),
              ("ctrl = ChokeMPC(model, ENV, CFG, u0=40.0)", RGBColor(0xE6,0xE6,0xE6)),
              ("", RGBColor(0xE6,0xE6,0xE6)),
              ("for k in range(n_steps):", RGBColor(0x82,0xAA,0xFF)),
              ("    u = ctrl.compute(meas, Q_target)", RGBColor(0xE6,0xE6,0xE6)),
              ("    Q, WHP, FLP, BHP = sim.step(u)", RGBColor(0xE6,0xE6,0xE6)),
              ("", RGBColor(0xE6,0xE6,0xE6)),
              ("# reproduce every figure and result:", RGBColor(0x8B,0xC3,0x4A)),
              ("$ python src/scenarios.py", RGBColor(0xFF,0xC1,0x07)),
              ("$ python src/studies.py", RGBColor(0xFF,0xC1,0x07)),
              ("# drive it with a different simulator:", RGBColor(0x8B,0xC3,0x4A)),
              ("$ python src/adopt_simulator.py module:Sim", RGBColor(0xFF,0xC1,0x07))]:
    para(tf, ln, size=8.2, color=c, space=0, font="Consolas")

pic(s_art, "scenario_C.png", 4.90, 1.12, w=8.05)
band(s_art, 4.90, 6.05, 8.05, 0.36,
     "Scenario C — infeasible target: SSTO caps at the max safe rate, BHP holds exactly at its limit, no violation",
     ORANGE, size=9.5)
tf = tb(s_art, 4.90, 6.50, 8.05, 0.32)
para(tf, "Full-size trend plots for Scenarios A, B and C, the step-test identification, the model "
         "cross-validation and the operating-envelope map are in  /figures ;  all numeric results in  /results .",
     size=9.4, color=GREY, italic=True, first=True, space=0)

# =========================================================== 8. REFERENCES
set_title(s_ref, "RESEARCH AND REFERENCES")
kill(s_ref, "TextBox 8")

tf = tb(s_ref, 0.38, 1.15, 6.20, 4.30, fill=RGBColor(0xF4,0xF8,0xFB), line=BLUE)
head(tf, "MODEL PREDICTIVE CONTROL", first=True)
for b in ["Qin, S. J. & Badgwell, T. A. (2003). “A survey of industrial model predictive control technology.” "
          "Control Engineering Practice, 11(7), 733–764. — source for the two-layer SSTO + dynamic-MPC architecture.",
          "Rawlings, J. B., Mayne, D. Q. & Diehl, M. (2017). Model Predictive Control: Theory, Computation and Design, 2nd ed. Nob Hill. — constraint handling, offset-free control, target calculation.",
          "Cutler, C. R. & Ramaker, B. L. (1980). “Dynamic Matrix Control — a computer control algorithm.” Proc. Joint Automatic Control Conference. — the constant output-disturbance estimator used here.",
          "Muske, K. R. & Badgwell, T. A. (2002). “Disturbance modeling for offset-free linear model predictive control.” Journal of Process Control, 12(5), 617–632.",
          "Honeywell Process Solutions. Profit Controller / RMPCT product documentation — industrial reference for range-control and steady-state optimisation layering."]:
    para(tf, b, size=9.8, bullet="▸", space=7)

tf = tb(s_ref, 6.75, 1.15, 6.20, 4.30, fill=RGBColor(0xFF,0xFB,0xF3), line=ORANGE)
head(tf, "PETROLEUM PRODUCTION ENGINEERING", color=ORANGE, first=True)
for b in ["Beggs, H. D. (2003). Production Optimization Using NODAL Analysis, 2nd ed. OGCI. — IPR/VLP coupling and the choke as the system boundary.",
          "Guo, B., Lyons, W. C. & Ghalambor, A. (2007). Petroleum Production Engineering: A Computer-Assisted Approach. Elsevier. — choke flow correlations and wellbore pressure traverse.",
          "Vogel, J. V. (1968). “Inflow performance relationships for solution-gas drive wells.” JPT, 20(1), 83–92. — inflow performance; a linear IPR was sufficient for this dataset.",
          "Sachdeva, R. et al. (1986). “Two-phase flow through chokes.” SPE 15657. — subcritical choke behaviour, Q ∝ CdA(u)·√ΔP.",
          "Jansen, J. D. (2017). Nodal Analysis of Oil and Gas Production Systems. SPE Textbook Series. — production-system modelling and constraint envelopes."]:
    para(tf, b, size=9.8, bullet="▸", space=7)
head(tf, "DATA", color=ORANGE, space=3)
para(tf, "Autonomous_Choke_Control_Simulated_Dataset.csv — reference dataset supplied with the problem "
         "statement, used only to calibrate the simulator and as an independent validation set for the "
         "identified control model.", size=9.8, space=0)

tf = tb(s_ref, 0.38, 5.62, 12.57, 1.18, fill=RGBColor(0xF1,0xF8,0xF1), line=GREEN)
head(tf, "WHERE THIS GOES NEXT", color=GREEN, size=11.5, first=True, space=4)
for b in ["Multi-well: replace the calibrated manifold correlation with a network model and add a shared separator-capacity constraint across wells.",
          "Adaptive: track reservoir depletion and rising water cut by scheduling periodic re-identification; the disturbance estimator already absorbs slow drift.",
          "Economic layer: replace “track the rate target” in the SSTO with “maximise oil revenue minus water-handling cost” — same architecture, richer objective.",
          "Promote WHT and annulus pressure from monitored variables to hard constraints once integrity limits are defined for the asset."]:
    para(tf, b, size=9.4, bullet="▸", color=DARK, space=2)

prs.save(OUT)
print("saved:", OUT, "|", len(prs.slides._sldIdLst), "slides")
