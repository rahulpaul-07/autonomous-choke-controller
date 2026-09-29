"""The web demo renders every view without an exception and reports the study's numbers."""
import os, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from streamlit.testing.v1 import AppTest

APP = os.path.join(ROOT, "app", "streamlit_app.py")


def metrics(at) -> dict:
    return {m.label: m.value for m in at.metric}


def test_simulator_view_default_scenario():
    started = time.perf_counter()
    at = AppTest.from_file(APP, default_timeout=120).run()
    elapsed = time.perf_counter() - started
    assert not at.exception, [e.message for e in at.exception]
    m = metrics(at)
    assert m["Target optimiser"] == "Constrained"
    assert m["Limiting constraint"] == "BHP min"
    assert m["Intervals outside envelope"].startswith("0 /")
    assert abs(float(m["Mean rate, last 30 h [bbl/hr]"]) - 164.89) < 0.05
    assert at.warning and "not safely reachable" in at.warning[0].value
    print(f"  scenario C renders in {elapsed:.1f} s, capped at the BHP limit, 0 violations  [OK]")


def test_every_preset_runs_clean():
    at = AppTest.from_file(APP, default_timeout=120).run()
    for preset in at.sidebar.selectbox[0].options:
        at.sidebar.selectbox[0].set_value(preset).run()
        assert not at.exception, f"{preset}: {[e.message for e in at.exception]}"
        assert metrics(at)["Intervals outside envelope"].startswith("0 /"), preset
        if preset.startswith("Recovery"):
            # results/kpi_summary.csv: hours_to_re_enter_envelope = 6
            assert at.info and "inside in 6 h" in at.info[0].value, at.info
    print("  every preset runs with zero audited violations; recovery re-enters in 6 h  [OK]")


def test_other_views_render():
    at = AppTest.from_file(APP, default_timeout=120).run()
    for view in ("How it works", "Study results"):
        at.button_group[0].set_value(view).run()
        assert not at.exception, f"{view}: {[e.message for e in at.exception]}"
    m = metrics(at)
    assert m["Oil vs a cautious operator, Scenario C"] == "+5.6 %"
    assert m["PI loop: intervals outside envelope"] == "69 %"
    print("  'How it works' and 'Study results' render, headline numbers match  [OK]")


if __name__ == "__main__":
    for t in (test_simulator_view_default_scenario, test_every_preset_runs_clean,
              test_other_views_render):
        print(t.__name__); t()
    print("\nALL APP TESTS PASSED")
