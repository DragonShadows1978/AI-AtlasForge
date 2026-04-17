#!/usr/bin/env python3
"""CLI wrapper for routing page fetch/extract through the local proxy service."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from WebProxy.client import fetch_web_via_proxy


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and extract a page through the local proxy service")
    parser.add_argument("url", help="URL to fetch")
    parser.add_argument("--max-chars", type=int, default=12000)
    args = parser.parse_args()

    payload = fetch_web_via_proxy(url=args.url, max_chars=args.max_chars)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
