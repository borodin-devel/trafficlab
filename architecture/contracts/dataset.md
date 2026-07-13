# Контракт набора данных

## Назначение

Dataset package — версионированный каталог нормализованного сетевого трафика. Основной аналитический формат — Apache Parquet. Метаинформация хранится в JavaScript Object Notation (JSON). Исходным файлом обычно служит Packet Capture Next Generation (PCAPNG).

## Структура

```text
dataset/
├── manifest.json
├── schema.json
├── packets.parquet
├── flows.parquet
├── sessions.parquet
├── statistics.json
├── validation.json
└── summary.md
```

Все перечисленные файлы обязательны. `manifest.json` содержит:

```json
{
  "artifact_type": "traffic_dataset",
  "artifact_version": "1.0.0",
  "schema_version": "1.0.0",
  "artifact_id": "sha256:...",
  "created_at": "2026-07-13T12:34:56.000000Z",
  "source": {
    "type": "pcapng",
    "sha256": "...",
    "relative_path": "..."
  },
  "decoder": {
    "name": "tshark",
    "version": "..."
  },
  "row_counts": {
    "packets": 0,
    "flows": 0,
    "sessions": 0
  },
  "time_unit": "nanosecond",
  "payload_retained": false,
  "files": {
    "schema.json": "sha256:...",
    "packets.parquet": "sha256:...",
    "flows.parquet": "sha256:...",
    "sessions.parquet": "sha256:...",
    "statistics.json": "sha256:...",
    "validation.json": "sha256:...",
    "summary.md": "sha256:..."
  },
  "lineage": []
}
```

`artifact_version` версионирует весь пакет. `schema_version` отдельно версионирует табличную схему. Обе версии следуют семантическому версионированию.

`files` является отображением относительных имён всех обязательных файлов пакета, кроме самого `manifest.json`, на их SHA-256 в формате `sha256:<hex>`. Манифест не включает собственный хеш, чтобы не создавать циклическую зависимость.

`artifact_id` равен SHA-256 канонического JSON-представления манифеста. Для вычисления из манифеста исключаются поля `artifact_id` и `created_at`, а из `files` — записи `validation.json` и `summary.md`: отчёт проверки и производный текст не определяют идентичность набора. Оставшиеся ключи на всех уровнях сортируются лексикографически; результат кодируется в UTF-8 без незначащих пробелов. Идентификатор записывается в формате `sha256:<hex>`.

## `packets.parquet`

```text
trace_id: string
session_id: string
flow_id: string
packet_index: uint64

timestamp_ns: int64
relative_time_ns: int64
inter_arrival_ns: int64

direction: enum[ingress, egress, unknown]

captured_length: uint32
original_length: uint32
payload_length: uint32?

interface_id: uint16
link_type: uint16
ether_type: uint16?

ip_version: uint8?
ip_protocol: uint8?
l4_protocol: enum[tcp, udp, icmp, icmpv6, other, none]

tcp_flags: uint16?
tcp_syn: bool?
tcp_ack: bool?
tcp_fin: bool?
tcp_rst: bool?
tcp_psh: bool?
tcp_urg: bool?

src_endpoint_id: string?
dst_endpoint_id: string?
src_port_class: uint16?
dst_port_class: uint16?

is_retransmission: bool?
is_out_of_order: bool?
is_fragment: bool?
```

Знак `?` обозначает nullable-поле. `src_endpoint_id` и `dst_endpoint_id` являются псевдонимами внутри trace. Реальные адреса по умолчанию не входят в готовый для модели набор данных.

## `flows.parquet`

```text
flow_id
session_id
first_timestamp_ns
last_timestamp_ns
duration_ns
l3_protocol
l4_protocol
packet_count
ingress_packet_count
egress_packet_count
ingress_bytes
egress_bytes
direction_switch_count
initial_direction
tcp_termination_type
```

## `sessions.parquet`

```text
session_id
trace_id
start_timestamp_ns
end_timestamp_ns
duration_ns
packet_count
flow_count
source_run_id
split_group
```

`split_group` разделяет целые сессии на обучающую (`train`), проверочную (`validation`) и тестовую (`test`) части.

## Ограниченное представление для языковой модели

Полный пакет не преобразуется целиком в Markdown или JSON Lines (JSONL). Конвертер создаёт ограниченный каталог контекста:

```text
llm-context/
├── manifest.json
├── schema.json
├── summary.md
├── distributions.json
├── flow_examples.jsonl
├── packet_windows.jsonl
└── validation.json
```

Выборка детерминирована, стратифицирована по направлению и протоколу, ограничена по размеру и снабжена схемой и единицами измерения.

## Инварианты

- `artifact_type` равен `traffic_dataset`.
- Все временные значения имеют целое число наносекунд.
- `packet_index` явно сохраняет порядок пакетов.
- `row_counts` совпадает с числом строк соответствующих таблиц.
- Ссылочные идентификаторы разрешаются между таблицами.
- Один вход и одна конфигурация дают один и тот же нормализованный пакет.
- `dataset/validation.json` содержит базовую структурную проверку публикуемого пакета и использует [общий формат отчёта](validation-report.md).
- Последующая проверка исходного набора не изменяет пакет и сохраняет отдельный stage-level отчёт.

## Потребители

Пакет создаёт стадия конвертации. Его читают валидация исходных данных, обучение, сравнение, эволюционный цикл, инспекция и генераторы ограниченного контекста.
