# Protocol Lifecycle

This policy prevents compatibility code from becoming an indefinite second write path.
Existing artifacts are never silently rewritten or upgraded in place.

| Protocol | Current role | Last write line | Validation support | Earliest removal |
| --- | --- | --- | --- | --- |
| `review-craft.run.v5` | current review write format | current | current | not scheduled |
| `review-craft.run.v4` | sealed historical input only | v0.6.x | through v0.9.x | v1.0.0 and not before 2027-02-01 |
| `review-craft.run.v3` | sealed historical input only | v0.5.x | through v0.9.x | v1.0.0 and not before 2027-02-01 |
| `review-craft.fix-attempt.v1` | current fix-attempt lineage | current | current | not scheduled |
| `review-craft.fix.v1` | explicit legacy compatibility path | v0.7.x | through v0.9.x | v1.0.0 and not before 2027-02-01 |
| `review-craft.delivery.v2` | current attempt-delivery format | current | current | not scheduled |
| `review-craft.delivery.v1` | explicit legacy compatibility path | v0.7.x | through v0.9.x | v1.0.0 and not before 2027-02-01 |

Beginning with v0.8.0, the runtime must not create new `fix.v1` or `delivery.v1` artifacts.
It may validate already-created artifacts until the end of the v0.9 line. Any removal needs
at least 90 days of release-note notice and contract fixtures proving that current formats
cover the supported workflows.

There is no automatic migration:

- restart an unfinished run.v3 or run.v4 with current preflight;
- prepare a new fix-attempt lineage from the sealed review instead of converting fix.v1;
- regenerate delivery v2 from a verified current attempt instead of converting delivery.v1.

Compatibility validators are read boundaries. New fields, semantics, or evidence claims
must not be backported into frozen protocols.
