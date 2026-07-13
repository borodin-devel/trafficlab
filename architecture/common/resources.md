# Управление ресурсами

## Декларация задачи

Каждая задача объявляет:

```text
cpu_threads
memory_mb
io_weight
requires_privilege
exclusive_group
supports_parallelism
```

Пример:

```text
capture:
    cpu_threads = 1
    memory_mb = 512
    exclusive_group = "network_setup"

semi_markov_train:
    cpu_threads = 2
    memory_mb = 2048

transformer_train:
    cpu_threads = 8
    memory_mb = 8192
    exclusive_group = "heavy_training"

compare:
    cpu_threads = 2
    memory_mb = 1024
```

Планировщик резервирует ресурсы до запуска и освобождает их после завершения процесса. Задачи одной непустой `exclusive_group` одновременно не выполняются.

## Политика для 16 GiB памяти

Стартовая конфигурация:

```text
global_memory_budget = 10 GiB
reserved_for_windows_and_wsl = remaining memory

max_parallel_heavy_train = 1
max_parallel_light_train = 2
max_parallel_generate = 2
max_parallel_compare = 4
max_parallel_capture = 1
```

Фактический бюджет рассчитывается по памяти, доступной внутри Windows Subsystem for Linux (WSL), а не по установленной памяти Windows. Оркестратор не запускает задачу, если её декларация превышает свободный бюджет.

## Защита от избыточной подписки

Оркестратор ограничивает внутренние пулы библиотек переменными:

```text
OMP_NUM_THREADS
MKL_NUM_THREADS
OPENBLAS_NUM_THREADS
NUMEXPR_NUM_THREADS
POLARS_MAX_THREADS
TORCH_NUM_THREADS
```

Число процессов и число потоков внутри библиотек не должны одновременно занимать все логические процессоры. Парсинг и запись работают пакетами. Apache Parquet записывается группами строк. Набор данных не загружается целиком без прямой необходимости. Захват не выполняет извлечение признаков в реальном времени.

## Инварианты

- Сумма зарезервированной памяти активных задач не превышает глобальный бюджет.
- Требование привилегий не приводит к запуску всей основной программы с повышенными правами.
- Тяжёлые вычисления выполняются в отдельных процессах.
- Решение планировщика и фактическое потребление записываются в наблюдаемость.

## Потребители

Политику применяют планировщик, subprocess runner, обучение, генерация, сравнение, захват и эволюционный цикл.
