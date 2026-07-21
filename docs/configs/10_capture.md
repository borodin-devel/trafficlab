# 10 Capture Configuration

The versioned template is
[`configs/10_capture.toml`](../../configs/10_capture.toml). A working copy,
when wanted, is `.configs/10_capture.toml`.

No capture settings are defined yet. The file is valid empty TOML so its
identity is established without guessing capture behavior.

When a setting is added, this document must define its TOML type, accepted
values, built-in default, and matching command-line override. The setting must
belong to the [10 capture application](../../architecture/apps/10_capture/README.md)
and follow the shared
[application configuration](../../architecture/CONFIGURATION.md) rules.
