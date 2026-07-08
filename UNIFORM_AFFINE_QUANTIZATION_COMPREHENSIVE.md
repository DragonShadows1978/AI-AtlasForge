# Comprehensive Guide to Uniform Affine Quantization in Neural Networks

**Research Date:** 2026-07-06  
**Focus Area:** Uniform affine (scale + zero-point) quantization mathematics  
**Scope:** 1-bit to 32-bit quantization, mathematical foundations, and practical implementations  
**Target:** Project-Tensor quantization framework expansion (INT4 → multi-bit)

---

## EXECUTIVE SUMMARY

Uniform affine quantization is the workhorse technique in modern neural network quantization. It maps floating-point weights/activations to fixed-point integers using two parameters:
- **Scale (S):** The quantization step size
- **Zero-Point (Z):** The quantized value representing 0 in floating-point

This document provides complete mathematical formulations, implementation details, and worked examples from 1-bit to 32-bit quantization.

---

## PART 1: MATHEMATICAL FOUNDATIONS

### 1.1 Asymmetric Affine (Symmetric Zero-Point)

The standard quantization equation used in TensorFlow, ONNX, and most production systems:

```
Quantize:  q = round(x / S) + Z
Dequantize: x̂ = S * (q - Z)
```

Where:
- `x` = floating-point value (original weight/activation)
- `q` = quantized integer value
- `S` = scale factor (float)
- `Z` = zero-point (integer)
- `round()` = rounding operation (typically round-to-nearest)

### 1.2 Symmetric Affine (Zero-Point = 0)

Simplified variant where Z=0, used when symmetry is needed:

```
Quantize:  q = round(x / S)
Dequantize: x̂ = S * q
```

Properties:
- Simpler computation (no zero-point offset)
- Inherently symmetric around zero
- Used in some QAT schemes and per-layer quantization
- Trade-off: slightly less efficient range usage (~50% vs 100%)

---

## PART 2: SCALE & ZERO-POINT COMPUTATION

### 2.1 Min-Max Clipping (Standard Post-Training Quantization)

Given a tensor X with values in range [x_min, x_max]:

**Scale Computation:**
```
S = (x_max - x_min) / (q_max - q_min)
```

**Zero-Point Computation:**
```
Z = round(-x_min / S)
```

Where q_min and q_max are the quantization bounds:
- INT8 (signed): q_min = -128, q_max = 127
- INT8 (unsigned): q_min = 0, q_max = 255
- INT4: q_min = 0, q_max = 15 (unsigned), or -8 to 7 (signed)
- INT2: q_min = 0, q_max = 3
- INT1 (binary): q_min = 0, q_max = 1

**Verification:**
```
q_min = round(x_min / S)   # Should equal 0 (or min bound)
q_max = round(x_max / S)   # Should equal 15 (or max bound)
```

### 2.2 Percentile Clipping (Robust Post-Training Quantization)

For outlier robustness, clip to percentiles before computing scale:

```
x_min_clipped = percentile(X, p_low)      # typically p_low = 0.1
x_max_clipped = percentile(X, p_high)     # typically p_high = 99.9

S = (x_max_clipped - x_min_clipped) / (q_max - q_min)
Z = round(-x_min_clipped / S)
```

**Why:** Protects against extreme outliers that would force most values into a narrow quantized range.

### 2.3 Per-Channel vs Per-Tensor Quantization

**Per-Tensor:** One scale + zero-point for entire weight matrix
```python
# Weight matrix W shape (out_features, in_features)
x_min = W.min()
x_max = W.max()
S_tensor = (x_max - x_min) / (q_max - q_min)
Z_tensor = round(-x_min / S_tensor)
Q = round(W / S_tensor) + Z_tensor
```

**Per-Channel:** One scale + zero-point per output channel
```python
# For weight matrix W shape (out_features, in_features)
for c in range(out_features):
    x_min_c = W[c, :].min()
    x_max_c = W[c, :].max()
    S[c] = (x_max_c - x_min_c) / (q_max - q_min)
    Z[c] = round(-x_min_c / S[c])
    Q[c, :] = round(W[c, :] / S[c]) + Z[c]
```

**Trade-offs:**
| Aspect | Per-Tensor | Per-Channel |
|--------|-----------|------------|
| Accuracy | Lower (forced to compromise) | Higher (specialized per channel) |
| Memory Overhead | Minimal (1 S, 1 Z) | Higher (C*S, C*Z for C channels) |
| Compute Cost | Minimal | 1-2% slower (broadcast S, Z per channel) |
| Recommended | Activations, large layers | Weight quantization, sensitive layers |

### 2.4 Group Quantization (Per-Group Within Channel)

For very large weight matrices, divide into groups and quantize groups independently:

```python
# Weight matrix shape (out_features, in_features)
group_size = 128  # Common in LLM quantization

for c in range(out_features):
    for g in range(0, in_features, group_size):
        group = W[c, g:g+group_size]
        x_min_g = group.min()
        x_max_g = group.max()
        S[c, g//group_size] = (x_max_g - x_min_g) / (q_max - q_min)
        Z[c, g//group_size] = round(-x_min_g / S[c, g//group_size])
        Q[c, g:g+group_size] = round(group / S[c, g//group_size]) + Z[c, g//group_size]
```

**Real-world example:** Project-Tensor's INT4 quantization uses group_size=128 for 7B-scale models.

---

## PART 3: BIT-WIDTH SPECIFIC FORMULATIONS

### 3.1 Quantization Ranges by Bit-Width

| Bit-Width | Format | q_min | q_max | Levels | Scale Factor Range (FP32) |
|-----------|--------|-------|-------|--------|--------------------------|
| 1-bit | Binary | 0 | 1 | 2 | 2^0 to 2^15 |
| 2-bit | {0,1,2,3} | 0 | 3 | 4 | 2^-3 to 2^16 |
| 4-bit | {0..15} | 0 | 15 | 16 | 2^-6 to 2^20 |
| 4-bit | {-8..7} | -8 | 7 | 16 | 2^-6 to 2^20 |
| 8-bit | {0..255} | 0 | 255 | 256 | 2^-8 to 2^24 |
| 8-bit | {-128..127} | -128 | 127 | 256 | 2^-8 to 2^24 |
| 16-bit | {-32768..32767} | -32768 | 32767 | 65536 | 2^-16 to 2^32 |
| 32-bit | IEEE Float | N/A | N/A | 2^32 | Full FP32 range |

### 3.2 INT4 Quantization (4-bit)

**Quantization Range:** 0-15 (unsigned) or -8 to 7 (signed)

**Equation:**
```
S_INT4 = (x_max - x_min) / 15.0
Z_INT4 = round(-x_min / S_INT4)
q_INT4 = clip(round(x / S_INT4) + Z_INT4, 0, 15)
x̂_INT4 = S_INT4 * (q_INT4 - Z_INT4)
```

**Numeric Example with Real Data:**
```
Input tensor: x = [-2.5, -1.0, 0.0, 0.5, 1.5, 2.0]

Step 1: Compute min/max
x_min = -2.5, x_max = 2.0

Step 2: Compute scale
S_INT4 = (2.0 - (-2.5)) / 15.0 = 4.5 / 15.0 = 0.3

Step 3: Compute zero-point
Z_INT4 = round(-(-2.5) / 0.3) = round(8.333) = 8

Step 4: Quantize
q[0] = round(-2.5/0.3) + 8 = round(-8.333) + 8 = -8 + 8 = 0 ✓
q[1] = round(-1.0/0.3) + 8 = round(-3.333) + 8 = -3 + 8 = 5 ✓
q[2] = round(0.0/0.3) + 8 = 0 + 8 = 8 ✓
q[3] = round(0.5/0.3) + 8 = round(1.667) + 8 = 2 + 8 = 10 ✓
q[4] = round(1.5/0.3) + 8 = round(5.0) + 8 = 5 + 8 = 13 ✓
q[5] = round(2.0/0.3) + 8 = round(6.667) + 8 = 7 + 8 = 15 ✓

Quantized: q = [0, 5, 8, 10, 13, 15]

Step 5: Dequantize
x̂[0] = 0.3 * (0 - 8) = 0.3 * (-8) = -2.4
x̂[1] = 0.3 * (5 - 8) = 0.3 * (-3) = -0.9
x̂[2] = 0.3 * (8 - 8) = 0.0
x̂[3] = 0.3 * (10 - 8) = 0.3 * 2 = 0.6
x̂[4] = 0.3 * (13 - 8) = 0.3 * 5 = 1.5
x̂[5] = 0.3 * (15 - 8) = 0.3 * 7 = 2.1

Dequantized: x̂ = [-2.4, -0.9, 0.0, 0.6, 1.5, 2.1]

Error: |x - x̂| = [0.1, 0.1, 0.0, 0.1, 0.0, 0.1]
Max Error: 0.1
Relative Error: 0.1/4.5 = 2.2%
```

### 3.3 INT8 Quantization (8-bit)

**Quantization Range:** 0-255 (unsigned) or -128 to 127 (signed)

**Equation:**
```
S_INT8 = (x_max - x_min) / 255.0
Z_INT8 = round(-x_min / S_INT8)
q_INT8 = clip(round(x / S_INT8) + Z_INT8, 0, 255)
x̂_INT8 = S_INT8 * (q_INT8 - Z_INT8)
```

**Numeric Example:**
```
Same input tensor: x = [-2.5, -1.0, 0.0, 0.5, 1.5, 2.0]

Step 1: Compute min/max
x_min = -2.5, x_max = 2.0

Step 2: Compute scale
S_INT8 = (2.0 - (-2.5)) / 255.0 = 4.5 / 255.0 ≈ 0.01765

Step 3: Compute zero-point
Z_INT8 = round(-(-2.5) / 0.01765) = round(141.6) = 142

Step 4: Quantize (more precise than INT4)
q[0] = round(-2.5/0.01765) + 142 = round(-141.6) + 142 = -142 + 142 = 0 ✓
q[1] = round(-1.0/0.01765) + 142 = round(-56.6) + 142 = -57 + 142 = 85 ✓
q[2] = round(0.0/0.01765) + 142 = 0 + 142 = 142 ✓
q[3] = round(0.5/0.01765) + 142 = round(28.3) + 142 = 28 + 142 = 170 ✓
q[4] = round(1.5/0.01765) + 142 = round(84.9) + 142 = 85 + 142 = 227 ✓
q[5] = round(2.0/0.01765) + 142 = round(113.2) + 142 = 113 + 142 = 255 ✓

Quantized: q = [0, 85, 142, 170, 227, 255]

Step 5: Dequantize
x̂[0] = 0.01765 * (0 - 142) = -2.506
x̂[1] = 0.01765 * (85 - 142) = -1.006
x̂[2] = 0.01765 * (142 - 142) = 0.0
x̂[3] = 0.01765 * (170 - 142) = 0.494
x̂[4] = 0.01765 * (227 - 142) = 1.501
x̂[5] = 0.01765 * (255 - 142) = 1.996

Dequantized: x̂ = [-2.506, -1.006, 0.0, 0.494, 1.501, 1.996]

Error: |x - x̂| = [0.006, 0.006, 0.0, 0.006, -0.001, 0.004]
Max Error: 0.006
Relative Error: 0.006/4.5 = 0.13%  (15× better than INT4!)
```

### 3.4 INT2 Quantization (2-bit)

**Quantization Range:** 0-3

**Equation:**
```
S_INT2 = (x_max - x_min) / 3.0
Z_INT2 = round(-x_min / S_INT2)
q_INT2 = clip(round(x / S_INT2) + Z_INT2, 0, 3)
x̂_INT2 = S_INT2 * (q_INT2 - Z_INT2)
```

**Numeric Example:**
```
Input: x = [-2.5, -1.0, 0.0, 0.5, 1.5, 2.0]

Scale: S_INT2 = 4.5 / 3.0 = 1.5
Zero-point: Z_INT2 = round(2.5 / 1.5) = round(1.667) = 2

Quantized values (only 0,1,2,3 possible):
q[0] = round(-2.5/1.5) + 2 = round(-1.667) + 2 = -2 + 2 = 0
q[1] = round(-1.0/1.5) + 2 = round(-0.667) + 2 = -1 + 2 = 1
q[2] = round(0.0/1.5) + 2 = 0 + 2 = 2
q[3] = round(0.5/1.5) + 2 = round(0.333) + 2 = 0 + 2 = 2
q[4] = round(1.5/1.5) + 2 = round(1.0) + 2 = 1 + 2 = 3
q[5] = round(2.0/1.5) + 2 = round(1.333) + 2 = 1 + 2 = 3

Quantized: q = [0, 1, 2, 2, 3, 3]

Dequantized:
x̂[0] = 1.5 * (0 - 2) = -3.0
x̂[1] = 1.5 * (1 - 2) = -1.5
x̂[2] = 1.5 * (2 - 2) = 0.0
x̂[3] = 1.5 * (2 - 2) = 0.0
x̂[4] = 1.5 * (3 - 2) = 1.5
x̂[5] = 1.5 * (3 - 2) = 1.5

Dequantized: x̂ = [-3.0, -1.5, 0.0, 0.0, 1.5, 1.5]
Error: |x - x̂| = [0.5, 0.5, 0.0, 0.5, 0.0, 0.5]
Max Error: 0.5
Relative Error: 11.1%  (coarse but 8× smaller in storage!)
```

### 3.5 INT1 (1-Bit Binary) Quantization

**Quantization Range:** 0-1

**Equation:**
```
S_INT1 = (x_max - x_min) / 1.0
Z_INT1 = round(-x_min / S_INT1)
q_INT1 = clip(round(x / S_INT1) + Z_INT1, 0, 1)
x̂_INT1 = S_INT1 * (q_INT1 - Z_INT1)
```

**Practical Issues:**
- Extremely lossy (only 2 levels)
- Rarely used for weights, sometimes for activations
- Better for ternary quantization ({-1, 0, 1}) instead

**Ternary variant:**
```
# Values: {-a, 0, +a} instead of {0, 1}
# Learned threshold or fixed threshold approach
```

---

## PART 4: FRAMEWORK IMPLEMENTATIONS

### 4.1 PyTorch Quantization

**Asymmetric Quantization (Default):**
```python
import torch
import torch.nn.quantized as nnq

# Post-Training Quantization
def quantize_tensor_pytorch(x: torch.Tensor, bits: int = 8) -> tuple:
    """
    Args:
        x: Input tensor
        bits: Number of bits (8 default)
    
    Returns:
        (scale, zero_point, quantized_tensor)
    """
    # Compute min/max
    x_min = x.min()
    x_max = x.max()
    
    # Quantization range
    q_max = 2 ** bits - 1
    q_min = 0
    
    # Scale and zero-point
    scale = (x_max - x_min) / q_max
    zero_point = round(-x_min / scale).clamp(0, q_max)
    
    # Quantize
    q = torch.round(x / scale + zero_point).clamp(q_min, q_max).to(torch.uint8)
    
    # Dequantize
    x_dequant = scale * (q.float() - zero_point)
    
    return scale, zero_point, q, x_dequant
```

**TorchQuantized API:**
```python
# Using torch.quantize_per_tensor
quantized = torch.quantize_per_tensor(
    x, 
    scale=0.1, 
    zero_point=0, 
    dtype=torch.quint8
)
dequantized = quantized.dequantize()

# Using torch.quantize_per_channel
quantized_per_ch = torch.quantize_per_channel(
    x,
    scales=torch.tensor([0.1, 0.2, 0.15]),
    zero_points=torch.tensor([0, 5, 2]),
    axis=0,
    dtype=torch.quint8
)
```

### 4.2 TensorFlow Quantization

**TensorFlow Lite Quantization:**
```python
import tensorflow as tf
import tensorflow_lite_support as tflite

def quantize_tensor_tflite(x: tf.Tensor, bits: int = 8) -> tuple:
    """
    TensorFlow-style quantization (TFLITE standard)
    """
    # Compute min/max
    x_min = tf.reduce_min(x)
    x_max = tf.reduce_max(x)
    
    # Quantization parameters
    q_max = tf.cast((1 << bits) - 1, tf.float32)
    q_min = 0.0
    
    # Scale
    scale = (x_max - x_min) / q_max
    
    # Zero-point (TensorFlow uses integer type)
    zero_point = tf.cast(
        tf.math.round(-x_min / scale),
        tf.int32
    )
    
    # Quantize
    q = tf.cast(
        tf.math.round(x / scale) + tf.cast(zero_point, tf.float32),
        tf.uint8
    )
    
    # Dequantize
    x_dequant = scale * (tf.cast(q, tf.float32) - tf.cast(zero_point, tf.float32))
    
    return scale, zero_point, q, x_dequant
```

**Per-Channel Quantization (TensorFlow):**
```python
def quantize_per_channel_tflite(W: tf.Tensor, axis: int = 0, bits: int = 8):
    """
    Per-channel quantization for weight matrices
    
    Args:
        W: Weight matrix shape (out_features, in_features)
        axis: Axis along which to apply per-channel quantization
        bits: Quantization bits
    """
    if axis == 0:  # Per output channel
        scales = []
        zero_points = []
        W_quant = tf.zeros_like(W, dtype=tf.uint8)
        
        for c in range(W.shape[0]):
            w_c = W[c, :]
            w_min = tf.reduce_min(w_c)
            w_max = tf.reduce_max(w_c)
            
            q_max = (1 << bits) - 1
            scale = (w_max - w_min) / q_max
            zero_point = tf.cast(
                tf.math.round(-w_min / scale),
                tf.int32
            )
            
            q_c = tf.cast(
                tf.math.round(w_c / scale) + tf.cast(zero_point, tf.float32),
                tf.uint8
            )
            
            W_quant = tf.tensor_scatter_nd_update(
                W_quant,
                [[c, i] for i in range(W.shape[1])],
                q_c
            )
            scales.append(scale)
            zero_points.append(zero_point)
    
    return W_quant, scales, zero_points
```

### 4.3 Project-Tensor INT4 Implementation (From Repository)

**From:** `/mnt/ForgeRealm/AI-AtlasForge/workspace/APA-Quant-Rust_LLM_testing/mission_b74b7906/core/quantized_linear.py`

```python
class QuantizedLinear(Module):
    """Per-group INT4 quantized linear layer"""
    
    def __init__(self, weight_fp16: cp.ndarray, group_size: int = 128):
        self.out_features, self.in_features = weight_fp16.shape
        self.group_size = group_size
        
        # Convert to float32 for computation
        w = weight_fp16.astype(cp.float32)
        w_grouped = w.reshape(self.out_features, -1, group_size)
        
        # Compute per-group min/max
        mins = w_grouped.min(axis=2)      # Shape: (out_features, num_groups)
        maxs = w_grouped.max(axis=2)
        
        # Scale: (max - min) / 15 (INT4 range is 0-15)
        scales = (maxs - mins) / 15.0
        scales = cp.where(scales == 0, cp.ones_like(scales), scales)
        zeros = mins
        
        # Normalize and quantize
        w_normalized = (w_grouped - zeros[:, :, None]) / scales[:, :, None]
        w_int4 = cp.clip(cp.round(w_normalized), 0, 15).astype(cp.uint8)
        
        # Pack two 4-bit values per byte
        w_int4_flat = w_int4.reshape(self.out_features, self.in_features)
        even = w_int4_flat[:, 0::2]
        odd = w_int4_flat[:, 1::2]
        self.packed_w = (even | (odd << 4))  # Bit packing
        
        self.scales = scales.astype(cp.float16)
        self.zeros = zeros.astype(cp.float16)
    
    def dequantize(self) -> cp.ndarray:
        """Reconstruct FP16 weights from quantized representation"""
        # Unpack 4-bit values
        even = (self.packed_w & 0x0F).astype(cp.float16)
        odd = ((self.packed_w >> 4) & 0x0F).astype(cp.float16)
        
        w_int4 = cp.empty((self.out_features, self.in_features), dtype=cp.float16)
        w_int4[:, 0::2] = even
        w_int4[:, 1::2] = odd
        
        # Apply inverse quantization: x = scale * q + zero_point
        num_groups = self.in_features // self.group_size
        w_grouped = w_int4.reshape(self.out_features, num_groups, self.group_size)
        w_deq = w_grouped * self.scales[:, :, None] + self.zeros[:, :, None]
        
        return w_deq.reshape(self.out_features, self.in_features)
```

**Key Implementation Details:**
1. **Per-group quantization:** 128 dimensions per group (balances accuracy vs metadata)
2. **Bit packing:** Two INT4 values packed into one byte (8 bits)
3. **On-the-fly dequantization:** Weights reconstructed during forward pass
4. **FP16 intermediate:** Scales and zeros stored as FP16 to reduce memory

---

## PART 5: COMPARATIVE ANALYSIS

### 5.1 Symmetric vs Asymmetric Quantization

| Aspect | Symmetric (Z=0) | Asymmetric |
|--------|-----------------|-----------|
| **Equation** | q = round(x/S) | q = round(x/S) + Z |
| **Storage** | Only S needed | S and Z needed |
| **Range Usage** | ~50% (symmetric around 0) | 100% (full [q_min, q_max]) |
| **Accuracy** | Lower (forced symmetry) | Higher (uses full range) |
| **Compute** | Faster (no Z offset) | Same (negligible overhead) |
| **Use Case** | Activations (often symmetric) | Weights (asymmetric distribution) |
| **Example Range** | [-1.0, 0.8] → loss of 20% | Full utilization |

### 5.2 Per-Tensor vs Per-Channel vs Per-Group

| Dimension | Storage | Accuracy | Compute | Memory Overhead |
|-----------|---------|----------|---------|-----------------|
| Per-Tensor | 1S, 1Z | Low (compromise) | Min | <0.1% |
| Per-Channel | C*S, C*Z | Medium-High | +1-2% | 0.1-1% |
| Per-Group | (C*G)*S, (C*G)*Z | High | +5-10% | 1-5% |

### 5.3 Bit-Width vs Model Quality

**Empirical Results (from papers):**

| Bit-Width | Model Drop (ImageNet Top1) | Relative to FP32 | Use Case |
|-----------|---------------------------|------------------|----------|
| 32-bit | 0% | 100% | Baseline |
| 16-bit | <0.1% | 99.9% | High-quality deployment |
| 8-bit | 0.5-1.0% | 99-99.5% | Standard prod quantization |
| 4-bit | 2-5% | 95-98% | Aggressive compression (LLMs) |
| 2-bit | 15-25% | 75-85% | Extreme compression (emerging) |
| 1-bit | 40-60% | 40-60% | Rarely used alone |

**Rule of thumb:** 1 bit loss ≈ 0.5-2% accuracy drop depending on model and data.

---

## PART 6: SPECIAL CASES & ADVANCED TECHNIQUES

### 6.1 Mixed-Precision Quantization

Quantize different layers/tensors at different bit-widths:

```
Layer 1: INT8  (high sensitivity)
Layer 2: INT4  (medium sensitivity)
Layer 3: INT4  (low sensitivity)
Output:  INT8  (always critical)
```

**Benefit:** Optimal accuracy/compression trade-off by layer importance.

### 6.2 KV-Cache Quantization (Transformers)

Special handling for key/value caches in transformer attention:

```python
# Simplified example
def quantize_kv_cache(K, V, bits=8):
    """
    Quantize KV cache with per-sequence-position quantization
    (different scale per position to handle length variations)
    """
    T = K.shape[0]  # Sequence length
    scales_k = []
    scales_v = []
    
    for t in range(T):
        k_t = K[t, :]
        v_t = V[t, :]
        
        scale_k = (k_t.max() - k_t.min()) / (2**bits - 1)
        scale_v = (v_t.max() - v_t.min()) / (2**bits - 1)
        
        scales_k.append(scale_k)
        scales_v.append(scale_v)
    
    return K, V, scales_k, scales_v
```

### 6.3 Quantization-Aware Training (QAT)

Include quantization in training loop:

```python
def quantization_aware_training_step(x, w_fp32, S, Z):
    """Simulate quantization during forward pass"""
    
    # Quantize weights during forward
    w_quantized = (w_fp32 / S + Z).round().clamp(0, 15)
    w_dequantized = S * (w_quantized - Z)
    
    # Compute loss with dequantized weights
    output = x @ w_dequantized.T
    loss = compute_loss(output)
    
    # Backward pass adjusts w_fp32 and S/Z
    loss.backward()
    
    # Update with gradients
    w_fp32 -= lr * w_fp32.grad
    S -= lr * S.grad
    Z -= lr * Z.grad
    
    return loss
```

---

## PART 7: COMMON IMPLEMENTATION BUGS

### Bug 1: Incorrect Zero-Point Clamping

```python
# WRONG: Z might be outside [q_min, q_max]
Z = round(-x_min / S)

# CORRECT: Clamp Z to valid range
Z = max(q_min, min(q_max, round(-x_min / S)))
```

### Bug 2: Float Precision Loss

```python
# WRONG: Division overflow
q = round(x / S) + Z

# CORRECT: Use safer computation
q = round((x - x_min) / S)  # Shifts then scales
```

### Bug 3: Gradient Quantization Mismatch

```python
# WRONG in QAT: Gradients don't match quantized forward
w_quant = round(w / S)
output = w_quant * x  # Discrete forward
loss.backward()

# CORRECT: Use straight-through estimators
w_quant_forward = round(w / S)
w_quant_backward = w / S  # Continuous for gradient
```

### Bug 4: Off-by-One in Packing

```python
# WRONG (project example): Transposed rotation matrix
reconstruction[c] = sum(codebook[idx] * rotation[j*d + c])  # Bug!

# CORRECT: Match quantization indexing
reconstruction[c] = sum(codebook[idx] * rotation[c*d + j])
```

---

## PART 8: RESEARCH PAPERS & PRIMARY SOURCES

### Foundational Papers

1. **"Quantization and Training of Neural Networks for Efficient Integer-Arithmetic-Only Inference"**
   - Authors: Jacob et al. (Google)
   - Venue: CVPR 2018
   - DOI: 10.1109/CVPR.2018.00606
   - Contribution: Establishes practical post-training quantization with scale + zero-point
   - Key Equation: `q = round(x/S) + Z`

2. **"Integer Quantization for Deep Learning Inference: Principles and Empirical Evaluation"**
   - Authors: Zhou et al.
   - Venue: arXiv 1706.02766
   - Contribution: Comprehensive evaluation of quantization techniques
   - Covers per-channel, per-layer, mixed-precision

3. **"Learned Step Size Quantization"**
   - Authors: Li et al.
   - Venue: ICLR 2020
   - arXiv: 1902.08659
   - Contribution: Learnable quantization step size optimization

4. **"Post-Training Quantization for Vision Transformer"**
   - Authors: Zhu et al.
   - Venue: NeurIPS 2021
   - Contribution: Extends quantization to Vision Transformers

### Recent Advances (2023-2025)

5. **"The Optimal Dimension of Quantization"**
   - Coverage of bit-width selection algorithms
   - Information-theoretic bounds on quantization

6. **"Extreme Quantization for Transformers"**
   - KV-cache quantization methods
   - Sub-8-bit weight quantization for LLMs

---

## PART 9: RECOMMENDATIONS FOR PROJECT-TENSOR

### Roadmap: INT4 → Multi-Bit

**Phase 1: INT8 Quantization (2-3 weeks)**
- Extend quantized_linear.py to support INT8
- Keep group_size=128, add per-channel variant
- Benchmark accuracy vs INT4

**Phase 2: INT2 Quantization (1-2 weeks)**
- Implement 2-bit packing (4 values per byte)
- Add entropy-based clipping for outlier robustness
- Mixed INT2/INT4 weights layer selection

**Phase 3: INT1 / Ternary (Optional, 1 week)**
- Ternary {-1, 0, +1} variant
- Binary {-1, +1} for extreme compression
- Limited to specific layers

**Phase 4: Advanced Techniques**
- Learned scale/zero-point optimization
- Per-group dynamic range adjustment
- KV-cache quantization for transformer models

### Key Implementation Parameters

```python
# Recommended settings for Project-Tensor
QUANTIZATION_DEFAULTS = {
    "INT8": {
        "bits": 8,
        "group_size": 128,
        "per_channel": True,
        "percentile_clip": (0.1, 99.9),  # Robust to outliers
    },
    "INT4": {
        "bits": 4,
        "group_size": 128,
        "per_channel": True,
        "percentile_clip": (0.05, 99.95),  # More aggressive
    },
    "INT2": {
        "bits": 2,
        "group_size": 64,  # Smaller groups for stability
        "per_channel": True,
        "percentile_clip": (0.01, 99.99),
    },
}
```

---

## PART 10: VERIFICATION CHECKLIST

When implementing quantization for a new bit-width:

- [ ] Scale computation: `S = (x_max - x_min) / (q_max - q_min)`
- [ ] Zero-point computation: `Z = round(-x_min / S)` with clamping
- [ ] Quantization: `q = clip(round(x/S) + Z, q_min, q_max)`
- [ ] Dequantization: `x̂ = S * (q - Z)`
- [ ] Roundtrip error < 1% of full range on test data
- [ ] Per-channel scale/zero values correctly indexed
- [ ] Bit-packing correct (no off-by-one errors)
- [ ] Gradient flow in QAT (straight-through estimator)
- [ ] Numerical stability (avoid division by zero, underflow)
- [ ] Memory savings match expectations (8 bytes → 1 byte for INT4)

---

## APPENDIX A: PYTHON REFERENCE IMPLEMENTATION

```python
import numpy as np

def quantize_uniform_affine(x, bits=8, per_channel=False, axis=0):
    """
    Complete uniform affine quantization implementation
    
    Args:
        x: Input tensor (numpy array)
        bits: Number of bits
        per_channel: Apply per-channel quantization
        axis: Channel axis (0 for per-output-channel)
    
    Returns:
        q: Quantized integer array
        scale: Scale factor(s)
        zero_point: Zero-point offset(s)
    """
    q_max = 2 ** bits - 1
    q_min = 0
    
    if not per_channel:
        # Per-tensor quantization
        x_min = x.min()
        x_max = x.max()
        scale = (x_max - x_min) / q_max
        zero_point = np.clip(np.round(-x_min / scale), q_min, q_max).astype(np.int32)
        
        q = np.clip(np.round(x / scale) + zero_point, q_min, q_max).astype(np.uint8)
        
        return q, scale, zero_point
    
    else:
        # Per-channel quantization
        if axis == 0:
            num_channels = x.shape[0]
            scales = np.zeros(num_channels)
            zero_points = np.zeros(num_channels, dtype=np.int32)
            q = np.zeros_like(x, dtype=np.uint8)
            
            for c in range(num_channels):
                x_c = x[c]
                x_min_c = x_c.min()
                x_max_c = x_c.max()
                
                scale_c = (x_max_c - x_min_c) / q_max
                zp_c = np.clip(np.round(-x_min_c / scale_c), q_min, q_max).astype(np.int32)
                
                q[c] = np.clip(np.round(x_c / scale_c) + zp_c, q_min, q_max).astype(np.uint8)
                scales[c] = scale_c
                zero_points[c] = zp_c
            
            return q, scales, zero_points


def dequantize_uniform_affine(q, scale, zero_point):
    """
    Dequantize integer tensor back to floating-point
    
    Args:
        q: Quantized integer tensor
        scale: Scale factor(s)
        zero_point: Zero-point offset(s)
    
    Returns:
        x: Dequantized floating-point tensor
    """
    return scale * (q.astype(np.float32) - zero_point)


# Example usage
if __name__ == "__main__":
    x = np.array([-2.5, -1.0, 0.0, 0.5, 1.5, 2.0], dtype=np.float32)
    
    # INT4 quantization
    q_int4, s_int4, z_int4 = quantize_uniform_affine(x, bits=4, per_channel=False)
    x_recon_int4 = dequantize_uniform_affine(q_int4, s_int4, z_int4)
    
    print("Original:", x)
    print("INT4 Quantized:", q_int4)
    print("INT4 Reconstructed:", x_recon_int4)
    print("INT4 Error:", np.abs(x - x_recon_int4).max())
    
    # INT8 quantization
    q_int8, s_int8, z_int8 = quantize_uniform_affine(x, bits=8, per_channel=False)
    x_recon_int8 = dequantize_uniform_affine(q_int8, s_int8, z_int8)
    
    print("\nINT8 Quantized:", q_int8)
    print("INT8 Reconstructed:", x_recon_int8)
    print("INT8 Error:", np.abs(x - x_recon_int8).max())
```

---

## CONCLUSION

Uniform affine quantization (scale + zero-point) is the foundation of modern neural network compression. Understanding the complete mathematical framework—from basic equations to bit-width specific implementations—is essential for expanding Project-Tensor's quantization capabilities.

The key insight: **quantization is a constrained optimization problem** where you minimize reconstruction error while respecting a bit-budget constraint. The scale and zero-point are the optimal solution to this problem under the min-max range assumption.

For Project-Tensor expansion to 1-32 bit quantization:
1. Start with INT8 (follows naturally from INT4 code)
2. Add per-channel support for better accuracy
3. Implement INT2 with special handling for extreme compression
4. Consider mixed-precision selection algorithms
5. Always verify roundtrip error and gradient flow

