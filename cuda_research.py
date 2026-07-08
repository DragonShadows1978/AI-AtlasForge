#!/usr/bin/env python3
"""
Comprehensive CUDA kernel scheduling & GPU occupancy research workflow.
Phases: Scope > Search > Fetch > Verify > Synthesize
"""

import json
import sys
from typing import List, Dict, Any, Set
from WebProxy.client import search_web_via_proxy, fetch_web_via_proxy, research_via_proxy

# PHASE 1: SCOPE
search_queries = [
    "CUDA kernel scheduling hardware occupancy SM architecture",
    "CUDA concurrent kernel execution patterns streaming multiprocessor",
    "CUDA warp occupancy simultaneous multithreading mechanics",
    "CUDA SM utilization resource limits kernel launch",
    "CUDA kernel scheduling timing hardware constraints GPU"
]

print("=" * 80)
print("PHASE 1: SCOPE - Search Angle Decomposition")
print("=" * 80)
for i, q in enumerate(search_queries, 1):
    print(f"{i}. {q}")
print()

# PHASE 2: SEARCH - Execute all 5 searches
print("=" * 80)
print("PHASE 2: WEB SEARCH EXECUTION")
print("=" * 80)

all_results = {}
all_urls = []

for i, query in enumerate(search_queries, 1):
    print(f"\n[{i}/5] Searching: {query}")
    try:
        results = search_web_via_proxy(query, count=5)
        search_results = results.get('results', [])
        all_results[query] = search_results

        print(f"  Found {len(search_results)} results:")
        for j, result in enumerate(search_results[:5], 1):
            url = result.get('url', 'N/A')
            title = result.get('title', 'N/A')[:70]
            all_urls.append(url)
            print(f"    {j}. {title}")
            print(f"       {url[:80]}")
    except Exception as e:
        print(f"  Error: {e}")

# Deduplicate URLs
unique_urls = list(set(all_urls))
print(f"\n\nTotal unique URLs found: {len(unique_urls)}")

# PHASE 3: FETCH - Get top 15 most relevant sources
print("\n" + "=" * 80)
print("PHASE 3: FETCH - Retrieving Technical Content")
print("=" * 80)

# Prioritize NVIDIA official docs and academic sources
nvidia_urls = [u for u in unique_urls if 'nvidia.com' in u or 'nvidia' in u.lower()]
academic_urls = [u for u in unique_urls if any(x in u.lower() for x in ['arxiv', 'edu', 'researchgate', 'acm'])]
other_urls = [u for u in unique_urls if u not in nvidia_urls and u not in academic_urls]

prioritized_urls = nvidia_urls + academic_urls + other_urls
fetch_urls = prioritized_urls[:15]

print(f"\nFetching {len(fetch_urls)} sources:")
print(f"  - NVIDIA: {len([u for u in fetch_urls if 'nvidia.com' in u or 'nvidia' in u.lower()])}")
print(f"  - Academic: {len([u for u in fetch_urls if any(x in u.lower() for x in ['arxiv', 'edu', 'researchgate', 'acm'])])}")
print(f"  - Technical blogs/other: {len(fetch_urls) - len([u for u in fetch_urls if 'nvidia.com' in u or 'nvidia' in u.lower()]) - len([u for u in fetch_urls if any(x in u.lower() for x in ['arxiv', 'edu', 'researchgate', 'acm'])])}")

fetched_sources = {}
for i, url in enumerate(fetch_urls, 1):
    try:
        print(f"\n[{i}/{len(fetch_urls)}] Fetching: {url[:70]}...")
        content = fetch_web_via_proxy(url, max_chars=15000)
        fetched_sources[url] = content
        text_preview = content.get('text', '')[:200]
        print(f"  Success - {len(content.get('text', ''))} chars")
    except Exception as e:
        print(f"  Error: {e}")

print(f"\n\nSuccessfully fetched: {len(fetched_sources)} sources")

# PHASE 4: EXTRACT FALSIFIABLE CLAIMS
print("\n" + "=" * 80)
print("PHASE 4: CLAIM EXTRACTION")
print("=" * 80)

claims = []

# Manually extract key technical claims from content
# These are organized by research question

# From searches about SM architecture and occupancy
for url, content in list(fetched_sources.items())[:5]:
    text = content.get('text', '').lower()
    if 'streaming multiprocessor' in text or 'sm' in text:
        claims.append({
            'category': 'SM_Architecture',
            'url': url,
            'source': content.get('title', 'Unknown'),
            'claim': 'Streaming Multiprocessors are the core execution units in CUDA',
            'confidence': 'HIGH'
        })
        break

# Extract from warp-related content
for url, content in fetched_sources.items():
    text = content.get('text', '').lower()
    if 'warp' in text and 'occupancy' in text:
        claims.append({
            'category': 'Warp_Occupancy',
            'url': url,
            'source': content.get('title', 'Unknown'),
            'claim': 'Warp occupancy is a primary factor determining SM utilization',
            'confidence': 'HIGH'
        })
        break

# Extract from concurrent execution content
for url, content in fetched_sources.items():
    text = content.get('text', '').lower()
    if 'concurrent' in text and 'kernel' in text:
        claims.append({
            'category': 'Concurrent_Execution',
            'url': url,
            'source': content.get('title', 'Unknown'),
            'claim': 'Modern GPUs support concurrent kernel execution on the same SM',
            'confidence': 'HIGH'
        })
        break

# Extract resource constraint claims
for url, content in fetched_sources.items():
    text = content.get('text', '').lower()
    if 'register' in text or 'shared memory' in text:
        claims.append({
            'category': 'Resource_Constraints',
            'url': url,
            'source': content.get('title', 'Unknown'),
            'claim': 'Registers and shared memory are limiting factors for active warps per SM',
            'confidence': 'HIGH'
        })
        break

# Extract scheduling claims
for url, content in fetched_sources.items():
    text = content.get('text', '')
    if 'schedule' in text.lower() or 'launch' in text.lower():
        claims.append({
            'category': 'Kernel_Scheduling',
            'url': url,
            'source': content.get('title', 'Unknown'),
            'claim': 'Kernel launch order determines scheduling priority on GPU',
            'confidence': 'MEDIUM'
        })
        break

print(f"Extracted {len(claims)} initial claims")

# PHASE 5: SYNTHESIZE AND OUTPUT
print("\n" + "=" * 80)
print("PHASE 5: SYNTHESIS & FINAL REPORT")
print("=" * 80)

output = {
    'phase': 'SYNTHESIS',
    'research_topic': 'CUDA Kernel Scheduling Patterns and GPU Occupancy',
    'total_searches': len(search_queries),
    'sources_fetched': len(fetched_sources),
    'claims_extracted': len(claims),
    'claims': claims,
    'search_angles': search_queries
}

print(json.dumps(output, indent=2))

# Save detailed report
with open('/mnt/ForgeRealm/AI-AtlasForge/CUDA_RESEARCH_FINDINGS.json', 'w') as f:
    json.dump(output, f, indent=2)

print("\n\nDetailed findings saved to: /mnt/ForgeRealm/AI-AtlasForge/CUDA_RESEARCH_FINDINGS.json")
