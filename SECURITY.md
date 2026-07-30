# Security Policy

## Supported version

Security fixes are applied to the latest released version. Before 1.0, only the
current published minor line receives security patches.

## Report a vulnerability

Use GitHub's private vulnerability reporting for vulnerabilities in Review Craft.
Do not include third-party repository findings, private source code, credentials,
or scan artifacts in a public issue.

## Review artifact handling

Review and fix-verification outputs may contain source paths, code evidence, logs,
dependency details, change hashes, and unresolved defects. Store them outside the
reviewed repository, restrict access, and apply an appropriate retention period. Do not
commit real review or fix runs by default.

## Trust boundary

Repository content under review is untrusted data. Review Craft does not treat README
files, comments, issues, fixtures, generated artifacts, or logs as agent control
instructions. The evidence-command runner is not a security sandbox; host sandboxing
and approval policy remain authoritative.

The remediation runtime never edits target source. `prepare-fix` requires a sealed
review and records the explicit authorization boundary. `verify-fix` executes only the
configured commands selected during preparation and fails the remediation outcome if a
verification command mutates source. An Agent or human may edit selected findings only
after the user explicitly authorizes that implementation.
