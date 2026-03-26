#!/usr/bin/env python3
"""Backward-compatible web entrypoint for plan-viewer."""

from __future__ import annotations

import sys

from plan_viewer import main as cli_main


def main() -> int:
    # Backward compatibility:
    #   python3 server.py
    #   python3 server.py 9000
    # Both map to the new CLI's `serve` command.
    argv = sys.argv[1:]
    if argv and argv[0].isdigit():
        return cli_main(["serve", "--port", argv[0], *argv[1:]])
    return cli_main(["serve", *argv])


if __name__ == "__main__":
    raise SystemExit(main())
