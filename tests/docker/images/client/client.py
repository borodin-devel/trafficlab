#!/usr/bin/env python3
"""Direct workload executable for deterministic and Internet Docker tests."""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
import urllib.request


def _traffic(host: str, tcp_count: int, udp_count: int, inter_request_seconds: float = 0.0) -> None:
    for index in range(tcp_count):
        if index > 0 and inter_request_seconds > 0.0:
            time.sleep(inter_request_seconds)
        payload = f"trafficlab-tcp-{index}".encode()
        with socket.create_connection((host, 18080), timeout=5.0) as connection:
            connection.sendall(payload)
            if connection.recv(4096) != b"ACK:" + payload:
                raise RuntimeError("invalid TCP reply")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.settimeout(5.0)
        broadcast_received = False
        for index in range(udp_count):
            if inter_request_seconds > 0.0:
                time.sleep(inter_request_seconds)
            payload = f"trafficlab-udp-{index}".encode()
            client.sendto(payload, (host, 18081))
            expected_ack = b"ACK:" + payload
            while True:
                try:
                    response = client.recv(4096)
                except TimeoutError as error:
                    raise RuntimeError(f"timed out waiting for UDP acknowledgement {expected_ack!r}") from error
                if response == b"TRAFFICLAB-INBOUND-BROADCAST":
                    broadcast_received = True
                elif response == expected_ack:
                    break
                else:
                    raise RuntimeError(f"invalid UDP reply while waiting for {expected_ack!r}")
        while not broadcast_received:
            try:
                response = client.recv(4096)
            except TimeoutError as error:
                raise RuntimeError("timed out waiting for inbound UDP broadcast") from error
            if response == b"TRAFFICLAB-INBOUND-BROADCAST":
                broadcast_received = True
            else:
                raise RuntimeError("invalid UDP reply while waiting for inbound broadcast")


def _https(url: str) -> None:
    with urllib.request.urlopen(url, timeout=15.0) as response:  # noqa: S310 - operator-supplied HTTPS only
        if not 200 <= response.status < 400:
            raise RuntimeError(f"HTTPS endpoint returned status {response.status}")
        response.read(4096)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    traffic = subparsers.add_parser("traffic")
    traffic.add_argument("--host", default="172.31.254.2")
    traffic.add_argument("--tcp-count", type=int, default=2)
    traffic.add_argument("--udp-count", type=int, default=3)
    traffic.add_argument("--inter-request-seconds", type=float, default=0.0)
    traffic.add_argument("--exit-code", type=int, default=0)
    hold = subparsers.add_parser("hold")
    hold.add_argument("--seconds", type=float, default=300.0)
    background = subparsers.add_parser("background")
    background.add_argument("--seconds", type=float, default=300.0)
    background.add_argument("--parent-seconds", type=float, default=0.0)
    https = subparsers.add_parser("https")
    https.add_argument("url")
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    if arguments.command == "traffic":
        _traffic(arguments.host, arguments.tcp_count, arguments.udp_count, arguments.inter_request_seconds)
        return arguments.exit_code
    if arguments.command == "hold":
        time.sleep(arguments.seconds)
        return 0
    if arguments.command == "background":
        subprocess.Popen([sys.executable, __file__, "hold", "--seconds", str(arguments.seconds)])
        _traffic("172.31.254.2", 1, 1)
        time.sleep(arguments.parent_seconds)
        return 0
    if arguments.command == "https":
        _https(arguments.url)
        return 0
    raise AssertionError("unreachable command")


if __name__ == "__main__":
    raise SystemExit(main())
