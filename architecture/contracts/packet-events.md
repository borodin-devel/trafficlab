# Контракт пакетных событий

## Назначение

Packet event — абстрактное пакетное событие. Модель и случайный генератор создают последовательность таких событий, не формируя байты пакета. Контракт не определяет устройство модели, планировщика или средства построения пакетов.

Версия последовательности задаётся полем `schema_version`. Начальная версия — `1.0.0`; она изменяется по правилам семантического версионирования. Поле `l4_protocol` обозначает протокол транспортного уровня — Layer 4 (L4).

## Обязательные поля

```text
trace_id: string
session_id: string
flow_id: string
packet_index: uint64
relative_time_ns: int64
inter_arrival_ns: int64
direction: enum[ingress, egress, unknown]
original_length: uint32
l4_protocol: enum[tcp, udp, icmp, icmpv6, other, none]
src_endpoint_id: string
dst_endpoint_id: string
```

Протокольные расширения являются nullable-полями:

```text
ether_type: uint16?
ip_version: uint8?
ip_protocol: uint8?
tcp_flags: uint16?
src_port_class: uint16?
dst_port_class: uint16?
```

Метаданные последовательности обязаны содержать `artifact_type = packet_events`, `artifact_version`, `schema_version`, `artifact_id`, `created_at`, `seed`, `generator`, `event_count` и `lineage`.

## Инварианты

- `packet_index` начинается с нуля и строго возрастает внутри trace.
- `relative_time_ns` не убывает; `inter_arrival_ns` неотрицателен.
- `event_count` совпадает с числом событий.
- Идентификаторы endpoint являются псевдонимами и не указывают случайно на реальные внешние узлы.
- События проходят независимую семантическую валидацию до материализации.
- Длина, контрольные суммы и зависимые поля пакета не являются обязанностью события.
- Одинаковый вход, конфигурация и `seed` дают одинаковую последовательность.

## Потребители

Контракт создают модели и генераторы. Его читают семантический валидатор, средство построения пакетов, автономный синтез, активная эмуляция, сравнение и эволюционный цикл.
