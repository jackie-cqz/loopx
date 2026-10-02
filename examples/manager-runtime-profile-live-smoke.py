#!/usr/bin/env python3
"""Opt-in private-manager profile qualification with a real authenticated Codex.

Creates only disposable LoopX configuration, workspace and manager Sessions.
Consumes model quota. Never resumes a user Session or edits the host home.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from loopx.capabilities.machine_configuration.builtins import build_builtin_machine_configuration_registry
from loopx.capabilities.machine_configuration.store import configure_machine_configuration
from loopx.chat_agent import CodexChatAgentError, _turn_prompt
from loopx.chat_manager import MANAGER_AGENT_GOAL_ID, manager_workspace
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore


def qualify(root: Path, codex_bin: str) -> dict:
    registry = root / "registry.json"
    registry.write_text(json.dumps({"goals": []}), encoding="utf-8")
    store = ChatSessionStore(root)
    controller = ChatRuntimeController(store=store, codex_bin=codex_bin,
        registry_path=registry, hard_timeout_sec=180)
    configuration = {"schema_version": "loopx_machine_configuration_v0", "namespaces": {}}

    def apply(profile):
        configuration["namespaces"]["manager_runtime"] = {
            "schema_version": "manager_runtime_profile_v0", "runtime_profile": profile}
        owner = build_builtin_machine_configuration_registry()
        preview = configure_machine_configuration(runtime_root=root,
            configuration=configuration, registry=owner, execute=False)
        configure_machine_configuration(runtime_root=root, configuration=configuration,
            registry=owner, execute=True, expected_plan_revision=preview["plan_revision"])
        readback = subprocess.run([sys.executable, "-m", "loopx.cli",
            "--registry", str(registry), "--runtime-root", str(root), "--format", "json",
            "machine-config", "inspect"], capture_output=True, text=True, check=True)
        assert profile in readback.stdout

    def open_manager(channel="manager"):
        session, _ = controller.open_session(goal_id=MANAGER_AGENT_GOAL_ID,
            agent_id="codex", work_dir=root, objective="profile qualification",
            mode="resume_latest", channel_id=channel)
        return session, controller.adapters[session["session_id"]]

    def check(session, adapter, profile, status="ready"):
        readback = store.public_session(store.load_session(session["session_id"]))["manager_runtime"]
        assert readback["runtime_profile"] == profile and readback["status"] == status
        expected = "danger-full-access" if profile == "trusted_owner" else "read-only"
        assert adapter.session.sandbox == readback["sandbox"] == expected
        instructions = (adapter.session.work_dir / "AGENTS.md").read_text(encoding="utf-8")
        assert instructions.startswith("# LoopX managed manager instructions")
        assert adapter.session.work_dir == manager_workspace(store.root,
            session["channel_id"], runtime_profile=profile)
        prompt = _turn_prompt("Synthetic qualification", context_summary=adapter.session.context_summary,
            execution_mode=False, runtime_profile=profile)
        assert ("effective runtime profile is trusted_owner" in prompt) == (profile == "trusted_owner")
        assert ("Do not edit files" in prompt) == (profile == "restricted")
        return readback

    def turn(session, name, text):
        pending, _ = controller.submit_turn(session_id=session["session_id"],
            client_turn_id=name, message=text, work_dir=root, objective="qualification")
        deadline = time.monotonic() + 200
        while time.monotonic() < deadline:
            result = store.load_turn(session["session_id"], pending["turn_id"])
            if result["status"] in {"completed", "failed", "interrupted"}:
                assert result["status"] == "completed", result.get("error_code")
                return
            time.sleep(0.1)
        raise TimeoutError("qualification Turn did not finish")

    try:
        default, adapter = open_manager()
        check(default, adapter, "restricted")
        turn(default, "profile-default", "Reply fixture-ready. Do not use tools or create Agents.")
        visible = store.messages(default["session_id"])
        assert any(row["role"] == "agent" for row in visible)
        apply("restricted")
        equal, same = open_manager()
        assert same is adapter and equal["upstream_thread_id"] == default["upstream_thread_id"]
        check(equal, same, "restricted")
        apply("trusted_owner")
        trusted, capable = open_manager()
        assert trusted["session_id"] == default["session_id"]
        assert trusted["upstream_thread_id"] != default["upstream_thread_id"]
        assert store.messages(trusted["session_id"]) == visible
        check(trusted, capable, "trusted_owner")
        marker = capable.session.work_dir / "synthetic-profile-proof.txt"
        turn(trusted, "profile-write", "In this disposable qualification workspace, write "
            "synthetic-profile-proof.txt containing exactly fixture-profile-ok. "
            "This one local file write is authorized. Do not create Agents or access external services.")
        assert marker.read_text(encoding="utf-8").strip() == "fixture-profile-ok"
        external, restricted = open_manager("manager.external.fixture")
        check(external, restricted, "restricted", "external_audience_restricted")
        apply("restricted")
        downgraded, restricted = open_manager()
        assert downgraded["upstream_thread_id"] != trusted["upstream_thread_id"]
        check(downgraded, restricted, "restricted")
        retained = store.messages(default["session_id"])
        controller.close()
        controller = ChatRuntimeController(store=store, codex_bin=codex_bin,
            registry_path=registry, hard_timeout_sec=180)
        restored, resumed = open_manager()
        # An upstream with no attempted Turn may have no persisted host record.
        # Recreate it with visible history; never retry a previously attempted Turn.
        assert restored["upstream_thread_id"] != downgraded["upstream_thread_id"]
        assert store.messages(restored["session_id"]) == retained
        check(restored, resumed, "restricted")
        # Invalid configuration is a read-only fallback, not a new permission.
        path = root / "machine" / "configuration.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["namespaces"]["manager_runtime"]["runtime_profile"] = "invalid-fixture"
        path.write_text(json.dumps(data), encoding="utf-8")
        invalid, unchanged = open_manager()
        assert unchanged is resumed
        check(invalid, unchanged, "restricted", "configuration_invalid")
        turn(invalid, "profile-recovered", "Reply fixture-recovered. Do not use tools or create Agents.")
        persisted = store.load_session(invalid["session_id"])
        controller.close()
        controller = ChatRuntimeController(store=store, codex_bin=codex_bin,
            registry_path=registry, hard_timeout_sec=180)
        persisted_resume, resumed = open_manager()
        assert persisted_resume["upstream_thread_id"] == persisted["upstream_thread_id"]
        check(persisted_resume, resumed, "restricted", "configuration_invalid")
        try:
            controller._start_adapter(agent_id="unsupported-fixture", work_dir=root,
                goal_id=MANAGER_AGENT_GOAL_ID, objective="fixture",
                manager_runtime={"runtime_profile": "trusted_owner"})
        except CodexChatAgentError as error:
            assert error.error_code == "manager_runtime_endpoint_unsupported"
        else:
            raise AssertionError("unsupported trusted endpoint was accepted")
        return {"ok": True, "host": "codex_app_server", "default_restricted": True,
            "equivalent_config_keeps_thread": True, "trusted_write_verified": True,
            "downgrade_rotates_thread": True, "unsubmitted_restart_retains_session_and_history": True,
            "persisted_restart_retains_thread": True, "external_audience_restricted": True, "invalid_config_restricted": True,
            "unsupported_trusted_endpoint_rejected": True}
    finally:
        controller.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-real-host", action="store_true")
    parser.add_argument("--codex-bin", default="codex")
    args = parser.parse_args()
    if not args.execute_real_host:
        parser.error("--execute-real-host is required; this smoke uses a real authenticated host")
    with tempfile.TemporaryDirectory(prefix="lxm-") as folder:
        print(json.dumps(qualify(Path(folder), args.codex_bin), sort_keys=True))


if __name__ == "__main__":
    main()
