#!/usr/bin/env python3
"""CLI wrapper for routing web search through the local proxy service."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from WebProxy.client import search_web_via_proxy


def main() -> None:
    parser = argparse.ArgumentParser(description="Search the web through the local proxy service")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--provider", default="auto")
    args = parser.parse_args()

    payload = search_web_via_proxy(query=args.query, count=args.count, provider=args.provider)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
