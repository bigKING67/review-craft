Apply the smallest source change justified by the supplied review candidate. Work only in
the current repository, modify only the allowed paths, do not install dependencies, do not
access the network, and do not inspect unrelated paths. Preserve the documented behavior,
compatibility constraints, and implementation constraints. If the candidate is incorrect,
insufficient, already resolved, or cannot be fixed safely within the allowed paths, make no
source change.

Allowed paths:

{{ALLOWED_PATHS_JSON}}

Review candidate:

{{REVIEW_JSON}}

External oracle evidence, when the evidence gate supplied any:

{{ORACLE_JSON}}

After editing, return only the JSON object required by the output schema. `claimedPaths`
must list only paths you actually changed. Use `NO_CHANGE` when you did not edit source.
