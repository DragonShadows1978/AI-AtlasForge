#!/usr/bin/env python3
import requests
import json
import sys

# Key URLs from the search results to fetch
urls = [
    "https://medium.com/data-science-ai-at-microsoft/a-practical-guide-to-int4-quantization-for-slms-gptq-vs-awq-olive-and-real-world-results-34f28c4d4eaf",
    "https://github.com/mit-han-lab/llm-awq",
    "https://arxiv.org/abs/2306.00978",
    "https://towardsdatascience.com/from-16-bit-to-2-bit-finding-the-best-trade-off-between-memory-efficiency-and-accuracy-2ecca87b86f",
    "https://towardsdatascience.com/extreme-compression-of-large-language-models-via-additive-quantization-cbadbf0b7f14",
    "https://towardsdatascience.com/taking-it-a-step-further-extreme-3-bit-quantization-of-llama-2-8ac0e3ebab63",
]

results = {}

for url in urls:
    print(f"\n{'='*70}")
    print(f"Fetching: {url}")
    print('='*70)

    try:
        response = requests.post(
            'http://localhost:8765/fetch',
            json={'url': url, 'timeout': 30},
            timeout=60
        )
        content = response.json()
        results[url] = content

        if 'content' in content:
            print(f"✓ Fetched {len(content.get('content', ''))} characters")
            print(content.get('content', '')[:500] + "...")
        else:
            print(f"Response keys: {list(content.keys())}")

    except Exception as e:
        print(f"Error: {e}")
        results[url] = {"error": str(e)}

with open('/mnt/ForgeRealm/AI-AtlasForge/quantization_sources_content.json', 'w') as f:
    json.dump(results, f, indent=2)

print("\n\nContent saved to quantization_sources_content.json")
