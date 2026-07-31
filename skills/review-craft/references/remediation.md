# Remediation and Fix Verification

Use this workflow only after a canonical review is sealed and the user explicitly
selects findings to change. The runtime binds evidence and verifies the result; it
never edits the target source itself.

## 1. Prepare before editing

The target must still match the sealed review. Select explicit finding IDs and the
smallest configured command set that can verify them:

```text
python3 <skill-root>/scripts/review_craft.py prepare-fix \
  --run-dir <sealed-run-dir> \
  --finding RC-FINDING-001 \
  --command test
```

Use `--all-actionable` only when the user authorized every actionable finding.
Use `--all-commands` only when running every configured command is proportionate.
`KEEP`, `DEFER`, and `MEASURE` outcomes are not actionable fix selections.

Preparation writes a `review-craft.fix.v1` session outside the target and records:

- the sealed review manifest and selected finding/decision hashes;
- the complete pre-change fingerprint and file hashes for the sealed review's configured
  source projection; excluded and out-of-scope contents remain outside that claim;
- verification criteria and the selected command configuration;
- `EXPLICIT_USER_REQUIRED` as the source-mutation authorization boundary.

Preparation is read-only. If it changes the target, treat that as a defect.

## 2. Apply only the authorized change

After preparation, use the host's normal editing tools to implement only the selected
findings. Preserve unrelated dirty work. Do not let a configured command or the
Review Craft runtime act as a source-editing mechanism.

For `DELETE` or `REWRITE`, follow the migration, compatibility, rollback, and
verification gates already bound in the canonical decision. Stop if those details are
not executable. A decision in a report is not itself authorization to modify source.

## 3. Create an assessment

Create a JSON assessment outside the target:

```json
{
  "documentType": "review-craft.fix-assessment",
  "schemaVersion": "review-craft.fix.v1",
  "kind": "AGENT_ASSISTED",
  "assessor": "Codex",
  "assessedAt": "2026-07-30T03:00:00Z",
  "findings": [
    {
      "findingId": "RC-FINDING-001",
      "status": "RESOLVED",
      "rationale": "The corrected branch now preserves the validated invariant.",
      "evidenceRefs": ["change:src/example.py", "command:test"]
    }
  ],
  "remainingRisks": []
}
```

Assessment statuses are `RESOLVED`, `LIKELY_RESOLVED`, `PARTIAL`, `UNRESOLVED`,
`REGRESSED`, and `NOT_APPLICABLE`. Assess every selected finding exactly once.

Evidence references are deliberately narrow:

- `change:<repository-relative-path>` must name a file changed from the baseline;
- `command:<configured-name>` must name a command executed by this verification;
- `manual:<description>` is accepted only for a declared `HUMAN` assessment.

An `AUTOMATED` resolved result requires command evidence. Source change alone is not
runtime or behavioral proof. `AGENT_ASSISTED` is not independent human validation.

## 4. Verify and revalidate

Run the selected commands and bind the assessment:

```text
python3 <skill-root>/scripts/review_craft.py verify-fix \
  --fix-dir <fix-dir> \
  --assessment <assessment.json>

python3 <skill-root>/scripts/review_craft.py validate-fix \
  --fix-dir <fix-dir>
```

The status is:

- `VERIFIED`: source changed, all selected commands passed, and every result is
  `RESOLVED` or `NOT_APPLICABLE`;
- `PARTIAL`: evidence is valid but at least one result remains likely or partial;
- `FAILED`: a command failed, timed out, mutated source, or a result is unresolved or
  regressed;
- `NO_CHANGES`: the source fingerprint still matches the prepared baseline.

`verify-fix` exits `0`, `3`, `4`, or `5` for those statuses respectively. Contract or
input errors exit `2`. `validate-fix` validates artifact integrity, not remediation
success; a content-valid `FAILED` result therefore validates successfully while still
remaining a failed remediation.

Every fix command receipt is also bound to the selected command configuration stored
by `prepare-fix`. Its name, argv, and cwd must match exactly. Recomputing receipt IDs,
output filenames, or hashes after changing argv/cwd is tampering and makes
`validate-fix` fail with exit `2`.
When a selected command declares semantic claims or artifacts, fix verification preserves
those receipt fields and their content identity. An unverified claim, rejected artifact,
or copied-artifact hash mismatch fails verification even if the subprocess exit code is
zero.

`verify-fix` owns an exclusive session lock from the pre-attempt freshness check through
command execution, assessment binding, terminal artifact creation, and validation. Exactly
one concurrent caller can produce the terminal result; another caller waits for the lock
and then exits `2` because the session is already completed. Sequential repeat calls are
rejected for the same reason.

A fix session is deliberately single-attempt and fail-closed:

- both `fix-assessment.json` and `fix-verification.json` make the session completed and
  read-only;
- only one terminal artifact means the session is incomplete and cannot be resumed;
- command receipts without both terminal artifacts mean an interrupted attempt and cannot
  be reused;
- the receipt ledger must contain exactly the receipts referenced by the terminal
  verification; any orphan or missing receipt makes `validate-fix` fail.

There is no automatic resume or attempt-history protocol in `review-craft.fix.v1`. To rerun
after a crash, rejected attempt, or further source edit, explicitly run `prepare-fix` again
and use the newly created fix session. Preserve the old session as failure evidence or
remove it only under the host's normal artifact-retention policy.

If a verification command mutates source, Review Craft stops before later commands,
records them in `skippedCommands`, and returns `FAILED`. It does not revert the mutation.

Any source change after verification invalidates the result. Prepare a new fix session
after further edits. Keep rollback instructions from the canonical decision available;
the runtime records evidence but does not perform rollback.

## Post-delivery attestation

Fix verification intentionally stops before Git delivery. Do not rewrite an old
`fix-verification.json` after commit, push, CI, or release state changes. After the host has
committed a `VERIFIED` fix, create a separate delivery artifact:

```bash
python3 <skill-root>/scripts/review_craft.py verify-delivery \
  --fix-dir <fix-dir>

python3 <skill-root>/scripts/review_craft.py validate-delivery \
  --delivery-dir <delivery-dir>
```

The local-only result is `PARTIAL`: it proves that the target is a clean Git checkout and
that its current source fingerprint still matches the captured fix verification, while
leaving remote delivery unknown. It does not run a network command.

Use explicit network-backed proof only when the user authorizes it:

```bash
python3 <skill-root>/scripts/review_craft.py verify-delivery \
  --fix-dir <fix-dir> \
  --verify-push \
  --github-run <github-actions-run-id>
```

`--verify-push` runs `git ls-remote` without a shell and verifies that the configured
remote branch SHA equals local `HEAD`. `--github-run` runs `gh run view` without a shell
and binds the run ID, workflow, head SHA, status, conclusion, URL, and normalized job list.
Failed, incomplete, unreadable, or mismatched requested proof produces a valid `FAILED`
attestation. `verify-delivery` exits `0`, `3`, or `4` for `VERIFIED`, `PARTIAL`, or `FAILED`;
contract and input errors exit `2`.

Every attempt creates a new content-bound directory under the system temporary directory
or `--output-root`:

```text
<delivery-dir>/
├── delivery-attestation.json
├── delivery-state.json
├── source/
│   ├── fix-plan.json
│   ├── fix-assessment.json
│   ├── fix-verification.json
│   └── source-configuration.json
└── evidence/
    ├── git-remote.json             # only when executed
    └── github-actions-run.json     # only when executed
```

Raw stdout and stderr are not stored. Command argv, duration, exit state, byte counts, and
output hashes remain in normalized evidence. GitHub Release and npm registry adapters are
not implemented in `delivery.v1`; both stages remain `NOT_VERIFIED`. `validate-delivery`
uses only copied artifacts and therefore continues to work after the original fix directory
or target checkout is unavailable.
