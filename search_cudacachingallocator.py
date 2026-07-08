#!/usr/bin/env python3
"""
Search for PyTorch CUDACachingAllocator documentation via web proxy
"""
import requests
import json
from urllib.parse import urljoin

proxy_base = "http://localhost:8765"

search_queries = [
    "PyTorch CUDACachingAllocator official documentation",
    "PyTorch CUDA memory allocator design architecture",
    "PyTorch memory management binning strategy blocks",
    "PyTorch CachingAllocator design document whitepaper",
    "PyTorch memory allocator caching strategy algorithm"
]

print("=" * 80)
print("Searching for PyTorch CUDACachingAllocator Documentation")
print("=" * 80)

all_urls = set()
results_by_query = {}

for query in search_queries:
    print(f"\n[SEARCH] {query}")
    print("-" * 80)

    try:
        response = requests.get(
            f"{proxy_base}/search",
            params={"q": query},
            timeout=15
        )
        if response.status_code == 200:
            data = response.json()
            results = data.get('results', [])
            results_by_query[query] = results

            print(f"Found {len(results)} results:")
            for i, result in enumerate(results[:5], 1):
                url = result.get('url', 'N/A')
                title = result.get('title', 'N/A')
                all_urls.add(url)
                print(f"  {i}. {title}")
                print(f"     URL: {url}")
        else:
            print(f"Error: Status {response.status_code}")
    except Exception as e:
        print(f"Error: {e}")

print("\n" + "=" * 80)
print(f"Total unique URLs found: {len(all_urls)}")
print("=" * 80)

print("\n[FETCHING] Top candidate sources...")
print("-" * 80)

# Priority URLs to fetch
fetch_priority = [
    url for url in all_urls
    if url and ('pytorch.org' in url or 'github.com/pytorch' in url)
][:10]

fetched_sources = {}

for url in fetch_priority:
    print(f"\nFetching: {url}")
    try:
        response = requests.post(
            f"{proxy_base}/fetch",
            json={"url": url},
            timeout=30
        )
        if response.status_code == 200:
            data = response.json()
            content = data.get('content', '')
            fetched_sources[url] = {
                'status': 'success',
                'content_length': len(content),
                'preview': content[:500] if content else 'N/A'
            }
            print(f"  ✓ Fetched ({len(content)} chars)")
        else:
            fetched_sources[url] = {'status': f'error_{response.status_code}'}
            print(f"  ✗ Error: Status {response.status_code}")
    except Exception as e:
        fetched_sources[url] = {'status': f'error_{str(e)[:50]}'}
        print(f"  ✗ Error: {e}")

# Save results
with open('/tmp/cudacachingallocator_search_results.json', 'w') as f:
    json.dump({
        'results_by_query': results_by_query,
        'fetched_sources': fetched_sources,
        'all_urls': list(all_urls)
    }, f, indent=2)

print("\n[COMPLETE] Results saved to /tmp/cudacachingallocator_search_results.json")
