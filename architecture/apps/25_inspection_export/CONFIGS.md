# Inspection Export Configuration

## Shared Selection

The application follows [shared configuration](../../CONFIGURATION.md). No
versioned template or application-specific setting is currently defined.

## Fixed Version-1 Behavior

Version 1 exports approved timestamp, length, interface, direction-when-known,
Ethernet/IP classification, and selected TCP flags. Payload bytes and addresses
are excluded. Parquet and JSONL represent the same approved logical fields.

## Unresolved Decisions

Exact column names, Arrow types, timestamp unit, row-group size, compression,
optional protocol fields, maximum JSONL size, and CLI overrides are unresolved.
They must be defined in a versioned schema before implementation.
