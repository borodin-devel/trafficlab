# Быстрый старт: работа с уже записанным дампом

Этот сценарий предназначен для **импортированной** эталонной записи в `dumps/`.
Одна команда создаёт воспроизводимый набор результатов: Trafficlab проверяет
конфигурацию, нормализует запись, выполняет `fit`, `generate`, `compare` и
финальную проверку.

Каталог `runs/` штатно исключён из Git. Коммит и push не создают резервную
копию результатов: сохраняйте нужные run-каталоги отдельно вместе со всеми
девятью каноническими артефактами.

## Рекомендуемый запуск одной командой

Из корня репозитория создайте именованный профиль, задайте в нём новый
`run.directory` и запустите импорт:

```bash
cp examples/configs/balanced.toml examples/configs/my-dump.toml
# Укажите новый run.directory в my-dump.toml.
uv run --locked trafficlab import-run \
  examples/configs/my-dump.toml dumps/my-dump
```

Каталог `dumps/my-dump` должен содержать ровно два обычных файла: один Ethernet
capture с расширением `.pcap` или `.pcapng` и один `capture.json`. Trafficlab
не изменяет исходные файлы, не выводит MAC заново и использует MAC, указанный в
`capture.json`. Поддерживаемые различия формата и порядка пакетов исправляются
внутри одного Python-процесса; Docker, shell, Wireshark и скрипты репозитория не
запускаются.

Вручную выполнять `preflight`, копировать файлы, запускать `fit`, `generate`
или `compare` не нужно. Все девять результатов остаются в настроенном
`run.directory`. После сбоя прочитайте `run.log` и повторите ту же команду с
неизменёнными TOML и source-парой: совместимые checkpoint и готовые этапы будут
переиспользованы. Если источник изменился, существующий run сохраняется и
отклоняется; для другого источника задайте новый `run.directory`.

Не запускайте для такого дампа `trafficlab run` или `trafficlab capture`:
эти команды предназначены для Docker-захвата.

## Что необходимо

Рабочая среда: Linux либо WSL2 с systemd, CPython 3.12 (для точного
воспроизведения checkpoint нужен 3.12.3) и [uv](https://docs.astral.sh/uv/).
Docker для `import-run` не нужен.

Из корня репозитория установите закреплённый Python и все зависимости, включая
необязательный desktop-dashboard:

```bash
uv python install 3.12.3
uv sync --locked --all-groups --all-extras
uv lock --check
uv run --locked python --version
uv run --locked trafficlab --version
uv run --locked --all-extras trafficlab-dashboard --help
```

Ожидаемая версия для детерминированных fixtures и checkpoint — `Python 3.12.3`.
`--locked` запрещает uv незаметно менять содержимое `uv.lock`.

## Как выглядит готовый дамп

Каждый готовый источник в `dumps/` имеет отдельный каталог:

```text
dumps/<dump-name>/
├── capture.json
└── <одно-имя>.pcap или <одно-имя>.pcapng
```

Выбирайте отдельный каталог с точной парой, без вложенных каталогов, ссылок и
посторонних файлов. `capture.json` должен содержать строгие метаданные
Trafficlab для Ethernet `eth0` и supplied unicast MAC цели. Этот MAC определяет
направления; импорт не доказывает его происхождение. Не редактируйте capture или
metadata после запуска: их точные байты входят в lineage результатов.

Быстрый выбор пары:

```bash
find "dumps" -mindepth 2 -maxdepth 2 -type f \
  \( -name 'capture.json' -o -iname '*.pcap' -o -iname '*.pcapng' \) -print | sort
```

## Продвинутый поэтапный запуск и resume

Следующий ручной сценарий нужен только для диагностики отдельного этапа или
явного resume. Для обычного импорта используйте одну команду выше.

Все пути ниже заключены в кавычки. Переменные относятся только к этой работе;
`HOME` не используется. Выполняйте блок из корня checkout.

```bash
export TL_REPO="$(pwd)"
export TL_RUN_NAME="cc-full-workflow-01"
export TL_DUMP_NAME="cc_full_workflow"

if [[ ! "$TL_RUN_NAME" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo "TL_RUN_NAME must contain only ASCII letters, digits, '_' or '-'" >&2
  exit 2
fi

export TL_DUMP_DIR="$TL_REPO/dumps/$TL_DUMP_NAME"
export TL_DUMP_PCAP="$TL_DUMP_DIR/trafficlab-ready-$TL_DUMP_NAME.pcapng"
export TL_DUMP_METADATA="$TL_DUMP_DIR/capture.json"
export TL_CONFIG_DIR="$TL_REPO/experiments/imported"
export TL_CONFIG="$TL_CONFIG_DIR/$TL_RUN_NAME.toml"
export TL_RUN_ROOT="$TL_REPO/runs/$TL_RUN_NAME"

test -f "$TL_DUMP_PCAP"
test -f "$TL_DUMP_METADATA"
test ! -e "$TL_RUN_ROOT"
mkdir -p "$TL_CONFIG_DIR"

# Сохраняем отдельную переносимую исходную конфигурацию, не меняя template.
cp "examples/configs/default.toml" "$TL_CONFIG"
sed -i "s|^directory = \"../../runs/default\"$|directory = \"../../runs/$TL_RUN_NAME\"|" "$TL_CONFIG"

# Только локальные проверки: создаёт runs/<имя>/experiment.toml и run.log.
uv run --locked trafficlab preflight "$TL_CONFIG" --config-only

# В новый run копируется пара; источник в dumps/ остаётся нетронутым.
cp "$TL_DUMP_METADATA" "$TL_RUN_ROOT/capture.json"
cp "$TL_DUMP_PCAP" "$TL_RUN_ROOT/reference.pcapng"

# На крупных дампах подгонка может длиться часы или дни.
time uv run --locked trafficlab fit "$TL_CONFIG"
time uv run --locked trafficlab generate "$TL_CONFIG"
time uv run --locked trafficlab compare "$TL_CONFIG"
```

`test ! -e` намеренно останавливает сценарий, если имя run уже занято: не
смешивайте результаты разных экспериментов. При новом прогоне выберите новое
`TL_RUN_NAME`, а не удаляйте существующий результат. После `preflight` не
меняйте `$TL_CONFIG`: команды повторно сверяют его с точным
`runs/<имя>/experiment.toml`.

Пути `run.directory` и `target.mounts[].source` относительны не к текущему
каталогу shell, а к каталогу TOML-файла. В walkthrough конфиг лежит в
`experiments/imported/`, поэтому `../../runs/<имя>` означает
`runs/<имя>` в корне checkout. В опубликованном `experiment.toml` эти
хостовые пути уже абсолютные; остальные значения остаются исходными.

## Что происходит по этапам

1. `preflight CONFIG --config-only` проверяет TOML, локальные пути, доступность
   места и создаёт либо открывает строго совпадающий run. Он не обращается к
   Docker и не импортирует дамп.
2. Копирование добавляет в этот run пару `capture.json` / `reference.pcapng`.
   Используйте именно `cp`, не `mv` и не ссылку на файл из `dumps/`.
3. `fit CONFIG` валидирует и нормализует reference, ищет лучший классический
   model family и публикует `best_model.json`. Если `genetic.resume = true`
   (так и есть в template), отсутствие checkpoint начинает поиск, а совместимый
   `checkpoint.json` продолжает его с последнего целиком оценённого поколения.
   Несовместимый checkpoint, изменённый config/reference или `resume = false`
   с checkpoint — ошибка, а не новый поиск поверх старого результата.
4. `generate CONFIG` загружает победившую модель, использует `run.final_seed`
   и final-ограничения, создаёт `generated.pcapng` на полном окне наблюдения.
5. `compare CONFIG` сравнивает reference и generated, сохраняет оценки,
   диагностику, веса и итог в `similarity.json`.

Точные завершённые результаты не перезаписываются молча. После сбоя прочитайте
`"$TL_RUN_ROOT/run.log"`; исправьте причину и повторите подходящий этап.
Checkpoint заменяем только во время совместимого resume; не копируйте его в
другой run.

Полностью завершённый обычный run содержит ровно девять канонических файлов:

```text
experiment.toml   capture.json       reference.pcapng
checkpoint.json   ga_history.csv     best_model.json
generated.pcapng  similarity.json    run.log
```

Проверка набора после последней команды:

```bash
find "$TL_RUN_ROOT" -maxdepth 1 -type f -printf '%f\n' | sort
```

## Открытие dashboard

Dashboard только читает уже завершённый/совместимый run: он не запускает
захват, подгонку, генерацию или сравнение и не пишет в выбранный каталог.

```bash
uv run --locked --all-extras trafficlab-dashboard "$TL_RUN_ROOT"
# либо без аргумента, чтобы открыть системный выбор каталога:
uv run --locked --all-extras trafficlab-dashboard
```

У некоторых ранее сохранённых экспериментов конфигурация лежит в
`runs/<имя>/default.toml`, а девять канонических артефактов — во вложенном
`runs/<имя>/artifacts/`. Для них передавайте именно каталог артефактов:

```bash
uv run --locked --all-extras trafficlab-dashboard \
  "runs/moutai-stock-price-response-success/artifacts"
```

Обязательны `reference.pcapng`, `generated.pcapng` и `capture.json`.
`similarity.json` включает views similarity/multiscale; `ga_history.csv`
включает историю GA только вместе с валидным совпадающим `experiment.toml`;
`best_model.json` проверяется как метаданные модели. Некорректный опциональный
файл отключает лишь зависящие от него виды, а не весь dashboard.

В selector доступны: временные ряды (throughput, packet rate, накопленные
байты/пакеты, размер кадра и IAT по времени), распределения (ECDF и
нормированные histogram), направления (uplink/downlink и balance), зависимости
(ACF и плотность size–IAT), а также similarity, multiscale discrepancy и
история GA. `Reference` и `Generated` независимо показывают две трассы; в
trace-аспекте нельзя отключить обе. В pair/run-level видах эти кнопки отключены
и их состояние сохранится для следующего trace-вида.

- Перетаскивание левой кнопкой мыши — pan обеих осей.
- Колесо — zoom вокруг курсора; `Shift` + колесо — только X; `Ctrl` + колесо —
  только Y.
- Двойной клик или `Reset` возвращает полный вычисленный вид.
- `Export` сохраняет текущий вид в выбранный PNG или SVG, включая viewport,
  видимые данные, подписи, legend и annotations. В run ничего не пишется,
  кроме случая, когда вы сами выбираете его как destination.

## Продвинутая внешняя подготовка capture

`scripts/prepare_traffic_dumps.py --help` поддерживает только следующие
параметры: позиционные пути (по умолчанию `dumps`), `--prefix PREFIX` и
`--organized-root ORGANIZED_ROOT`. Для подготовки нужны программы Wireshark
`editcap` и `reordercap` в `PATH`. Скрипт создаёт упорядоченные,
валидированные копии и не модифицирует источники; существующий output он не
заменяет.

Для уже существующих файлов создайте новые готовые пары в отдельном каталоге:

```bash
export TL_RAW_DIR="$TL_REPO/raw-captures"
export TL_PREPARED_DIR="$TL_REPO/dumps/prepared-local"
uv run --locked python "scripts/prepare_traffic_dumps.py" \
  --organized-root "$TL_PREPARED_DIR" "$TL_RAW_DIR"
```

Результат каждой source-записи расположен как
`"$TL_PREPARED_DIR/<source-stem>/trafficlab-ready-<source-stem>.pcapng"` и
`"$TL_PREPARED_DIR/<source-stem>/capture.json"`. Затем выберите этот каталог
как `TL_DUMP_DIR` в walkthrough. Чтобы подготовить только PCAPNG-копии рядом с
источниками, без `capture.json` и организованного layout, допустим также:

```bash
uv run --locked python "scripts/prepare_traffic_dumps.py" "$TL_RAW_DIR"
```

Для новых запусков внешняя подготовка обычно не нужна: `import-run` сам
нормализует поддерживаемые PCAP/PCAPNG. Скрипт остаётся только для отдельной
ручной диагностики или подготовки копий вне основного workflow.

## Частые проблемы

| Симптом | Что сделать |
| --- | --- |
| `run directory already exists` или `existing run is not reusable` | Не переиспользуйте имя. Создайте новое `TL_RUN_NAME`; для продолжения используйте исходный неизменённый TOML и тот же run. |
| Файлы «не найдены» после переноса TOML | Проверьте путь относительно каталога TOML, а не `pwd`. Для layout выше `run.directory` должен быть `../../runs/<имя>`. |
| `fit` сообщает о недостающих/некорректных capture artifacts | Выполните config-only preflight для нового run, затем скопируйте оба файла под точными именами `capture.json` и `reference.pcapng`. |
| Ошибка PCAPNG/metadata/direction | Подготовьте исходник скриптом выше. Нужны читаемый Ethernet PCAPNG и согласованный `capture.json`; не используйте произвольный PCAP без валидированной пары. |
| Resume отклонён | Не меняйте TOML, `reference.pcapng` или `capture.json` между попытками. Продолжайте только совместимый checkpoint либо начинайте новый run. |
| Dashboard не запускается/Qt не находит display | Повторите `uv sync --locked --all-groups --all-extras`. На headless host GUI не предназначен для интерактивного запуска; для тестов используйте `QT_QPA_PLATFORM=offscreen`, например `QT_QPA_PLATFORM=offscreen uv run --locked --all-extras pytest -q tests/trafficlab_dashboard`. |
| Dashboard отвергает run или отключает часть видов | Требуемая тройка — `reference.pcapng`, `generated.pcapng`, `capture.json`. Сначала завершите `generate`; затем `compare` для similarity views. Читайте точную причину в status/error dialog. |

Для полного Docker-захвата нужен отдельный runnable experiment с реальными
`target` и `capture` значениями; сначала выполняется полный
`trafficlab preflight CONFIG`, затем допустим `trafficlab run CONFIG`. Это
иной workflow, не замена импортированного сценария выше.
