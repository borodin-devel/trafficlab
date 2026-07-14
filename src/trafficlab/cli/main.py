"""Точка входа CLI; команды стадий добавляются по мере реализации."""

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(prog="trafficlab")
    parser.add_argument("command", nargs="?", help="capture, convert, validate, train, generate, compare или evolve")
    parser.parse_args()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
