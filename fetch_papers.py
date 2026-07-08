#!/usr/bin/env python3
"""
Search for papers on monosemanticity vs polysemanticity in language models
"""

import urllib.request
import urllib.parse
import json
import xml.etree.ElementTree as ET
import sys

def search_arxiv(query_term, max_results=10):
    """Search ArXiv for papers"""
    base_url = "http://export.arxiv.org/api/query?"

    params = {
        'search_query': f'cat:cs.CL+AND+all:{query_term}',
        'start': 0,
        'max_results': max_results,
        'sortBy': 'relevance',
        'sortOrder': 'descending'
    }

    url = base_url + urllib.parse.urlencode(params)

    try:
        response = urllib.request.urlopen(url, timeout=10)
        data = response.read().decode('utf-8')

        root = ET.fromstring(data)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        entries = root.findall('atom:entry', ns)

        papers = []
        for entry in entries:
            title = entry.find('atom:title', ns).text.strip()
            authors = []
            for author in entry.findall('atom:author', ns):
                name = author.find('atom:name', ns).text
                authors.append(name)

            published = entry.find('atom:published', ns).text[:10]
            arxiv_id = entry.find('atom:id', ns).text.split('/abs/')[-1]
            summary = entry.find('atom:summary', ns).text.strip()

            papers.append({
                'title': title,
                'authors': authors,
                'date': published,
                'arxiv_id': arxiv_id,
                'summary': summary,
                'url': f'https://arxiv.org/abs/{arxiv_id}'
            })

        return papers
    except Exception as e:
        print(f"Error searching for '{query_term}': {e}", file=sys.stderr)
        return []

if __name__ == '__main__':
    print("=" * 80)
    print("SEARCHING FOR MONOSEMANTICITY / POLYSEMANTICITY PAPERS")
    print("=" * 80)

    queries = [
        "polysemantic+neurons",
        "monosemanticity",
        "superposition+neural",
        "mechanistic+interpretability",
        "scaling+laws+interpretability"
    ]

    all_papers = []

    for query_term in queries:
        print(f"\nSearching ArXiv for: {query_term}")
        papers = search_arxiv(query_term, max_results=5)

        print(f"Found {len(papers)} results")
        for i, paper in enumerate(papers):
            print(f"\n  [{i+1}] {paper['title']}")
            print(f"      Authors: {', '.join(paper['authors'][:3])}")
            print(f"      Date: {paper['date']}")
            print(f"      URL: {paper['url']}")
            all_papers.append(paper)

    print("\n" + "=" * 80)
    print(f"TOTAL PAPERS FOUND: {len(all_papers)}")
    print("=" * 80)

    # Save to JSON for further processing
    with open('/mnt/ForgeRealm/AI-AtlasForge/papers_found.json', 'w') as f:
        json.dump(all_papers, f, indent=2)

    print(f"\nResults saved to papers_found.json")
