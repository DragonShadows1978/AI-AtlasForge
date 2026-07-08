#!/usr/bin/env python3
"""
Comprehensive Research on Differentiable Quantization Techniques
Searches for key papers on straight-through estimators, QAT, and quantization methods
"""
import json
import requests
import time
from datetime import datetime

PROXY_URL = "http://localhost:8765"

# Key papers and search queries targeting seminal works
research_targets = [
    ("Bengio BINARIZED NEURAL NETWORKS straight-through estimator arXiv 2016", "binarized neural networks"),
    ("Hubara Quantization-Aware Training deep neural networks arxiv", "quantization aware training"),
    ("Zhou post-training quantization neural networks 2023 2024", "post-training quantization"),
    ("straight-through estimator gradient flow quantization courbariaux", "straight-through estimator"),
    ("Gumbel-softmax soft quantization differentiable", "gumbel softmax quantization"),
    ("learned quantization parameters scaling factors", "learned quantization parameters"),
    ("binarized weights networks precision", "binarized weights networks"),
    ("quantization gradient estimation neural networks", "quantization gradient estimation"),
    ("quantization-aware training methods convergence arxiv", "QAT convergence methods"),
    ("post-training quantization calibration techniques", "PTQ calibration techniques"),
]

results = {}

print("=" * 70)
print("DIFFERENTIABLE QUANTIZATION TECHNIQUES RESEARCH")
print("=" * 70)
print(f"Research Start Time: {datetime.now().isoformat()}\n")

for query, category in research_targets:
    print(f"[SEARCH] {category}")
    print(f"  Query: {query}")
    try:
        response = requests.post(
            f"{PROXY_URL}/search",
            json={"query": query, "num_results": 8},
            timeout=30
        )
        if response.status_code == 200:
            data = response.json()
            results[category] = data.get('results', [])
            print(f"  Status: Found {len(results[category])} results")
            # Show top results
            for i, result in enumerate(results[category][:3], 1):
                print(f"    {i}. {result.get('title', 'N/A')[:80]}...")
        else:
            print(f"  Status: Error {response.status_code}")
    except Exception as e:
        print(f"  Status: Exception - {str(e)[:60]}")
    print()
    time.sleep(1)

# Save results to file
output_file = '/mnt/ForgeRealm/AI-AtlasForge/quantization_search_results.json'
with open(output_file, 'w') as f:
    json.dump({
        'timestamp': datetime.now().isoformat(),
        'proxy_url': PROXY_URL,
        'total_categories': len(results),
        'results': results,
        'summary': {cat: len(items) for cat, items in results.items()}
    }, f, indent=2)

print("\n" + "=" * 70)
print("RESEARCH SUMMARY")
print("=" * 70)
print(f"Results saved to: {output_file}")
print(f"Total search categories: {len(results)}")
for category, items in results.items():
    print(f"  {category}: {len(items)} results")
print("=" * 70)
