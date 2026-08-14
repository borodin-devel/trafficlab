# Reference Observation Window Design

## Problem

Trafficlab currently has three unrelated notions of duration: a configured
multiscale horizon, trial/final generation duration limits, and the actual time
span of the reference packets. The documents do not say which duration defines
the scientific comparison. Two implementations could therefore crop different
events or compare a short generated trace with a longer reference.

The MVP needs one explicit observation window shared by model fitting,
generation, genetic evaluation, and final comparison. It must remain simple for
one researcher and must not add capture metadata or configuration.

## Decision

Derive one observation-window duration from the reference trace:

\[
W=t_n-t_1.
\]

The reference must contain at least two packets, all timestamps must be finite
and nondecreasing, and \(W\) must be finite and strictly positive. Normalize the
reference timestamps once:

\[
t'_i=t_i-t_1.
\]

The normalized reference therefore starts at `0`, ends at `W`, and includes both
endpoints. Silence before the first reference packet and after the last reference
packet is intentionally outside the MVP observation window.

This same complete `W` is used for every enabled model family, every genetic
trial seed, final generation, and all similarity methods. Trafficlab does not use
a shorter genetic trial window.

## Trace Preparation

The scientific boundary prepares traces before fitting or evaluation:

1. validate and normalize the reference, deriving `W`;
2. shift the generated trace so its first packet is at `0`;
3. retain reference and generated events whose normalized timestamps lie in the
   closed interval `[0, W]`;
4. discard generated events after `W` before any similarity method receives the
   trace.

An event exactly at `W` is included. A generated trace may naturally have its
last packet before `W`; the remaining interval is valid trailing silence. A
generated trace must be nonempty before it can be shifted and must still satisfy
every enabled method's minimum sample requirement after cropping.

Model-generated traces already place their first packet at `0`, so their shift
is normally a no-op. Applying the same rule at the comparison boundary also
makes checked-in or independently produced PCAPNG fixtures unambiguous.

This preparation uses ordinary event sequences plus the explicit scalar `W`.
The MVP does not introduce a trace wrapper class.

## Interfaces and Artifacts

Pass the derived duration explicitly through the research interfaces:

```text
generate(fitted model, seed, observation_window, limits) -> canonical trace
evaluate(reference, generated, observation_window, settings) -> score, diagnostics
```

`best_model.json` stores `observation_window_seconds` so `trafficlab generate`
can work without reopening the reference capture. `similarity.json` stores the
same value and every similarity method reports it in diagnostics. The existing
reference hash continues to identify the input from which the value was derived.

No new artifact is added. Strict `capture.json` remains exactly the interface and
target MAC. PCAPNG files remain packet containers and do not need separate window
metadata.

## Configuration

Remove the independent multiscale horizon and trial/final generation-duration
settings. They duplicate or can contradict the derived reference window.

Keep configurable multiscale widths. Once the reference is parsed, every width
must be finite, positive, unique, strictly increasing, and no larger than `W`.
The existing direction-bin cell cap is then calculated from `W`.

Keep packet-count, output-size, and wall-time limits as reliability guards. They
bound resource use but never define a shorter scientific trace.

## Model Generation and Failure Semantics

Every model simulates the entire closed interval `[0, W]`:

- emit an event whose timestamp is at most `W`;
- do not emit an event whose timestamp is greater than `W`;
- finish normally when simulation establishes that the next event would occur
  after `W`.

A last packet well before `W` is not truncation when the next simulated event is
after the window. By contrast, reaching a packet-count, output-size, or wall-time
guard before the model completes the window is an incomplete generation:

- during genetic evaluation, it is an invalid candidate with a direct reason and
  documented worst fitness;
- during final `trafficlab generate`, it is a stage error and no incomplete final
  output is published.

Trial and final seeds remain distinct. Only seeds and reliability budgets may
differ; the observation window never differs.

## Similarity Semantics

All four methods consume the same normalized, cropped events and explicit `W`:

- frame-size KS uses frame lengths of packets in `[0, W]`;
- IAT KS uses intervals between packets in `[0, W]`;
- autocorrelation uses IAT and frame-length sequences in `[0, W]`;
- multiscale rate uses `W` directly as its horizon.

Frame-size KS, IAT KS, and autocorrelation remain sample-based and do not invent
boundary samples. Multiscale rate represents a generated trace that ends early
with trailing zero bins, so it is the existing component that measures missing
packet/byte volume during the rest of the window.

For multiscale width `h`, continue to use

\[
B=\lceil W/h\rceil
\]

left-closed/right-open bins, with timestamp `W` included in the last bin. The
existing outbound-before-inbound layout, formulas, weights, and
\(2\sum_h B_h\le C_{max}\) cap do not change.

## Validation and Tests

Focused unit tests prove:

- reference times `[10, 11, 13]` produce `W = 3` and normalized times
  `[0, 1, 3]`;
- fewer than two reference packets, nonfinite/decreasing timestamps, and zero
  reference span are rejected;
- a generated event exactly at `W` is retained and an event after `W` is removed;
- all four similarity methods receive and report the same `W`;
- multiscale width validation and cell counts use the derived `W`;
- a naturally early last packet is valid and produces trailing multiscale zeros;
- every model retains a scripted event at `W`, excludes later events, and
  completes the same window for every seed;
- reaching a reliability guard before completion invalidates a candidate, while
  final generation fails without publishing incomplete output;
- saved and reloaded `best_model.json` preserves
  `observation_window_seconds` exactly.

In-process integration tests run the checked-in reference through normalization,
all three competing model families, generation, and all four similarity methods.
They assert that the same derived window appears throughout candidate evaluation,
the winning model, final generation, and `similarity.json`.

## Documentation Scope

Implementation updates only the architecture documents that own this contract:

- `architecture/SYSTEM.md` for canonical normalization, research interfaces,
  configuration, and artifacts;
- the traffic-model catalog and three model documents for full-window
  generation;
- the similarity catalog and multiscale document for the derived horizon;
- the genetic strategy for common full-window trials and incomplete-generation
  handling;
- `architecture/TESTING.md` and `architecture/ROADMAP.md` for required evidence
  and phase deliverables.

The capture lifecycle and strict two-field `capture.json` do not change. This
fix does not define genetic operators, change seed policy, add new models or
metrics, or address other architecture-audit findings.
