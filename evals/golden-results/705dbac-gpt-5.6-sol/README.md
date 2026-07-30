# 705dbac / GPT-5.6-Sol Golden Snapshot

This directory contains the sanitized, content-bound projection of one matched 12+12
real-host evaluation completed on 2026-07-29. It does not contain prompts, fixture copies,
raw host output, logs, adapter argv, provider URLs, credentials, or machine-local paths.

## Bound environment

- source revision: `705dbac96bf8ff3dbd585d330b8cd5bc251c9489`;
- Review Craft version: `0.3.0`;
- host: `codex-cli 0.146.0`;
- model and reasoning: `gpt-5.6-sol`, `xhigh`;
- provider protocol: Codex Responses API;
- suite: all 12 controlled fixtures, six positive and six negative;
- adjudication: `AGENT_ASSISTED`, protocol `matched-semantic-adjudication-v1`.

This snapshot was produced by Codex adapter `0.2.0` and eval run v2, before structured usage
sidecars were introduced. Its missing usage object means token and tool-call cost is
unavailable in the canonical snapshot, not zero. The recorded duration remains structured.

## Result

Both treatments reached 100% semantic seeded-issue recall, 100% semantic finding
precision, and 0% clean-negative false-positive rate. Review Craft reached 100% semantic
decision accuracy; the ordinary-prompt baseline reached 75%. Review Craft took 927,819 ms
versus 447,243 ms for the baseline in this single matched run.

The result supports a narrow claim: on this controlled fixture suite and matched host,
Review Craft preserved detection quality while making more proportionate remediation
decisions. It does not establish universal superiority, independent human adjudication,
stable cost ratios, equivalence to native diff review, or superiority over Codex Security.

## Integrity and verification

- canonical snapshot content hash: `4103118a3f8287a8d5be5e15ad60e5b4de711284641e24ac6d76c8abb5573364`;
- file SHA-256: `b73264d7c9b0c47a934ac2fd4a9afbb509a6432988db02ee852a893c939a9d4c`.

`scripts/validate.py` validates the snapshot schema and recomputes its canonical content
hash. The snapshot binds the external run, adjudication, and comparison hashes, but it is
not a self-contained replacement for those raw artifacts or an authenticity signature.
