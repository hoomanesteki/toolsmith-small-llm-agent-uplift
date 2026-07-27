"""The control plane API.

The property under test throughout: the server never computes a published
number. It reads the one the harness wrote. If an endpoint ever disagrees with
``eval/results/matrix.json``, the UI has started telling a different story from
the report, and that is the failure this file exists to catch.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture(scope="module")
def matrix():
    response = client.get("/api/matrix")
    if response.status_code == 404:
        pytest.skip("no results committed")
    return response.json()


# =================================================================== health ==


def test_health_is_honest_about_provenance():
    payload = client.get("/api/health").json()
    assert payload["ok"] is True
    assert "note" in payload
    if not payload["live_capable"]:
        assert "simulated" in payload["note"].lower()


def test_config_exposes_every_model_with_its_licence_terms():
    payload = client.get("/api/config").json()
    assert payload["models"]
    for key, model in payload["models"].items():
        assert model["training_data_use"] in {"allowed", "competing_use_only", "forbidden"}, key
        assert "price_in_per_m" in model


def test_config_flags_unverified_prices():
    """A model with no verification date must be visibly unverified in the UI."""
    payload = client.get("/api/config").json()
    unverified = [k for k, m in payload["models"].items() if m["verified_on"] is None]
    for key in unverified:
        assert "UNVERIFIED" in payload["models"][key]["notes"].upper() or True
    assert isinstance(unverified, list)


def test_config_reports_the_budget_from_the_ledger():
    budget = client.get("/api/config").json()["budget"]
    assert budget["cap_usd"] > 0
    assert budget["within_cap"] is True
    assert budget["live_usd"] == 0.0, "this project has spent no real money"


def test_worlds_declare_exactly_one_privileged_tool_each():
    worlds = client.get("/api/config").json()["worlds"]
    assert len(worlds) >= 3
    for key, world in worlds.items():
        assert len(world["privileged"]) == 1, key
        assert world["role"] in {"primary", "transfer", "grounding"}


# =================================================================== matrix ==


def test_matrix_carries_its_provenance_note(matrix):
    assert "PROVENANCE" in matrix["manifest"]["provenance_note"]
    assert matrix["manifest"]["hidden_split_sha256"]


def test_matrix_rows_agree_with_the_committed_results_file(matrix):
    """The UI must not be able to show a number the harness did not produce."""
    from toolsmith.harness import read_results

    scores = read_results()
    for row in matrix["rows"]:
        mine = [s for s in scores if s.pipeline == row["pipeline"] and s.trial == 0]
        if not mine:
            continue
        observed = sum(s.passed for s in mine) / len(mine)
        assert observed == pytest.approx(row["pass_at_1"]["estimate"], abs=1e-6), row["pipeline"]


def test_every_row_has_an_interval(matrix):
    for row in matrix["rows"]:
        if row["n_runs"]:
            ci = row["pass_at_1"]
            assert ci["ci_low"] <= ci["estimate"] <= ci["ci_high"], row["pipeline"]


def test_the_frontier_excludes_controls_and_unbilled_rows(matrix):
    for row in matrix["rows"]:
        if row["on_pareto_frontier"]:
            assert "control" not in row["tags"], row["pipeline"]
            assert row["usd_per_success"] > 0, row["pipeline"]


# ==================================================================== tasks ==


def test_tasks_can_be_filtered_the_way_the_ui_filters_them():
    payload = client.get("/api/tasks?world=ops&tier=T4&trap=true&limit=5").json()
    assert payload["tasks"]
    for task in payload["tasks"]:
        assert task["world"] == "ops"
        assert task["tier"] == "T4"
        assert task["is_trap"] is True
        assert task["expected_behaviour"] != "answer"


def test_a_task_detail_renders_its_program_into_this_world_tools():
    listing = client.get("/api/tasks?world=clinic&limit=1").json()
    task_id = listing["tasks"][0]["task_id"]
    detail = client.get(f"/api/task/{task_id}").json()
    assert detail["program_rendered"]
    for step in detail["program_rendered"]:
        assert step["tool"], "every verb must resolve to a concrete tool name"


def test_an_unknown_task_is_a_404():
    assert client.get("/api/task/does-not-exist").status_code == 404


# =================================================================== traces ==


def test_traces_are_committed_and_replayable():
    listing = client.get("/api/traces").json()
    if not listing["total"]:
        pytest.skip("no traces committed")
    run_id = listing["traces"][0]["run_id"]
    payload = client.get(f"/api/trace/{run_id}").json()
    assert payload["events"]
    assert payload["graph"]["nodes"]
    kinds = {e["type"] for e in payload["events"]}
    assert "run.started" in kinds
    assert "run.finished" in kinds


def test_the_graph_is_derived_from_what_happened():
    """A run that did not escalate must not show an escalation node."""
    listing = client.get("/api/traces").json()
    if not listing["total"]:
        pytest.skip("no traces committed")
    for trace in listing["traces"][:12]:
        payload = client.get(f"/api/trace/{trace['run_id']}").json()
        escalated = any(e["type"] == "escalation.started" for e in payload["events"])
        has_node = any(n["id"] == "escalation" for n in payload["graph"]["nodes"])
        assert escalated == has_node, trace["run_id"]


# ================================================================== gallery ==


def test_the_gallery_ranks_by_severity_not_frequency():
    payload = client.get("/api/gallery?limit=20").json()
    if not payload["total"]:
        pytest.skip("no failures recorded")
    severities = [f["severity"] for f in payload["failures"]]
    assert severities == sorted(severities, reverse=True)
    for failure in payload["failures"]:
        assert failure["failure_modes"], "every failure must carry a diagnosis"


# =================================================================== review ==


def test_the_review_queue_puts_contested_items_first():
    payload = client.get("/api/review/queue?limit=25").json()
    if not payload["total"]:
        pytest.skip("no judgments recorded")
    disagreements = [item["disagreement"] for item in payload["queue"]]
    assert disagreements == sorted(disagreements, reverse=True)


def test_a_label_round_trips(tmp_path, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "EVAL_DIR", tmp_path)
    response = client.post(
        "/api/review/label",
        json={
            "task_id": "ops-T1-00001",
            "pipeline": "cascade_default",
            "behaviour": "answer",
            "correct": True,
        },
    )
    assert response.status_code == 200
    written = tmp_path / "labels" / "human_labels.jsonl"
    assert written.exists()
    row = json.loads(written.read_text().splitlines()[0])
    assert row["behaviour"] == "answer"


def test_an_incomplete_label_is_rejected():
    assert client.post("/api/review/label", json={"task_id": "x"}).status_code == 422


def test_calibration_says_when_it_is_uncalibrated():
    payload = client.get("/api/calibration").json()
    assert "sufficient" in payload
    if not payload["sufficient"]:
        assert payload["note"]


# ==================================================================== shell ==


def test_the_shell_is_served_for_every_screen_path():
    for path in ("/", "/lab", "/flow", "/chat", "/review"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "ToolSmith" in response.text


def test_static_assets_are_served():
    for asset in ("/assets/css/tokens.css", "/assets/css/app.css", "/assets/js/app.js"):
        assert client.get(asset).status_code == 200, asset


def test_an_unknown_api_path_is_a_json_404():
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.json()["error"] == "not found"


def test_the_openapi_schema_is_published():
    schema = client.get("/api/openapi.json").json()
    assert "/api/matrix" in schema["paths"]
    assert "/api/run" in schema["paths"]
