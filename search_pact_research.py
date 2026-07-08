#!/usr/bin/env python3
"""
Deep research on PACT quantization with regularization, NAS, and AutoML.
Searches ArXiv, conference proceedings, and academic databases.
"""

import json
import requests
from datetime import datetime
from urllib.parse import quote

def search_arxiv(query, max_results=15):
    """Search ArXiv for papers matching query."""
    try:
        # ArXiv API endpoint
        base_url = "http://api.arxiv.org/query?"
        search_query = f"search_query=(ti:{query}+OR+abs:{query})"
        url = f"{base_url}{search_query}&sortBy=submittedDate&sortOrder=descending&start=0&max_results={max_results}"

        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            return response.text
        else:
            print(f"ArXiv API error: {response.status_code}")
            return None
    except Exception as e:
        print(f"ArXiv search error: {e}")
        return None

def parse_arxiv_response(xml_response):
    """Parse ArXiv XML response and extract paper details."""
    import xml.etree.ElementTree as ET
    papers = []
    try:
        root = ET.fromstring(xml_response)
        # ArXiv uses namespace
        ns = {'atom': 'http://www.w3.org/2005/Atom', 'arxiv': 'http://arxiv.org/schemas/atom'}

        for entry in root.findall('atom:entry', ns):
            paper = {}
            paper['title'] = entry.findtext('atom:title', '', ns).strip()
            paper['arxiv_id'] = entry.findtext('atom:id', '', ns).split('/abs/')[-1] if 'abs' in entry.findtext('atom:id', '', ns) else ''
            paper['published'] = entry.findtext('atom:published', '', ns)[:10]  # YYYY-MM-DD
            paper['summary'] = entry.findtext('atom:summary', '', ns).strip()

            # Extract authors
            authors = []
            for author in entry.findall('atom:author', ns):
                authors.append(author.findtext('atom:name', '', ns))
            paper['authors'] = authors

            # Build URL
            if paper['arxiv_id']:
                paper['url'] = f"https://arxiv.org/abs/{paper['arxiv_id']}"
                papers.append(paper)
    except Exception as e:
        print(f"Error parsing ArXiv XML: {e}")

    return papers

# Main search queries
search_queries = [
    "PACT quantization regularization",
    "NAS quantization aware training",
    "AutoML quantization",
    "learned clipping quantization",
    "post-training adaptive clipping",
]

all_papers = []

print("Searching ArXiv for PACT quantization papers...")
print("=" * 80)

for query in search_queries:
    print(f"\nSearching: {query}")
    response = search_arxiv(query, max_results=20)
    if response:
        papers = parse_arxiv_response(response)
        print(f"Found {len(papers)} papers")
        all_papers.extend(papers)

# Deduplicate by arxiv_id
seen_ids = set()
unique_papers = []
for paper in all_papers:
    if paper['arxiv_id'] not in seen_ids:
        seen_ids.add(paper['arxiv_id'])
        unique_papers.append(paper)

# Sort by date (newest first)
unique_papers.sort(key=lambda x: x.get('published', ''), reverse=True)

# Save results
output = {
    "timestamp": datetime.now().isoformat(),
    "total_papers": len(unique_papers),
    "search_queries": search_queries,
    "papers": unique_papers[:50]  # Top 50 results
}

with open('/mnt/ForgeRealm/AI-AtlasForge/pact_research_results.json', 'w') as f:
    json.dump(output, f, indent=2)

print("\n" + "=" * 80)
print(f"Total unique papers found: {len(unique_papers)}")
print("Results saved to pact_research_results.json")

# Print summary
print("\nTop papers by relevance:")
for i, paper in enumerate(unique_papers[:15], 1):
    print(f"\n{i}. {paper['title'][:80]}")
    print(f"   ArXiv: {paper['arxiv_id']}")
    print(f"   Date: {paper['published']}")
    print(f"   Authors: {', '.join(paper['authors'][:3])}")
    print(f"   URL: {paper['url']}")
