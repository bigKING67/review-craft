# 13ad6f2 / GPT-5.6-Sol Four-Arm Ablation

This directory contains the sanitized, content-bound projection of one complete matched
four-arm evaluation over 12 controlled fixtures on 2026-08-07. It does not contain raw
prompts, fixture copies, host output, logs, tool output, adapter argv, provider URLs,
credentials, or machine-local paths.

This v1 result is frozen historical evidence. `ADVERSARIAL_PROMPT` is retained only as a
negative control because it regressed against ordinary review in this run. Neither it nor
the combined `RISK_LENS_ADVERSARIAL` treatment is an active prompt, default experiment arm,
or product recommendation. The active v2 protocol tests `RISK_LENS_REVIEW` without generic
adversarial wording so the risk lens can be evaluated independently.

## Bound environment

- source revision: `13ad6f29562f3371444d0458c8f901d24d746af8`;
- Review Craft version: `0.6.1`;
- host: `codex-cli 0.147.0`;
- model and reasoning: `gpt-5.6-sol`, `xhigh`;
- provider protocol: Codex Responses API with WebSockets disabled;
- suite: six matched positive/negative pairs, 12 cases per arm;
- adjudication: `AGENT_ASSISTED`, protocol `isolated-blind-full-v1`;
- source state: clean and stable for all four arms;
- usage: complete for all 48 cases;
- verifier boundary: zero executions in arms A-C and 12 successful executions in arm D.

## Result

| Arm | Semantic decision accuracy | Seeded recall | Finding precision | False-positive rate | Evidence adequacy | Falsification adequacy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Ordinary prompt | 66.67% | 83.33% | 80.00% | 50.00% | 58.33% | 45.45% |
| Adversarial prompt | 50.00% | 66.67% | 72.73% | 66.67% | 58.33% | 41.67% |
| Risk-lens adversarial | 83.33% | 66.67% | 100.00% | 0.00% | 83.33% | 66.67% |
| Review Craft evidence loop | 91.67% | 100.00% | 100.00% | 0.00% | 100.00% | 100.00% |

Against the ordinary prompt, the evidence loop observed +25.00 percentage points semantic
decision accuracy, +16.67 points seeded recall, +20.00 points finding precision, and
50.00 points lower false-positive rate. It used 1.3138x duration, 1.7643x total tokens, and
1.5405x tool calls.

Against the risk-lens adversarial arm, the evidence loop observed +8.34 percentage points
semantic decision accuracy and +33.33 points seeded recall while preserving 100% precision
and a 0% false-positive rate. It used 1.4067x duration, 1.8945x total tokens, and 1.5833x
tool calls.

The ordinary adversarial instruction alone did not improve this run: it reduced semantic
decision accuracy and seeded recall while increasing the false-positive rate. Adding the
specified risk lens improved precision, restraint, and decision accuracy, but still missed
two seeded issues. The evidence loop closed those misses with materially higher cost.

## Attempt and adjudication lineage

An earlier pre-registered full-suite attempt ended `PARTIAL` after one provider stream
failure. It was preserved outside the repository and was not adjudicated, compared, or
exported. This snapshot comes from the next independent attempt; it is not a repaired or
completed projection of the partial attempt.

The first treatment-blinded adjudicator output for this complete attempt was rejected by
the canonical validator because one outcome contradicted the corresponding
`findingDetected` value. That invalid output was preserved outside the repository and was
not used. A fresh ephemeral adjudicator reran the same blinded bundle with the existing
outcome consistency rules stated explicitly; all 48 entries then passed canonical
unblinding and validation. No treatment mapping was supplied, but this retry remains an
adjudicator-process limitation.

## Interpretation boundary

This single bound run supports a narrow observation: for these fixtures and this exact
host, model, reasoning profile, evidence loop, verifier, and adjudication protocol, the
Review Craft evidence loop produced the strongest semantic result and consumed the most
resources. It does not establish rerun stability, cross-model or cross-repository
generality, independent human agreement, or universal causal superiority. Outputs and
tool traces may reveal intervention characteristics even though treatment labels and raw
prompts were withheld.

The result supports keeping the evidence loop as an explicit high-assurance review profile
candidate. It does not yet support enabling the loop for every ordinary review by default.

## Integrity and verification

- canonical snapshot content hash: `c060c3b1a73cb7295dcc87b279aef299d73421656443a0d0a9426c7e12504eb1`;
- file SHA-256: `c129dcb1c053c9bf46b08c2be437ac543fa8d96141ed8106dc5fc9a1afd61358`;
- ablation content hash: `e8a7dbe9fdb4e3bd5b5c0b2a09f93ed9a62dc647b5d52cf1337fcb1f91284762`;
- adjudication result content hash: `d118f455c63c2af5bbf54ee7b80454e04fae32d5e3ae74f9e90ce572242be319`;
- comparison content hash: `da3311785c50f981eaa3a39506bd29b29252c604c73a97093c6706e24ff0015c`.

`scripts/validate.py` validates the snapshot schema and recomputes its canonical content
hash. The snapshot binds the external run, adjudication, and comparison artifacts but is
not an authenticity signature or a replacement for those raw external artifacts.
