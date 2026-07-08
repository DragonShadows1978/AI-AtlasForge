#!/usr/bin/env python3
"""
Search for academic papers on KV cache transfer between heterogeneous LLMs
"""
import requests
import json
from typing import List, Dict

# Web proxy endpoint
proxy_url = "http://localhost:8765"

# Multiple search queries to comprehensively cover the topic
search_queries = [
    "KV cache transfer heterogeneous models arxiv 2023 2024 2025",
    "KV cache reuse different model sizes language models",
    "cross-model KV cache sharing heterogeneous transformers",
    "speculative decoding KV cache compatibility models",
    "prompt cache transfer different LLM architectures",
    "KV cache adaptation different transformer families Llama Mistral GPT",
    "attention cache reuse heterogeneous model pairs",
    "cross-architecture key-value cache compatibility transfer",
    "KV cache sharing different sized transformers inference",
    "heterogeneous model KV cache empirical study",
]

all_results = []
fetched_papers = {}

def search_papers(query: str) -> List[Dict]:
    """Search for papers using the web proxy"""
    print(f"\nSearching: {query}")
    print("-" * 70)

    try:
        response = requests.post(
            f"{proxy_url}/search",
            json={"query": query},
            timeout=30
        )
        response.raise_for_status()
        results = response.json()

        if isinstance(results, dict) and 'results' in results:
            search_results = results['results']
        elif isinstance(results, list):
            search_results = results
        else:
            search_results = results

        # Process results
        results_list = []
        if isinstance(search_results, list):
            for result in search_results[:10]:  # Top 10 per query
                if isinstance(result, dict):
                    title = result.get('title', result.get('name', 'N/A'))
                    url = result.get('url', result.get('link', 'N/A'))
                    snippet = result.get('snippet', result.get('description', ''))

                    # Check if it's academic (arxiv, pdf, conference)
                    if any(x in url.lower() for x in ['arxiv', '.pdf', 'openreview', 'aclweb', 'ieeexplore', 'neurips', 'icml', 'iclr']):
                        results_list.append({
                            'query': query,
                            'title': title,
                            'url': url,
                            'snippet': snippet
                        })
                        print(f"  - {title[:100]}")
                        print(f"    {url[:100]}")

        return results_list
    except Exception as e:
        print(f"Error searching: {e}")
        return []

def fetch_paper_details(url: str) -> Dict:
    """Fetch full paper details from URL"""
    print(f"  Fetching: {url[:80]}")
    try:
        response = requests.post(
            f"{proxy_url}/fetch",
            json={"url": url},
            timeout=30
        )
        response.raise_for_status()
        content = response.json()
        return content
    except Exception as e:
        print(f"    Error fetching: {e}")
        return {}

def main():
    print("="*70)
    print("SEARCHING FOR KV CACHE TRANSFER PAPERS (2023-2025)")
    print("="*70)

    # Phase 1: Search
    for query in search_queries:
        results = search_papers(query)
        all_results.extend(results)

    # Deduplicate by URL
    unique_urls = {}
    for result in all_results:
        url = result['url']
        if url not in unique_urls:
            unique_urls[url] = result

    print(f"\n\n{'='*70}")
    print(f"FOUND {len(unique_urls)} UNIQUE ACADEMIC SOURCES")
    print(f"{'='*70}")

    # Phase 2: Fetch top papers
    print("\nFetching full paper details...")
    for i, (url, result) in enumerate(list(unique_urls.items())[:20], 1):
        print(f"\n[{i}] {result['title'][:80]}")
        details = fetch_paper_details(url)
        if details:
            fetched_papers[url] = {
                'title': result['title'],
                'url': url,
                'snippet': result['snippet'],
                'content': details.get('text', '')[:2000]
            }

    # Phase 3: Output results
    print(f"\n\n{'='*70}")
    print("FINAL RESULTS")
    print(f"{'='*70}\n")

    for i, (url, paper) in enumerate(fetched_papers.items(), 1):
        print(f"\n{'='*70}")
        print(f"[{i}] {paper['title']}")
        print(f"{'='*70}")
        print(f"URL: {paper['url']}\n")
        print(f"Snippet:\n{paper['snippet'][:500]}\n")
        if paper['content']:
            print(f"Abstract/Content Preview:\n{paper['content'][:800]}\n")

if __name__ == "__main__":
    main()
