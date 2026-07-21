# Implementation Structure

## Component Classification

Standalone applications are `preflight`, `capture`, `convert`,
`inspection_export`, `genetic_training`, `model_creation`,
`traffic_generation`, `similarity_evaluation`, and `trafficlab`. They own
commands, configuration resolution, input/output validation, run directories,
and process boundaries.

Shared libraries are [artifact I/O](../libs/artifact_io/README.md),
[PCAPNG I/O](../libs/pcap_io/README.md),
[configuration](../libs/configuration/README.md),
[lineage](../libs/lineage/README.md),
[resource management](../libs/resource_management/README.md), and
[observability](../libs/observability/README.md). They expose deterministic
value and file-validation interfaces; they never select pipeline stages.

Replaceable modules are traffic models, genetic strategies, and similarity
methods. Registration resolves a stable name to one implementation. Each
module owner defines its schema, validation, algorithm, deterministic seed
handling, and version identity.

## Proposed Source and Test Tree

```text
src/trafficlab/
  apps/{preflight,capture,convert,inspection_export,genetic_training,
        model_creation,traffic_generation,similarity_evaluation,trafficlab}/
  libs/{artifact_io,pcap_io,configuration,lineage,resource_management,observability}/
  traffic_models/<model_name>/
  genetic_models/<model_name>/
  similarity_methods/<method_name>/
tests/
  apps/<application_name>/
  libs/<library_name>/
  traffic_models/<model_name>/
  genetic_models/<model_name>/
  similarity_methods/<method_name>/
  integration/
configs/
docs/
architecture/
```

An application's imperative shell calls functional-core library functions.
Functional cores accept values and bytes, return values and typed failures,
and have no file, subprocess, network, terminal, clock, random, or privilege
side effect. Shells inject those boundaries and record effective seeds.

## File Interfaces

PCAPNG remains canonical packet interchange. Contract packages use a manifest,
declared schema version, relative artifact names, SHA-256 hashes for every
non-manifest member, input lineage, and `launch.toml`; detached successful
status hashes the final manifest. Single-file artifacts also use detached
status rather than embedding their own digest. ML tabular data uses Apache Parquet with
Apache Arrow schema metadata; LLM inspection uses UTF-8 JSON Lines (JSONL).
Neither derived format replaces the source PCAPNG.

## Reading

Follow [project architecture](README.md), [configuration](../CONFIGURATION.md),
and each application or module owner before changing a boundary.
