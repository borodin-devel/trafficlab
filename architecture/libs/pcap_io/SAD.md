# PCAPNG I/O Software Architecture Document

## Context, Goals, and Boundaries

PCAPNG metadata must survive capture, conversion, modelling, generation, and
comparison. This library owns structural parsing and writing plus reusable
metadata validation. Component owners define which packets and features apply.

## Structure and Data Flow

A streaming parser maps sections, interfaces, timestamp resolution/offset,
captured length, original length, and packet bytes to immutable records in file
order. Pure validators enforce an operation's supported link types and metadata.
A writer preserves or constructs explicit records without implicit sorting.

## Errors, Performance, Security, and Observability

Malformed blocks, inconsistent lengths, unsupported metadata, truncation beyond
declared bounds, and non-finite times are typed failures. Parsing uses bounded
buffers and explicit file-size/resource limits. Input is untrusted; parsers must
use maintained libraries where suitable and never execute embedded content.

## Testing, Decisions, Risks, and Limits

Fixtures cover multiple interfaces, resolutions, offsets, truncation, malformed
blocks, and round trips. Exact backend and byte-preservation guarantees for
unsupported optional blocks are unresolved. Raw 802.11/Radiotap is unsupported
unless a future component contract says otherwise.
