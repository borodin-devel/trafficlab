# Task 2 report: Предварительная проверка и захват

## Status

Complete.

Commits:

- `29bcc84` (`docs: describe capture architecture`) — исходная реализация;
- `88f2f35` (`docs: address capture architecture review`) — первый раунд исправлений review;
- `3133fac` (`docs: address second capture review`) — второй раунд исправлений review.

## Files

- `architecture/stages/00-preflight/README.md`
- `architecture/stages/10-capture/README.md`
- `architecture/stages/10-capture/lifecycle.md`
- `architecture/stages/10-capture/network-environment.md`
- `architecture/stages/10-capture/process-control.md`
- `architecture/stages/10-capture/packet-capture.md`
- `architecture/stages/10-capture/publication.md`
- `architecture/implementations/capture-backends/netns-dumpcap.md`
- `architecture/implementations/capture-backends/ebpf-cgroup.md`
- `architecture/implementations/capture-backends/container.md`
- `architecture/implementations/validators/packet-capture.md`

## Verification

### Brief: CAP requirements

```bash
for id in CAP-001 CAP-002 CAP-003 CAP-004 CAP-005 CAP-006 CAP-007 CAP-008 CAP-009 CAP-010 CAP-011 CAP-012 CAP-013 CAP-014 CAP-015; do rg -q "$id" architecture/stages/10-capture/README.md || exit 1; done
```

Result: exit `0`, no output. All 15 identifiers are present in the owning stage README.

### Brief: detailed-topic search

```bash
rg -n 'until-exit|duration-or-exit|network namespace|cgroup v2|dumpcap|capinfos' architecture/stages/00-preflight architecture/stages/10-capture architecture/implementations/capture-backends architecture/implementations/validators/packet-capture.md
```

Result: exit `0`. Matches were found in the intended owners and references. `until-exit` and `duration-or-exit` occur in `architecture/stages/10-capture/lifecycle.md`; detailed `capinfos` checks occur in `architecture/implementations/validators/packet-capture.md`; the concrete namespace/veth/dumpcap topology occurs in `architecture/implementations/capture-backends/netns-dumpcap.md`.

### Brief: whitespace

```bash
git diff --check -- architecture/stages/00-preflight architecture/stages/10-capture architecture/implementations
```

Result: exit `0`, no output.

## Second review fixes

Исправлены все четыре пункта второго review:

- preflight условно проверяет `mergecap`, когда включён loopback-захват и требуется объединение файлов;
- metadata eBPF backend содержит точное значение `Статус: будущая`, а недоступность первой версии остаётся в основном тексте;
- в container backend используются формулировки «монтирований, терминала и настройки пользователя»;
- в отчёт добавлены SHA первого (`88f2f35`) и второго (`3133fac`) раундов исправлений.

### Focused second-review checks

```bash
rg -n 'mergecap|loopback-захват' architecture/stages/00-preflight/README.md architecture/implementations/capture-backends/netns-dumpcap.md
```

Result: exit `0`, ровно 2 строки. `netns-dumpcap.md` задаёт условие объединения loopback-файла, а preflight при этом условии требует `mergecap`.

```bash
rg -n '^Статус: будущая$' architecture/implementations/capture-backends/ebpf-cgroup.md
rg -n 'недоступна для первой версии|не входит в первую версию' architecture/implementations/capture-backends/ebpf-cgroup.md
```

Result: обе команды завершились с exit `0`; первая вывела ровно 1 строку с точным metadata-значением, вторая — ровно 2 строки основного текста о недоступности первой версии.

```bash
rg -n 'mount, terminal|user-настроек' architecture/implementations/capture-backends/container.md
```

Result: exit `1`, no output. Удалена смешанная англо-русская формулировка.

```bash
rg -n 'монтирований, терминала и настройки пользователя' architecture/implementations/capture-backends/container.md
```

Result: exit `0`, ровно 1 строка с требуемой формулировкой.

Повторно выполнены focused-проверки первого review: `dataset_usable`, `CAP-002|CAP-005`, `offload|snaplen` и `PCAPNG` завершились с exit `0` и вывели соответственно 5, 4, 10 и 1 строку; поиск скопированных подробных значений `TSO|GSO|GRO|LRO|snaplen = 256|snaplen = 0` завершился с exit `1`, no output.

```bash
git diff --check
```

Result: exit `0`, no output.

### Relative links

```bash
perl -MFile::Basename=dirname -MFile::Spec -e '$bad=0; for $f (@ARGV) { open $h, "<", $f or die $!; local $/; $s=<$h>; while ($s =~ /\]\(([^)]+)\)/g) { $l=$1; next if $l =~ m{^https?://}; ($p=$l)=~s/#.*$//; next if $p eq ""; $q=File::Spec->rel2abs($p,dirname($f)); if (!-e $q) { print "$f: $l\n"; $bad=1 } } } exit $bad' architecture/stages/00-preflight/README.md architecture/stages/10-capture/*.md architecture/implementations/capture-backends/*.md architecture/implementations/validators/packet-capture.md
```

Result: exit `0`, no output. Every relative file target exists.

### Single owner for completion modes

```bash
rg -l 'until-exit|duration-or-exit' architecture/stages/10-capture architecture/implementations/capture-backends architecture/implementations/validators/packet-capture.md
```

Result: exit `0`, exactly one file:

```text
architecture/stages/10-capture/lifecycle.md
```

### Implementation metadata

```bash
for f in architecture/implementations/capture-backends/*.md architecture/implementations/validators/packet-capture.md; do sed -n '1,3p' "$f" | rg -q '^Статус:|^Контракт:|^Используется стадиями:' || exit 1; done
```

Result: exit `0`, no output.

### Commit scope

```bash
git diff --cached --name-only
```

Result before commit: exit `0`, exactly the 11 files listed above.

```bash
git commit -m "docs: describe capture architecture"
```

Result: exit `0`; commit `29bcc84`; `11 files changed, 520 insertions(+)`.

## Self-review

- Re-read the staged diff against every step in `task-2-brief.md`.
- Confirmed both stage README files follow the standard stage sections.
- Confirmed `CAP-001` through `CAP-015` are owned by the capture stage and are not copied into implementation documents.
- Confirmed the three completion modes are defined only in `lifecycle.md`; other stage files link to that section.
- Confirmed general network, process, packet-recording and publication rules remain in stage documents, while the concrete namespace/veth/nftables/DNS proxy/dumpcap scheme remains in `netns-dumpcap.md`.
- Confirmed alternative backends contain only their own trade-offs and status.
- Confirmed `capinfos`/`tshark` mechanics remain in the validator and publication policy remains in the stage.
- Reviewed first-use expansion of abbreviations in every standalone document. Fixed WSL/cgroup ordering before the implementation topology and removed one accidental untranslated word.
- Confirmed no file outside Task 2 was staged or committed.

## Concerns

None.

## Review fixes

Исправлены все findings Task 2:

- eBPF backend явно исключён из CAP-контракта первой версии из-за несовместимости с `CAP-002` и `CAP-005`; для него потребуется новая версия контракта;
- gate следующей стадии требует одновременно `status = completed` в `stage.json` и `dataset_usable = true` в `validation.json`; владельцем вычисления поля указан валидатор;
- структурно корректный пустой PCAPNG может завершиться как `completed`, но получает `dataset_usable = false` и не передаётся дальше;
- подробные значения offload и `snaplen` удалены из документов стадии и backend, вместо них оставлены относительные ссылки на `architecture/common/platform.md`;
- первое употребление PCAPNG в `lifecycle.md` раскрыто, `ID` заменено на «Идентификатор».

### Focused review checks

```bash
rg -n 'dataset_usable' architecture/stages/10-capture architecture/implementations/capture-backends architecture/implementations/validators/packet-capture.md
rg -n 'CAP-002|CAP-005' architecture/stages/10-capture architecture/implementations/capture-backends architecture/implementations/validators/packet-capture.md
rg -n 'offload|snaplen' architecture/stages/10-capture architecture/implementations/capture-backends architecture/implementations/validators/packet-capture.md
rg -n 'PCAPNG' architecture/stages/10-capture/lifecycle.md
```

Result: каждая команда завершилась с exit `0`. Stdout содержит соответственно ровно 5, 4, 10 и 1 строку. Gate встречается в README и публикации, владелец поля указан в публикации, несовместимость eBPF с обоими требованиями указана в README и eBPF-документе, а первое употребление PCAPNG в `lifecycle.md` раскрыто как Packet Capture Next Generation.

```bash
rg -n 'TSO|GSO|GRO|LRO|snaplen = 256|snaplen = 0' architecture/stages/10-capture architecture/implementations/capture-backends architecture/implementations/validators/packet-capture.md
```

Result: exit `1`, no output. Копий подробных значений offload и `snaplen` в документах Task 2 нет; канонические значения остаются в `architecture/common/platform.md`.

```bash
git diff --check
```

Result: exit `0`, no output.
