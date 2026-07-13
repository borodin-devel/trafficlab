# Контракт каталога запуска

## Назначение

Run directory — каталог одного запуска эксперимента или отдельной программы. Он объединяет конфигурацию, происхождение данных, журналы и каталоги выполненных стадий.

Версия структуры хранится в обязательном поле `schema_version` файла `run.json`. Начальная версия — `1.0.0`; она изменяется по правилам семантического версионирования.

## Обязательные поля `run.json`

`run.json` содержит как минимум:

```json
{
  "artifact_type": "trafficlab_run",
  "artifact_version": "1.0.0",
  "schema_version": "1.0.0",
  "run_id": "01J...",
  "created_at": "2026-07-13T12:34:56.123456Z",
  "status": "running",
  "resolved_config_sha256": "sha256:...",
  "environment_sha256": "sha256:...",
  "lineage_sha256": "sha256:..."
}
```

`status` принимает `running`, `completed`, `failed` или `cancelled`. `artifact_version` версионирует весь каталог, а `schema_version` — структуру `run.json`.

## Имя и структура

```text
run/YYYYMMDDTHHMMSS.ffffffZ_<ulid>/
```

ULID — Universally Unique Lexicographically Sortable Identifier, лексикографически сортируемый уникальный идентификатор.

```text
run/20260713T123456.123456Z_01J.../
├── run.json
├── config.input.toml
├── config.resolved.toml
├── environment.json
├── lineage.json
├── events.jsonl
├── stages/
│   ├── 00_preflight/
│   │   ├── stage.json
│   │   ├── system.json
│   │   └── validation.json
│   ├── 10_capture/
│   │   ├── stage.json
│   │   ├── target.json
│   │   ├── network.json
│   │   ├── raw/
│   │   │   └── target.pcapng
│   │   ├── statistics.json
│   │   ├── validation.json
│   │   └── logs/
│   ├── 20_convert/
│   │   ├── stage.json
│   │   └── dataset/
│   ├── 30_validate_source/
│   │   ├── stage.json
│   │   └── validation.json
│   ├── 40_train/
│   │   ├── semi_markov__<id>/
│   │   └── transformer__<id>/
│   ├── 50_generate/
│   │   ├── semi_markov__<id>/
│   │   └── transformer__<id>/
│   ├── 60_validate_synthetic/
│   ├── 70_compare/
│   │   ├── metrics.parquet
│   │   ├── comparison.json
│   │   └── report.md
│   └── 80_evolve/
│       ├── state.json
│       ├── population.parquet
│       ├── generation_0000/
│       ├── generation_0001/
│       └── champions/
├── reports/
│   ├── final.json
│   └── final.md
└── logs/
```

`run.json`, `config.resolved.toml`, `environment.json`, `lineage.json` и `stages/` обязательны. `config.input.toml` отсутствует, если программа не получала исходную конфигурацию. Остальные каталоги и файлы появляются только у выполняемых стадий.

Отдельная программа создаёт полноценный каталог запуска только со своими стадиями. Пример конвертации внешнего файла:

```text
run/<id>/
├── run.json
├── config.resolved.toml
└── stages/
    ├── 00_external_input/
    ├── 20_convert/
    └── 30_validate_source/
```

## Инварианты

- `artifact_type` равен `trafficlab_run`.
- `run_id` совпадает с ULID в имени каталога.
- Хеши метаданных совпадают с содержимым соответствующих файлов.
- Каждый каталог стадии содержит файл по [контракту состояния стадии](stage.md).
- Пути внутри метаданных относительны корню каталога запуска.
- Каталог одной стадии не изменяет файлы другой стадии.
- Наличие каталога или файла не означает, что стадия завершена.
- Опубликованные артефакты не изменяются на месте.

## Потребители

Структуру создают все консольные программы. Её читают оркестратор, команды инспекции и возобновления, генераторы отчётов и пользовательские инструменты анализа.
