# Model Creation Configuration

## Interface

The known command is `model_creation --model NAME --output-dir DIR`. No
application-specific TOML setting is defined. Shared startup-record behavior
still applies.

## Ownership

Normal values and validation belong to each selected model's `CONFIGS.md` and
detailed schema. The application must not override them or select a model
implicitly. It publishes only the builder state; preparation/finalization and
generation-ready fields belong to the selected model and genetic training.
