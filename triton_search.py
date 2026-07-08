#!/usr/bin/env python3
"""Search for Triton attention implementation research and benchmarks."""

import requests
import json
import time

# Use the local web proxy for searches
proxy_url = "http://localhost:8765"

queries = [
    "Triton attention kernel research paper benchmark",
    "OpenAI Triton language attention optimization",
    "Triton attention implementation GitHub benchmark results"
]

results = {}

for query in queries:
    print(f"\n{'='*70}")
    print(f"Query: {query}")
    print('='*70)

    try:
        response = requests.post(
            f"{proxy_url}/search",
            json={"query": query, "count": 10},
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        results[query] = data

        # Display results
        if "results" in data:
            for i, result in enumerate(data["results"][:10], 1):
                title = result.get('title', 'No title')
                url = result.get('url', 'No URL')
                snippet = result.get('snippet', 'No snippet')[:250]
                print(f"\n[{i}] {title}")
                print(f"    URL: {url}")
                print(f"    {snippet}...")
    except Exception as e:
        print(f"Error: {e}")

    time.sleep(1)

# Save comprehensive results
with open("/tmp/triton_attention_search.json", "w") as f:
    json.dump(results, f, indent=2)

print("\n" + "="*70)
print("Results saved to /tmp/triton_attention_search.json")
print("="*70)
