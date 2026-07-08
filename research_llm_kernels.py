#!/usr/bin/env python3
"""
Research script to investigate proprietary LLM inference kernel designs
"""
import requests
import json
import time

def test_web_proxy():
    """Test if the web proxy is available"""
    proxy_url = "http://localhost:8765"

    try:
        health = requests.get(f"{proxy_url}/health", timeout=2)
        print(f"Web proxy health: {health.status_code}")
        if health.status_code == 200:
            print(json.dumps(health.json(), indent=2))
        return True
    except Exception as e:
        print(f"Web proxy not running: {e}")
        return False

def search_together_ai():
    """Search for Together.ai kernel architecture"""
    proxy_url = "http://localhost:8765"
    queries = [
        "Together.ai kernel architecture optimization",
        "Together.ai LLM inference kernel design",
        "Together.ai CUDA kernel performance",
    ]

    results = {}
    for query in queries:
        try:
            response = requests.post(
                f"{proxy_url}/search",
                json={"query": query},
                timeout=10
            )
            if response.status_code == 200:
                results[query] = response.json()
            else:
                results[query] = f"Status {response.status_code}"
        except Exception as e:
            results[query] = f"Error: {e}"

    return results

def search_anyscale_ray():
    """Search for Anyscale/Ray AIR kernel design"""
    proxy_url = "http://localhost:8765"
    queries = [
        "Anyscale Ray AIR kernel optimization LLM",
        "Ray inference kernel design architecture",
        "Anyscale vLLM kernel integration",
    ]

    results = {}
    for query in queries:
        try:
            response = requests.post(
                f"{proxy_url}/search",
                json={"query": query},
                timeout=10
            )
            if response.status_code == 200:
                results[query] = response.json()
            else:
                results[query] = f"Status {response.status_code}"
        except Exception as e:
            results[query] = f"Error: {e}"

    return results

def search_modal_labs():
    """Search for Modal Labs kernel optimization"""
    proxy_url = "http://localhost:8765"
    queries = [
        "Modal Labs kernel optimization LLM inference",
        "Modal Labs CUDA kernel design",
        "Modal Labs inference platform architecture",
    ]

    results = {}
    for query in queries:
        try:
            response = requests.post(
                f"{proxy_url}/search",
                json={"query": query},
                timeout=10
            )
            if response.status_code == 200:
                results[query] = response.json()
            else:
                results[query] = f"Status {response.status_code}"
        except Exception as e:
            results[query] = f"Error: {e}"

    return results

def search_runwayml():
    """Search for RunwayML inference optimization"""
    proxy_url = "http://localhost:8765"
    queries = [
        "RunwayML inference kernel optimization",
        "RunwayML LLM serving architecture",
        "RunwayML GPU kernel design",
    ]

    results = {}
    for query in queries:
        try:
            response = requests.post(
                f"{proxy_url}/search",
                json={"query": query},
                timeout=10
            )
            if response.status_code == 200:
                results[query] = response.json()
            else:
                results[query] = f"Status {response.status_code}"
        except Exception as e:
            results[query] = f"Error: {e}"

    return results

if __name__ == "__main__":
    print("Testing web proxy availability...")
    if test_web_proxy():
        print("\n" + "="*60)
        print("Searching for Together.ai kernel information...")
        print("="*60)
        together_results = search_together_ai()
        print(json.dumps(together_results, indent=2))

        time.sleep(1)
        print("\n" + "="*60)
        print("Searching for Anyscale/Ray AIR kernel information...")
        print("="*60)
        anyscale_results = search_anyscale_ray()
        print(json.dumps(anyscale_results, indent=2))

        time.sleep(1)
        print("\n" + "="*60)
        print("Searching for Modal Labs kernel information...")
        print("="*60)
        modal_results = search_modal_labs()
        print(json.dumps(modal_results, indent=2))

        time.sleep(1)
        print("\n" + "="*60)
        print("Searching for RunwayML inference information...")
        print("="*60)
        runway_results = search_runwayml()
        print(json.dumps(runway_results, indent=2))
    else:
        print("Web proxy is not available. Cannot proceed with research.")
