"""HTTP service: happy paths, input validation, session bounds and error hygiene."""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient

from service import api

client = TestClient(api.app, raise_server_exceptions=False)
MEAS = {"oil_rate": 120.0, "whp": 250.0, "flp": 175.0, "bhp": 3050.0, "choke_pct": 40.0}


def test_envelope_and_target():
    env = client.get("/envelope").json()
    assert abs(env["max_safe_rate"]["Q_bbl_hr"] - 166.2) < 0.1
    assert env["max_safe_rate"]["binding_constraint"] == "BHP_min"

    capped = client.post("/target", json={"target_rate": 200}).json()
    assert capped["capped"] and capped["mode"] == "CONSTRAINED"
    assert 160 < capped["achievable_rate"] < 166.2
    reachable = client.post("/target", json={"target_rate": 100}).json()
    assert not reachable["capped"] and reachable["mode"] == "TRACKING"
    print("  /envelope and /target report the cap and the binding limit  [OK]")


def test_decide_keeps_state_across_calls():
    first = client.post("/decide", json={"measurement": MEAS, "target_rate": 150}).json()
    sid = first["session_id"]
    assert abs(first["choke_move_pct"]) <= 5.0 + 1e-9
    second = client.post("/decide", json={"measurement": MEAS, "target_rate": 150,
                                          "session_id": sid}).json()
    assert second["session_id"] == sid
    assert client.delete(f"/decide/{sid}").status_code == 200
    assert client.delete(f"/decide/{sid}").status_code == 404
    print("  /decide is stateful per session and sessions can be closed  [OK]")


def test_invalid_input_is_rejected():
    cases = [
        ("/target", {"target_rate": -5}),
        ("/target", {"target_rate": "lots"}),
        ("/decide", {"measurement": {**MEAS, "choke_pct": 150}, "target_rate": 100}),
        ("/decide", {"measurement": {**MEAS, "bhp": 1e9}, "target_rate": 100}),
        ("/decide", {"measurement": MEAS, "target_rate": 100, "session_id": "../../etc"}),
        ("/simulate", {"hours": 100000}),
        ("/simulate", {"target_step_at_hour": 20}),
        ("/simulate", {"hours": 50, "target_step_at_hour": 60, "target_rate_after_step": 120}),
    ]
    for path, body in cases:
        r = client.post(path, json=body)
        assert r.status_code == 422, f"{path} {body}: expected 422, got {r.status_code}"
    # NaN is not valid JSON for a measurement: send it raw
    r = client.post("/decide", content='{"measurement": {"oil_rate": NaN, "whp": 250, "flp": 175, '
                                       '"bhp": 3050, "choke_pct": 40}, "target_rate": 100}',
                    headers={"content-type": "application/json"})
    assert r.status_code == 422, f"NaN measurement: expected 422, got {r.status_code}"
    print(f"  {len(cases) + 1} malformed requests rejected with 422  [OK]")


def test_sessions_are_bounded():
    old = api.MAX_SESSIONS
    api.MAX_SESSIONS = 5
    try:
        with api._SESSIONS_LOCK:
            api._SESSIONS.clear()
        for i in range(12):
            client.post("/decide", json={"measurement": MEAS, "target_rate": 100,
                                         "session_id": f"s{i}"})
        assert len(api._SESSIONS) == 5, f"{len(api._SESSIONS)} sessions held, cap is 5"
        assert "s11" in api._SESSIONS and "s0" not in api._SESSIONS, "LRU eviction order wrong"
    finally:
        api.MAX_SESSIONS = old
    print("  session store is capped and evicts least-recently-used  [OK]")


def test_internal_errors_do_not_leak():
    original = api.run_closed_loop

    def boom(*a, **k):
        raise RuntimeError("secret internal path /srv/app/src/controller.py")

    api.run_closed_loop = boom
    try:
        r = client.post("/simulate", json={"hours": 10})
    finally:
        api.run_closed_loop = original
    assert r.status_code == 500
    assert "secret" not in r.text and "controller.py" not in r.text, r.text
    print("  a server error returns a generic message, not the exception  [OK]")


def test_simulate_audits_zero_violations():
    r = client.post("/simulate", json={"target_rate": 120, "initial_choke_pct": 40, "hours": 140,
                                       "seed": 3, "target_step_at_hour": 30,
                                       "target_rate_after_step": 200}).json()
    assert r["violating_intervals"] == 0 and abs(r["settled_rate"] - 164.9) < 0.5
    print(f"  /simulate scenario C: {r['settled_rate']} bbl/hr, 0 violations  [OK]")


if __name__ == "__main__":
    for t in (test_envelope_and_target, test_decide_keeps_state_across_calls,
              test_invalid_input_is_rejected, test_sessions_are_bounded,
              test_internal_errors_do_not_leak, test_simulate_audits_zero_violations):
        print(t.__name__); t()
    print("\nALL SERVICE TESTS PASSED")
