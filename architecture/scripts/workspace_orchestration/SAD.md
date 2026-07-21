# Workspace Orchestration Software Architecture Document

## Role

`scripts/workspace_orchestration.sh` is the manually invoked command-line or
interactive entry point for workspace administration.

## Authority

It displays the selected action plan and required privilege before it sequences
backup, setup, verify, or rollback. It may invoke the privileged setup and
rollback scripts after operator confirmation.

Normal Trafficlab execution and automated tests never call orchestration.

## Behavior

Orchestration passes an explicit workspace identity and argument vector to each
script. It does not hide durable configuration changes, suppress script
failures, or substitute a global cleanup for manifest-scoped rollback.

## Tests

Automated tests verify command selection, argument construction, confirmation
requirements, and failure propagation with fake script executables. They never
invoke privileged operations.

## Architecture Qualities and Limits

Action selection and argument construction are pure; terminal confirmation and
child processes form the shell. It runs one bounded operator workflow at a time,
logs displayed plan/decisions/status, and never captures child failure as success.
Deployment is manual Bash. Risks are confused-deputy privilege, ambiguous
workspace identity, and confirmation bypass; explicit identities, displayed
authority, and mandatory confirmation guard them.
