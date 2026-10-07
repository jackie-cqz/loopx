import assert from "node:assert/strict";
import test from "node:test";
import {normalizeExploreResultAttachment} from "../../loopx/control_plane/capabilities/explore_result_writeback.ts";

const attachment = {
  schema_version: "explore_result_attachment_v0", node_id: "tail-bound",
  question: "Does a finite prefix establish a tail bound?",
  applicability: "Finite prefix only", input_revision: "fixture-v1",
  observation: "A divergent tail shares the prefix.",
  interpretation: "Require a uniform estimate.", status: "refuted",
  evidence_refs: ["validation:prefix", "validation:tail"],
};

test("two explicit sources normalize and coalesce equivalent scoped evidence", () => {
  const other = {...attachment, observation: ` ${attachment.observation} `,
    evidence_refs: ["validation:tail", "validation:prefix", "validation:tail"]};
  assert.deepEqual(normalizeExploreResultAttachment({attachment, other_attachment: other}), attachment);
});

test("neither a conflicting nor malformed secondary source can be silently preferred", () => {
  for (const other of [
    {...attachment, applicability: "Uniform tail bound"},
    {...attachment, interpretation: "Transfer the bound unconditionally."},
    {...attachment, evidence_refs: ["validation:other"]},
  ]) {
    assert.throws(() => normalizeExploreResultAttachment({attachment, other_attachment: other}), /sources conflict/);
  }
  for (const other of [null, [], {...attachment, evidence_refs: []}]) {
    assert.throws(() => normalizeExploreResultAttachment({attachment, other_attachment: other}));
  }
  assert.throws(() => normalizeExploreResultAttachment({attachment: null}));
});
