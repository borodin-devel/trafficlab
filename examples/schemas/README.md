# Public artifact schemas

This directory contains generated, checked JSON Schema documents for
Trafficlab's public scientific artifacts. Versioned subdirectories make a
compatibility break explicit; the current set is
[`scientific-artifact-v5/`](scientific-artifact-v5/).

Generate or verify the complete set from the repository root:

```bash
uv run --locked python scripts/generate_artifact_schemas.py
uv run --locked python scripts/generate_artifact_schemas.py --check
```

The schemas are derived from the strict Pydantic artifact models and rendered
as canonical UTF-8 JSON. Do not edit `*.schema.json` by hand. The generator owns
the schema files but deliberately preserves the README beside them.

## Fields in each `*.schema.json` document

| Field | Description |
| --- | --- |
| `$schema` | JSON Schema dialect URI; all current documents use Draft 2020-12. |
| `$id` | Stable local schema identifier, equal to the schema filename. |
| `$defs` | Reusable nested record definitions referenced with `$ref`; absent when the root needs none. |
| `title` | Generated name of the root Pydantic model. |
| `description` | Human-readable purpose of the artifact or nested record, when the model declares one. |
| `type` | Required JSON value kind, normally `object` for an artifact root. |
| `properties` | Map from allowed field names to their schemas. |
| `required` | Field names that every valid object must contain. |
| `additionalProperties` | Whether undeclared keys are permitted; public artifact records set this to `false`. |

Nested schemas also use standard keywords such as `$ref`, `anyOf`, `oneOf`,
`items`, `prefixItems`, `const`, `enum`, `minimum`, `maximum`, `minItems`, and
`maxItems`. They select a referenced definition, express a tagged union, define
array items or fixed tuples, constrain literal values, and bound numbers or
array lengths respectively.

The artifact field catalog is in
[`scientific-artifact-v5/README.md`](scientific-artifact-v5/README.md).
