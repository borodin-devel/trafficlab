# Контракт результата метрики

## Назначение

Metric result — машиночитаемый результат сравнения исходного и синтетического наборов данных. Каждая метрика возвращает значение, направление оптимизации и диагностику. Отдельные результаты хранятся в `metrics.parquet`, сводка в формате JavaScript Object Notation (JSON) — в `comparison.json`, человекочитаемое представление — в `report.md`.

Начальная `schema_version` обоих машиночитаемых файлов — `1.0.0`; версия изменяется по правилам семантического версионирования.

## Поля `metrics.parquet`

```text
comparison_id: string
source_artifact_id: string
synthetic_artifact_id: string
metric_id: string
metric_family: string
value: float64?
optimization_direction: enum[minimize, maximize, target]
target_value: float64?
status: enum[pass, invalid, error]
diagnostics: string
```

`diagnostics` содержит сериализованный JSON-объект. При `status = pass` поле `value` содержит конечное числовое значение. При `status = invalid` или `status = error` поле `value` равно `null`, а диагностика объясняет причину. Числовые sentinel-значения для ошибок и недействительных результатов запрещены.

## Поля `comparison.json`

```json
{
  "artifact_type": "metric_result",
  "artifact_version": "1.0.0",
  "schema_version": "1.0.0",
  "comparison_id": "...",
  "source_artifact_id": "sha256:...",
  "results": [
    {
      "synthetic_artifact_id": "sha256:...",
      "status": "pass",
      "hard_valid": true,
      "quality_vector": [0.0, 0.0, 0.0, 0.0, 0.0],
      "weighted_score": null
    }
  ]
}
```

Каждая запись `results[]` относится ровно к одному синтетическому артефакту. `results[].status` принимает `pass`, `invalid` или `error` и агрегирует состояние обязательных результатов метрик этого кандидата. Значение `error` используется, если хотя бы один обязательный результат имеет `error`; иначе отсутствие или `invalid` хотя бы одного обязательного результата даёт `invalid`; только полный набор результатов `pass` даёт `pass`. Позиции числового `results[].quality_vector` имеют фиксированный смысл:

```text
quality_vector = [
    distribution_score,
    sequence_score,
    protocol_score,
    flow_score,
    classifier_score
]
```

Логическое поле `results[].hard_valid` истинно только тогда, когда для указанного `results[].synthetic_artifact_id` пройдены все обязательные правила валидации. Если отсутствует хотя бы один обязательный результат либо он имеет `invalid` или `error`, статус кандидата не равен `pass`, а `quality_vector` и `weighted_score` равны `null`. Диагностика строк метрик и кандидата перечисляет каждый отсутствующий компонент и его причину. При полном успешном векторе `weighted_score` может оставаться `null`, если профиль не задаёт сводную оценку; это поле используется только для пользовательского интерфейса, сортировки и порога завершения.

## Инварианты

- Сводный score не заменяет отдельные строки метрик.
- `results` содержит запись для каждого сравниваемого синтетического артефакта, а значения `synthetic_artifact_id` в нём уникальны.
- При `results[].status = pass` поле `quality_vector` содержит `distribution_score`, `sequence_score`, `protocol_score`, `flow_score` и `classifier_score` в указанном порядке; иначе оно равно `null`.
- Многокритериальная оптимизация использует числовой `results[].quality_vector`, а не `results[].weighted_score`.
- Кандидат с неполным вектором, `hard_valid = false` или статусом `invalid` либо `error` не участвует в ранжировании Парето и эволюционном отборе.
- Ошибка, недействительность или отсутствие компонента никогда не представляется числовым sentinel-значением.
- Сравнение выполняется над пакетами по [контракту набора данных](dataset.md), не непосредственно над Packet Capture Next Generation (PCAPNG).
- Обучающие и тестовые данные метрик разделяются по целым сессиям.

## Потребители

Контракт создаёт стадия сравнения. Его читают отчётность, пользовательский интерфейс, эволюционный цикл, условия завершения и команды инспекции.
