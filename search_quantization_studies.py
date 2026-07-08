#!/usr/bin/env python3
import requests
import json
import sys

queries = [
    "AWQ 4-bit 3-bit 2-bit quantization results",
    "2-bit quantization accuracy LLaMA Mistral",
    "extreme quantization 2-bit 3-bit weight quantization",
    "low-bit quantization performance comparison"
]

all_results = {}

for query in queries:
    print(f"\n{'='*70}")
    print(f"SEARCH: {query}")
    print('='*70)

    try:
        response = requests.post(
            'http://localhost:8765/search',
            json={'query': query, 'limit': 10},
            timeout=30
        )
        results = response.json()
        all_results[query] = results

        if 'results' in results:
            for i, result in enumerate(results['results'][:5], 1):
                print(f"\n[{i}] {result.get('title', 'N/A')}")
                print(f"    URL: {result.get('link', 'N/A')}")
                print(f"    Snippet: {result.get('snippet', 'N/A')[:250]}...")
    except Exception as e:
        print(f"Error: {e}")

print("\n\nTotal results collected:", sum(len(all_results.get(q, {}).get('results', [])) for q in queries))

# Save structured results
with open('/mnt/ForgeRealm/AI-AtlasForge/quantization_search_results.json', 'w') as f:
    json.dump(all_results, f, indent=2)

print("Results saved to quantization_search_results.json")
