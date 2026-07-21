# Traffic Generation Configuration

## Interface and Ownership

The command is `traffic_generation --model-file PATH --output PATH
--max-packets N --max-output-bytes N --max-proposals N`. Each limit is a
required positive integer no greater than `9223372036854775807`; Booleans are
invalid and no default or environment fallback exists. Model-specific
`CONFIGS.md` owns seeds, stopping, parameters, and model constraints.

A packet-count stop must not exceed `max_packets`; a window-count stop must
have `window_count * max_events_per_window <= max_packets`. Proposal accounting
includes accepted, rejected, resampled, and first-beyond-duration proposals.
The complete PCAPNG, including headers and metadata, must remain within
`max_output_bytes`.

## Unresolved Rendering Decisions

The fixed Ethernet template, interface metadata, timestamp resolution,
development randomized-source registration, and any additional application CLI
overrides are unresolved. They must be documented and fixture-tested before
generation implementation; no implicit defaults may be invented.
