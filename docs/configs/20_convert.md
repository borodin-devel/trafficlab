# 20 Convert Configuration

The versioned template is
[`configs/20_convert.toml`](../../configs/20_convert.toml). A working copy,
when wanted, is `.configs/20_convert.toml`.

No conversion settings are defined yet. The file is valid empty TOML so its
identity is established without guessing conversion behavior or the reference
profile's field serialization.

When a setting is added, this document must define its TOML type, accepted
values, built-in default, and matching command-line override. The setting must
belong to the [20 convert application](../../architecture/apps/20_convert/README.md)
and follow the shared
[application configuration](../../architecture/CONFIGURATION.md) rules.
