# Платформа и ограничения

## Поддерживаемая среда

Первая версия запускает Linux-приложения внутри текущего дистрибутива Windows Subsystem for Linux version 2 (WSL 2). Захват Windows-приложений и процессов другого WSL-дистрибутива не поддерживается.

Минимальная среда включает Python 3.12, `ip` из `iproute2`, `nft` из `nftables`, `dumpcap`, `capinfos`, `tshark`, `ethtool` и control group version 2 (cgroup v2). Preflight проверяет наличие конкретных команд и `/sys/fs/cgroup/cgroup.controllers`, а не названия пакетов дистрибутива.

## Сетевой уровень

Захват на виртуальном Ethernet-интерфейсе `veth` показывает Ethernet внутри Linux-среды WSL, а не физический Ethernet или Wi-Fi Windows. В этой системе Layer 2 (L2) означает длину кадра в точке захвата, Ethernet type, адреса виртуальной топологии, направление и временную последовательность.

Данные не описывают Wi-Fi retransmissions, radiotap, физический Virtual Local Area Network (VLAN), аппаратные временные метки, реальный Ethernet padding и поведение драйвера сетевого адаптера Windows.

Unix domain sockets, pipe, First In First Out (FIFO), shared memory и стандартные потоки не являются IP-трафиком и не попадают в Packet Capture Next Generation (PCAPNG). Loopback внутри целевого namespace виден только при отдельном захвате этого интерфейса.

## Режим сети WSL

WSL может работать в режиме Network Address Translation (NAT) или mirrored networking. Режим определяется в preflight и сохраняется в метаданных запуска. Возможности IPv6, Virtual Private Network (VPN), multicast и доступа к Windows через localhost зависят от режима; стадия не предполагает их наличие без проверки.

DNS tunneling Windows 11 может обходить ожидаемую точку пакетного захвата. Для воспроизводимого захвата целевой namespace использует явный DNS proxy в основном namespace WSL. Его адрес и фактическая конфигурация сохраняются в метаданных.

## Сетевая разгрузка и длина захвата

Перед захватом helper проверяет и по возможности отключает TCP Segmentation Offload (TSO), Generic Segmentation Offload (GSO), Generic Receive Offload (GRO), Large Receive Offload (LRO), а также TX и RX checksum offload на экспериментальных интерфейсах. Запрошенные и фактические значения сохраняются раздельно. Валидатор учитывает функции, которые отключить не удалось.

Рекомендуемая максимальная сохраняемая длина пакета — `snaplen = 256`. Она сохраняет заголовки, флаги и исходную длину, но сокращает payload. Значение `snaplen = 0` разрешает полный пакет для специальных исследований.

## Ограничения масштаба

Первая версия не включает распределённое обучение, Kubernetes, Ray, Dask, Celery, Kafka и сервер MLflow. Фактический бюджет памяти определяется внутри WSL по [общей политике ресурсов](resources.md).

## Потребители

Ограничения учитывают preflight, захват, валидация, воспроизводимость, планировщик ресурсов и пользовательские отчёты.
