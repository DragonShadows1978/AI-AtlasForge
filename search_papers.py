#!/usr/bin/env python3
"""
Search for papers on cache transfer, speculative decoding, and related topics.
"""
import requests
import json
import re
from typing import Dict, List, Set

PROXY_URL = "http://localhost:8765"

def search(query: str, count: int = 20) -> Dict:
    """Execute a web search through the AtlasForge proxy."""
    try:
        response = requests.post(
            f"{PROXY_URL}/search",
            json={"query": query, "count": count},
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"  Error: {e}")
        return {}

def extract_arxiv_papers(results: Dict) -> List[tuple]:
    """Extract arxiv paper IDs and titles from search results."""
    papers = []

    if not results or 'results' not in results:
        return papers

    for result in results.get('results', []):
        title = result.get('title', '')
        url = result.get('url', '')
        snippet = result.get('snippet', '')

        # Look for arxiv URLs
        arxiv_match = re.search(r'arxiv\.org/(?:abs|pdf)/(\d+\.\d+)', url)
        if arxiv_match:
            arxiv_id = arxiv_match.group(1)
            papers.append((arxiv_id, title, url))
        elif 'arxiv.org' in url and arxiv_match is None:
            # Try in the title/snippet
            arxiv_match = re.search(r'(\d+\.\d+)', title + ' ' + snippet)
            if arxiv_match:
                arxiv_id = arxiv_match.group(1)
                papers.append((arxiv_id, title, url))

    return papers

def main():
    queries = [
        'cache transfer language model site:arxiv.org',
        'speculative decoding KV cache',
        'model adaptation attention cache',
        'KV-cache pooling OR cache projection'
    ]

    all_papers: Dict[str, List] = {}
    unique_papers: Set[str] = set()
    papers_by_id: Dict[str, tuple] = {}

    print("Conducting TIER 3 searches for papers on cache transfer and related topics\n")
    print("=" * 80)

    for i, query in enumerate(queries, 1):
        print(f"\nSearch {i}/4: {query}")
        print("-" * 80)

        results = search(query, count=20)
        all_papers[query] = results

        papers = extract_arxiv_papers(results)

        if papers:
            print(f"Found {len(papers)} arxiv papers:")
            for arxiv_id, title, url in papers:
                print(f"  - {arxiv_id}: {title[:100]}")
                if arxiv_id not in papers_by_id:
                    papers_by_id[arxiv_id] = (title, url)
                    unique_papers.add(arxiv_id)
                else:
                    print(f"    (duplicate found in multiple searches)")
        else:
            print("No arxiv papers found in results")

        # Show a sample of results for debugging
        if results.get('results'):
            print(f"\nSample results (up to 3):")
            for r in results['results'][:3]:
                print(f"  Title: {r.get('title', '')[:80]}")
                print(f"  URL: {r.get('url', '')[:80]}")
                print()

    print("\n" + "=" * 80)
    print(f"\nUNIQUE PAPERS FOUND: {len(unique_papers)}\n")

    for arxiv_id in sorted(unique_papers):
        title, url = papers_by_id[arxiv_id]
        print(f"{arxiv_id}: {title}")

    print("\n" + "=" * 80)
    print("Summary:")
    print(f"  Total unique papers: {len(unique_papers)}")
    print(f"  Total searches conducted: {len(queries)}")
    print(f"  Queries with results: {sum(1 for q in queries if q in all_papers and all_papers[q].get('results'))}")

if __name__ == "__main__":
    main()
