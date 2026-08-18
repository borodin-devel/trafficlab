#!/usr/bin/env python3
"""Deterministic TCP/UDP endpoint used only by real-Docker capture tests."""

from __future__ import annotations

import signal
import socket
import sys
import threading
import time

HOST = "0.0.0.0"
TCP_PORT = 18080
UDP_PORT = 18081
MAX_PAYLOAD = 4096
NOISE_PORT = 18082
_stop = threading.Event()


def _stop_server(_signal: int, _frame: object) -> None:
    _stop.set()


def _tcp_connection(connection: socket.socket) -> None:
    with connection:
        payload = connection.recv(MAX_PAYLOAD)
        if not payload:
            return
        if payload.startswith(b"GET "):
            body = b"trafficlab-endpoint\n"
            connection.sendall(
                b"HTTP/1.1 200 OK\r\n"
                + f"Content-Length: {len(body)}\r\n".encode()
                + b"Connection: close\r\n\r\n"
                + body
            )
        else:
            connection.sendall(b"ACK:" + payload)


def _serve_tcp() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((HOST, TCP_PORT))
        listener.listen()
        listener.settimeout(0.2)
        while not _stop.is_set():
            try:
                connection, _address = listener.accept()
            except TimeoutError:
                continue
            threading.Thread(target=_tcp_connection, args=(connection,), daemon=True).start()


def _serve_udp() -> None:
    broadcast_sent = False
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        server.bind((HOST, UDP_PORT))
        server.settimeout(0.2)
        while not _stop.is_set():
            try:
                payload, address = server.recvfrom(MAX_PAYLOAD)
            except TimeoutError:
                continue
            server.sendto(b"ACK:" + payload, address)
            if not broadcast_sent:
                server.sendto(b"TRAFFICLAB-INBOUND-BROADCAST", ("172.31.254.255", address[1]))
                broadcast_sent = True


def _serve_noise() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, NOISE_PORT))
        server.settimeout(0.2)
        while not _stop.is_set():
            try:
                payload, address = server.recvfrom(MAX_PAYLOAD)
            except TimeoutError:
                continue
            server.sendto(b"NOISE-ACK:" + payload, address)


def _send_unrelated_noise() -> None:
    announced = False
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.settimeout(0.2)
        while not _stop.is_set():
            try:
                client.sendto(b"TRAFFICLAB-UNRELATED-UNICAST", ("172.31.254.3", NOISE_PORT))
                payload = client.recv(MAX_PAYLOAD)
            except TimeoutError:
                continue
            if payload == b"NOISE-ACK:TRAFFICLAB-UNRELATED-UNICAST" and not announced:
                print("noise-exchange-ready", flush=True)
                announced = True
            time.sleep(0.02)


def main(argv: list[str]) -> int:
    signal.signal(signal.SIGTERM, _stop_server)
    signal.signal(signal.SIGINT, _stop_server)
    if argv == ["noise"]:
        threads = [threading.Thread(target=_serve_noise)]
    elif argv == ["server"]:
        threads = [
            threading.Thread(target=_serve_tcp),
            threading.Thread(target=_serve_udp),
            threading.Thread(target=_send_unrelated_noise),
        ]
    else:
        raise SystemExit("usage: server.py {server|noise}")
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
