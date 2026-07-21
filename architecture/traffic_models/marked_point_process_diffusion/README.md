# Marked Point-Process Diffusion Traffic Model

## Purpose and Interface

`marked_point_process_diffusion` is a mature selectable Layer 2 model that
samples a bounded variable-count event window then denoises all active event
values jointly under history/seed context.

## Inputs, Outputs, Dependencies, and Configuration

Fitting consumes prepared per-file windows; generation consumes a
generation-ready trained model and outputs complete accepted windows. Shared source and
lineage rules are in [neural common rules](../NEURAL_MARKED_POINT_PROCESS.md).

## Related Components and Execution

[Model creation](../../apps/40_model_creation/README.md) publishes immutable
builders, [genetic training](../../apps/30_genetic_training/README.md) applies
candidate hyperparameters before fitting local weights, and
[traffic generation](../../apps/50_traffic_generation/README.md) executes
generation-ready models. [PCAP I/O](../../libs/pcap_io/README.md) owns
packet-file mechanics. Supported models run offline and unprivileged;
unresolved models remain unselectable.

## Documents and Reading Order

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) ·
[General information](00_GENERAL_INFO.md) · [Representation](01_WINDOW_REPRESENTATION.md) ·
[Diffusion mathematics](02_DIFFUSION_MATHEMATICS.md) · [Fitting](03_FITTING_SELECTION.md) ·
[Generation and validation](04_GENERATION_VALIDATION.md) · [Roadmap](ROADMAP.md)
