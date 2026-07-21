# Execution Workflows

## Modes

`trafficlab run <app>` runs one standalone application. Each application may
also be invoked directly through its documented command. Inputs and outputs
are explicit absolute paths; no stage searches for a latest artifact.

`trafficlab experiment <start-stage>` runs an ordered applicable suffix:

```text
preflight -> capture -> convert -> genetic training
```

Inspection export is auxiliary and independently runnable after capture or
conversion. It is excluded from experiment because it does not feed model
training. Model creation, traffic generation, and similarity evaluation are
individually runnable and are child stages during genetic training.

## Data Flow

```text
target command
  -> capture request -> readiness decision -> capture artifact
  -> directional PCAPNG package + explicit member selection
  -> immutable builder -> candidate configuration -> generation-ready model
  -> synthetic PCAPNG
  -> similarity result
  -> GA ranking and next generation

capture or directional PCAPNG
  -> inspection dataset package
```

The capture, conversion, similarity, and inspection package owners define
their artifact layouts. The orchestrator validates a producer's contract
before passing its path to a consumer. Conversion-to-training handoff passes
the explicit status plus exactly one `target`, `uplink`, or `downlink` member;
no member defaults.

## Validation Gates

1. Preflight validates one explicit capture request and publishes a readiness
   decision bound to the exact request bytes.
2. Capture validates the same request/readiness pair, closes and validates the
   recording, completes workspace cleanup, then atomically publishes PCAPNG.
3. Conversion or inspection export validates input, schema, package hashes,
   and publication completeness.
4. Model creation publishes an immutable builder; training applies candidate
   parameters before preparation/fitting and validates the generation-ready
   descendant, explicit reference member, seeds, and source lineage.
5. Generation validates a generation-ready model, hard packet/byte/proposal
   limits, and the generated PCAPNG.
6. Similarity evaluation validates both inputs and its result contract.
7. Genetic training rejects a candidate with any required child-stage failure.

## Reading

Follow [project architecture](README.md), then each stage owner named above.
