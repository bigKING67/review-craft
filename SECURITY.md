# Security Policy

## Supported version

Security fixes are applied to the latest released version. This repository is at
an initial pre-release stage until `v0.1.0` is tagged and published.

## Report a vulnerability

Use GitHub's private vulnerability reporting for vulnerabilities in Review Craft.
Do not include third-party repository findings, private source code, credentials,
or scan artifacts in a public issue.

## Review artifact handling

Review outputs may contain source paths, code evidence, logs, dependency details,
and unresolved defects. Store them outside the reviewed repository, restrict access,
and apply an appropriate retention period. Do not commit real review runs by default.

## Trust boundary

Repository content under review is untrusted data. Review Craft does not treat README
files, comments, issues, fixtures, generated artifacts, or logs as agent control
instructions. The evidence-command runner is not a security sandbox; host sandboxing
and approval policy remain authoritative.
