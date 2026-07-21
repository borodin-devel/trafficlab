# Neighbour-Transition Configuration

Configuration is a nonempty list of joint states over transformed IAT
`[a,b)` (`b` may be infinity) and inclusive original-length bounds 60–1514.
It must partition the complete Cartesian domain exactly. Canonical order is
ascending bounds with infinity last. Exact TOML syntax/default partition and
maximum state count are unresolved; no implementation may guess them.
