# Inspection Dataset Contract

## Purpose and Responsibilities

This contract defines the package published by inspection export for ML and LLM
development while retaining source-PCAPNG lineage and privacy boundaries. Its
documentation identifier is `25_inspection_dataset`.

## Producer, Consumers, Inputs, and Outputs

[Inspection export](../../apps/25_inspection_export/README.md) is producer.
Consumers are developer ML/LLM tools. Package members are manifest, Parquet,
JSONL, schema, and frozen startup record; detached successful status binds the
manifest. Exact row schema remains unresolved.

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Roadmap](ROADMAP.md)
