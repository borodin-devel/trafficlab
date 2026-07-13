# Контракт отчёта валидации

## Назначение

`validation.json` хранит единый машиночитаемый результат проверки артефакта в формате JavaScript Object Notation (JSON). Профиль определяет набор правил для конкретного типа входа.

Начальная `schema_version` — `1.0.0`; она изменяется по правилам семантического версионирования.

## Обязательные поля

```json
{
  "schema_version": "1.0.0",
  "validated_artifact_id": "sha256:...",
  "status": "pass",
  "profile": "source-pcap-v1",
  "rules": [
    {
      "id": "PCAP-STRUCT-001",
      "status": "pass",
      "severity": "error",
      "observed": "pcapng",
      "expected": "pcapng"
    }
  ],
  "errors": 0,
  "warnings": 0
}
```

Верхнеуровневый `status` принимает `pass`, `pass_with_warnings` или `fail`. Поле `rules[].status` принимает `pass`, `fail` или `skip`. Поле `rules[].severity` принимает `warning` или `error`. `observed` и `expected` могут содержать JSON-значение или `null`.

## Инварианты

- `errors` равно числу непройденных правил с `severity = error`.
- `warnings` равно числу непройденных правил с `severity = warning`.
- `fail` обязателен при `errors > 0`.
- `pass_with_warnings` обязателен при `errors = 0` и `warnings > 0`.
- `pass` допустим только при `errors = 0` и `warnings = 0`.
- Идентификатор правила стабилен внутри версии профиля.
- `validated_artifact_id` совпадает с идентификатором одного явно определённого проверяемого артефакта.

## Потребители

Контракт создают валидаторы захвата, набора данных, пакетных событий, модели и синтетического трафика. Его читают стадии, оркестратор, кэш, отчётность и эволюционный цикл.
