# Architecture Document Tree Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перенести всё содержимое `ARCHITECTURE.md` и `CAPTURE.md` в связанное дерево `architecture/`, убрать повторы и удалить исходные файлы.

**Architecture:** Главный путь чтения повторяет стадии рабочего конвейера. Стабильные форматы, общесистемные правила и взаимозаменяемые реализации находятся в отдельных ветвях; документы стадий ссылаются на них относительными ссылками.

**Tech Stack:** Markdown, относительные ссылки Markdown, Git, стандартные команды `rg`, `find` и `git diff`.

## Global Constraints

- Стадии конвейера являются главным способом навигации.
- Один факт имеет один основной документ; полные копии определений, требований и алгоритмов запрещены.
- Взаимозаменяемые реализации одного типа располагаются на одном уровне дерева.
- Нумерация стадий использует шаг 10.
- Идентификаторы `CAP-*`, `DAT-*`, `MOD-*`, `GEN-*`, `CMP-*` и `EVO-*` сохраняются.
- Каждый самостоятельный документ раскрывает термин или сокращение при первом употреблении.
- Текст пишется по-русски, простым, сухим и понятным языком.
- Внутренние ссылки являются относительными.
- После полного переноса `ARCHITECTURE.md` и `CAPTURE.md` удаляются без корневых файлов-указателей.
- Согласованная спецификация: `docs/superpowers/specs/2026-07-13-architecture-document-tree-design.md`.

---

### Task 1: Общие правила и файловые контракты

**Files:**
- Create: `architecture/contracts/stage.md`
- Create: `architecture/contracts/run-directory.md`
- Create: `architecture/contracts/dataset.md`
- Create: `architecture/contracts/packet-events.md`
- Create: `architecture/contracts/model-artifact.md`
- Create: `architecture/contracts/metric-result.md`
- Create: `architecture/contracts/validation-report.md`
- Create: `architecture/common/artifact-lifecycle.md`
- Create: `architecture/common/reproducibility.md`
- Create: `architecture/common/orchestration.md`
- Create: `architecture/common/resources.md`
- Create: `architecture/common/observability.md`
- Create: `architecture/common/security.md`
- Create: `architecture/common/platform.md`

**Interfaces:**
- Consumes: согласованную спецификацию; `ARCHITECTURE.md:195-270`, `ARCHITECTURE.md:716-928`, `ARCHITECTURE.md:1298-1500`; платформенные ограничения из `CAPTURE.md`.
- Produces: основные контракты и общие правила, на которые ссылаются все последующие стадии.

- [ ] **Step 1: Составить карту исходных разделов задачи**

Зафиксировать в рабочих заметках владельцев содержания:

```text
stage.md              <- ARCHITECTURE 4.2-4.3
run-directory.md      <- ARCHITECTURE 19
dataset.md            <- ARCHITECTURE 10.1-10.6
packet-events.md      <- ARCHITECTURE 3.5, 13
model-artifact.md     <- ARCHITECTURE 11
metric-result.md      <- ARCHITECTURE 3.6, 15.6
validation-report.md  <- ARCHITECTURE 14.1
artifact-lifecycle.md <- ARCHITECTURE 4.2-4.3
reproducibility.md    <- ARCHITECTURE 4.1
orchestration.md      <- ARCHITECTURE 1, 8.11
resources.md          <- ARCHITECTURE 4.4, 17
observability.md      <- ARCHITECTURE 18
security.md           <- ARCHITECTURE 2.2; CAPTURE 2.2, 18
platform.md           <- ARCHITECTURE 2.3; CAPTURE 3, 5, 11, 18
```

- [ ] **Step 2: Написать контракты**

Для каждого файла определить назначение, обязательные поля, версии, инварианты и потребителей. Перенести структуры JSON, Parquet и каталогов без изменения имён полей. В `packet-events.md` описать абстрактное событие, но не алгоритм конкретной модели или renderer.

- [ ] **Step 3: Написать общие правила**

Разделить воспроизводимость, жизненный цикл, ресурсы, наблюдаемость, безопасность и платформу. Не повторять структуры контрактов. В `orchestration.md` раскрыть «ориентированный ациклический граф — Directed Acyclic Graph (DAG)» и описать кэширование, возобновление и частичный запуск.

- [ ] **Step 4: Проверить полноту и отсутствие повторов**

Run:

```bash
rg -n '^#|stage_key|schema_version|quality_vector|global_memory_budget|QueueHandler|WSL' architecture/contracts architecture/common
git diff --check -- architecture/contracts architecture/common
```

Expected: все перечисленные понятия имеют одного владельца; `git diff --check` не выводит ошибок.

- [ ] **Step 5: Commit**

```bash
git add architecture/contracts architecture/common
git commit -m "docs: add shared architecture contracts"
```

---

### Task 2: Предварительная проверка и захват

**Files:**
- Create: `architecture/stages/00-preflight/README.md`
- Create: `architecture/stages/10-capture/README.md`
- Create: `architecture/stages/10-capture/lifecycle.md`
- Create: `architecture/stages/10-capture/network-environment.md`
- Create: `architecture/stages/10-capture/process-control.md`
- Create: `architecture/stages/10-capture/packet-capture.md`
- Create: `architecture/stages/10-capture/publication.md`
- Create: `architecture/implementations/capture-backends/netns-dumpcap.md`
- Create: `architecture/implementations/capture-backends/ebpf-cgroup.md`
- Create: `architecture/implementations/capture-backends/container.md`
- Create: `architecture/implementations/validators/packet-capture.md`

**Interfaces:**
- Consumes: `architecture/contracts/stage.md`, `architecture/contracts/run-directory.md`, общие правила Task 1; `ARCHITECTURE.md:80-123`, `ARCHITECTURE.md:271-435`; весь `CAPTURE.md`.
- Produces: описание первых двух стадий и трёх равноправных реализаций захвата.

- [ ] **Step 1: Написать предварительную проверку и обзор стадии захвата**

`00-preflight/README.md` должен перечислять проверки WSL 2, control group version 2, внешних команд, прав, диска, памяти, сети, Domain Name System и конфликтов подсети. `10-capture/README.md` должен содержать требования `CAP-001`–`CAP-015`, входы, выходы, инварианты и ссылки на подробные файлы.

- [ ] **Step 2: Разделить общий процесс захвата по владельцам**

```text
lifecycle.md           <- подготовка, готовность, режимы завершения, очистка
network-environment.md <- адресация, маршруты, DNS, входящие соединения, offload
process-control.md      <- cgroup v2, launcher, UID/GID, сигналы, TUI
packet-capture.md       <- dumpcap, интерфейсы, направление, snaplen, остановка
publication.md          <- файлы стадии, capinfos, статус и публикация
```

Перенести режимы `until-exit`, `duration` и `duration-or-exit` один раз в `lifecycle.md`. Другие файлы должны ссылаться на этот раздел.

- [ ] **Step 3: Написать реализации захвата**

`netns-dumpcap.md` получает подробную схему Linux network namespace, `veth`, `nftables`, DNS proxy, `dumpcap` и ограничения. `ebpf-cgroup.md` и `container.md` получают только собственные преимущества, недостатки и статус. Каждый файл начинает блок метаданных `Статус`, `Контракт`, `Используется стадиями`.

- [ ] **Step 4: Написать реализацию первичной проверки PCAPNG**

`implementations/validators/packet-capture.md` описывает проверки `capinfos` и `tshark`, различие структурной корректности и пригодности набора данных. Правила публикации остаются в `stages/10-capture/publication.md`.

- [ ] **Step 5: Проверить требования и границы**

Run:

```bash
for id in CAP-001 CAP-002 CAP-003 CAP-004 CAP-005 CAP-006 CAP-007 CAP-008 CAP-009 CAP-010 CAP-011 CAP-012 CAP-013 CAP-014 CAP-015; do rg -q "$id" architecture/stages/10-capture/README.md || exit 1; done
rg -n 'until-exit|duration-or-exit|network namespace|cgroup v2|dumpcap|capinfos' architecture/stages/00-preflight architecture/stages/10-capture architecture/implementations/capture-backends architecture/implementations/validators/packet-capture.md
git diff --check -- architecture/stages/00-preflight architecture/stages/10-capture architecture/implementations
```

Expected: все `CAP-*` найдены; подробные темы имеют одного основного владельца; ошибок пробелов нет.

- [ ] **Step 6: Commit**

```bash
git add architecture/stages/00-preflight architecture/stages/10-capture architecture/implementations/capture-backends architecture/implementations/validators/packet-capture.md
git commit -m "docs: describe capture architecture"
```

---

### Task 3: Конвертация и проверка исходного набора

**Files:**
- Create: `architecture/stages/20-convert/README.md`
- Create: `architecture/stages/30-validate-source/README.md`
- Create: `architecture/stages/30-validate-source/rules.md`
- Create: `architecture/implementations/packet-decoders/tshark.md`
- Create: `architecture/implementations/validators/dataset.md`
- Create: `architecture/implementations/feature-extractors/README.md`

**Interfaces:**
- Consumes: dataset и validation contracts; `ARCHITECTURE.md:124-138`, `ARCHITECTURE.md:503-539`, `ARCHITECTURE.md:632-660`, `ARCHITECTURE.md:1039-1111`.
- Produces: стадии построения и проверки исходного набора, decoder `tshark`, правила dataset validation и контракт расширения признаков.

- [ ] **Step 1: Написать стадию конвертации**

Перенести требования `DAT-001`–`DAT-010`, потоковую обработку, детерминизм, группировку packet/flow/session и поддерживаемые режимы экспорта. Поля набора не копировать: дать ссылку на `contracts/dataset.md`.

- [ ] **Step 2: Написать стадию проверки исходного набора**

В `README.md` описать место стадии и результат. В `rules.md` разместить канонический список проверок схемы, индексов, времени, длин, направлений, ссылочной целостности, хешей и разделения сессий.

- [ ] **Step 3: Написать decoder, validator и точку расширения признаков**

`tshark.md` описывает выбранный decoder и его границы. `validators/dataset.md` описывает реализацию правил `rules.md`. `feature-extractors/README.md` описывает единый контракт, требования к схеме признаков и явную регистрацию без выдумывания отсутствующих реализаций.

- [ ] **Step 4: Проверить покрытие**

Run:

```bash
for n in $(seq -w 1 10); do rg -q "DAT-0$n" architecture/stages/20-convert/README.md || exit 1; done
rg -n 'packets.parquet|flows.parquet|sessions.parquet' architecture/contracts/dataset.md
git diff --check -- architecture/stages/20-convert architecture/stages/30-validate-source architecture/implementations/packet-decoders architecture/implementations/validators/dataset.md architecture/implementations/feature-extractors
```

Expected: требования `DAT-*` присутствуют; схема хранится в контракте; ошибок пробелов нет.

- [ ] **Step 5: Commit**

```bash
git add architecture/stages/20-convert architecture/stages/30-validate-source architecture/implementations/packet-decoders architecture/implementations/validators/dataset.md architecture/implementations/feature-extractors
git commit -m "docs: describe dataset preparation"
```

---

### Task 4: Обучение и модели

**Files:**
- Create: `architecture/stages/40-train/README.md`
- Create: `architecture/implementations/models/independent-empirical.md`
- Create: `architecture/implementations/models/conditional-empirical.md`
- Create: `architecture/implementations/models/semi-markov.md`
- Create: `architecture/implementations/models/gru-autoregressive.md`
- Create: `architecture/implementations/models/transformer-autoregressive.md`
- Create: `architecture/implementations/models/vae-sequence.md`
- Create: `architecture/implementations/artifact-serializers/json-parquet-npz.md`
- Create: `architecture/implementations/artifact-serializers/safetensors.md`

**Interfaces:**
- Consumes: dataset и model artifact contracts; `ARCHITECTURE.md:139-151`, `ARCHITECTURE.md:540-552`, `ARCHITECTURE.md:661-668`, `ARCHITECTURE.md:904-993`.
- Produces: общий процесс обучения, шесть равноправных моделей и безопасные способы сериализации.

- [ ] **Step 1: Написать общий процесс обучения**

Перенести `MOD-001`–`MOD-008`, правила выбора через `model.type`, требования к feature schema, seed, resource budget, параллелизм и запрет прямой зависимости от захвата.

- [ ] **Step 2: Написать модели первой версии**

Каждая модель получает назначение, входные признаки, способ обучения, способ генерации, ограничения и статус. Формат model artifact не копировать. `semi-markov.md` раскрывает полумарковскую модель; `gru-autoregressive.md` раскрывает Gated Recurrent Unit; `vae-sequence.md` раскрывает Variational Autoencoder.

- [ ] **Step 3: Написать сериализаторы**

`json-parquet-npz.md` описывает JSON, Apache Parquet и NumPy NPZ без объектов. `safetensors.md` описывает веса нейронных сетей. Оба файла ссылаются на `contracts/model-artifact.md`; запрет Python pickle остаётся в контракте.

- [ ] **Step 4: Проверить равноправие моделей и требования**

Run:

```bash
for n in $(seq -w 1 8); do rg -q "MOD-00$n" architecture/stages/40-train/README.md || exit 1; done
find architecture/implementations/models -maxdepth 1 -type f | sort
rg -n 'Статус:|Контракт:|Используется стадиями:' architecture/implementations/models architecture/implementations/artifact-serializers
git diff --check -- architecture/stages/40-train architecture/implementations/models architecture/implementations/artifact-serializers
```

Expected: шесть моделей находятся на одном уровне; все требования и метаданные присутствуют.

- [ ] **Step 5: Commit**

```bash
git add architecture/stages/40-train architecture/implementations/models architecture/implementations/artifact-serializers
git commit -m "docs: describe training and models"
```

---

### Task 5: Генерация и проверка синтетического трафика

**Files:**
- Create: `architecture/stages/50-generate/README.md`
- Create: `architecture/stages/50-generate/offline-synthesis.md`
- Create: `architecture/stages/50-generate/active-emulation.md`
- Create: `architecture/stages/60-validate-synthetic/README.md`
- Create: `architecture/stages/60-validate-synthetic/rules.md`
- Create: `architecture/implementations/packet-renderers/scapy.md`
- Create: `architecture/implementations/generators/model-based.md`
- Create: `architecture/implementations/generators/random.md`
- Create: `architecture/implementations/generators/replay-perturbed.md`
- Create: `architecture/implementations/generators/active-emulation.md`
- Create: `architecture/implementations/validators/synthetic-traffic.md`

**Interfaces:**
- Consumes: packet event, dataset и validation contracts; `ARCHITECTURE.md:152-167`, `ARCHITECTURE.md:546-558`, `ARCHITECTURE.md:669-676`, `ARCHITECTURE.md:994-1038`, `ARCHITECTURE.md:1112-1130`.
- Produces: offline и active generation, четыре режима генерации, Scapy renderer и synthetic validator.

- [ ] **Step 1: Написать стадию генерации**

Перенести `GEN-001`–`GEN-009`. `README.md` хранит общий поток от модели до абстрактных событий. `offline-synthesis.md` хранит материализацию PCAPNG. `active-emulation.md` хранит процесс с двумя изолированными endpoint и запрет выхода в реальную сеть.

- [ ] **Step 2: Написать реализации**

Создать равноправные режимы `model-based`, `random`, `replay-perturbed` и `active-emulation`. `scapy.md` описывает только построение корректных Ethernet/IP/TCP/UDP-пакетов, длины, checksum и потоковую запись.

- [ ] **Step 3: Написать синтетическую проверку**

`rules.md` хранит канонические проверки времени, длины, TCP state, checksum, адресов, числа flow/session, burst rate и mode collapse. Файл реализации validator ссылается на правила без их полного повтора.

- [ ] **Step 4: Проверить требования и границы**

Run:

```bash
for n in $(seq -w 1 9); do rg -q "GEN-00$n" architecture/stages/50-generate/README.md || exit 1; done
find architecture/implementations/generators -maxdepth 1 -type f | sort
git diff --check -- architecture/stages/50-generate architecture/stages/60-validate-synthetic architecture/implementations/packet-renderers architecture/implementations/generators architecture/implementations/validators/synthetic-traffic.md
```

Expected: все `GEN-*` найдены; четыре режима лежат на одном уровне; правила не скопированы в validator.

- [ ] **Step 5: Commit**

```bash
git add architecture/stages/50-generate architecture/stages/60-validate-synthetic architecture/implementations/packet-renderers architecture/implementations/generators architecture/implementations/validators/synthetic-traffic.md
git commit -m "docs: describe synthetic generation"
```

---

### Task 6: Сравнение и метрики

**Files:**
- Create: `architecture/stages/70-compare/README.md`
- Create: `architecture/stages/70-compare/evaluation.md`
- Create: `architecture/implementations/metrics/distributions.md`
- Create: `architecture/implementations/metrics/sequences.md`
- Create: `architecture/implementations/metrics/protocols.md`
- Create: `architecture/implementations/metrics/flows-and-sessions.md`
- Create: `architecture/implementations/metrics/classifier-two-sample-test.md`

**Interfaces:**
- Consumes: dataset и metric result contracts; `ARCHITECTURE.md:168-179`, `ARCHITECTURE.md:559-566`, `ARCHITECTURE.md:677-685`, `ARCHITECTURE.md:1131-1225`.
- Produces: стадия сравнения, схема многокритериальной оценки и пять равноправных семейств метрик.

- [ ] **Step 1: Написать стадию сравнения**

Перенести `CMP-001`–`CMP-007`, входы, выходы и запрет замены индивидуальных метрик одним score. `evaluation.md` описывает `quality_vector`, Pareto ranking и роль сводной оценки.

- [ ] **Step 2: Написать семейства метрик**

Распределить существующие метрики без копий: распределения, последовательности, протоколы, flows/sessions и Classifier Two-Sample Test. В последнем файле раскрыть термин и ограничения интерпретации.

- [ ] **Step 3: Проверить покрытие**

Run:

```bash
for n in $(seq -w 1 7); do rg -q "CMP-00$n" architecture/stages/70-compare/README.md || exit 1; done
find architecture/implementations/metrics -maxdepth 1 -type f | sort
git diff --check -- architecture/stages/70-compare architecture/implementations/metrics
```

Expected: семь требований и пять семейств метрик присутствуют.

- [ ] **Step 4: Commit**

```bash
git add architecture/stages/70-compare architecture/implementations/metrics
git commit -m "docs: describe comparison metrics"
```

---

### Task 7: Эволюционный цикл

**Files:**
- Create: `architecture/stages/80-evolve/README.md`
- Create: `architecture/stages/80-evolve/search-cycle.md`
- Create: `architecture/implementations/optimizers/nsga-ii.md`

**Interfaces:**
- Consumes: model, dataset, validation и metric contracts; стадии train, generate, validate-synthetic и compare; `ARCHITECTURE.md:180-194`, `ARCHITECTURE.md:567-580`, `ARCHITECTURE.md:686-695`, `ARCHITECTURE.md:1226-1297`.
- Produces: циклическая стадия поиска и выбранная реализация многокритериального оптимизатора.

- [ ] **Step 1: Написать стадию и цикл поиска**

Перенести `EVO-001`–`EVO-008`, genotype hash, cache, genealogy, checkpoints, ограничения и условия завершения. `search-cycle.md` должен ссылаться на повторно вызываемые стадии, а не копировать их процессы.

- [ ] **Step 2: Написать NSGA-II**

Раскрыть Non-dominated Sorting Genetic Algorithm II. Описать недоминируемую сортировку, Pareto selection, mutation и crossover. Не копировать общий цикл стадии.

- [ ] **Step 3: Проверить покрытие**

Run:

```bash
for n in $(seq -w 1 8); do rg -q "EVO-00$n" architecture/stages/80-evolve/README.md || exit 1; done
rg -n '40-train|50-generate|60-validate-synthetic|70-compare' architecture/stages/80-evolve/search-cycle.md
git diff --check -- architecture/stages/80-evolve architecture/implementations/optimizers
```

Expected: восемь требований найдены; цикл содержит ссылки на четыре стадии.

- [ ] **Step 4: Commit**

```bash
git add architecture/stages/80-evolve architecture/implementations/optimizers
git commit -m "docs: describe evolutionary search"
```

---

### Task 8: Обзор системы и каталог реализаций

**Files:**
- Create: `architecture/README.md`
- Create: `architecture/system/overview.md`
- Create: `architecture/system/scope.md`
- Create: `architecture/system/pipeline.md`
- Create: `architecture/system/programs.md`
- Create: `architecture/system/modules.md`
- Create: `architecture/system/scenarios.md`
- Create: `architecture/implementations/README.md`

**Interfaces:**
- Consumes: все документы Tasks 1–7; `ARCHITECTURE.md:1-79`, `ARCHITECTURE.md:436-746`, `ARCHITECTURE.md:1501-1586`.
- Produces: единая точка входа, линейная карта стадий, описание программ и модулей, каталог точек расширения.

- [ ] **Step 1: Написать системные документы**

```text
overview.md  <- итоговое архитектурное решение
scope.md     <- входит, не входит, ограничения первой версии
pipeline.md  <- порядок 00-80, входы/выходы и цикл evolution
programs.md  <- console entry points и их ответственность
modules.md   <- внутренние библиотеки и их границы
scenarios.md <- команды полного, частичного и возобновлённого запуска
```

- [ ] **Step 2: Написать каталог реализаций**

Перенести список заменяемых контрактов и правило явной регистрации. Перечислить все дочерние каталоги `implementations/` и дать ссылки. Не повторять описание конкретных реализаций.

- [ ] **Step 3: Написать главную карту**

`architecture/README.md` должен содержать рекомендуемый порядок чтения, полное дерево верхнего уровня, правила выбора владельца нового документа и ссылки на все стадии.

- [ ] **Step 4: Проверить навигацию**

Run:

```bash
for stage in 00-preflight 10-capture 20-convert 30-validate-source 40-train 50-generate 60-validate-synthetic 70-compare 80-evolve; do rg -q "$stage" architecture/README.md architecture/system/pipeline.md || exit 1; done
find architecture/implementations -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort
git diff --check -- architecture/README.md architecture/system architecture/implementations/README.md
```

Expected: все девять стадий доступны из двух карт; все семейства реализаций перечислены.

- [ ] **Step 5: Commit**

```bash
git add architecture/README.md architecture/system architecture/implementations/README.md
git commit -m "docs: add architecture navigation"
```

---

### Task 9: Документы проекта и окончательный перенос

**Files:**
- Create: `architecture/project/repository.md`
- Create: `architecture/project/dependencies.md`
- Create: `architecture/project/code-style.md`
- Create: `architecture/project/testing.md`
- Create: `architecture/project/assets.md`
- Create: `architecture/project/roadmap.md`
- Create: `architecture/project/decisions.md`
- Create: `architecture/project/risks.md`
- Delete: `ARCHITECTURE.md`
- Delete: `CAPTURE.md`

**Interfaces:**
- Consumes: `ARCHITECTURE.md:1587-2181`, полный результат Tasks 1–8 и критерии готовности спецификации.
- Produces: проектные правила, полное дерево без исходных монолитных документов и итоговую проверку ссылок и терминов.

- [ ] **Step 1: Написать документы проекта**

```text
repository.md   <- структура репозитория и границы исходного кода
dependencies.md <- Python 3.12, uv, runtime и optional dependencies
code-style.md   <- functional core, imperative shell, правила классов
testing.md      <- unit, contract, integration, system, fixtures, coverage
assets.md       <- Git, Git LFS, DVC/object storage, manifests
roadmap.md      <- девять этапов разработки
decisions.md    <- ADR-001 - ADR-016 с раскрытием Architecture Decision Record
risks.md        <- риски и меры
```

- [ ] **Step 2: Проверить карту переноса исходных заголовков**

Run:

```bash
rg -n '^#{1,6} ' ARCHITECTURE.md CAPTURE.md
rg -n '^#{1,6} ' architecture
```

Сопоставить каждый содержательный исходный заголовок с новым документом. Особое внимание: L2, DNS, TUI, offload, run directory, модели, метрики, ресурсы, активная эмуляция, тесты, решения и риски.

- [ ] **Step 3: Проверить относительные Markdown-ссылки**

Run:

```bash
while IFS= read -r file; do
  base=$(dirname "$file")
  rg -o '\[[^]]+\]\(([^)#]+\.md)(#[^)]+)?\)' "$file" | sed -E 's/.*\]\(([^)#]+\.md).*/\1/' | while IFS= read -r link; do
    test -f "$base/$link" || { echo "$file: broken link: $link"; exit 1; }
  done || exit 1
done < <(find architecture -type f -name '*.md' | sort)
```

Expected: команда не выводит `broken link` и завершается с кодом 0.

- [ ] **Step 4: Проверить язык, идентификаторы и структуру**

Run:

```bash
rg -n 'TBD|TODO|backend|renderer|DAG|C2ST|GRU|VAE|NSGA-II|WSL|TUI|L2|NAT|DNS' architecture
for prefix in CAP DAT MOD GEN CMP EVO; do rg -q "$prefix-" architecture || exit 1; done
find architecture -type f -name '*.md' | sort
git diff --check
```

Проверить каждое найденное сокращение: первое употребление в самостоятельном документе должно иметь расшифровку. Английские `backend` и `renderer` должны быть заменены русским описанием либо пояснены.

- [ ] **Step 5: Удалить исходные документы**

Удалить `ARCHITECTURE.md` и `CAPTURE.md` через patch после успешных Steps 2–4. Не создавать корневые файлы-указатели.

- [ ] **Step 6: Выполнить финальную проверку**

Run:

```bash
test ! -e ARCHITECTURE.md
test ! -e CAPTURE.md
test -f architecture/README.md
test -f architecture/system/pipeline.md
test -f architecture/stages/10-capture/README.md
test -f architecture/implementations/models/semi-markov.md
git status --short
git diff --check
```

Expected: исходные файлы отсутствуют; обязательные точки входа существуют; ошибок пробелов нет; `git status` показывает только ожидаемые документы архитектуры и план.

- [ ] **Step 7: Commit**

```bash
git add architecture/project architecture
git commit -m "docs: complete architecture document tree"
```

---

### Task 10: Итоговая проверка против спецификации

**Files:**
- Verify: `architecture/**/*.md`
- Verify: `docs/superpowers/specs/2026-07-13-architecture-document-tree-design.md`

**Interfaces:**
- Consumes: полностью собранное дерево.
- Produces: доказательство выполнения всех десяти критериев готовности.

- [ ] **Step 1: Проверить покрытие спецификации**

Проверить по очереди все десять пунктов раздела «Критерии готовности». Для каждого пункта указать файл или команду, подтверждающую выполнение. При найденном пробеле исправить основной документ, не добавляя копию.

- [ ] **Step 2: Проверить рабочее дерево**

Run:

```bash
git status --short
git log --oneline -10
git diff HEAD --check
find architecture -type f -name '*.md' | wc -l
```

Expected: нет незакоммиченных изменений от переноса; история содержит отдельные тематические коммиты; дерево содержит все файлы согласованной спецификации.

- [ ] **Step 3: Передать результат**

Сообщить путь `architecture/README.md`, число созданных документов, удаление двух исходных файлов, результаты проверки ссылок и `git diff --check`.
