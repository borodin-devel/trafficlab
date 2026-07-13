# Контракт артефакта модели

## Назначение

Model artifact — самостоятельный версионированный результат обучения. Он переносим между стадиями и не требует доступа к стадии захвата.

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
  "data_files": []
}
```

`artifact_version` версионирует каталог модели. `schema_version` версионирует `model.json`. Обе версии следуют семантическому версионированию.

## Форматы данных

- JSON и Apache Parquet хранят статистические параметры.
- NumPy Zip (NPZ) хранит числовые массивы без объектов.
- Safetensors хранит веса нейронной сети.
- Python pickle и joblib не являются межпрограммным контрактом.

Если внутренняя библиотека требует pickle, такой файл считается локальным и недоверенным. Его разрешено загружать только при совпадении `environment_hash`; он не включается в переносимый `data_files`.

## Инварианты

- `artifact_type` равен `traffic_model`.
- `dataset_hash`, `dataset_schema_version`, параметры и `seed` позволяют установить происхождение обучения.
- `feature_schema_hash` соответствует `feature_schema.json`.
- Все пути в `data_files` относительны корню артефакта.
- Каждый указанный файл существует и входит в вычисление `artifact_id`.
- Валидация использует [общий формат отчёта](validation-report.md).

## Потребители

Артефакт создаёт стадия обучения. Его читают генерация, сравнение вариантов, эволюционный цикл, инспекция и повторное использование обученной модели.
