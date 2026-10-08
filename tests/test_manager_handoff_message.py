"""An inbox receipt is not receiver execution; explain the delivered brief."""

import pytest

from loopx.capabilities.manager_context.execution import handoff_message


RECEIPT = {"goal_id": "research", "agent_id": "worker", "status": "delivered"}
BRIEF = {
    "schema_version": "collaboration_brief_v0",
    "purpose": "Check the reported failure and return a verified fix.",
    "context": "This worker owns the affected module. Keep the owner's latest correction.",
    "constraints": ["Preserve existing permissions."],
    "inputs": [],
    "acceptance": ["Reproduce the failure, then verify the fix."],
    "return_requirement": "Return the result to this conversation.",
}


@pytest.mark.parametrize(
    "execution",
    [
        {"submitted": False},
        {"submitted": False, "reason": "execution_not_launchable"},
    ],
)
def test_receipt_does_not_establish_receiver_execution(execution):
    message = handoff_message(RECEIPT, execution)
    assert "research / worker" in message
    assert "尚未启动" not in message
    assert "是否已开始处理尚未核实" in message
    assert "处理结论" in message and "本次对话" in message


def test_ack_explains_existing_brief_without_changing_it():
    original = {**BRIEF, "constraints": list(BRIEF["constraints"])}
    message = handoff_message(RECEIPT, {"submitted": False}, brief=BRIEF)
    for field in ("purpose", "context", "return_requirement"):
        assert BRIEF[field] in message
    assert BRIEF["constraints"][0] in message
    assert BRIEF["acceptance"][0] in message
    assert BRIEF == original


def test_full_brief_is_not_dumped_into_ack():
    long_brief = {**BRIEF, "context": "c" * 6000, "constraints": ["x" * 1000] * 12}
    message = handoff_message(RECEIPT, {"submitted": False}, brief=long_brief)
    assert len(message) < 2000
    assert "完整简报已投递" in message
    assert long_brief["context"] == "c" * 6000
    assert len(long_brief["constraints"]) == 12


def test_submission_is_separate_from_completion():
    message = handoff_message(
        RECEIPT, {"submitted": True, "status": "prepared"}, brief=BRIEF
    )
    assert "已提交受控执行" in message
    assert "受理不代表完成" in message
    assert "是否已开始处理尚未核实" not in message
