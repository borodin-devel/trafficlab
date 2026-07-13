# Контракт результата метрики

## Назначение

Metric result — машиночитаемый результат сравнения исходного и синтетического наборов данных. Каждая метрика возвращает значение, направление оптимизации и диагностику. Отдельные результаты хранятся в `metrics.parquet`, сводка — в `comparison.json`, человекочитаемое представление — в `report.md`.

Начальная `schema_version` обоих машиночитаемых файлов — `1.0.0`; версия изменяется по правилам семантического версионирования.

## Поля `metrics.parquet`

```text
comparison_id: string
source_artifact_id: string
synthetic_artifact_id: string
metric_id: string
metric_family: string
value: float64
optimization_direction: enum[minimize, maximize, target]
target_value: float64?
status: enum[pass, invalid, error]
diagnostics: string
```

`diagnostics` содержит сериализованный JSON-объект. Он не заменяет `value` и не влияет на его единицы измерения.

## Поля `comparison.json`

```json
{
  "artifact_type": "metric_result",
  "artifact_version": "1.0.0",
  "schema_version": "1.0.0",
  "comparison_id": "...",
  "source_artifact_id": "sha256:...",
  "synthetic_artifact_ids": ["sha256:..."],
  "hard_valid": true,
  "quality_vector": [0.0, 0.0, 0.0, 0.0, 0.0],
  "weighted_score": null
}
```

Позиции `quality_vector` имеют фиксированный смысл:

```text
quality_vector = [
    distribution_score,
    sequence_score,
    protocol_score,
    flow_score,
    classifier_score
]
```

Логическое поле `hard_valid` истинно только тогда, когда пройдены все обязательные правила валидации. `weighted_score` необязателен и используется только для пользовательского интерфейса, сортировки и порога завершения.

## Инварианты

- Сводный score не заменяет отдельные строки метрик.
- `quality_vector` всегда содержит `distribution_score`, `sequence_score`, `protocol_score`, `flow_score` и `classifier_score` в указанном порядке.
- Многокритериальная оптимизация использует сам `quality_vector`, а не `weighted_score`.
- Сравнение выполняется над пакетами по [контракту набора данных](dataset.md), не непосредственно над Packet Capture Next Generation (PCAPNG).
- Обучающие и тестовые данные метрик разделяются по целым сессиям.

## Потребители

Контракт создаёт стадия сравнения. Его читают отчётность, пользовательский интерфейс, эволюционный цикл, условия завершения и команды инспекции.
