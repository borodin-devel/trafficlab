# Workspace Orchestration Script

## Purpose and Interface

`workspace_orchestration.sh` is the manual CLI or interactive entry point that
displays and sequences backup, setup, verify, or rollback after confirmation.

## Inputs, Outputs, Dependencies, and Context

Input is explicit workspace identity and action. Output is the selected child
script result without suppression. It may cross a privilege boundary only after
displaying plan and receiving operator confirmation.

Selectable actions are [backup](../backup_system_configuration/README.md),
[setup](../setup_capture_workspace/README.md),
[verification](../verify_capture_workspace/README.md), and
[rollback](../rollback_capture_workspace/README.md).

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Roadmap](ROADMAP.md)
