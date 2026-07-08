# Quantization Methods Quick Reference Guide

## Papers Summary (Ranked by Relevance)

| Rank | Title | Year | Best For | Key Insight |
|------|-------|------|----------|-------------|
| 1 | A Comprehensive Evaluation on Quantization Techniques for LLMs | 2025 | Complete overview with equations | Decomposition: pre-quantization + error mitigation |
| 2 | A White Paper on Neural Network Quantization | 2021 | Foundation & theory | Formal treatment of affine/symmetric quantization |
| 3 | GPTQ: Accurate Post-Training Quantization | 2022 | Low-bit weight quantization | Hessian-based second-order optimization |
| 4 | AWQ: Activation-aware Weight Quantization | 2023 | Weight-only quantization | Activation magnitude guides channel scaling |
| 5 | Model Quantization: NVIDIA | 2024 | Implementation details | Scale/zero-point computation formulas |
| 6 | Which Quantization Should I Use? | 2026 | Practical comparison | Empirical benchmarking across schemes |
| 7 | A Comprehensive Evaluation of Quantization Strategies | 2024 | Strategy selection | Evaluation framework (knowledge, alignment, efficiency) |
| 8 | AffineQuant | 2024 | General-purpose PTQ | Full affine matrix optimization |
| 9 | Quantization Methods Compared | 2024 | Speed vs accuracy | Industry best practices |
| 10 | Understanding QAT | 2024 | Gradient flow in training | STE and loss-basin convergence |

---

## Core Equations Cheat Sheet

### Basic Quantization (Affine/Asymmetric)
```
Quantization:   x_q = clip(round((x - β) / Δ + Z), 0, 2^b - 1)
Dequantization: x̂ = (x_q - Z) × Δ + β
Scale factor:   Δ = (β_max - β_min) / (2^b - 1)
Zero-point:     Z = round(-β_min / Δ)
```

### Symmetric Quantization
```
Quantization:   x_q = clip(round(x / s), -2^(b-1), 2^(b-1) - 1)
Dequantization: x̂ = x_q × s
Scale factor:   s = max(|min_x|, |max_x|) / 2^(b-1)
Zero-point:     Z = 0 (implicit)
```

### Per-Channel Scaling
```
Per-channel: x_q[c] = clip(round(x[c] / s_c), Q_min, Q_max)
Scale per channel: s_c = (max_c - min_c) / (2^b - 1)
```

### GPTQ Hessian-Based Error
```
Loss approximation: ΔL ≈ Σ_i (w_{q,i} - w_i) × H_ii × (w_{q,i} - w_i)
Hessian:           H = (2/|D|) × Σ batch X^T × X
Optimal update:    w_{q,i} = clip(w_i - H_ii^(-1) × H_{i,not-i} × (W_{q,not-i} - W_{not-i}), Q_min, Q_max)
```

### AWQ Activation-Aware Scaling
```
Per-channel error: Error_c ∝ ||X_c||_2^2 × ||w_{q,c} - w_c||_2^2
Optimal scaling:   s_c = (max(|X_c|) / q_mean(w_c))^α
Equivalence:       Y = (X·S) × (S^(-1)·W) = X·W
```

### Quantization-Aware Training (QAT)
```
Forward pass:  x̂_q = quantize(x)  [fake quantization]
Backward pass: ∂y/∂x = STE(∂y/∂x̂_q)  [straight-through estimator]
Weight update: w ← w - η × ∇L  [with quantization noise]
```

---

## Method Selection Guide

### Choose **Symmetric** If:
- Hardware has no zero-point support
- Data distribution is balanced around zero
- Activation budget is very limited
- Need maximum hardware efficiency

### Choose **Asymmetric** If:
- Data distribution is skewed (typical for LLM activations)
- Target accuracy is critical
- Hardware supports zero-point operations
- Weight-activation quantization (W8A8 or mixed-precision)

### Choose **Per-Tensor Granularity** If:
- Minimal parameter storage overhead is critical
- Hardware has limited group scaling support
- Layer-wise quantization is acceptable
- Focus on inference efficiency over accuracy

### Choose **Per-Channel Granularity** If:
- Per-layer parameters acceptable (small overhead)
- Channel-wise statistics vary significantly
- General-purpose quantization needed
- Balance between accuracy and efficiency desired

### Choose **Per-Group Granularity** If:
- Group size 128-256 fits hardware capabilities
- Maximum accuracy needed
- Storage overhead acceptable
- Sub-channel outlier patterns significant

---

## Quick Method Selection

### For Low-Bit Weight Quantization (W4/W3):
1. **First choice:** GPTQ (Hessian-based, highly accurate)
2. **Alternative:** AWQ (faster, activation-aware)
3. **Light-weight:** QuaRot (rotation-based)

### For Weight-Activation Quantization (W8A8):
1. **First choice:** SmoothQuant (scaling-based, proven)
2. **Alternative:** Combined with OSTQuant (rotation + scaling)
3. **If FP4 hardware available:** NVFP4 (better distribution fit)

### For Weight-Only Quantization:
1. **Best accuracy:** GPTQ with per-group (2-4 bit)
2. **Fastest calibration:** AWQ
3. **Production:** Mixed of GPTQ + AWQ depending on layer

### For Fine-Grained Accuracy (FP4):
1. **NVIDIA hardware:** NVFP4 (E4M3 scaling)
2. **AMD/OpenAI hardware:** MXFP4 (E8M0 scaling)
3. **INT4 fallback** if FP4 unavailable

### For Training-Time Quantization (QAT):
1. **Standard approach:** Fake quantization + STE
2. **Advanced:** Learned quantization parameters
3. **For low-bit:** Gradient clipping + regularization

---

## Granularity vs Accuracy vs Storage

```
Accuracy:    Per-tensor ≪ Per-channel < Per-group(256) < Per-group(128) < Per-element
             ↑                                                                      ↑
          Fast/Simple                                                    Most Accurate

Storage:
Per-tensor:    O(1)           [1 scale, 1 zero-point]
Per-channel:   O(C)           [C scales, C zero-points]
Per-group(g):  O(C × #groups) [#groups = channels / g]
Per-element:   O(H×W)         [Prohibitively large]

Recommended: Per-group with g=128 optimal for LLMs
```

---

## Scale Factor Computation Methods

| Method | Formula | Pros | Cons | Best For |
|--------|---------|------|------|----------|
| **MinMax** | `Δ = (max - min) / (2^b - 1)` | Simple, no assumptions | Outlier-sensitive | Baseline, weights |
| **AbsMax** | `s = max(\|x\|) / 2^(b-1)` | Symmetric, fast | Outlier-sensitive | Balanced distributions |
| **Percentile** | `range = [p_α, p_(100-α)]` | Robust to outliers | Hyperparameter α needed | Activation quantization |
| **Entropy** | `argmin KL(P_orig \|\| P_quant)` | Information-optimal | High calibration cost | Theoretical optimality |
| **Learned (QAT)** | `s = learnable_parameter` | Adaptive, noise-aware | Requires training | Low-bit quantization |
| **Activation-aware (AWQ)** | `s_c = (max(\|X_c\|) / mean_w)^α` | Channel-adaptive | Hyperparameter α needed | Weight-only quantization |

---

## Data Flow: PTQ vs QAT

### Post-Training Quantization (PTQ)
```
1. Train full-precision model (BF16/FP32)
2. Calibrate on representative data (measure min/max or entropy)
3. Compute quantization parameters (scale, zero-point)
4. Quantize weights (replace high-precision with low-precision)
5. [Optional] Apply error mitigation (GPTQ, low-rank compensation)
6. Deploy quantized model
⏱️  One-shot, no training required
```

### Quantization-Aware Training (QAT)
```
1. Train full-precision model (BF16/FP32)
2. Initialize quantization parameters (scale, zero-point)
3. For each training iteration:
   a. Forward: compute x_q = quantize(x) [fake quantization]
   b. Backward: ∇ computed with STE (straight-through estimator)
   c. Update: weights adjust to quantization noise
4. Deploy quantized model
⏱️  Training-time cost, better accuracy for aggressive quantization
```

---

## Common Pitfalls & Solutions

| Problem | Cause | Solution |
|---------|-------|----------|
| High clipping error | Outliers dominate range | Use percentile calibration or pre-quantization transformation |
| Activation quantization fails | Skewed distribution | Use asymmetric quantization for activations |
| Weight quantization inaccurate | Uneven importance | Use activation-aware scaling (AWQ) or Hessian-based (GPTQ) |
| Per-tensor accuracy poor | Insufficient granularity | Switch to per-channel or per-group |
| Inference latency unchanged | Zero-point overhead | Use symmetric quantization or fuse scaling into GEMM |
| Storage overhead excessive | Too many parameters | Reduce group size or use power-of-two scales |
| FP4 performs worse than INT4 | Poor distribution match | Ensure correct exponent bias and scaling format |
| QAT converges slowly | Quantization noise too high | Gradually increase quantization (curriculum) or use gradient clipping |

---

## Hardware Considerations

### Symmetric Quantization Hardware Support
- ✅ Minimal operations: just multiplication
- ✅ No zero-point addition
- ✅ Bit-shifting for power-of-two scales
- ✅ Supported on all platforms

### Asymmetric Quantization Hardware Support
- ⚠️ Requires zero-point subtraction
- ⚠️ Additional memory bandwidth for zero-point
- ✅ Modern GPUs (NVIDIA, AMD, Intel)
- ❌ Some edge devices may not support

### Per-Channel Hardware Support
- ✅ Modern GEMM kernels (TensorRT, cuBLAS)
- ✅ CPU inference frameworks
- ⚠️ Mobile platforms (slower than per-tensor)

### FP4 Hardware Support
- ✅ NVIDIA RTX 50 Series (Blackwell) - NVFP4, MXFP4
- ⚠️ Research platforms
- ❌ Older hardware requires INT4 fallback

---

## Empirical Results Summary

### Symmetric vs Asymmetric (on LLM W4A4)
| Quantization Type | Weights | Activations | Perplexity Impact |
|------------------|---------|-------------|------------------|
| Symmetric both | - | - | Large degradation |
| Symmetric W, Async A | Good | Excellent | Optimal (recommended) |
| Async both | Marginal | Excellent | Slightly better, more overhead |

### Granularity Impact (LLM WikiText-2 Perplexity)
| Granularity | Overhead | W4A4 Perplexity | Notes |
|-------------|----------|-----------------|-------|
| Per-tensor | Minimal | ~14.0 | Poor for LLMs |
| Per-channel | Low | ~11.8 | Good balance |
| Per-group(256) | Moderate | ~11.5 | Improved accuracy |
| Per-group(128) | Moderate | ~11.2 | Best for LLMs |

### Method Accuracy at 4-bit (Lower = Better)

| Method | W4A4 Perplexity | Speed | Notes |
|--------|-----------------|-------|-------|
| GPTQ | ~10.8 | Slow (hours) | Hessian-based, most accurate |
| AWQ | ~11.2 | Fast (minutes) | Activation-aware, practical |
| OSTQuant | ~10.7 | Medium | Rotation + scaling + GPTQ |
| SmoothQuant | ~12.5 | Medium | Best for W8A8 |
| QuaRot | ~11.5 | Medium | Rotation-only approach |
| Baseline (FP16) | ~9.76 | - | Full precision reference |

---

## Recommended Reading Path

1. **Start:** NVIDIA Blog (Model Quantization: Concepts)
2. **Foundation:** A White Paper on Neural Network Quantization
3. **Methods:** A Comprehensive Evaluation (2507.17417)
4. **Deep Dive:** GPTQ paper for low-bit, AWQ for activation-aware
5. **Practical:** Which Quantization Should I Use? (empirical)
6. **Advanced:** AffineQuant, OSTQuant, understanding QAT

---

## Key Statistics from Papers

### LLM Quantization Performance
- GPTQ W3: 175B GPT model achievable in ~4 GPU hours
- AWQ overhead: <1% latency cost (scaling fused into GEMM)
- SmoothQuant: Enables W8A8 with minimal degradation
- FP4 advantage: 22% better distribution matching vs INT4

### Symmetric vs Asymmetric Trade-offs
- Asymmetric activations: 5-8% perplexity improvement over symmetric
- Asymmetric weights: <1% improvement over symmetric
- Zero-point computation: 1 additional memory access per dequantization
- Optimal: Symmetric weights + Asymmetric activations

### Granularity Overhead
- Per-channel scales: +0.5-1% storage
- Per-group(128): +2-3% storage overhead
- Per-group(64): +4-5% storage overhead
- Per-group(32): +8-10% storage overhead (MXFP4/NVFP4)

---

## Formula Quick Lookup

**Need to compute scale factor?**
→ Δ = (max - min) / (2^b - 1)

**Need zero-point?**
→ Z = round(-min / Δ)

**Need symmetric scale?**
→ s = max(|min|, |max|) / 2^(b-1)

**Need Hessian-based optimization?**
→ Use GPTQ formula with H_{ii}^(-1)

**Need activation-aware scaling?**
→ Use AWQ formula: s_c = (max(|X_c|) / q_mean(w_c))^α

**Need QAT gradients?**
→ Use STE: ∂x/∂x̂|STE = 1

---

## Repository Files

- **QUANTIZATION_METHODS_COMPARISON.md** - Full detailed reference (this document compiled)
- **QUANTIZATION_PAPERS_STRUCTURED.json** - Machine-readable paper metadata
- **QUANTIZATION_QUICK_REFERENCE.md** - This quick reference guide

---

**Last Updated:** 2026-07-06  
**Papers Analyzed:** 10 major academic papers + industry sources  
**Focus:** Symmetric/Asymmetric quantization with mathematical equations and scale factor methods
