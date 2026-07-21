# Applications

## Classification

Applications are standalone commands owning process lifecycle, configuration
resolution, file validation/publication, and startup diagnostics. Numeric
directory prefixes define pipeline reading order only.

## Application Catalog

| Order | Runtime name | Responsibility |
| --- | --- | --- |
| 00 | [preflight](00_preflight/README.md) | Read-only capture readiness |
| 10 | [capture](10_capture/README.md) | Target-tree packet capture |
| 20 | [convert](20_convert/README.md) | Directional PCAPNG conversion |
| 25 | [inspection_export](25_inspection_export/README.md) | ML/LLM inspection export |
| 30 | [genetic_training](30_genetic_training/README.md) | Candidate evolution and fitting orchestration |
| 40 | [model_creation](40_model_creation/README.md) | Normal model-file creation |
| 50 | [traffic_generation](50_traffic_generation/README.md) | Synthetic PCAPNG generation |
| 60 | [similarity_evaluation](60_similarity_evaluation/README.md) | One reference/generated comparison |
| 99 | [trafficlab](99_trafficlab/README.md) | Top-level run and experiment orchestration |

Each directory contains README, SAD, SRS, CONFIGS, and ROADMAP. Applications
exchange only explicit contracts and never read another application's settings.
Every child accepts the shared managed `--attempt-dir` argument and otherwise
creates a direct attempt under `run/` as defined by
[application configuration](../CONFIGURATION.md#startup-record).
