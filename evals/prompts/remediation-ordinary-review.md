Perform one bounded, read-only engineering review of the repository in the current working
directory. The repository may contain a real defect or intentionally correct code. Do not
assume either outcome, modify files, install dependencies, access the network, or inspect
unrelated paths.

Use this project-specific risk lens only as attention guidance; it is not evidence:

{{RISK_LENS_JSON}}

Return only the JSON object required by the output schema. Report at most one candidate.
When there is no concrete problem, set `findingDetected` to false and choose a proportionate
KEEP, DEFER, MEASURE, or DOCUMENT decision. State the hypothesis plus an observable confirm
condition and falsify condition even when the concern is rejected. Use repository-relative
locations.
