# Advanced Neural Traffic Models Design

## Goal

Add two independently selectable advanced Layer 2 traffic-model architecture
owners: `neural_hawkes` and `marked_point_process_diffusion`.

## Scope

Create or update these architecture documents:

- `architecture/traffic_models/NEURAL_MARKED_POINT_PROCESS.md`
- `architecture/traffic_models/neural_hawkes/README.md`
- `architecture/traffic_models/marked_point_process_diffusion/README.md`
- `architecture/apps/30_genetic_training/README.md`
- `docs/configs/30_genetic_training.md`

Register both model names in `architecture/traffic_models/README.md`. This is
architecture documentation only: no Python implementation, templates,
datasets, or test fixtures.

## Shared Neural Marked-Point-Process Rules

Both models remain Layer 2 only. A traffic event contains a non-negative IAT
and an original Ethernet frame length from 60 through 1514 bytes. They do not
model packet bytes, addresses, IP, TCP, UDP, flows, direction, sessions, or
higher protocols.

Each model accepts exactly one reference-source mode:

- one supported reference capture file; or
- one directory of supported reference capture files.

Directory membership is enumerated in deterministic relative-path order.
Every file is independently validated, and no IAT, transition, or sequence
crosses a file boundary. An unreadable, unsupported, or inadequate capture is
a clear preparation failure.

The models derive deterministic, non-overlapping fixed-window training
examples. For a folder, whole files are reserved for validation when possible;
otherwise the documented fallback is a chronological, non-overlapping window
split. A single capture always uses that chronological window split. Inputs,
assignments, configuration, seed, library identities and versions, learned
weight hash, and every reference artifact hash are recorded in lineage.

One reference capture is supported but may overfit. Model diagnostics must
state source mode, capture count, window count, train/validation assignment,
and the limitation of a single-capture source.

For a directory source, each capture remains an independent target. A
candidate fits from its deterministic training assignment, then generates and
evaluates one matching artifact for every validation capture. Its default
validation similarity is the equal-weight arithmetic mean of those per-capture
similarities. No capture is merged with another.

Every fixed window is self-contained: the IAT of its first event is measured
from that window's start. Later IATs are measured from the preceding event in
the same window.

Neural weights are fitted inside each candidate by its model-specific,
deterministic procedure. Genetic training searches only explicit high-level
hyperparameters; it does not directly mutate individual learned weights.

## `neural_hawkes`

`neural_hawkes` is a self-attentive neural marked temporal point process. It
generates traffic causally, one frame at a time. A history encoder consumes
earlier `(IAT, frame length)` events and parameterizes a conditional law for
the next non-negative IAT and next continuous frame length.

Frame length is modeled continuously during fitting and sampling, then mapped
deterministically to an integer valid Ethernet length. The owner defines the
conditional-intensity or equivalent time-density representation, attention
history limits, mark-density representation, loss, fitting/validation
procedure, model-file contents, generation stopping rules, determinism,
failures, and future offline tests.

Its normal untrained builder file explicitly declares every architecture,
optimizer, fitting, validation/split, zero-IAT observation, stopping, seed,
runtime-determinism, and candidate-hyperparameter control. A trained file adds
validated learned weights and lineage; it does not rely on hidden defaults.

## `marked_point_process_diffusion`

`marked_point_process_diffusion` is a conditional marked temporal
point-process diffusion model. It generates one fixed window as a whole,
learning to transform noise into an ordered sequence of normalized
`(log1p(IAT), frame length)` events. It conditions only on previously generated
traffic history and its configured seed; it has no app identity, address,
payload, protocol, or external-label condition.

Its output conversion yields non-negative IATs and deterministically rounded,
valid integer Ethernet lengths. The owner defines fixed-window representation,
variable event-count handling, diffusion/noise process, conditioning,
denoising network, training loss, sampling, model-file contents, stopping,
determinism, failure behavior, and future offline tests.

For mathematical completeness, Gaussian diffusion applies to the fixed-shape
active-event value tensor. It uses a declared beta schedule, predicts noise,
derives the clean-value estimate deterministically from that prediction and
schedule, and applies a declared reverse update. Event count is a separate
context-conditioned categorical distribution: it is trained with categorical
cross-entropy and sampled once before Gaussian value reverse sampling; it is
not an unspecified categorical diffusion process.

Every completed sampled window must be admissible as a whole. Its first IAT is
from the window start, every IAT is non-negative, and their cumulative sum is
strictly less than the window duration. A sample outside that support fails;
it is never truncated, clipped, rescaled, or partially emitted.

## Shared Configuration and Safety

All model architecture, fitting, window, split, optimizer, epoch, learning
rate, history, hidden-size, diffusion, generation, seed, and dependency
parameters are explicit configuration and are recorded in `launch.toml`.
Normal templates remain future work. Fitting and generation operate without
live capture, network access, sudo, root, or other elevated privilege.

`30_genetic_training` and its configuration description extend their existing
one-capture contract to these models' file-or-directory source modes. They own
deterministic training/validation assignment, one generated/evaluated artifact
per validation capture, equal-weight aggregation, aggregate lineage and
diagnostics, and failure if a required capture-level fit, generation, or
evaluation fails. Existing non-neural models retain their one-capture workflow.

## Verification

Future unit tests cover deterministic directory enumeration, file-boundary
separation, windowing, source-mode validation, split assignment, lineage,
event-domain constraints, output conversion, seed reproducibility, and
model-specific probability or diffusion mathematics. Future integration tests
use small offline Ethernet pcapng fixtures only.
