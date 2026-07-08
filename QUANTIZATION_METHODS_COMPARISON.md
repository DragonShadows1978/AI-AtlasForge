# Quantization Methods Comparison: Symmetric vs Asymmetric & Key Techniques

## Overview
This document catalogs research papers comparing quantization methods with emphasis on mathematical formulations, symmetric vs asymmetric approaches, and scale factor methodologies.

---

## Paper 1: A Comprehensive Evaluation on Quantization Techniques for Large Language Models

**Title:** A Comprehensive Evaluation on Quantization Techniques for Large Language Models  
**Authors:** Multiple authors (2024-2025)  
**Publication Year:** 2025  
**Venue:** arXiv (arxiv.org/html/2507.17417v2)  
**Status:** Peer-reviewed conference quality  

### Key Contributions:
- **Decomposition Framework:** Splits quantization into two steps:
  1. Pre-quantization transformation (shifting, scaling, rotation)
  2. Quantization error mitigation (GPTQ, low-rank compensation)

### Core Equations:

#### Symmetric Quantization Formula:
```
x_q = clip(round(x / s), Q_min, Q_max)
```
- Forces zero point to align with floating point (z = 0)
- Results in quantization range symmetric with respect to zero
- Simpler dequantization: no zero-point addition needed

#### Asymmetric (Affine) Quantization Formula:
```
x_q = clip(round((x - z_fp) / s + z_int), Q_min, Q_max)
```
Where:
- z_fp = floating-point zero position
- z_int = integer zero position
- Allows zero point to shift dynamically per activation distribution

#### Shifting (Outlier Suppression):
For linear layers: X̂ = X - T, where T ∈ ℝ^(1×C_in) is channel-wise shifting vector
- Eliminates asymmetry across channels
- Counterterm added to bias: B̂ = B + T·W

#### Scaling (SmoothQuant-style):
For linear layers: X̂ = X·S, where S is diagonal scaling matrix
- Channel-wise activation scaling reduces outliers
- Inverse scaling applied to weights: Ŵ = S^(-1)·W
- Formula: Y = (X·S)·(S^(-1)·W) = X·W (mathematically equivalent)

#### Rotation (QuaRot, SpinQuant):
```
X̂ = X·O (where O is orthogonal matrix)
Ŵ = O^T·W
```
- Adjusts data distribution, reduces outliers
- Typically uses random Hadamard matrices for efficiency
- Output preserved: Y = (X·O)·(O^T·W) = X·W

### Self-Compensation Error Mitigation (GPTQ-style):
Loss approximation using Hessian:
```
L(W) ≈ L(W_q) + ∇L(W_q)^T·dq(W_q) + (1/2)·dq(W_q)^T·H·dq(W_q)
```
Where:
- dq(W_q) = W - dequantize(W_q)
- H = Hessian matrix of loss at W_q
- Goal: minimize δ (weight perturbation) to minimize quantization loss

### Low-Rank Compensation (Zero-Rank Quantization v2, CALDERA):
```
Y = (X·W_q) + A·B
```
Where:
- A ∈ ℝ^(C_in × r), B ∈ ℝ^(r × C_out)
- r << min(d, k) (bottleneck rank)
- A·B ≈ (W - W_q) (reconstruction of quantization error)
- Low-rank matrices stored in higher precision (INT8 or FP16)

### FP4 Quantization (MXFP4, NVFP4):
```
Value = (-1)^S × 2^E × (1 + M)  [E2M1 format]
```
- 1 sign bit, 2 exponent bits, 1 mantissa bit
- Representable values: {-6, -4, -3, -2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2, 3, 4, 6}
- MXFP4: 32-element groups, E8M0 scaling factors (powers of 2)
- NVFP4: 16-element groups, E4M3 scaling factors (more precise), extra per-tensor FP32 scaling

### Key Findings:
- **Symmetric vs Asymmetric:** Asymmetric quantization for activations yields significant benefits; benefits for weights are minor
- **Optimal Configuration:** Symmetric weights + Asymmetric activations combines accuracy with computational efficiency
- **Granularity Impact:** Finer granularity (per-group) improves accuracy but increases storage overhead for scale factors
- **Rotation Effectiveness:** Highly effective for INT4 and MXFP4; less effective for NVFP4 (due to small group size 16)
- **Best Overall Performance:** Optimized rotation + scaling + GPTQ + low-rank compensation

### Pre-Quantization Transformation Comparison:
| Method | Formula | Use Case | Computational Cost |
|--------|---------|----------|-------------------|
| Shifting | X̂ = X - T | Outlier suppression | Low |
| Scaling | X̂ = X·S | Smooth distribution | Medium |
| Rotation | X̂ = X·O | Incoherence reduction | Medium |
| Combined | X̂ = ((X - T)·S)·O | Best performance | Higher |

---

## Paper 2: A White Paper on Neural Network Quantization

**Title:** A White Paper on Neural Network Quantization  
**Authors:** Markus Nagel et al.  
**Publication Year:** 2021  
**Venue:** arXiv (arxiv.org/pdf/2106.08295)  
**Status:** Comprehensive review paper  

### Fundamental Quantization Formula:

#### General Affine Quantization:
```
x_q = Δ × clip(round((x - β)/Δ + Z), 0, 2^b - 1)
```
Where:
- x = original floating-point value
- Δ = scale factor (step size)
- β = clipping minimum
- Z = zero-point offset
- b = bit width
- clip() = bounds to representable range [0, 2^b - 1]

#### Scale Factor Calculation (MinMax Method):
```
Δ = (β_max - β_min) / (2^b - 1)
```
Where β_min, β_max are clipping boundaries

#### Symmetric Quantization Simplification:
"Symmetric quantization is a simplified version of the general asymmetric case"
- Constraint: Z = 0 (zero-point fixed at origin)
- Efficiency: Eliminates addition operations in hardware
- Trade-off: Suboptimal for asymmetric data distributions

#### Zero-Point Calculation (Asymmetric):
```
Z = round(-β_min / Δ)
```
Ensures floating-point 0 maps to integer Z_int within range [0, 2^b - 1]

### Power-of-Two Restrictions:
```
Δ = 2^(-k)  [for certain hardware]
```
- Enables bit-shifting instead of division
- Reduces computational cost at inference
- Constrains expressiveness of scale factors
- Trade-off between rounding and clipping error complexity

### URL for Direct Access:
https://arxiv.org/pdf/2106.08295

---

## Paper 3: Model Quantization - Concepts, Methods, and Why It Matters (NVIDIA)

**Title:** Model Quantization: Concepts, Methods, and Why It Matters  
**Authors:** NVIDIA Technical Blog  
**Publication Year:** 2023-2024  
**Venue:** NVIDIA Developer Blog  

### Quantization Range Mapping:

#### Core Formula:
```
x_q = clip(round((x - α) × s + z), α_q, β_q)
```
Where:
- x ∈ [α, β] (input range)
- x_q ∈ [α_q, β_q] (quantized range, e.g., INT8: [-128, 127])
- s = scale factor
- z = zero-point
- clip() bounds output to representable range

### Scale Factor and Zero-Point Computation:

#### Asymmetric (Affine):
```
s = (β - α) / (β_q - α_q)
z = α_q - (α × s)
```

#### Symmetric:
```
s = max(|α|, |β|) / max(|α_q|, |β_q|)
z = 0  [by definition]
```

### Per-Tensor vs Per-Channel Granularity:
- **Per-Tensor:** Single scale/zero-point for entire tensor
  - Formula: s = scalar, z = scalar
  - Hardware efficient, lower accuracy
  
- **Per-Channel:** Individual scale/zero-point per input channel
  - Formula: s ∈ ℝ^C, z ∈ ℝ^C (vectors)
  - Better accuracy, moderate overhead
  
- **Per-Block:** Sub-channel grouping for fine-grained control
  - Formula: s ∈ ℝ^(C × groups_per_channel)
  - Highest accuracy, maximum storage overhead

### AbsMax Method (Symmetric):
```
s = max(|x|) / max(|x_q|)
```
Simple, symmetric around zero, suitable for activations with balanced distributions

---

## Paper 4: Which Quantization Should I Use? Unified Evaluation of llama.cpp Quantization

**Title:** Which Quantization Should I Use? A Unified Evaluation of llama.cpp Quantization on Llama-3.1-8B-Instruct  
**Authors:** Multiple authors  
**Publication Year:** 2026  
**Venue:** arXiv (arxiv.org/html/2601.14277)  
**Status:** Recent empirical comparison  

### Quantization Schemes Evaluated:
- Q2_K, Q3_K, Q4_K, Q5_K, Q6_K (K-quant variants with mixed-precision)
- Q4_0, Q5_0, Q6_0 (basic quantization)
- I-quant variants (importance-weighted)

### Comparison Framework:
- **Dimensions:** Accuracy-compression tradeoff, task sensitivity, throughput
- **Metrics:** Perplexity (WikiText-2), zero-shot benchmarks (GSM8K, HellaSwag, IFEval, MMLU, TruthfulQA)
- **Evaluation:** Hardware-controlled, normalized comparisons across schemes

### Key Findings:
- K-quant methods outperform basic methods at same bitwidth
- Per-group quantization provides better accuracy vs size tradeoff
- Mixed-precision (different bitwidths for weights/activations) optimal
- Task-specific sensitivity varies significantly

### URL:
https://arxiv.org/html/2601.14277

---

## Paper 5: AffineQuant - Affine Transformation Quantization for LLMs

**Title:** AffineQuant: Affine Transformation Quantization for Large Language Models  
**Authors:** Multiple authors  
**Publication Year:** 2024  
**Venue:** arXiv (arxiv.org/html/2403.12544v1)  

### Generalized Affine Transformation Approach:

#### Standard PTQ Limitation:
Previous methods optimize only linear scaling (diagonal matrices):
```
X̂ = X·S^(-1)  (activation scaling)
Ŵ = S·W       (weight scaling)
```
Limited optimization scope → higher quantization error

#### AffineQuant Innovation:
Full affine matrix optimization:
```
X̂ = X·M^(-1)  (M is affine matrix, not just diagonal)
Ŵ = M·W
```
Where M can be:
- Diagonal (scaling-only) - subcase
- General affine matrix - more expressive

#### Reversibility Guarantee:
```
Y = (X·M^(-1))·(M·W) = X·W  [mathematically identical]
```
- Outputs unchanged despite transformation
- Enables merging with LayerNorm weights/bias
- Zero inference overhead

#### Layer-wise Optimization:
Learns affine matrices per layer to minimize quantization error while:
- Maintaining mathematical equivalence
- Preserving model output exactly
- Enabling efficient fusion with normalization operations

### URL:
https://arxiv.org/html/2403.12544v1

---

## Paper 6: A Comprehensive Evaluation of Quantization Strategies for LLMs

**Title:** A Comprehensive Evaluation of Quantization Strategies for Large Language Models  
**Authors:** Multiple authors  
**Publication Year:** 2024  
**Venue:** arXiv (arxiv.org/html/2402.16775v1)  

### Evaluation Framework:
Three critical dimensions:
1. **Knowledge & Capacity:** Perplexity, zero-shot reasoning
2. **Alignment:** Instruction-following performance
3. **Efficiency:** Memory, latency, throughput

### Methods Compared:
- **Baseline:** Round-to-Nearest (RTN)
- **Statistical:** MinMax, Percentile, Entropy calibration
- **Advanced PTQ:** SmoothQuant, GPTQ, AWQ, OmniQuant
- **Hybrid:** Mixed-precision approaches

### Calibration Methods:
```
MinMax: range = [min(x), max(x)]
Percentile: range = [percentile(x, α), percentile(x, 100-α)]
Entropy: range = argmin KL(P_original || P_quantized)
```

### Key Insights:
- Statistical calibration significantly impacts accuracy
- SmoothQuant best for W8A8 (works on both weights and activations)
- GPTQ excels at low-bit weight-only quantization
- Trade-offs between calibration cost and accuracy gains

### URL:
https://arxiv.org/html/2402.16775v1

---

## Paper 7: Quantization Methods Compared - Speed vs Accuracy (RunPod)

**Title:** Quantization Methods Compared: Speed vs. Accuracy in Model Deployment  
**Authors:** RunPod Blog  
**Publication Year:** 2024  
**Venue:** https://www.runpod.io/blog/quantization-methods-speed-vs-accuracy  

### Primary Quantization Methods Taxonomy:

| Method | Abbreviation | Key Features | Equations |
|--------|--------------|--------------|-----------|
| **Post-Training Quantization** | PTQ | No retraining, calibration-based | `x_q = clip(round((x - β)/Δ), 0, 2^b-1)` |
| **Quantization-Aware Training** | QAT | Simulates quantization during training | `Loss = L(W_int, x); ∇L computed with quantization` |
| **Mixed-Precision** | MP | Different bitwidths per layer | `x_q^(l) ∈ bitwidth_l` |
| **Dynamic Quantization** | DQ | Per-sample quantization parameters | `Δ(x_i), z(x_i) computed per sample` |

### Post-Training Quantization (PTQ) Formula:
```
Steps:
1. Calibrate: measure min/max on representative data
2. Scale: Δ = (max - min) / (2^b - 1)
3. Quantize: x_q = clip(round((x - min) / Δ), 0, 2^b - 1)
4. Deploy: x_approx = (x_q × Δ) + min
```

### Quantization-Aware Training (QAT):
```
Forward Pass: x̂_q = quantize(x)  [fake quantization]
Backward Pass: ∇x = STE(∇x̂_q)   [straight-through estimator]
Update: x ← x - η × ∇x            [SGD with quantization noise awareness]
```

### URL:
https://www.runpod.io/blog/quantization-methods-speed-vs-accuracy

---

## Paper 8: GPTQ - Accurate Post-Training Quantization

**Title:** GPTQ: Accurate Post-Training Quantization for Generative Pre-trained Transformers  
**Authors:** Elias Frantar, Saleh Ashkboos, Torsten Hoefler, Dan Alistarh  
**Publication Year:** 2022  
**Venue:** arXiv (arxiv.org/pdf/2210.17323), NeurIPS 2022  
**Status:** Highly influential, widely adopted method  

### Core Innovation: Hessian-Based Second-Order Quantization

#### Loss Approximation (Quadratic Proxy):
```
ΔL ≈ ||W_q - W||_H^2 = Σ_i (w_{q,i} - w_i) × H_{ii} × (w_{q,i} - w_i)
```
Where:
- H = Hessian matrix of loss function w.r.t. weights
- H_{ii} = diagonal elements (second-order sensitivity)
- Focuses quantization on high-sensitivity parameters

#### Hessian Computation:
```
H = (2/|D|) × Σ_{batch ∈ D} X^T × X
```
Where X is input activations batch, D is calibration dataset

#### Layer-wise MSE Minimization:
```
Minimize: ||X·W_q - X·W||_2^2
Subject to: W_q is quantized (low-precision)
```

#### Greedy Block Quantization (Fixed Order):
For each weight w_i:
```
w_{q,i} = argmin_{q ∈ quantization_space} ||X·w_q - X·w||_2^2
Optimal: w_{q,i} = clip(w_i - H_{ii}^{-1} × H_{i,not-i} × (W_{q,not-i} - W_{not-i}), Q_min, Q_max)
```

#### Key Advantages:
- Per-channel/per-group precision supported
- No backpropagation required
- Quantizes entire models in ~4 GPU hours (175B parameters)
- Handles 2, 3, 4-bit weight quantization

#### Limitations Addressed by Derivatives:
- GPTAQ: Accounts for layer-wise input deviation
- QuIP/QuIP#: LDL decomposition for better compensation
- GQuant: Per-feature gradients instead of uniform MSE

### URL:
https://arxiv.org/pdf/2210.17323

---

## Paper 9: AWQ - Activation-Aware Weight Quantization

**Title:** AWQ: Activation-aware Weight Quantization for LLM Compression and Acceleration  
**Authors:** Jiangfeng Lin, Junyang Tang et al.  
**Publication Year:** 2023  
**Venue:** MLSys 2024 (Best Paper Award), arXiv:2306.00978  
**Status:** State-of-the-art weight-only quantization  

### Core Insight: Activation-Guided Scaling

#### Problem Formulation:
Quantization error in linear layers:
```
Error = ||X·W_q - X·W||_2^2
      = ||X·(W_q - W)||_2^2
      = Σ_c ||X_c ⊙ (w_{q,c} - w_c)||_2^2
```
Where ⊙ is element-wise multiplication, c indexes channels

#### Observation:
Error is proportional to activation magnitude:
```
Error_c ∝ ||X_c||_2^2 × ||w_{q,c} - w_c||_2^2
```
Larger activations → larger quantization error impact

#### AWQ Solution: Per-Channel Scaling with Activation Awareness
```
New Formulation:
W' = S^{-1} × W        [weight scaling]
X' = X × S             [activation scaling, inverse relationship]
Output: Y = X' × W' = X × W  [mathematically identical]

Where S is diagonal with:
s_c = (max(|X_c|) / q_mean(w_c))^α
```
- α ∈ [0, 1] exponent (searched via grid search)
- Protects salient (high-magnitude) weight channels
- Reduces relative quantization error for critical channels

#### Implementation:
```
Algorithm:
1. Compute per-channel activation statistics: max(|X_c|), mean(|X_c|)
2. Grid search α ∈ [0, 1] minimizing quantization loss on calibration data
3. Compute scaling: s_c = (max(|X_c|) / q_mean(w_c))^α
4. Apply: W' = diag(S^{-1}) × W
5. Fuse scaling into dequantization or matrix multiplication (no inference overhead)
```

#### Key Properties:
- **No Backpropagation:** Calibration only
- **Hardware-Efficient:** Scaling fused into GEMM operations
- **Activation-Aware:** Leverages activation distribution
- **Generalizes:** Works across domains/modalities without overfitting

#### Compared Methods in AWQ Analysis:
- Baseline PTQ (MinMax)
- Mixed-precision (expensive, impractical)
- Importance-based scaling (weight-only)
- Activation-aware scaling (AWQ solution)

### URL:
https://arxiv.org/pdf/2306.00978

---

## Paper 10: Understanding Quantization-Aware Training - QAT Gradient Analysis

**Title:** Understanding Quantization-Aware Training: Gradients at Quantized Weights Bias to the Low-Loss Basin  
**Authors:** Hanyang Li et al.  
**Publication Year:** 2024  
**Venue:** arXiv (arxiv.org/html/2606.09012)  
**Status:** Recent theoretical analysis  

### QAT Fundamental Equations:

#### Fake Quantization Forward Pass:
```
x̂ = clip(round(x / s + z), Q_min, Q_max)
x_dq = (x̂ - z) × s  [dequantization for computation]
```

#### Straight-Through Estimator (STE):
Since quantization is non-differentiable, gradients bypass quantization:
```
Forward: y = f(quantize(x))
Backward: ∂y/∂x = ∂y/∂x̂ × ∂x̂/∂x|STE
         where ∂x̂/∂x|STE = 1  [identity, not actual quantization gradient]
```

#### Key Insight - Gradients at Quantized Weights:
```
Full-precision weight update: w ← w - η × ∇L(w)
QAT property: Gradient ∇L computed at quantized weight state
            → gradients biased toward low-loss regions for quantized values
```

#### Convergence Behavior:
```
Model learns to settle in flat loss regions w.r.t. quantization perturbations:
min ∇²L(w_q) relative to full-precision baseline
```

### QAT vs PTQ Comparison:

| Aspect | QAT | PTQ |
|--------|-----|-----|
| **Training Time** | High (full training loop) | Low (calibration only) |
| **Gradient Path** | Quantization-aware (STE) | No gradients |
| **Adaptation** | Weights adapt to quantization noise | Static post-training |
| **Accuracy (low-bit)** | Better (for aggressive quantization) | Worse (needs error mitigation) |
| **Complexity** | High | Low |
| **Hardware Requirements** | Significant (training) | Minimal |

### URL:
https://arxiv.org/html/2606.09012

---

## Fundamental Equations Reference

### Affine (Asymmetric) Quantization:
```
Quantization:  x_q = clip(round((x - β) / Δ + Z), 0, 2^b - 1)
Dequantization: x̂ = (x_q - Z) × Δ + β
Scale factor: Δ = (β_max - β_min) / (2^b - 1)
Zero-point: Z = round(-β_min / Δ)
```

### Symmetric Quantization:
```
Quantization:  x_q = clip(round(x / s), -2^(b-1), 2^(b-1) - 1)
Dequantization: x̂ = x_q × s
Scale factor: s = max(|min_x|, |max_x|) / 2^(b-1)
Zero-point: Z = 0 (implicit)
```

### Per-Channel Scaling:
```
x_q[c] = clip(round(x[c] / s_c), Q_min, Q_max)
Where s_c = (max_c - min_c) / (2^b - 1)
```

### Per-Group Quantization (group size g):
```
For groups G_i (size g within each channel):
s_{c,i} = scale for channel c, group i
x_q[c, g_i] = clip(round(x[c, g_i] / s_{c,i}), Q_min, Q_max)
```

### Weight Perturbation in Quantization Error:
```
ΔL(δ) ≈ L(W) + ∇L(W)^T × δ + (1/2) × δ^T × H(W) × δ
Where: δ = W - W_q (quantization-induced perturbation)
```

### Hessian-based Quantization Error:
```
Error = ||W_q - W||_H^2 = Σ_i (w_{q,i} - w_i) × H_{ii} × (w_{q,i} - w_i)
```

### Activation-Aware Scaling (AWQ):
```
Channel-wise error: Error_c ∝ ||X_c||_2^2 × ||w_{q,c} - w_c||_2^2
Optimal scaling: s_c = (max(|X_c|) / q_mean(w_c))^α
```

---

## Comparison Summary Table

| Method | Symmetry | Granularity | Error Mitigation | Equation-Based | Computational Cost | Best For |
|--------|----------|-------------|-----------------|-----------------|-------------------|----------|
| MinMax (Baseline) | Asym | Per-tensor | None | Simple range | Very Low | Baseline comparisons |
| AbsMax (Symmetric) | Sym | Per-tensor | None | Simple absmax | Very Low | Activations with balanced dist. |
| SmoothQuant | Asym | Per-channel | Scaling | Outlier smoothing | Medium | W8A8 quantization |
| GPTQ | Asym | Per-group | Hessian-based | Quadratic loss approx. | High | Low-bit weight quantization |
| AWQ | Asym | Per-channel | Activation-aware scaling | Activation magnitudes | Low | Weight-only quantization |
| QuaRot | Asym | Per-channel | Rotation + scaling | Hadamard transform | Medium | Outlier reduction |
| AffineQuant | Asym | Per-layer | Affine transformation | Full matrix optimization | Medium-High | General-purpose PTQ |
| QAT (with STE) | Variable | Configurable | Weight adaptation | Straight-through estimator | Very High | High-accuracy low-bit |
| NVFP4 | Asym | Per-group (16) | Floating-point mantissa | E2M1 + E4M3 scaling | Low | Recent hardware (RTX 50) |

---

## Recommended Reading Order

1. **Foundation:** A White Paper on Neural Network Quantization (2106.08295)
2. **Implementation Details:** Model Quantization - NVIDIA Blog
3. **Method Comparison:** A Comprehensive Evaluation on Quantization Techniques (2507.17417)
4. **GPTQ Deep Dive:** GPTQ: Accurate Post-Training Quantization (2210.17323)
5. **AWQ Details:** AWQ: Activation-aware Weight Quantization (2306.00978)
6. **Practical Evaluation:** Which Quantization Should I Use? (2601.14277)
7. **Advanced Topics:** AffineQuant (2403.12544), OSTQuant (ICLR 2025)

---

## Key Mathematical Insights

### Symmetric vs Asymmetric Trade-offs:

**Symmetric Advantages:**
- Simpler hardware (no zero-point operations)
- Lower dequantization cost
- Efficient for balanced distributions

**Symmetric Disadvantages:**
- Suboptimal for asymmetric data
- Wastes quantization range on unused side
- Higher clipping error for skewed distributions

**Asymmetric Advantages:**
- Optimal range utilization
- Better for naturally asymmetric distributions (LLM activations)
- Reduced clipping loss

**Asymmetric Disadvantages:**
- Extra zero-point parameter storage/computation
- More complex hardware implementation
- One additional addition per dequantization

### Scale Factor Insights:

Power-of-two constraints (Δ = 2^(-k)):
- Enable bit-shifting hardware
- Reduce flexibility in scale selection
- Increase error from constrained expressiveness

Learned vs Calibration-based:
- Calibration: fixed post-training, no adaptation
- Learned (QAT): adaptive, reduces quantization noise through training
- Hybrid: initial calibration + fine-tuning

### Granularity vs Storage Trade-off:

```
Accuracy: Per-tensor < Per-channel < Per-group (smaller) < Per-element
Storage: Per-tensor: O(1)
         Per-channel: O(C)  [C = num channels]
         Per-group: O(C × num_groups_per_channel)
         Per-element: O(H × W) [prohibitive]
```

Optimal balance typically at per-group with group size 128-256.

---

## Research Frontiers

1. **Mixed-Precision Quantization:** Different bitwidths for different layers based on sensitivity
2. **Dynamic Quantization:** Per-sample or per-token calibration for improved accuracy
3. **FP4/MXFP4 Optimization:** Leveraging floating-point formats for better distribution matching
4. **Rotation-Based Methods:** Incoherence reduction through Hadamard/orthogonal transforms
5. **Low-Rank Compensation:** SVD-based error reconstruction with minimal overhead
6. **Hardware Co-design:** Quantization schemes optimized for specific accelerators

---

## Document Information

**Last Updated:** 2026-07-06  
**Papers Reviewed:** 10 major papers + web research  
**Coverage:** Symmetric/asymmetric methods, mathematical formulations, scale factors, clipping ranges, recent advances (NVFP4, OSTQuant, FlatQuant)  
**Focus:** LLM quantization, but principles apply to CNN/transformer models generally

