#!/usr/bin/env python3
"""
Extract detailed metadata from KV cache papers found through web search
"""
import re

# Papers identified from the search with direct URL access
papers = [
    {
        "title": "HeteroCache: A Dynamic Retrieval Approach to Heterogeneous KV Cache Compression for Long-Context LLM Inference",
        "url": "https://arxiv.org/html/2601.13684v1",
        "venue": "arXiv:2601.13684",
        "year": 2026,
        "focus": "heterogeneous KV cache compression",
        "key_insight": "Attention heads exhibit diverse target compression ratios; dynamic retrieval approach overcomes static compression limitations"
    },
    {
        "title": "SwiftCache: Efficient LLM Serving for Multi-turn Conversations with Heterogeneous KV Cache Sharing",
        "url": "https://arxiv.org/html/2606.16135v1",
        "venue": "arXiv:2606.16135",
        "year": 2026,
        "models_tested": ["Qwen3-family", "LWM-1M-Text"],
        "focus": "KV cache sharing across multi-turn conversations",
        "metrics": "P99 TTFT reduction up to 69%, extends max inference length",
        "key_insight": "Collaborative inference with heterogeneous GPU/CPU cache placement"
    },
    {
        "title": "FlowKV: A Disaggregated Inference Framework with Low-Latency KV Cache Transfer and Load-Aware Scheduling",
        "url": "https://arxiv.org/html/2504.03775v1",
        "venue": "arXiv:2504.03775",
        "year": 2025,
        "focus": "KV cache transfer in disaggregated inference",
        "metrics": "96% reduction in KV cache transmission latency (0.944s → 0.053s)",
        "key_insight": "Addresses PagedAttention block-wise fragmentation; load-aware scheduling for prefill/decode nodes"
    },
    {
        "title": "PiKV: KV Cache Management System for Mixture of Experts",
        "url": "https://arxiv.org/html/2508.06526v1",
        "venue": "arXiv:2508.06526",
        "year": 2025,
        "architecture": "Mixture of Experts (MoE)",
        "focus": "KV cache distribution across expert-sharded architecture",
        "baselines": ["H2O", "StreamingLLM", "TOVA"],
        "key_insight": "Expert-sharded KV storage with PiKV routing to reduce token-to-KV access latency"
    },
    {
        "title": "Comparative Characterization of KV Cache Management Strategies for LLM Inference",
        "url": "https://arxiv.org/html/2604.05012v1",
        "venue": "arXiv:2604.05012",
        "year": 2026,
        "focus": "comparative study of KV cache management frameworks",
        "key_insight": "Systematizes trade-offs between memory consumption and inference performance across different KV cache strategies"
    },
    {
        "title": "KVLink: Accelerating Large Language Models via Efficient KV Cache Reuse",
        "url": "https://arxiv.org/html/2502.16002v2",
        "venue": "arXiv:2502.16002",
        "year": 2025,
        "focus": "cross-query KV cache reuse",
        "datasets": ["Question-answering", "Text summarization"],
        "key_insight": "Precompute document KV caches separately; concatenate during inference for cache reuse"
    },
    {
        "title": "CryptoGen: Secure Transformer Generation with Encrypted KV-Cache Reuse",
        "url": "https://arxiv.org/html/2602.08798v1",
        "venue": "arXiv:2602.08798",
        "year": 2026,
        "focus": "encrypted KV cache reuse for privacy-preserving inference",
        "key_insight": "First system to enable persistent encrypted KV cache reuse; addresses privacy in untrusted environments"
    },
    {
        "title": "DroidSpeak: KV Cache Sharing for Cross-LLM Communication and Multi-LLM Serving",
        "url": "https://arxiv.org/html/2411.02820v4",
        "venue": "arXiv:2411.02820",
        "year": 2024,
        "focus": "KV cache reuse ACROSS DIFFERENT LLMs",
        "constraint": "Same architecture required (e.g., Llama-7B and Llama-13B, or Mistral variants)",
        "key_insight": "CRITICAL: Enables compound AI systems where one model reuses another model's prefix KV cache"
    },
    {
        "title": "QuantSpec: Self-Speculative Decoding with Hierarchical Quantized KV Cache",
        "url": "https://arxiv.org/html/2502.10424v1",
        "venue": "arXiv:2502.10424",
        "year": 2025,
        "focus": "KV cache quantization in speculative decoding",
        "applications": "Edge devices, long-context inference",
        "key_insight": "Maintains double full-precision buffer for speculative decoding compatibility"
    },
    {
        "title": "Semantic Cache Distillation: Efficient State Transfer via Reuse and Selective Patching",
        "url": "https://arxiv.org/html/2606.07684",
        "venue": "arXiv:2606.07684",
        "year": 2026,
        "focus": "KV cache reuse across HETEROGENEOUS MODELS",
        "model_pairs": [
            {"base": "Qwen-7B", "variant": "Qwen-7B-Chat"},
            {"base": "Qwen-32B", "variant": "Qwen-32B-Chat"}
        ],
        "metrics": "F1 0.7850 vs Oracle, 7.0ms latency increase",
        "key_finding": "Standard KV quantization (4-bit) fails across heterogeneous models (F1 0.1289)",
        "key_insight": "SCD with selective patching recovers near-full performance for base/fine-tuned model pairs"
    },
    {
        "title": "Adaptive KV Cache Reuse for Fast Long-Context LLM Serving",
        "url": "https://arxiv.org/html/2605.24022v1",
        "venue": "arXiv:2605.24022",
        "year": 2026,
        "focus": "non-prefix KV cache reuse across hardware tiers",
        "system": "CacheTune",
        "key_insight": "Addresses semantic consistency recovery and compute-I/O co-optimization for GPU-external cache pools"
    },
    {
        "title": "Reconstructing KV Caches with Cross-Layer Fusion for Enhanced Transformers",
        "url": "https://arxiv.org/html/2512.03870v2",
        "venue": "arXiv:2512.03870",
        "year": 2025,
        "focus": "cross-layer KV cache sharing within model",
        "method": "FusedKV",
        "metrics": "50% cache memory reduction with lower validation perplexity",
        "model_range": "332M to 4B parameters",
        "key_insight": "Top-layer values derived from bottom layer; keys from bottom+middle layers"
    }
]

# Additional papers mentioned in search results but not yet fully extracted
additional_papers = {
    "A Survey on Large Language Model Acceleration based on KV Cache Management": {
        "url": "https://arxiv.org/pdf/2412.19442",
        "year": 2024,
        "type": "survey",
        "focus": "comprehensive review of KV cache management techniques"
    },
    "Cache-to-Cache: Direct Semantic Communication": {
        "url": "https://arxiv.org/pdf/2510.03215",
        "year": 2025,
        "focus": "direct semantic cache communication"
    },
    "Homogeneous Keys, Heterogeneous Values: Exploiting Local KV Cache Asymmetry for Long-Context LLMs": {
        "url": "https://arxiv.org/html/2506.05410",
        "year": 2025,
        "venue": "OpenReview",
        "focus": "KV asymmetry in heterogeneous settings"
    },
    "Ada-KV: Optimizing KV Cache Eviction by Adaptive Budget": {
        "url": "https://arxiv.org/pdf/2407.11550",
        "year": 2024,
        "focus": "adaptive KV cache eviction strategies"
    },
    "Reducing Transformer Key-Value Cache Size with Cross-Layer Attention": {
        "url": "https://arxiv.org/pdf/2405.12981",
        "year": 2024,
        "venue": "OpenReview + MIT Thesis",
        "focus": "cross-layer KV cache reduction"
    }
}

def print_papers():
    print("=" * 90)
    print("COMPREHENSIVE SURVEY: KV CACHE TRANSFER & REUSE IN HETEROGENEOUS LANGUAGE MODELS")
    print("=" * 90)
    print("\n")

    # Directly relevant to cross-model/heterogeneous transfer
    print("╔" + "═" * 88 + "╗")
    print("║ TIER 1: DIRECTLY ADDRESSING HETEROGENEOUS MODEL KV CACHE TRANSFER/REUSE       ║")
    print("╚" + "═" * 88 + "╝\n")

    tier1_papers = [papers[7], papers[9], papers[3], papers[1]]  # DroidSpeak, SCD, FlowKV, SwiftCache
    for i, paper in enumerate(tier1_papers, 1):
        print(f"\n[TIER1-{i}] {paper['title']}")
        print(f"{'─' * 88}")
        print(f"URL:  {paper['url']}")
        print(f"Year: {paper['year']} | Venue: {paper.get('venue', 'arXiv')}")

        if 'model_pairs' in paper:
            print(f"Model Pairs Tested:")
            for pair in paper['model_pairs']:
                print(f"  • {pair.get('base', 'N/A')} ↔ {pair.get('variant', 'N/A')}")

        if 'models_tested' in paper:
            print(f"Models: {', '.join(paper['models_tested'])}")

        if 'metrics' in paper:
            print(f"Performance Metrics: {paper['metrics']}")

        if 'constraint' in paper:
            print(f"Constraint: {paper['constraint']}")

        if 'key_finding' in paper:
            print(f"Key Finding: {paper['key_finding']}")

        print(f"Key Insight: {paper.get('key_insight', 'N/A')}")

    print("\n\n")
    print("╔" + "═" * 88 + "╗")
    print("║ TIER 2: KV CACHE MANAGEMENT & OPTIMIZATION (SINGLE-MODEL & ARCHITECTURE)    ║")
    print("╚" + "═" * 88 + "╝\n")

    tier2_papers = [papers[0], papers[4], papers[5], papers[8], papers[10], papers[11]]
    for i, paper in enumerate(tier2_papers, 1):
        print(f"\n[TIER2-{i}] {paper['title']}")
        print(f"{'─' * 88}")
        print(f"URL:  {paper['url']}")
        print(f"Year: {paper['year']} | Venue: {paper.get('venue', 'arXiv')}")

        if 'metrics' in paper:
            print(f"Performance: {paper['metrics']}")

        print(f"Focus: {paper.get('focus', 'N/A')}")
        print(f"Key Insight: {paper.get('key_insight', 'N/A')}")

    print("\n\n")
    print("╔" + "═" * 88 + "╗")
    print("║ TIER 3: SPECIALIZED APPLICATIONS (ENCRYPTION, SPECULATIVE DECODING, MoE)   ║")
    print("╚" + "═" * 88 + "╝\n")

    tier3_papers = [papers[6], papers[2]]  # CryptoGen, PiKV
    for i, paper in enumerate(tier3_papers, 1):
        print(f"\n[TIER3-{i}] {paper['title']}")
        print(f"{'─' * 88}")
        print(f"URL:  {paper['url']}")
        print(f"Year: {paper['year']} | Venue: {paper.get('venue', 'arXiv')}")
        print(f"Application: {paper.get('focus', 'N/A')}")
        print(f"Key Insight: {paper.get('key_insight', 'N/A')}")

    print("\n\n")
    print("╔" + "═" * 88 + "╗")
    print("║ KEY FINDINGS ON HETEROGENEOUS MODEL COMPATIBILITY                          ║")
    print("╚" + "═" * 88 + "╝\n")

    findings = [
        ("DroidSpeak", "Architecture must match for KV reuse (Llama-7B↔Llama-13B, Mistral variants OK)"),
        ("SCD (Semantic Cache Distillation)", "Direct KV reuse across heterogeneous models fails; semantic gap causes degradation"),
        ("SCD", "4-bit KV quantization across heterogeneous models collapses quality (F1: 0.1289)"),
        ("SCD", "Selective patching + distillation recovers performance (F1: 0.7850) with 7ms latency overhead"),
        ("FlowKV", "KV cache transfer latency dominant bottleneck (0.944s → 0.053s with optimization)"),
        ("HeteroCache", "Attention heads show diverse compression targets; one-size-fits-all fails"),
        ("Reconstructing KV", "Cross-layer KV sharing within model: values from bottom, keys from bottom+middle"),
        ("SwiftCache", "Multi-turn conversations enable heterogeneous GPU/CPU cache placement strategies"),
    ]

    for system, finding in findings:
        print(f"• [{system:30s}] {finding}")

    print("\n\n")
    print("╔" + "═" * 88 + "╗")
    print("║ ADDITIONAL REFERENCES (SURVEYS & RELATED WORK)                             ║")
    print("╚" + "═" * 88 + "╝\n")

    for title, details in additional_papers.items():
        print(f"\n• {title}")
        print(f"  Year: {details.get('year', '?')} | Type: {details.get('type', 'Research')}")
        print(f"  Focus: {details.get('focus', 'N/A')}")
        print(f"  URL: {details.get('url', 'N/A')}")

    print("\n\n")
    print("=" * 90)
    print("RESEARCH GAPS & OPEN QUESTIONS")
    print("=" * 90)
    print("""
1. CROSS-ARCHITECTURE TRANSFER: DroidSpeak requires same architecture. No papers yet study
   KV cache transfer between fundamentally different architectures (e.g., Llama → Mistral).

2. QUANTIZATION ACROSS MODELS: SCD shows 4-bit quantization fails across heterogeneous models.
   Optimal quantization strategy for heterogeneous pairs remains open.

3. ADAPTER LAYERS: SCD's "patching" mechanism is preliminary. More sophisticated adapters
   (e.g., learned linear transformations) not yet systematically explored.

4. SPECULATIVE DECODING WITH REUSE: QuantSpec addresses speculative decoding but not yet
   combined with cross-model KV reuse (e.g., draft from Llama, target from Qwen).

5. EMPIRICAL TRANSFER MATRICES: No comprehensive compatibility matrix (Model A KV → Model B)
   showing exact performance degradation by model pair.

6. MoE KV SHARING: PiKV addresses expert sharding but not cross-MoE-model reuse.
""")

if __name__ == "__main__":
    print_papers()
