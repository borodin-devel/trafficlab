# 00 Preflight Configuration

The versioned template is
[`configs/00_preflight.toml`](../../configs/00_preflight.toml). A working copy,
when wanted, is `.configs/00_preflight.toml`.

No preflight settings are defined yet. The file is valid empty TOML so its
identity is established without guessing readiness requirements.

When a setting is added, this document must define its TOML type, accepted
values, built-in default, and matching command-line override. The setting must
belong to the [00 preflight application](../../architecture/apps/00_preflight/README.md)
and follow the shared
[application configuration](../../architecture/CONFIGURATION.md) rules.
