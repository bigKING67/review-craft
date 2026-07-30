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
- the complete pre-change source fingerprint and file hashes;
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

If a verification command mutates source, Review Craft stops before later commands,
records them in `skippedCommands`, and returns `FAILED`. It does not revert the mutation.

Any source change after verification invalidates the result. Start a new verification
after further edits. Keep rollback instructions from the canonical decision available;
the runtime records evidence but does not perform rollback.
