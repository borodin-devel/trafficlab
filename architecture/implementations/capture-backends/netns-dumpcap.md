Статус: основная
Контракт: [стадия захвата](../../stages/10-capture/README.md)
Используется стадиями: [предварительная проверка](../../stages/00-preflight/README.md), [захват](../../stages/10-capture/README.md)

# Network namespace, veth и dumpcap

## Назначение

Реализация изолирует трафик приложения сетевой топологией. Linux network namespace — сетевое пространство имён Linux — получает собственные интерфейсы, адреса, маршруты, firewall и сокеты. Пара виртуальных Ethernet-интерфейсов (`veth`) образует единственную внешнюю точку прохождения пакетов цели.

Основной namespace Windows Subsystem for Linux (WSL) соединяется с target namespace. Дерево приложения отслеживается через control group version 2 (cgroup v2).

## Топология

```text
основной namespace WSL
    |
    | host-side veth -> dumpcap
    |
target network namespace
    |
    +-- eth0
    +-- lo
    |
application cgroup
    |
application + descendants
```

WSL сохраняет один конец `veth` в основном namespace. Второй конец перемещается в target namespace и получает имя `eth0`. Потомки приложения наследуют namespace. `dumpcap` записывает host-side интерфейс, который принадлежит только одному запуску.

Опциональная запись `lo` выполняется отдельным процессом внутри target namespace, но вне cgroup приложения. Файлы внешнего и loopback-захвата объединяются `mergecap`; Packet Capture Next Generation (PCAPNG) сохраняет описания разных интерфейсов.

## Подготовка сети

Helper создаёт короткие уникальные имена и выделяет непересекающуюся подсеть. Он включает `lo`, назначает адреса сторонам `veth` и добавляет default route. Для выхода наружу helper включает forwarding и создаёт scoped-таблицу `nftables` с Network Address Translation (NAT) masquerade и правилами forwarding.

Таблица именуется ресурсом запуска и удаляется целиком. Реализация никогда не выполняет глобальный `flush ruleset`. Для нового входящего соединения она может создать явное перенаправление порта либо подключить контролируемый peer namespace.

## DNS proxy

Domain Name System (DNS) proxy работает в основном namespace WSL и не входит в cgroup приложения. Файл `/etc/netns/<name>/resolv.conf` направляет цель на адрес gateway `veth`. Запрос и ответ между целью и proxy проходят через точку захвата; upstream-трафик proxy туда не входит.

Такой путь не зависит от того, обрабатывает ли WSL системный DNS через tunneling. Адрес proxy, upstream и фактический resolver сохраняются в сетевом манифесте.

## Запись и offload

Перед запуском `dumpcap` helper применяет [общую политику offload](../../common/platform.md#сетевая-разгрузка-и-длина-захвата) к интерфейсам. Неизменяемые возможности записываются как фактическое ограничение.

Основной `dumpcap` не входит в target namespace и cgroup. Readiness проверяется по живому процессу, созданному файлу и существующему host-side интерфейсу. Детальные режимы времени и сигналы принадлежат [жизненному циклу стадии](../../stages/10-capture/lifecycle.md).

## Привилегии и очистка

Минимальный helper с повышенными правами создаёт и удаляет namespace, `veth`, адреса, маршруты, правила `nftables`, DNS-конфигурацию и cgroup. Launcher сбрасывает Group Identifier (GID), User Identifier (UID), дополнительные группы и capabilities до запуска приложения.

Очистка удаляет перенаправления, таблицу NAT, `veth`, namespace, cgroup и временный `resolv.conf`. Все действия ограничены ресурсами текущего запуска и идемпотентны.

## Преимущества

- Граница трафика определяется выделенным интерфейсом, а не Process Identifier (PID).
- Потомки автоматически используют тот же сетевой стек.
- Двунаправленный внешний трафик проходит через одну контролируемую точку.
- Приложение не требует модификации, а терминальный интерфейс не проксируется.

## Ограничения

- Нужны повышенные права для настройки сети и cgroup.
- Loopback не виден на host-side `veth` без отдельной записи.
- Виртуальный Layer 2 (L2), канальный уровень, отличается от физического интерфейса Windows.
- Приложение видит другой адрес и интерфейс.
- Network namespace не изолирует файловую систему, процессы, Inter-Process Communication (IPC), пользователей и системные вызовы; это не sandbox недоверенного приложения.

## Ссылки

- [network_namespaces(7)](https://man7.org/linux/man-pages/man7/network_namespaces.7.html)
- [ip-netns(8)](https://man7.org/linux/man-pages/man8/ip-netns.8.html)
- [dumpcap(1)](https://www.wireshark.org/docs/man-pages/dumpcap.html)
- [Control Group v2](https://docs.kernel.org/admin-guide/cgroup-v2.html)
- [Сеть WSL](https://learn.microsoft.com/en-us/windows/wsl/networking)
- [ethtool(8)](https://man7.org/linux/man-pages/man8/ethtool.8.html)
