# Структура репозитория

Trafficlab поставляется как один Python-дистрибутив. Исходный код Python находится в `src/trafficlab/`. Конфигурации, схемы, тесты и проектные ресурсы не становятся неявными зависимостями библиотечного кода.

## Дерево

```text
trafficlab/
├── pyproject.toml
├── uv.lock
├── .python-version
├── README.md
├── LICENSE
├── CHANGELOG.md
├── Makefile
├── configs/
│   ├── experiments/
│   ├── models/
│   ├── metrics/
│   ├── validation/
│   └── search/
├── schemas/
│   ├── artifact/
│   ├── dataset/
│   ├── config/
│   └── validation/
├── src/
│   └── trafficlab/
│       ├── __init__.py
│       ├── py.typed
│       ├── cli/
│       │   ├── main.py
│       │   ├── capture.py
│       │   ├── convert.py
│       │   ├── validate.py
│       │   ├── train.py
│       │   ├── generate.py
│       │   ├── compare.py
│       │   ├── evolve.py
│       │   └── inspect.py
│       ├── contracts/
│       │   ├── artifacts.py
│       │   ├── capture.py
│       │   ├── dataset.py
│       │   ├── models.py
│       │   ├── metrics.py
│       │   ├── validation.py
│       │   └── errors.py
│       ├── runstore/
│       │   ├── paths.py
│       │   ├── hashes.py
│       │   ├── manifest.py
│       │   ├── atomic.py
│       │   ├── cache.py
│       │   └── lineage.py
│       ├── capture/
│       │   ├── service.py
│       │   ├── lifecycle.py
│       │   ├── signals.py
│       │   ├── preflight.py
│       │   └── backends/
│       │       ├── protocol.py
│       │       ├── netns_dumpcap.py
│       │       ├── container.py
│       │       └── ebpf_cgroup.py
│       ├── privileged/
│       │   ├── main.py
│       │   ├── cgroup.py
│       │   ├── namespace.py
│       │   ├── veth.py
│       │   ├── nftables.py
│       │   ├── dns.py
│       │   ├── offload.py
│       │   └── cleanup.py
│       ├── pcap/
│       │   ├── decode.py
│       │   ├── normalize.py
│       │   ├── direction.py
│       │   ├── render.py
│       │   ├── inspect.py
│       │   └── backends/
│       │       ├── tshark.py
│       │       └── scapy.py
│       ├── dataset/
│       │   ├── schema.py
│       │   ├── reader.py
│       │   ├── writer.py
│       │   ├── batching.py
│       │   ├── sampling.py
│       │   ├── flows.py
│       │   ├── sessions.py
│       │   └── export.py
│       ├── validation/
│       │   ├── engine.py
│       │   ├── reports.py
│       │   ├── profiles.py
│       │   └── rules/
│       │       ├── pcap.py
│       │       ├── dataset.py
│       │       ├── model.py
│       │       └── synthetic.py
│       ├── features/
│       │   ├── windows.py
│       │   ├── distributions.py
│       │   ├── transitions.py
│       │   └── protocol_state.py
│       ├── models/
│       │   ├── protocol.py
│       │   ├── registry.py
│       │   ├── artifacts.py
│       │   ├── independent_empirical/
│       │   ├── conditional_empirical/
│       │   ├── semi_markov/
│       │   ├── gru/
│       │   └── transformer/
│       ├── generation/
│       │   ├── events.py
│       │   ├── random.py
│       │   ├── protocol_state.py
│       │   ├── renderer.py
│       │   └── active/
│       │       ├── scheduler.py
│       │       ├── endpoint.py
│       │       └── topology.py
│       ├── comparison/
│       │   ├── service.py
│       │   ├── aggregation.py
│       │   ├── report.py
│       │   └── metrics/
│       │       ├── distributions.py
│       │       ├── sequences.py
│       │       ├── flows.py
│       │       ├── protocols.py
│       │       └── c2st.py
│       ├── evolution/
│       │   ├── genotype.py
│       │   ├── population.py
│       │   ├── operators.py
│       │   ├── selection.py
│       │   ├── scheduler.py
│       │   ├── checkpoint.py
│       │   └── termination.py
│       ├── orchestration/
│       │   ├── dag.py
│       │   ├── task.py
│       │   ├── resources.py
│       │   ├── executor.py
│       │   ├── subprocesses.py
│       │   ├── resume.py
│       │   └── pipeline.py
│       └── observability/
│           ├── logging.py
│           ├── events.py
│           ├── metrics.py
│           └── resources.py
├── tests/
│   ├── unit/trafficlab/
│   │   ├── contracts/
│   │   ├── runstore/
│   │   ├── capture/
│   │   ├── pcap/
│   │   ├── dataset/
│   │   ├── validation/
│   │   ├── features/
│   │   ├── models/
│   │   ├── generation/
│   │   ├── comparison/
│   │   ├── evolution/
│   │   ├── orchestration/
│   │   └── observability/
│   ├── integration/
│   │   ├── capture/
│   │   ├── pcap/
│   │   ├── dataset/
│   │   ├── models/
│   │   ├── generation/
│   │   └── orchestration/
│   ├── system/
│   │   ├── full_pipeline/
│   │   ├── partial_runs/
│   │   ├── tui_capture/
│   │   ├── timeout_cleanup/
│   │   └── resume/
│   ├── contract/
│   │   ├── artifact_schema/
│   │   ├── model_plugins/
│   │   └── metric_plugins/
│   └── fixtures/
│       ├── pcapng/
│       ├── datasets/
│       └── configs/
├── assets/
│   ├── README.md
│   ├── fixtures/
│   ├── reference/
│   ├── raw/
│   ├── models/
│   └── external/
├── run/
│   └── .gitkeep
├── docs/
│   ├── architecture/
│   ├── adr/
│   ├── schemas/
│   ├── development/
│   └── experiments/
└── scripts/
    ├── bootstrap-wsl.sh
    ├── install-capture-helper.sh
    ├── preflight.sh
    └── clean-orphans.sh
```

## Границы

- `configs/` хранит версионируемые входные конфигурации. Код не изменяет их во время запуска.
- `schemas/` хранит машиночитаемые схемы публичных файлов. Их смысл описан в [контрактах архитектуры](../contracts/).
- `src/trafficlab/cli/` содержит только адаптеры интерфейса командной строки — Command-Line Interface (CLI). Поведение программ описано в [системном документе](../system/programs.md).
- `src/trafficlab/contracts/` содержит представление контрактов в коде. Оно не выполняет ввод-вывод.
- Остальные пакеты соответствуют [границам внутренних модулей](../system/modules.md).
- `tests/` зеркалит пакет, а интеграционные, системные и контрактные проверки имеют отдельные каталоги. Политику задаёт [тестовая стратегия](testing.md).
- `assets/` подчиняется [политике крупных файлов](assets.md). `run/` содержит локальные результаты и не коммитится.
- `docs/` хранит пользовательские и проектные материалы. Архитектурное дерево верхнего уровня остаётся источником архитектурных решений до окончательного размещения документации проекта.
- `scripts/*.sh` остаются исполняемыми проектными скриптами для ограниченных операций установки, предварительной проверки и очистки. Рабочая логика Python принадлежит пакету.

Имена файлов дерева фиксируют ожидаемые границы, а не требуют создавать пустые модули заранее. Новая ответственность сначала относится к существующему модулю; новый пакет создаётся только при появлении самостоятельного контракта.
