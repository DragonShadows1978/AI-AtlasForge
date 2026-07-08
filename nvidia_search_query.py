#!/usr/bin/env python3
"""Query NVIDIA resources on CUDA pipeline optimization via web proxy."""

import requests
import json
import sys
from typing import Dict, List, Any

# The web proxy should be running on localhost:8765
PROXY_URL = "http://localhost:8765"

# Search queries targeting NVIDIA content
queries = [
    "NVIDIA blog CUDA kernel pipeline optimization",
    "GTC CUDA software pipelining async copy",
    "NVIDIA parallel forall async pipeline memory bandwidth",
    "NVIDIA developer blog Ampere Hopper kernel optimization",
    "NVIDIA CUDA optimization latency hiding pipeline"
]

def run_searches() -> Dict[str, Any]:
    """Execute all search queries and collect results."""
    results = {}

    for query in queries:
        try:
            print(f"Searching: {query[:60]}...", file=sys.stderr)
            response = requests.post(
                f"{PROXY_URL}/search",
                json={"query": query},
                timeout=30
            )
            response.raise_for_status()
            results[query] = response.json()
            print(f"✓ Query completed", file=sys.stderr)
        except Exception as e:
            print(f"✗ Error: {e}", file=sys.stderr)
            results[query] = {"error": str(e)}

    return results

def format_results(results: Dict[str, Any]) -> None:
    """Pretty-print search results."""
    print("\n" + "="*100)
    print("NVIDIA CUDA PIPELINE OPTIMIZATION - WEB SEARCH RESULTS")
    print("="*100)

    all_urls = set()
    query_urls = {}

    for query, data in results.items():
        print(f"\n\nQUERY {len(query_urls)+1}: {query}")
        print("-" * 100)

        if "error" in data:
            print(f"ERROR: {data['error']}")
            continue

        if "results" in data:
            query_results = []
            for i, result in enumerate(data["results"][:5], 1):
                title = result.get('title', 'N/A')
                url = result.get('url', 'N/A')
                snippet = result.get('snippet', 'N/A')[:180]

                print(f"\n{i}. Title: {title}")
                print(f"   URL: {url}")
                print(f"   Snippet: {snippet}...")

                if url != 'N/A':
                    query_results.append(url)
                    all_urls.add(url)

            query_urls[query] = query_results

    print("\n\n" + "="*100)
    print("UNIQUE URL SUMMARY")
    print("="*100)
    print(f"\nTotal unique URLs collected: {len(all_urls)}\n")

    for i, url in enumerate(sorted(all_urls), 1):
        print(f"{i}. {url}")

if __name__ == "__main__":
    try:
        results = run_searches()
        format_results(results)
    except KeyboardInterrupt:
        print("\nSearch interrupted", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)
