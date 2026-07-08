#!/usr/bin/env python3
"""Search for grid-resident and persistent kernel compute research for LLM decode."""

import subprocess
import json
import sys

# Multiple search queries to get comprehensive coverage
queries = [
    "grid-resident compute decode LLM kernel",
    "persistent kernel inference GPU attention",
    "kernel persistence GPU decode iterations arxiv",
    "resident kernels LLM inference",
    "grid resident kernel compute CUDA",
    "kernel launch overhead GPU inference",
    "persistent kernels GPU memory decode",
    "LLM inference kernel reuse patterns"
]

all_results = {}

for query in queries:
    print(f"\n{'='*60}\nSearching: {query}\n{'='*60}", file=sys.stderr)

    # Using the web proxy search endpoint
    result = subprocess.run(
        ["curl", "-s", "http://localhost:8765/search",
         "-H", "Content-Type: application/json",
         "-d", json.dumps({"q": query, "num": 15})],
        capture_output=True,
        text=True,
        timeout=30
    )

    try:
        data = json.loads(result.stdout)
        if "results" in data:
            all_results[query] = []
            for i, r in enumerate(data["results"], 1):
                entry = {
                    "rank": i,
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("snippet", "")
                }
                all_results[query].append(entry)
                print(f"\n[{i}] {r.get('title', 'N/A')}", file=sys.stderr)
                print(f"    URL: {r.get('url', 'N/A')}", file=sys.stderr)
    except Exception as e:
        print(f"Error with query '{query}': {e}", file=sys.stderr)

# Output as JSON for processing
print(json.dumps(all_results, indent=2))
