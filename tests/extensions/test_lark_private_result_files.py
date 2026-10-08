"""Real file snapshots and the original App's single-message result recovery."""

import json
import os
import asyncio
import subprocess
import sys
from pathlib import Path

import pytest
from test_lark_private_manager_returns import private_return, return_root  # noqa: F401
from test_native_steward_private import steward, finish  # noqa: F401

from loopx.capabilities.manager_context import acknowledge, _root, _write, POLICY_SCHEMA
from loopx.capabilities.manager_context.roundtrip import drain, report, reply_status

pytestmark = pytest.mark.skipif(os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"),
                                reason="result file snapshots require POSIX no-follow directory opens")


@pytest.fixture
def file_return(private_return):  # noqa: F811
    sender, session, turn, route, row, provider, transport, replies = private_return
    registry = sender.server.registry_path
    goal = next(g for g in json.loads(registry.read_text())["goals"] if g["id"] == route["goal_id"])
    workspace = Path(goal["repo"])
    workspace.mkdir(parents=True, exist_ok=True)
    ref = "result-fixture.bin"
    raw = b"\x00binary result\xff\x01"
    (workspace / ref).write_bytes(raw)
    original_runner = transport.runner
    uploads, downloads = [], []
    state = {"wrong_bytes": False, "download_available": True}

    def runner(args, cwd=None, timeout=None):
        if "files" in args and "create" in args:
            assert args[args.index("--profile") + 1] == "steward-app"
            assert args[args.index("--as") + 1] == "bot"
            if "--dry-run" not in args:
                uploads.append((Path(cwd) / args[args.index("--file") + 1]).read_bytes())
            return {"returncode": 0, "stdout": json.dumps({"ok": True, "data": {"file_key": "file_result_fixture"}})}
        if "+messages-resources-download" in args:
            downloads.append(args)
            if not state["download_available"]:
                return {"returncode": 1, "stdout": ""}
            raw_bytes = b"wrong bytes" if state["wrong_bytes"] else uploads[0]
            (Path(cwd) / args[args.index("--output") + 1]).write_bytes(raw_bytes)
            return {"returncode": 0, "stdout": json.dumps({"ok": True})}
        if "--attachment" in args:
            # The CLI merges its explicit keys into the post attachment zone.
            args = list(args)
            index = args.index("--content") + 1
            content = json.loads(args[index])
            content["files"] = [{"key": args[args.index("--attachment") + 1]}]
            args[index] = json.dumps(content)
        return original_runner(args, cwd, timeout)

    transport.runner = runner
    acknowledge(sender.root, route["goal_id"], route["agent_id"], route["request_id"], "adopt", "Return the requested file")
    def publish(refs=None, update_id=None):
        return report(sender.root, route["goal_id"], route["agent_id"], route["request_id"],
                      "conclusion", "The requested result is attached.", registry=registry,
                      attachment_refs=refs if refs is not None else [ref], update_id=update_id, workspace=workspace)
    return sender, session, turn, route, row, provider, transport, replies, workspace, raw, publish, uploads, downloads, state


def test_snapshot_is_returned_once_and_restart_only_reads_the_saved_message(file_return):
    sender, session, turn, route, row, provider, transport, replies, workspace, raw, publish, uploads, downloads, state = file_return
    assert publish()["ok"]
    (workspace / "result-fixture.bin").write_bytes(b"later workspace edit")
    state["download_available"] = False
    store = transport.core.controller.store
    drain(sender.root, sender.server.registry_path, store, sender)
    assert len(replies) == 1 and uploads == [raw]
    saved = _root(sender.root) / "replies" / route["request_id"] / "conclusion.delivery.json"
    assert json.loads(saved.read_text())["status"] == "verification_required"
    state["download_available"] = True
    drain(sender.root, sender.server.registry_path, store, sender)
    assert reply_status(sender.root, {"request_id": route["request_id"]})[0]["status"] == "delivered"
    assert len(replies) == 1 and uploads == [raw] and len(downloads) == 2
    assert drain(sender.root, sender.server.registry_path, store, sender) == 0
    with pytest.raises(ValueError, match="conflicting replacement"):
        publish()


def test_wrong_download_is_not_file_delivery_and_cannot_trigger_a_resend(file_return):
    sender, _, _, route, _, _, transport, replies, _, _, publish, uploads, _, state = file_return
    publish()
    state["wrong_bytes"] = True
    store = transport.core.controller.store
    drain(sender.root, sender.server.registry_path, store, sender)
    drain(sender.root, sender.server.registry_path, store, sender)
    assert reply_status(sender.root, {"request_id": route["request_id"]})[0]["status"] == "explicit_unverified"
    assert len(replies) == len(uploads) == 1


def test_file_intake_rejects_escape_symlink_special_files_and_revoked_return(file_return, tmp_path):
    sender, session, _, route, _, _, _, replies, workspace, _, publish, uploads, _, _ = file_return
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"private outside")
    (workspace / "linked.bin").symlink_to(outside)
    (workspace / "directory-link").symlink_to(tmp_path, target_is_directory=True)
    (workspace / "empty.bin").touch()
    os.mkfifo(workspace / "pipe")
    for ref in ["../outside.bin", str(outside), "linked.bin", "directory-link/outside.bin", "empty.bin", "pipe", "."]:
        with pytest.raises((ValueError, OSError)):
            publish([ref])
    assert not uploads and not replies
    _write(_root(sender.root) / "policy.json", {"schema_version": POLICY_SCHEMA, "sources": {}})
    publish()
    drain(sender.root, sender.server.registry_path, sender.server.lark_private_conversations.core.controller.store, sender)
    assert not uploads and not replies


def test_changed_snapshot_cannot_upload_or_send(file_return):
    from loopx.control_plane.collaboration.result_files import result_file_path

    sender, _, _, route, _, _, transport, replies, _, _, publish, uploads, _, _ = file_return
    publish()
    result = json.loads((_root(sender.root) / "replies" / route["request_id"] / "conclusion.json").read_text())
    result_file_path(sender.root, result["attachments"][0]).write_bytes(b"changed snapshot")
    drain(sender.root, sender.server.registry_path, transport.core.controller.store, sender)
    assert not uploads and not replies


def test_lost_resource_record_after_send_never_uploads_or_resends(file_return):
    sender, _, _, route, _, _, transport, replies, _, _, publish, uploads, _, state = file_return
    publish()
    state["download_available"] = False
    drain(sender.root, sender.server.registry_path, transport.core.controller.store, sender)
    for path in (_root(sender.root) / "lark-result-files").glob("*.json"):
        path.unlink()
    drain(sender.root, sender.server.registry_path, transport.core.controller.store, sender)
    assert reply_status(sender.root, {"request_id": route["request_id"]})[0]["status"] == "explicit_unverified"
    assert len(replies) == len(uploads) == 1


@pytest.mark.parametrize("surface", ["cli", "mcp"])
def test_worker_ingress_returns_the_file_through_the_original_app(file_return, surface):
    sender, _, _, route, _, _, transport, replies, workspace, raw, _, uploads, _, _ = file_return
    if surface == "cli":
        result = subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--format", "json",
            "--registry", str(sender.server.registry_path), "--runtime-root", str(sender.root),
            "manager-inbox", "report", "--goal-id", route["goal_id"], "--agent-id", route["agent_id"],
            "--request-id", route["request_id"], "--reply-text", "The report is attached.",
            "--attachment-ref", "result-fixture.bin"], cwd=workspace, text=True, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["ok"]
    else:
        mcp = pytest.importorskip("mcp")
        ClientSession, StdioServerParameters = mcp.ClientSession, mcp.StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def exercise():
            params = StdioServerParameters(command=sys.executable, args=["-m", "loopx.collaboration_mcp",
                "--registry", str(sender.server.registry_path), "--runtime-root", str(sender.root),
                "--goal-id", route["goal_id"], "--agent-id", route["agent_id"], "--workspace", str(workspace)])
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("return_result", {"request_id": route["request_id"],
                    "text": "The report is attached.", "attachment_refs": ["result-fixture.bin"]})
                assert not result.isError

        asyncio.run(exercise())
    drain(sender.root, sender.server.registry_path, transport.core.controller.store, sender)
    assert reply_status(sender.root, {"request_id": route["request_id"]})[0]["status"] == "delivered"
    assert len(replies) == 1 and uploads == [raw]
