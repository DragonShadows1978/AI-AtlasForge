# HQQ (Half-Quadratic Quantization) - Comprehensive Research Index

**Last Updated**: 2026-07-06  
**Research Scope**: Official implementations, integration patterns, benchmarks, and deployment guides  
**Status**: Complete and verified

---

## Document Overview

This index provides navigation to all HQQ research, code implementations, and deployment documentation. All documents are cross-linked and organized by use case.

### Quick Navigation

- **[HQQ_RESEARCH_REPORT.md](HQQ_RESEARCH_REPORT.md)** — Theory, APIs, and complete code walkthroughs
- **[HQQ_API_REFERENCE_AND_INTEGRATION.md](HQQ_API_REFERENCE_AND_INTEGRATION.md)** — Complete API reference, integration patterns, troubleshooting
- **[HQQ_INSTALLATION_AND_DEPLOYMENT.md](HQQ_INSTALLATION_AND_DEPLOYMENT.md)** — Setup, production deployments, services
- **[HQQ_QUANTIZATION_BENCHMARKS_RESEARCH.md](HQQ_QUANTIZATION_BENCHMARKS_RESEARCH.md)** — Performance benchmarks, latency/throughput data
- **This document** — Master index and navigation guide

---

## 1. Understanding HQQ: Where to Start

### For Beginners
1. Start with **HQQ_RESEARCH_REPORT.md** sections 1-5
   - Official repository information
   - Core concepts and theory
   - Basic API examples
2. Run installation from **HQQ_INSTALLATION_AND_DEPLOYMENT.md**
3. Try "Quick Start" example

### For ML Engineers
1. Read **HQQ_API_REFERENCE_AND_INTEGRATION.md** — Part 1 (Core API)
2. Study integration patterns in Part 3
3. Review configuration options in Part 2
4. Implement with one of the patterns from **HQQ_RESEARCH_REPORT.md** Section 13

### For Systems/Platform Engineers
1. Read **HQQ_INSTALLATION_AND_DEPLOYMENT.md** completely
2. Study deployment patterns (Flask, Kubernetes)
3. Review performance optimization from **HQQ_API_REFERENCE_AND_INTEGRATION.md** Part 5
4. Use benchmarking script and optimize for your hardware

### For Research/Academics
1. Review **HQQ_QUANTIZATION_BENCHMARKS_RESEARCH.md** completely
2. Study implementation details in **HQQ_RESEARCH_REPORT.md** Sections 2-5
3. Cross-reference with official GitHub: https://github.com/mobiusml/HQQ

---

## 2. Document Purposes & Content Map

### HQQ_RESEARCH_REPORT.md
**Purpose**: Comprehensive HQQ overview with theory, APIs, and implementation examples

**Key Sections**:
- Section 1-2: Official repo info and structure
- Section 3: Core concepts (half-quadratic optimization)
- Section 4: API reference with code snippets
  - 4.1: Basic quantization API
  - 4.2: Transformers integration
  - 4.3: Layer-specific quantization
  - 4.4: Dequantization & inference
  - 4.5: Fine-tuning with quantized weights
  - 4.6: Advanced configuration
- Section 5: Integration patterns
- Section 6: Performance characteristics
- Section 7: Key functions and method signatures
- Section 8-12: Installation, usage examples
- Section 13: **Complete code implementation guide** (10 production-ready examples)

**Use When**: Implementing HQQ quantization, understanding API, learning integration patterns

---

### HQQ_API_REFERENCE_AND_INTEGRATION.md
**Purpose**: Complete API reference, integration patterns, and troubleshooting

**Key Sections**:
- Part 1: Core API Reference
  - Class: HQQLinear (complete with parameters and examples)
  - Function: hqq_global_conf.initialize()
  - Module: hqq.backends
- Part 2: Quantization Configuration Reference
  - Configuration dictionary format
  - Configuration presets (8-bit, 4-bit, 2-bit, mobile, accurate)
- Part 3: Integration Patterns
  - Pattern 1: Hugging Face Transformers
  - Pattern 2: Custom PyTorch Models
  - Pattern 3: Mixed-Precision Strategy
  - Pattern 4: Inference Server Integration
- Part 4: Troubleshooting Guide
  - Issue 1: OutOfMemoryError
  - Issue 2: High accuracy drop
  - Issue 3: Slow inference
  - Issue 4: Import errors
- Part 5: Performance Optimization Tips
- Part 6: Pre-quantized models

**Use When**: Looking up API details, choosing configuration, debugging issues, optimizing performance

---

### HQQ_INSTALLATION_AND_DEPLOYMENT.md
**Purpose**: Step-by-step setup, production deployment, and service architecture

**Key Sections**:
- Quick Start (5 minutes)
- Full Installation Guide
  - Option 1: PyPI (recommended)
  - Option 2: From source
  - Option 3: Docker
- Installation Verification (test script)
- Environment Configuration
- Production Deployment Patterns
  - Pattern 1: Flask web service
  - Pattern 2: Batch processing service
  - Pattern 3: Kubernetes deployment
- Common Issues & Solutions
- Benchmarking script

**Use When**: Setting up development/production environment, deploying services, fixing installation issues

---

### HQQ_QUANTIZATION_BENCHMARKS_RESEARCH.md
**Purpose**: Comprehensive performance benchmarks and latency/throughput data

**Key Content** (11 tiers):
- Tier 1: HQQ framework overview
- Tier 2: 4-bit benchmarks on LLaMA-2 (7B, 13B, 70B)
- Tier 3: Mixed-precision quantization
- Tier 4: Hardware-specific benchmarks (CPU, GPU, edge)
- Tier 5: Production serving metrics
- Tier 6: Quantization time analysis
- Tier 7: Scaling analysis
- Tier 8: Bit-width accuracy trade-offs
- Tier 9: Real-world application benchmarks
- Tier 10: Comparison with other methods (INT8, GPTQ, AWQ)
- Tier 11: Batch size effects

**Key Metrics**:
- Latency (ms/token) across hardware
- Throughput (tokens/second)
- Memory usage and compression ratios
- Quantization time
- Accuracy loss (perplexity)

**Use When**: Evaluating performance for your use case, planning infrastructure, comparing with alternatives

---

## 3. Common Workflows & Document Path

### Workflow 1: "I want to quantize my model"
1. **HQQ_RESEARCH_REPORT.md** Section 4 (Basic API)
2. **HQQ_RESEARCH_REPORT.md** Section 13 (Code examples)
3. **HQQ_API_REFERENCE_AND_INTEGRATION.md** Part 2 (Configuration)
4. Code and test

### Workflow 2: "I want to integrate HQQ with Transformers"
1. **HQQ_API_REFERENCE_AND_INTEGRATION.md** Part 3, Pattern 1
2. **HQQ_RESEARCH_REPORT.md** Section 5.1
3. **HQQ_RESEARCH_REPORT.md** Section 13, Step 4 (Transformers quantization)
4. Test and benchmark

### Workflow 3: "I need to deploy HQQ to production"
1. **HQQ_INSTALLATION_AND_DEPLOYMENT.md** Full Installation
2. **HQQ_INSTALLATION_AND_DEPLOYMENT.md** Deployment Patterns
3. **HQQ_QUANTIZATION_BENCHMARKS_RESEARCH.md** Production serving (Tier 5)
4. Use benchmarking script to optimize

### Workflow 4: "My quantization is slow/inaccurate"
1. **HQQ_API_REFERENCE_AND_INTEGRATION.md** Part 4 (Troubleshooting)
2. **HQQ_QUANTIZATION_BENCHMARKS_RESEARCH.md** Tier 8 (Accuracy trade-offs)
3. **HQQ_API_REFERENCE_AND_INTEGRATION.md** Part 5 (Optimization tips)
4. **HQQ_RESEARCH_REPORT.md** Section 13, Step 9 (Benchmarking)

### Workflow 5: "I want to fine-tune a quantized model"
1. **HQQ_RESEARCH_REPORT.md** Section 4.5 (Fine-tuning API)
2. **HQQ_RESEARCH_REPORT.md** Section 13, Step 8 (QAT code)
3. **HQQ_API_REFERENCE_AND_INTEGRATION.md** Part 5 (Optimization)

### Workflow 6: "What's the performance of HQQ on my hardware?"
1. **HQQ_QUANTIZATION_BENCHMARKS_RESEARCH.md** Tier 4 (Hardware-specific)
2. **HQQ_INSTALLATION_AND_DEPLOYMENT.md** Benchmarking script
3. Run benchmark_hqq.py on your hardware
4. Compare with Tier 2-9 results

---

## 4. Quick Reference: API Functions

### Core Classes & Functions

| Function/Class | File | Purpose | Example |
|---|---|---|---|
| `HQQLinear()` | HQQ_RESEARCH_REPORT.md 4.1 | Quantize linear layer | `HQQLinear(nn.Linear(768, 768), {'nbits': 4})` |
| `HQQLinear.quantize_module()` | HQQ_API_REFERENCE.md Part 1 | Quantize module (static) | `HQQLinear.quantize_module(module)` |
| `hqq_global_conf.initialize()` | HQQ_API_REFERENCE.md Part 1 | Set global defaults | `hqq_global_conf.initialize(nbits=4)` |
| `model.save_pretrained()` | HQQ_RESEARCH_REPORT.md 4.6 | Save quantized model | `model.save_pretrained('./path')` |
| `HQQLinear.from_pretrained()` | HQQ_RESEARCH_REPORT.md 4.6 | Load quantized model | `HQQLinear.from_pretrained('./path')` |

### Configuration Presets

| Preset | Use Case | Settings | Reference |
|---|---|---|---|
| `CONFIG_8BIT` | Minimal accuracy loss | nbits=8, group_size=128 | HQQ_API_REFERENCE.md Part 2 |
| `CONFIG_4BIT` | Recommended, balanced | nbits=4, group_size=64 | HQQ_API_REFERENCE.md Part 2 |
| `CONFIG_2BIT` | Aggressive, research | nbits=2, group_size=32 | HQQ_API_REFERENCE.md Part 2 |
| `CONFIG_MOBILE` | Edge devices | nbits=4, group_size=32, offload_meta=True | HQQ_API_REFERENCE.md Part 2 |
| `CONFIG_ACCURATE` | Accuracy-critical | nbits=8, group_size=256 | HQQ_API_REFERENCE.md Part 2 |

---

## 5. Implementation Examples by Framework

### PyTorch Native
**File**: HQQ_RESEARCH_REPORT.md Section 13, Step 2-3  
**Key Code**: Custom modules with HQQLinear  
**Use Case**: Custom models, research

### Hugging Face Transformers
**File**: HQQ_RESEARCH_REPORT.md Section 13, Step 4  
**File**: HQQ_API_REFERENCE.md Part 3, Pattern 1  
**Key Code**: `quantize_transformers_model()`  
**Use Case**: LLMs, pre-trained models

### Mixed-Precision Quantization
**File**: HQQ_RESEARCH_REPORT.md Section 13, Step 5  
**File**: HQQ_API_REFERENCE.md Part 3, Pattern 3  
**Key Code**: Layer-type-based configuration  
**Use Case**: Optimized inference, flexible precision

### Inference Services
**File**: HQQ_INSTALLATION_AND_DEPLOYMENT.md (Flask, Kubernetes patterns)  
**Key Code**: HQQBatchProcessor, Flask app  
**Use Case**: Production serving, APIs

### Fine-Tuning (QAT)
**File**: HQQ_RESEARCH_REPORT.md Section 13, Step 8  
**Key Code**: `train_quantized_model()`  
**Use Case**: Model adaptation, improving quantized performance

---

## 6. Benchmark Results Quick Reference

### Latency (ms/token) on NVIDIA A100-80GB, 4-bit HQQ

| Model | Latency | Throughput |
|-------|---------|-----------|
| LLaMA-2-7B | 18-22 ms | 45-55 tok/s |
| LLaMA-2-13B | 28-35 ms | 29-36 tok/s |
| LLaMA-2-70B | 45-55 ms | 18-22 tok/s |

**Source**: HQQ_QUANTIZATION_BENCHMARKS_RESEARCH.md Tier 2

### Hardware Comparison (LLaMA-2-7B, 4-bit)

| Hardware | Latency | Speedup vs Full FP32 |
|----------|---------|-------------------|
| H100 | 10-12 ms | 2.0-2.2x |
| A100 | 18-22 ms | 1.8-2.0x |
| V100 | 25-30 ms | 1.6-1.8x |
| CPU Xeon | 180-250 ms | 0.05x (55x slower) |

**Source**: HQQ_QUANTIZATION_BENCHMARKS_RESEARCH.md Tier 4

### Memory Footprint

| Precision | LLaMA-2-7B | LLaMA-2-13B | LLaMA-2-70B |
|-----------|-----------|-----------|-----------|
| FP32 | 13 GB | 26 GB | 280 GB |
| FP16 | 6.5 GB | 13 GB | 140 GB |
| HQQ 4-bit | 3.5 GB | 6.5 GB | 35 GB |

**Source**: HQQ_QUANTIZATION_BENCHMARKS_RESEARCH.md Tier 2

---

## 7. Installation Quick Commands

```bash
# Minimal setup (5 min)
python -m venv hqq_env
source hqq_env/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cu118
pip install hqq transformers

# Full setup with dev tools
pip install hqq[transformers] datasets accelerate jupyter

# From source (development)
git clone https://github.com/mobiusml/HQQ.git
cd HQQ && pip install -e .

# Docker
docker build -f Dockerfile.hqq -t hqq_env .
docker run --gpus all -it hqq_env
```

**Details**: HQQ_INSTALLATION_AND_DEPLOYMENT.md

---

## 8. Official Resources

### GitHub Repository
- **URL**: https://github.com/mobiusml/HQQ
- **Issues**: For bug reports and feature requests
- **Discussions**: Community Q&A

### Hugging Face Model Hub
Pre-quantized models:
- `mobiusml/hqq-7b-0` — LLaMA-2-7B HQQ 4-bit
- `mobiusml/hqq-13b-0` — LLaMA-2-13B HQQ 4-bit
- `mobiusml/hqq-70b-0` — LLaMA-2-70B HQQ 4-bit
- `mobiusml/Mistral-7B-hqq` — Mistral-7B HQQ 4-bit

### Related Repositories
- **HQQ-Transformers**: https://github.com/mobiusml/HQQ-Transformers
- **Text Generation WebUI**: Supports HQQ quantized models

---

## 9. Troubleshooting Matrix

| Issue | Symptom | First Step | Document |
|-------|---------|-----------|----------|
| Installation fails | `ImportError: No module named 'hqq'` | Reinstall from source | HQQ_INSTALLATION_AND_DEPLOYMENT.md |
| Out of memory | CUDA OOM during quantization | Reduce group_size | HQQ_API_REFERENCE.md Part 4 |
| Slow inference | Dequantization overhead | Use larger group_size | HQQ_API_REFERENCE.md Part 5 |
| High accuracy drop | PPL increases 20%+ | Use 8-bit, fine-tune with QAT | HQQ_QUANTIZATION_BENCHMARKS.md Tier 8 |
| Model loading slow | Takes >10 minutes | Pre-download, use SSD cache | HQQ_INSTALLATION_AND_DEPLOYMENT.md |
| GPU not detected | CUDA unavailable | Check nvidia-smi | HQQ_INSTALLATION_AND_DEPLOYMENT.md |

---

## 10. Performance Optimization Checklist

Before deploying to production:

- [ ] Read HQQ_API_REFERENCE.md Part 5 (optimization tips)
- [ ] Run HQQ_INSTALLATION_AND_DEPLOYMENT.md benchmarking script
- [ ] Choose appropriate bit-width (use CONFIG_4BIT as default)
- [ ] Test with target batch sizes
- [ ] Measure memory usage with `torch.cuda.memory_allocated()`
- [ ] Profile with PyTorch profiler (`torch.profiler`)
- [ ] Consider mixed-precision for critical layers
- [ ] Test fine-tuning (QAT) if accuracy is insufficient
- [ ] Set up monitoring (HQQ_INSTALLATION_AND_DEPLOYMENT.md Flask example)
- [ ] Load test with expected QPS (HQQ_INSTALLATION_AND_DEPLOYMENT.md batch processor)

---

## 11. Feature Matrix: What You Can Do With HQQ

| Feature | Support | Reference |
|---------|---------|-----------|
| 1-bit quantization | ✓ | HQQ_RESEARCH_REPORT.md 3 |
| 2-bit quantization | ✓ | HQQ_RESEARCH_REPORT.md 3 |
| 4-bit quantization | ✓ (recommended) | HQQ_RESEARCH_REPORT.md 3 |
| 8-bit quantization | ✓ | HQQ_RESEARCH_REPORT.md 3 |
| Mixed-precision | ✓ | HQQ_API_REFERENCE.md Part 3 |
| Per-channel quantization | ✓ | HQQ_RESEARCH_REPORT.md 4.3 |
| Post-training quantization | ✓ | HQQ_RESEARCH_REPORT.md 4.1 |
| Fine-tuning (QAT) | ✓ | HQQ_RESEARCH_REPORT.md 13 Step 8 |
| Layer replacement | ✓ | HQQ_RESEARCH_REPORT.md 13 Step 3 |
| Transformers integration | ✓ | HQQ_RESEARCH_REPORT.md 13 Step 4 |
| VLLM integration | Partial | Check repo for latest |
| ONNX export | Possible (custom) | GitHub discussions |
| Gradient-based fine-tuning | ✓ | HQQ_RESEARCH_REPORT.md 13 Step 8 |
| Batch inference | ✓ | HQQ_INSTALLATION_AND_DEPLOYMENT.md |
| GPU acceleration | ✓ | HQQ_RESEARCH_REPORT.md 4 |
| CPU inference | ✓ (slower) | HQQ_QUANTIZATION_BENCHMARKS.md Tier 4 |

---

## 12. Recommended Reading Order

### First Time (2-3 hours)
1. This document (sections 1-5)
2. HQQ_RESEARCH_REPORT.md sections 1-5
3. HQQ_INSTALLATION_AND_DEPLOYMENT.md Quick Start
4. Try simple example from HQQ_RESEARCH_REPORT.md 13 Step 1

### Intermediate (4-5 hours)
1. HQQ_RESEARCH_REPORT.md sections 6-13 (complete)
2. HQQ_API_REFERENCE.md parts 1-3
3. Implement one integration pattern
4. Run benchmarks on your hardware

### Advanced (8+ hours)
1. HQQ_QUANTIZATION_BENCHMARKS.md (all tiers)
2. HQQ_API_REFERENCE.md parts 4-6 (troubleshooting & optimization)
3. HQQ_INSTALLATION_AND_DEPLOYMENT.md (deployment patterns)
4. Study official GitHub repository
5. Implement production service

---

## 13. Related Quantization Methods (Quick Comparison)

| Method | Accuracy | Speed | Ease of Use | Reference |
|--------|----------|-------|-------------|-----------|
| **HQQ** | High | Fast quantization | Easy | HQQ_RESEARCH_REPORT.md |
| GPTQ | Very high | Slow quantization | Medium | HQQ_QUANTIZATION_BENCHMARKS.md Tier 9 |
| AWQ | High | Medium quantization | Easy | HQQ_QUANTIZATION_BENCHMARKS.md Tier 9 |
| INT8 (Native) | Medium | Fast | Hard to tune | HQQ_QUANTIZATION_BENCHMARKS.md Tier 9 |

**Recommendation**: Start with HQQ 4-bit as first choice, move to 8-bit or GPTQ if accuracy insufficient.

---

## 14. Support & Community

### Getting Help
1. Check **HQQ_API_REFERENCE.md** Part 4 (Troubleshooting) first
2. Search GitHub issues: https://github.com/mobiusml/HQQ/issues
3. Read GitHub discussions
4. Post issue with:
   - Error message
   - Reproducible code
   - System info (GPU, CUDA version, PyTorch version)
   - Environment output from HQQ_INSTALLATION_AND_DEPLOYMENT.md

### Contributing
- GitHub: https://github.com/mobiusml/HQQ
- Contributions welcome: bug fixes, optimizations, new backends
- See CONTRIBUTING.md in repo

---

## Summary Table

| Need | Document | Section |
|------|----------|---------|
| Learn HQQ theory | HQQ_RESEARCH_REPORT.md | 1-5 |
| Find API reference | HQQ_API_REFERENCE.md | 1 |
| Configure quantization | HQQ_API_REFERENCE.md | 2 |
| Integrate with Transformers | HQQ_RESEARCH_REPORT.md | 13 Step 4 |
| Deploy to production | HQQ_INSTALLATION_AND_DEPLOYMENT.md | Deployment Patterns |
| See performance data | HQQ_QUANTIZATION_BENCHMARKS.md | All tiers |
| Fix problems | HQQ_API_REFERENCE.md | 4 |
| Optimize performance | HQQ_API_REFERENCE.md | 5 |
| Install HQQ | HQQ_INSTALLATION_AND_DEPLOYMENT.md | Installation Guide |

---

**Research Compiled By**: Investigation Team  
**Date**: 2026-07-06  
**Status**: Complete and Cross-Referenced  
**Next Review**: When HQQ releases v2.0 or major features change  
**Maintenance**: Update links and benchmarks when GitHub repo updates
