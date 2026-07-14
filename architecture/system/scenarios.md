# Сценарии запуска

Команды ниже показывают границы поддерживаемых сценариев. Каталог запуска хранит версионированные артефакты и позволяет оркестратору проверять происхождение, кэш и состояние каждой стадии.

## Полный эксперимент

```bash
trafficlab run configs/experiments/http-client.toml
```

Команда строит полный ориентированный ациклический граф — Directed Acyclic Graph (DAG) — из конфигурации Tom's Obvious Minimal Language (TOML).

## Только захват

```bash
trafficlab-capture \
  --run-root run \
  --mode duration-or-exit \
  --duration 120 \
  -- curl https://example.invalid
```

После `--` аргументы передаются приложению как неизменённый массив `argv`, без интерпретации командной оболочкой (shell).

Интерактивное терминальное приложение запускается до его завершения:

```bash
trafficlab-capture \
  --mode until-exit \
  -- \
  interactive-tui --profile test
```

## Конвертация внешнего захвата

```bash
trafficlab-convert \
  --input assets/raw/sample.pcapng \
  --output-run run
```

Внешний файл Packet Capture Next Generation (PCAPNG) регистрируется как вход с собственным происхождением.

## Обучение без захвата

```bash
trafficlab-train \
  --dataset run/<id>/stages/20_convert/dataset \
  --model semi_markov
```

Входной набор должен иметь успешный отчёт проверки исходных данных.

## Генерация для тестирования

```bash
trafficlab-generate random \
  --profile tcp-client \
  --packets 10000
```

Случайный режим позволяет проверить генерацию, валидаторы и сравнение без захвата и обучения.

## Только сравнение

```bash
trafficlab-compare \
  --reference <dataset-a> \
  --candidate <dataset-b> \
  --candidate <dataset-c>
```

Все наборы должны быть неизменяемыми и иметь успешные связанные отчёты проверки.

## Частичный запуск

```bash
trafficlab run experiment.toml \
  --from train \
  --input-dataset <dataset>
```

Оркестратор строит минимальный подграф выбранной стадии, обязательных предшественников без готового кэша и требуемых проверок. Внешний набор регистрируется отдельным узлом происхождения.

## Возобновление

```bash
trafficlab resume run/<id>
```

Завершённые узлы с корректными хешами используются повторно. Прерванные, невалидные и отсутствующие узлы выполняются заново вместе с их зависимыми узлами.

## Только эволюционный поиск

```bash
trafficlab-evolve \
  --reference <dataset> \
  --search-space configs/search/semi-markov.toml
```

Исходный набор должен быть проверен. Поиск сохраняет поколения и делегирует обучение, генерацию, проверку и сравнение общему оркестратору.
