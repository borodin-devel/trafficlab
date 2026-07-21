# Back Up System Configuration Script

## Purpose and Interface

`backup_system_configuration.sh` reads only durable values explicitly named by
an approved setup plan, records restoration data, and performs no mutation.

## Inputs, Outputs, Dependencies, and Context

Input is explicit workspace identity and named values. Output is scoped backup
information for setup manifest and rollback. Public values may appear in the
operator diff. Protected values are stored only in a private mode-`0600` file;
the ordinary manifest contains its relative reference and digest. Manual
authority may be required to read or restore them. It depends on no normal
Trafficlab application.

Related operator boundaries are [setup](../setup_capture_workspace/README.md)
and [rollback](../rollback_capture_workspace/README.md).

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Roadmap](ROADMAP.md)
