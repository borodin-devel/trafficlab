# Контракт артефакта модели

## Назначение

Model artifact — самостоятельный версионированный результат обучения. Метаданные хранятся в формате JavaScript Object Notation (JSON). Артефакт переносим между стадиями и не требует доступа к стадии захвата.

## Структура

```text
model/
├── model.json
├── feature_schema.json
├── training_metrics.json
├── validation.json
├── environment.json
└── data/
    ├── parameters.parquet
    ├── arrays.npz
    └── weights.safetensors
```

`model.json`, `feature_schema.json`, `training_metrics.json`, `validation.json` и `environment.json` обязательны. В `data/` присутствуют только нужные конкретной модели файлы.

## Обязательные поля `model.json`

```json
{
  "artifact_type": "traffic_model",
  "artifact_version": "1.0.0",
  "schema_version": "1.0.0",
  "artifact_id": "sha256:...",
  "created_at": "2026-07-13T12:34:56.000000Z",
  "model_type": "semi_markov",
  "implementation_version": "...",
  "dataset_hash": "sha256:...",
  "dataset_schema_version": "1.0.0",
  "feature_schema_hash": "sha256:...",
  "parameters": {},
  "seed": 0,
  "environment_hash": "sha256:...",
  "files": {
    "feature_schema.json": "sha256:...",
    "training_metrics.json": "sha256:...",
    "environment.json": "sha256:...",
    "data/parameters.parquet": "sha256:...",
    "data/arrays.npz": "sha256:...",
    "data/weights.safetensors": "sha256:..."
  }
}
```

`artifact_version` версионирует каталог модели. `schema_version` версионирует `model.json`. Обе версии следуют семантическому версионированию.

`files` отображает относительные имена `feature_schema.json`, `training_metrics.json`, `environment.json` и каждого файла под `data/` на SHA-256 в формате `sha256:<hex>`. Набор ключей под `data/` зависит от типа модели. Сам `model.json` в `files` не входит, чтобы не создавать ссылку на собственный хеш. `validation.json` также не входит: его `validated_artifact_id` ссылается на уже вычисленный `artifact_id`, поэтому включение отчёта создало бы цикл.

## Идентичность

`artifact_id` равен SHA-256 канонического JSON-представления `model.json`. Перед канонизацией из объекта рекурсивно исключаются `artifact_id`, поля временных меток `created_at`, `updated_at` и `validated_at`, производное поле `summary`, ссылки проверки `validation`, `validation_report`, `validation_artifact_id` и `validated_artifact_id`, а также поля, описывающие только размещение: `path`, `relative_path` и `summary_path`. Любое другое поле участвует в идентичности; новый исключаемый ключ требует новой версии схемы.

Ключи на всех уровнях сортируются лексикографически. JSON кодируется в UTF-8 (Unicode Transformation Format, 8-bit — 8-битный формат преобразования Unicode) без незначащих пробелов. Хеши в `files` тем самым связывают идентичность с содержимым обязательных метаданных и всех файлов модели под `data/`, но не с путями размещения, производной сводкой, временными метками или отчётом проверки.

Перед использованием потребитель проверяет, что набор ключей `files` полон, повторно вычисляет SHA-256 каждого компонента и затем повторно вычисляет канонический `artifact_id`. Несовпадение запрещает использование артефакта.

## Форматы данных

- JSON и Apache Parquet хранят статистические параметры.
- NumPy Zip (NPZ) хранит числовые массивы без объектов.
- Safetensors хранит веса нейронной сети.
- Python pickle и joblib запрещены в публичных артефактах, локальных кэшах, загруженных файлах и результатах прошлых запусков.

Совпадение `environment_hash`, происхождения или хеша содержимого не делает pickle безопасным. Если внешняя библиотека может экспортировать модель только в pickle или joblib, интеграция этой модели не поддерживается до появления безопасного преобразования или экспорта в один из разрешённых форматов.

## Инварианты

- `artifact_type` равен `traffic_model`.
- `dataset_hash`, `dataset_schema_version`, параметры и `seed` позволяют установить происхождение обучения.
- `feature_schema_hash` соответствует `feature_schema.json`.
- Все пути в `files` относительны корню артефакта.
- Каждый указанный файл существует, имеет совпадающий SHA-256 и входит в вычисление `artifact_id` через отображение `files`.
- `validation.json`, производные сводки, временные метки и поля размещения не определяют идентичность модели.
- Валидация использует [общий формат отчёта](validation-report.md).

## Потребители

Артефакт создаёт стадия обучения. Его читают генерация, сравнение вариантов, эволюционный цикл, инспекция и повторное использование обученной модели.
