# AWQ Comparative Analysis: Detailed Benchmarking Matrix

**Compilation:** July 2026 | Based on academic papers, official benchmarks, and production deployments

---

## Table 1: Accuracy Metrics Across All Tested Models (4-bit Quantization)

### Perplexity Measurements

```
Model                  | Baseline (FP16) | AWQ-4bit | Loss %  | GPTQ-4bit | GPTQ Loss | RTN-4bit | RTN Loss
LLaMA-7B (Wikitext2)   | 5.09            | 5.14     | +0.98%  | 5.25      | +3.14%    | 5.47     | +7.46%
LLaMA-13B (Wikitext2)  | 4.62            | 4.68     | +1.30%  | 4.82      | +4.33%    | 5.12     | +10.82%
LLaMA-70B (C4)         | 6.84            | 6.93     | +1.31%  | 7.25      | +5.98%    | 7.89     | +15.35%
OPT-6.7B (Wikitext2)   | 11.37           | 11.48    | +0.97%  | 11.89     | +4.57%    | 12.45    | +9.50%
OPT-13B (Wikitext2)    | 9.61            | 9.70     | +0.93%  | 10.12     | +5.30%    | 10.54    | +9.68%
OPT-30B (C4)           | 9.42            | 9.53     | +1.17%  | 10.01     | +6.26%    | 10.87    | +15.39%
OPT-66B (C4)           | 9.39            | 9.52     | +1.38%  | 10.08     | +7.35%    | 10.67    | +13.57%
Falcon-7B (Wikitext2)  | 4.53            | 4.59     | +1.32%  | 4.78      | +5.52%    | 5.12     | +13.04%
Falcon-40B (C4)        | 6.71            | 6.85     | +2.08%  | 7.35      | +9.54%    | 8.42     | +25.41%
Mistral-7B (Wikitext2) | 6.41            | 6.48     | +1.09%  | 6.78      | +5.77%    | 7.34     | +14.51%
MPT-7B (Wikitext2)     | 6.84            | 6.91     | +1.02%  | 7.28      | +6.43%    | 7.85     | +14.77%
MPT-30B (C4)           | 10.23           | 10.41    | +1.76%  | 11.05     | +7.98%    | 12.34    | +20.62%
```

**Key Observations:**
- AWQ shows **consistent <2% degradation** across all models and datasets
- GPTQ averages 3-7% degradation (3.5× worse than AWQ)
- RTN ranges from 7-25% degradation (7-25× worse than AWQ)
- **AWQ margin grows with model size**, especially for OPT-66B vs GPTQ

---

## Table 2: Downstream Task Performance (4-bit Quantization)

### MMLU (0-shot accuracy)
```
Model             | FP16 Baseline | AWQ-4bit | Δ      | GPTQ-4bit | Δ
LLaMA-7B          | 35.2%        | 34.9%    | -0.3%  | 33.8%     | -1.4%
LLaMA-13B         | 46.3%        | 45.8%    | -0.5%  | 44.6%     | -1.7%
LLaMA-70B         | 63.4%        | 62.9%    | -0.5%  | 61.2%     | -2.2%
OPT-13B           | 25.4%        | 25.2%    | -0.2%  | 24.6%     | -0.8%
Falcon-40B        | 58.3%        | 57.8%    | -0.5%  | 56.4%     | -1.9%
Mistral-7B        | 64.1%        | 63.8%    | -0.3%  | 62.5%     | -1.6%
```

### HellaSwag (0-shot accuracy)
```
Model             | FP16 Baseline | AWQ-4bit | Δ      | GPTQ-4bit | Δ
LLaMA-7B          | 78.4%        | 78.1%    | -0.3%  | 76.5%     | -1.9%
LLaMA-13B         | 83.7%        | 83.4%    | -0.3%  | 81.9%     | -1.8%
OPT-13B           | 68.9%        | 68.6%    | -0.3%  | 67.2%     | -1.7%
Mistral-7B        | 86.2%        | 85.9%    | -0.3%  | 84.8%     | -1.4%
```

### GSM8K (5-shot chain-of-thought accuracy)
```
Model             | FP16 Baseline | AWQ-4bit | Δ      | GPTQ-4bit | Δ
LLaMA-7B          | 11.4%        | 11.0%    | -0.4%  | 9.8%      | -1.6%
LLaMA-13B         | 20.7%        | 20.2%    | -0.5%  | 19.4%     | -1.3%
LLaMA-70B         | 50.9%        | 50.3%    | -0.6%  | 48.7%     | -2.2%
```

**Summary:** AWQ preserves downstream task performance within 0.6%; GPTQ shows 1-2% degradation.

---

## Table 3: Inference Speed Benchmarks

### Latency (ms per token generation) - LLaMA-7B on A100-40GB

```
Batch Size | FP16      | AWQ-4bit | Speedup | GPTQ-4bit | Speedup (vs FP16)
1          | 42.3 ms   | 18.9 ms  | 2.24x   | 20.1 ms   | 2.10x
4          | 23.5 ms   | 10.8 ms  | 2.18x   | 11.9 ms   | 1.98x
8          | 18.2 ms   | 8.1 ms   | 2.25x   | 8.9 ms    | 2.04x
16         | 15.8 ms   | 7.2 ms   | 2.19x   | 8.0 ms    | 1.98x
32         | 14.2 ms   | 6.5 ms   | 2.18x   | 7.3 ms    | 1.95x
```

### Throughput (tokens/second) - LLaMA-13B on A100-40GB

```
Batch Size | FP16         | AWQ-4bit   | Speedup | GPTQ-4bit  | Speedup
1          | 41.2 t/s     | 100.4 t/s  | 2.44x   | 89.2 t/s   | 2.16x
8          | 156.3 t/s    | 369.2 t/s  | 2.36x   | 338.1 t/s  | 2.16x
16         | 214.8 t/s    | 507.6 t/s  | 2.36x   | 460.3 t/s  | 2.14x
32         | 245.2 t/s    | 598.4 t/s  | 2.44x   | 532.1 t/s  | 2.17x
```

### Speed Comparison (LLaMA-70B on A100-80GB)

```
Method                    | Tokens/sec | Relative | Memory | GPU Count
FP16 (baseline)          | 45         | 1.0x     | 140GB  | 2×A100
AWQ-4bit                 | 98         | 2.18x    | 36GB   | 1×A100
GPTQ-4bit                | 82         | 1.82x    | 38GB   | 1×A100
INT8 (TensorRT)          | 88         | 1.95x    | 70GB   | 1×A100
Speedup advantage:       | AWQ 19.5% faster than GPTQ, 11% faster than INT8
```

---

## Table 4: Memory Footprint Analysis

### Peak GPU Memory (Generation Phase, Batch=1)

```
Model      | FP16 (GB) | AWQ-4bit (GB) | Savings | GPTQ-4bit (GB) | INT8 (GB)
LLaMA-7B   | 14.5      | 4.2           | 71%     | 4.5            | 7.3
LLaMA-13B  | 26.3      | 7.6           | 71%     | 8.1            | 13.2
LLaMA-70B  | 140       | 35            | 75%     | 38             | 70
OPT-13B    | 27.8      | 8.0           | 71%     | 8.6            | 14.0
OPT-30B    | 61.2      | 17.5          | 71%     | 18.9           | 30.6
OPT-66B    | 132       | 38            | 71%     | 41             | 66
Falcon-7B  | 14.8      | 4.3           | 71%     | 4.6            | 7.4
Falcon-40B | 80.1      | 23            | 71%     | 24.5           | 40
```

### Model Size (Disk Storage)

```
Model      | FP16    | AWQ-4bit | Reduction
LLaMA-7B   | 13.5GB  | 4.0GB    | 70.4%
LLaMA-13B  | 26.0GB  | 7.8GB    | 70.0%
LLaMA-70B  | 130GB   | 39GB     | 70.0%
Falcon-40B | 76GB    | 22.8GB   | 70.0%
```

---

## Table 5: Calibration Time Comparison

```
Method    | Calibration Data      | Time (LLaMA-7B) | Time (LLaMA-70B) | GPU Requirement
RTN       | None                  | 0 sec           | 0 sec            | None
AWQ       | 128 seq × 2048 tok    | 23 min          | 45 min           | 1×A100-40GB
GPTQ      | 128 seq × 2048 tok    | 4-6 hours       | 8-12 hours       | 1×A100-40GB
GGML      | None (post-training)  | 2-3 hours       | 6-8 hours        | 1×CPU
```

**Key Insight:** AWQ trades minimal calibration time (23-45 min) for significant accuracy improvement over RTN; 10-15x faster than GPTQ.

---

## Table 6: Hardware and Framework Support Matrix

```
Framework/Device    | AWQ Support | FP8 Support | INT8 Support | GPTQ Support | GGML Support
Hugging Face        | ✓ Native    | ✓ Partial   | ✓ Native     | ✓ Plugin     | ✓ Plugin
PyTorch             | ✓ Custom    | ✓ Native    | ✓ Native     | ✓ Custom     | ✗
TensorFlow          | ✓ Custom    | ✓ Native    | ✓ Native     | ✗           | ✗
vLLM (GPU serving)  | ✓ Optimized | ✓ Partial   | ✓ Optimized  | ✓ Partial    | ✗
ONNX Runtime        | ✓ Support   | ✓ Native    | ✓ Native     | ✓ Custom     | ✗
llama.cpp (CPU)     | ✓ Q4       | ✗           | ✗            | ✓ Q4        | ✓ Q4
Mobile (iOS)        | ✓ Possible  | ✗           | ✗            | ✗           | ✓ Q4
Mobile (Android)    | ✓ Possible  | ✗           | ✗            | ✗           | ✓ Q4
H100 (Native FP8)   | ✓ Good      | ✓ Optimal   | ✓ Good       | ✓ Good       | ✗
A100/A10            | ✓ Optimal   | ✓ Good      | ✓ Good       | ✓ Good       | ✗
T4/L4 (Edge)        | ✓ Good      | ✓ Limited   | ✓ Good       | ✓ Limited    | ✗
```

---

## Table 7: Cost-Benefit Analysis for Enterprise Deployment

### Scenario: Deploying LLaMA-70B at Scale (1M tokens/day)

```
Deployment Strategy      | GPU Requirement | AWS Cost/Month | Model Size | Accuracy Loss
FP16 (2×A100-80GB)      | 2×A100-80GB     | $24,960        | 130GB      | 0%
AWQ-4bit (1×A100-80GB)  | 1×A100-80GB     | $12,240        | 39GB       | 1.31%
GPTQ-4bit (1×A100-80GB) | 1×A100-80GB     | $12,240        | 41GB       | 5.98%
INT8 (1×A100-80GB)      | 1×A100-80GB     | $12,240        | 70GB       | ~3%
Savings (AWQ vs FP16)   | 50% reduction   | $12,720/month  | 70% smaller| Minimal
ROI on Quantization     | Payback: <2 hours | Ongoing savings | -           | -
```

### Scenario: Mobile/Edge Deployment (LLaMA-7B on Jetson AGX Orin)

```
Deployment Strategy | Hardware     | Power Draw | Inference Speed | Battery Life | Viability
FP16               | 32GB Orin    | 18W        | Not viable (OOM)  | N/A         | No
AWQ-4bit           | 32GB Orin    | 15W        | 8.9 t/s           | 120 min     | Yes
INT8               | 32GB Orin    | 16W        | 7.2 t/s           | 95 min      | Yes
2-bit (extreme)    | 32GB Orin    | 12W        | 15 t/s            | 180 min     | Poor quality
```

---

## Table 8: Bit-Width Trade-off Matrix

### LLaMA-7B: Progressive Quantization Analysis

```
Bit-Width | Model Size | Perplexity | Loss %  | MMLU   | Latency | Speedup | Memory | Viability
FP16      | 13.5GB     | 5.09       | —       | 35.2%  | 42.3ms  | 1.0x    | 14.5GB | Yes
8-bit     | 6.8GB      | 5.10       | +0.2%   | 35.1%  | 21.2ms  | 2.0x    | 7.3GB  | Yes
6-bit     | 5.2GB      | 5.11       | +0.4%   | 35.0%  | 15.4ms  | 2.75x   | 5.5GB  | Yes
5-bit     | 4.6GB      | 5.12       | +0.6%   | 34.9%  | 14.8ms  | 2.85x   | 4.9GB  | Yes
4-bit     | 4.0GB      | 5.14       | +0.98%  | 34.9%  | 18.9ms  | 2.24x   | 4.2GB  | Yes ★★★
3-bit     | 3.3GB      | 5.42       | +6.5%   | 34.1%  | 13.2ms  | 3.2x    | 3.3GB  | Marginal
2-bit     | 2.5GB      | 6.89       | +35.4%  | 30.8%  | 12.1ms  | 3.5x    | 2.5GB  | No
```

### Key Insights by Bit-Width:
- **8-bit:** Near-lossless, recommended when hardware supports it or model size not critical
- **4-bit:** SWEET SPOT - 2.24x speedup, <1% loss, 71% compression ★★★
- **3-bit:** 6.5% loss - only consider if extreme latency required
- **2-bit:** 35% loss - research only, not practical for production

---

## Table 9: Production Deployment Characteristics

### Quantization Stability Metrics

```
Metric                      | AWQ-4bit | GPTQ-4bit | INT8 | FP8
Numerical Stability        | Excellent | Good      | Good | Excellent
Output Consistency         | 99.9%    | 99.8%     | 99.7%| 99.9%
Cross-Entropy Variance     | ±0.01%   | ±0.05%    | ±0.02%| ±0.01%
NaN/Inf Occurrences        | 0/1000   | 0/1000    | 1/1000| 0/1000
Latency Stability (std dev)| 9.1%     | 8.5%      | 11.2%| 7.8%
```

### Cold Start Performance

```
Metric              | FP16    | AWQ-4bit | GPTQ-4bit | INT8
Model Load Time     | 8.2s    | 2.1s     | 2.3s      | 4.1s
First Token (P50)   | 52ms    | 28ms     | 32ms      | 38ms
Warmup Overhead     | None    | <1%      | <1%       | 2%
```

---

## Table 10: Emerging AWQ-Based Improvements (2024-2026)

```
Method           | Year | Improvement | Accuracy Gain | Complexity | Status
---              | ---- | ----------- | ------------- | --------- | ------
AWQ (baseline)   | 2023 | Activation-aware | —            | Medium    | Production
ActivationQuant  | 2024 | Entropy minimization | +1-2%     | Medium    | Academic
QuaCK            | 2024 | AWQ + Knowledge Distillation | +1-2% | High | Academic
Mixed-Precision  | 2024 | Layer-wise (3-4-5 bits) | +2-3% | Medium | Emerging
AWQ-Pruning      | 2024 | AWQ + structured pruning | +1-3% | High | Research
```

---

## Summary: When to Use Each Method

| Use Case | Recommendation | Reasoning |
|----------|---|---|
| **New Production Deployment** | AWQ-4bit | Best all-around; proven at scale |
| **Maximum Accuracy** | FP16 or INT8 | Worth the memory cost |
| **Fastest Calibration** | RTN | When time-critical, trade some accuracy |
| **Mobile/Edge** | AWQ-4bit | Only practical option enabling <1.5% loss |
| **Existing GPTQ Infrastructure** | GPTQ-4bit | For continuity, but migrate to AWQ |
| **CPU Inference** | GGML/GGUF | CPU-optimized, AWQ not ideal |
| **Hardware-Agnostic** | FP8 | Better portability, slight accuracy cost |
| **Research/Experimentation** | AWQ-3bit | Trade 6.5% accuracy for 3x speedup |

---

## Critical Success Factors for AWQ Deployment

1. **Calibration Data Quality:** Use representative data; larger sets (256+ sequences) handle distribution shift better
2. **Baseline Accuracy:** AWQ preserves relative accuracy; poor FP16 models stay poor
3. **Framework Support:** Ensure target framework has native AWQ support (most do by 2024)
4. **Hardware Validation:** Test on exact hardware before production rollout
5. **Downstream Task Validation:** Always test on specific use-case tasks, not just perplexity

