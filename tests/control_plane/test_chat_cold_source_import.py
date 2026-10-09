"""Packaged-compatible HTTP transport with real cold source, backup and stores."""
from __future__ import annotations

import http.client
import json
import threading

import pytest

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.effect_runtime import effect_runtime_result, restart_effect_runtime
from test_cold_source_import_cli import workspace


@pytest.fixture
def cold_api(tmp_path, monkeypatch):
    _, state, _, body, runtime, _, _ = workspace(tmp_path, monkeypatch)
    registry = tmp_path / "project/.loopx/registry.json"
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.runtime_root = runtime
    server.registry_path = registry
    server.runtime_root_override = str(runtime)
    server.verbose = False
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def call(action="", payload=None, origin=None):
        client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=90)
        path = "/api/chat/goal-storage" + (f"/import/{action}" if action else "?goal_id=cold")
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        client.request("POST" if payload is not None else "GET", path,
            body=json.dumps(payload) if payload is not None else None, headers=headers)
        response = client.getresponse()
        value = json.loads(response.read())
        code = response.status
        client.close()
        public = json.dumps(value)
        assert str(tmp_path) not in public and body not in public
        assert "plan_path" not in value and "source_snapshot" not in value
        return code, value

    def read():
        return effect_runtime_result("coordination.local_authority.todo_list", {
            "schema_version": "loopx_local_coordination_todo_list_request_v0",
            "runtime_root": str(runtime), "goal_id": "cold", "role": None, "status": None,
            "todo_id": None, "agent_id": None, "limit": None})

    yield call, read, state, body, runtime
    server.shutdown()
    worker.join(5)
    server.server_close()
    restart_effect_runtime()


def prepare(call, provider="sqlite"):
    code, value = call("preview", {"goal_id": "cold", "provider": provider, "handoff_mode": "hard_lease"})
    assert code == 200 and value["ok"], value
    return value


def carrier(plan):
    return {key: plan[key] for key in ("goal_id", "operation_id", "plan_sha256")}


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_cold_http_preview_reload_confirm_original_recovery(cold_api, provider):
    call, read, state, body, runtime = cold_api
    original = state.read_bytes()
    assert call()[1]["current"]["canonical"] is False
    plan = prepare(call, provider)
    assert plan["source_inventory"] == {"todo_count": 2, "archived_todo_count": 1,
        "lease_count": 0, "source_handoff_mode": "legacy"}
    assert plan["coordination_source_backup_verified"] is True
    assert plan["complete_goal_backup_verified"] is False
    assert plan["authority_changed"] is False and not plan["legacy_writer_fenced"]
    saved = carrier(plan)
    restart_effect_runtime()
    code, observed = call("recover", saved)
    assert code == 200 and observed["status"] == "prepared", observed
    assert observed["operation_id"] == plan["operation_id"]
    assert not observed["legacy_writer_fenced"] and not observed["current"]["canonical"]
    assert state.read_bytes() == original
    assert not list(runtime.rglob("writer-fence.json"))
    code, refused = call("apply", {**saved, "writers_stopped": False})
    assert code == 409 and refused["reason_code"] == "cold_import_operator_stop_confirmation_required"
    code, applied = call("apply", {**saved, "writers_stopped": True})
    assert code == 200 and applied["status"] == "applied", applied
    assert applied["execution_authority_granted"] is False
    assert applied["current"]["provider"] == provider
    rows = read()["todos"]
    assert next(row for row in rows if row["todo_id"] == "todo_current")["text"] == body
    assert next(row for row in rows if row["todo_id"] == "todo_archived")["evidence"] == "original"
    from loopx.todos import add_goal_todo
    registry = state.parents[3] / ".loopx/registry.json"
    add_goal_todo(registry_path=registry, goal_id="cold", role="agent", text="Keep later write",
        claimed_by="agent-a", note="Original metadata")
    later = read()
    state.unlink()
    restart_effect_runtime()
    code, recovered = call("recover", saved)
    assert code == 200 and recovered["status"] == "replayed", recovered
    assert recovered["current"]["todo_count"] == 3
    assert read() == later
    assert call("apply", {**saved, "writers_stopped": True})[0] == 200
    assert read() == later


def test_cold_http_changed_source_backup_and_untrusted_inputs(cold_api):
    call, read, state, _, runtime = cold_api
    plan = prepare(call)
    saved = carrier(plan)
    assert call("apply", {**saved, "writers_stopped": True, "plan": "injected"})[0] == 400
    assert call("recover", {**saved, "operation_id": "../wrong"})[0] == 400
    assert call("recover", {**saved, "goal_id": "unknown"})[0] == 400
    assert call("preview", {"goal_id": "cold", "provider": "sqlite", "handoff_mode": "hard_lease"},
        "https://untrusted.example")[0] == 403
    assert call("preview", {"goal_id": "cold", "provider": "postgresql", "handoff_mode": "hard_lease"})[0] == 409
    assert call("preview", {"goal_id": "cold", "provider": "sqlite", "handoff_mode": "legacy"})[0] == 409
    state.write_text(state.read_text() + "\nChanged after preview\n")
    code, refused = call("apply", {**saved, "writers_stopped": True})
    assert code == 409 and refused["reason_code"] == "source_changed_retry", refused
    assert not list(runtime.rglob("writer-fence.json"))
    fresh = prepare(call)
    archives = list((runtime / "backups/cold-import").glob(f"*{fresh['operation_id']}*.tar.gz"))
    assert len(archives) == 1
    archives[0].write_bytes(b"Damaged after review")
    code, refused = call("apply", {**carrier(fresh), "writers_stopped": True})
    assert code == 409 and refused["reason_code"] == "cold_import_backup_changed", refused
    assert call()[1]["current"]["canonical"] is False
    assert not list(runtime.rglob("writer-fence.json"))
