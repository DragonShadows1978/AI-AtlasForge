# TensorFlow Quantization - Technical Implementation Reference

## Mathematical Formulas (Formal Definitions)

### 1. Scale Factor Computation

**Definition**: Maps the continuous float range to discrete integer range.

```
scale = (max_range - min_range) / (num_levels - 1)

Where:
  max_range ∈ ℝ  (maximum float value)
  min_range ∈ ℝ  (minimum float value)
  num_levels = 2^num_bits  (total integer levels)
  num_bits ∈ {1, 2, ..., 16}  (quantization precision)
```

**Expanded for common bit-widths**:
```
INT8 (num_bits=8):
  scale = (max_range - min_range) / (2^8 - 1) = (max_range - min_range) / 255

INT4 (num_bits=4):
  scale = (max_range - min_range) / (2^4 - 1) = (max_range - min_range) / 15

INT16 (num_bits=16):
  scale = (max_range - min_range) / (2^16 - 1) = (max_range - min_range) / 65535
```

### 2. Clipping Function

**Definition**: Ensures input values stay within quantization range.

```
clip(x, min, max) = {
  min,  if x < min
  x,    if min ≤ x ≤ max
  max,  if x > max
}

In code: clip(x) = max(min_range, min(x, max_range))
```

**Purpose**: Prevent overflow/underflow during quantization; handles outliers.

### 3. Quantization Transformation (MIN_COMBINED Mode - TensorFlow Default)

**Definition**: Maps float value to integer discrete value.

```
quantize(x) = round((clip(x, min_range, max_range) - min_range) / scale)

Expanded:
quantize(x) = round((clip(x) - min_range) / scale)

Where:
  x ∈ ℝ  (input float value)
  clip(x) ∈ [min_range, max_range]  (clipped value)
  scale = (max_range - min_range) / (2^num_bits - 1)
  round(·) = standard rounding (HALF_AWAY_FROM_ZERO mode)
```

**Result**: Integer in range [0, 2^num_bits - 1]

**Step-by-step Algorithm**:
```
1. clipped ← max(min_range, min(x, max_range))
2. shifted ← clipped - min_range
3. scaled ← shifted / scale
4. quantized ← round(scaled)
5. return quantized ∈ [0, 2^num_bits - 1]
```

**Numerical Example**:
```
Input: x = 0.642
Range: min_range = -1.0, max_range = 1.0
Bits: num_bits = 8

Step 1: scale = (1.0 - (-1.0)) / 255 = 2.0 / 255 ≈ 0.00784
Step 2: clipped = clip(0.642, -1.0, 1.0) = 0.642
Step 3: shifted = 0.642 - (-1.0) = 1.642
Step 4: scaled = 1.642 / 0.00784 ≈ 209.44
Step 5: quantized = round(209.44) = 209

Result: 209 ∈ [0, 255] ✓
```

### 4. Dequantization (Reconstruction)

**Definition**: Inverse operation to recover approximate float values.

```
dequantize(q) = q * scale + min_range

Where:
  q ∈ [0, 2^num_bits - 1]  (quantized integer)
  scale = (max_range - min_range) / (2^num_bits - 1)
  min_range ∈ ℝ
```

**Result**: Float value approximately equal to original (with quantization error).

**Continuing Example**:
```
quantized = 209
scale = 0.00784
min_range = -1.0

dequantized = 209 * 0.00784 + (-1.0)
            = 1.638 - 1.0
            = 0.638

Error: |0.642 - 0.638| = 0.004
Relative error: 0.004 / 0.642 ≈ 0.62%
```

### 5. Zero-Point Offset (Asymmetric Quantization)

**Definition**: Represents where float value 0.0 maps in integer space.

```
zero_point = round(-min_range / scale)

Where:
  min_range: minimum float value
  scale: as defined above
  zero_point ∈ [0, 2^num_bits - 1]
```

**Purpose**: Ensures 0.0 maps accurately, important for bias terms and operations.

**Continuing Example**:
```
zero_point = round(-(-1.0) / 0.00784)
           = round(1.0 / 0.00784)
           = round(127.55)
           = 128

Verification:
- Float value 0.0 should map to: round((0.0 - (-1.0)) / 0.00784) = round(127.55) = 128
- Dequantized 128: 128 * 0.00784 + (-1.0) = 1.003 - 1.0 = 0.003 ✓
```

### 6. Quantization Error Bounds

**Definition**: Maximum error from single value quantization.

```
Quantization error ≤ scale / 2 = (max_range - min_range) / (2 * (2^num_bits - 1))

Examples:
INT8 (num_bits=8):
  Max error = (max - min) / 510

INT4 (num_bits=4):
  Max error = (max - min) / 30

INT16 (num_bits=16):
  Max error = (max - min) / 131070
```

**For normalized range [-1, 1]**:
```
INT8:  Max error ≤ 2 / 510 ≈ 0.00392
INT4:  Max error ≤ 2 / 30 ≈ 0.0667
INT16: Max error ≤ 2 / 131070 ≈ 0.0000153
```

### 7. Per-Channel Quantization (Axis-based)

**Definition**: Separate quantization for each position along specified axis.

```
For tensor T of shape (d₀, d₁, ..., dₙ), quantizing per-channel along axis=k:

For each index i₀, ..., i_{k-1}, i_{k+1}, ..., iₙ:
  min_i = min(T[j₀, ..., j_{k-1}, :, j_{k+1}, ..., jₙ] for all j_k)
  max_i = max(T[j₀, ..., j_{k-1}, :, j_{k+1}, ..., jₙ] for all j_k)
  scale_i = (max_i - min_i) / (2^num_bits - 1)
  
  For each element in slice:
    quantized[j₀, ..., jₙ] = round((T[j₀, ..., jₙ] - min_i) / scale_i)
```

**Practical Example (Weight Matrix)**:
```
Weights shape: (64, 32)  # 64 output channels, 32 input features

Per-channel along axis=0 (per output channel):
  For each output channel i ∈ [0, 63]:
    min_i = min(weights[i, :])    # Minimum over input features
    max_i = max(weights[i, :])    # Maximum over input features
    scale_i = (max_i - min_i) / 255
    
    For each input feature j ∈ [0, 31]:
      quantized[i, j] = round((weights[i, j] - min_i) / scale_i)

Result: 64 separate scales (one per output channel)
```

### 8. Rounding Functions

**HALF_AWAY_FROM_ZERO** (TensorFlow Default):
```
round_half_away(x) = {
  floor(x) + 1,  if x - floor(x) ≥ 0.5  (round up for x ≥ 0)
  floor(x),      otherwise              (round down)
  
  Equivalently: sign(x) * floor(|x| + 0.5)
}

Examples:
  0.4 → 0
  0.5 → 1
  0.6 → 1
  -0.5 → -1
  -0.4 → 0
```

**HALF_TO_EVEN** (Banker's Rounding):
```
round_half_even(x) = {
  nearest_integer,           if |x - nearest| ≠ 0.5
  even_of_two_nearest,       if |x - nearest| = 0.5
}

Examples:
  0.5 → 0 (even)
  1.5 → 2 (even)
  2.5 → 2 (even)
  -0.5 → 0 (even)
  -1.5 → -2 (even)
```

### 9. Quantization Mode Formulas

**MIN_COMBINED** (Asymmetric, Standard):
```
scale = (max_range - min_range) / (2^num_bits - 1)
zero_point = -min_range / scale
quantized = round((x - min_range) / scale)
dequantized = quantized * scale + min_range
```

**MIN_FIRST** (Min-detection variant):
```
min_abs = min(|min_range|, |max_range|)
max_abs = max(|min_range|, |max_range|)
scale = max_abs / (2^(num_bits-1) - 1)  # Different denominator
quantized = round(x / scale)
dequantized = quantized * scale
```

**SCALED** (Power-of-2 scaling):
```
Let scale = 2^(-shift) for some integer shift ≥ 0
Find shift such that (max_range - min_range) / scale ≈ 2^num_bits
quantized = round_half_even((x - min_range) / scale)
dequantized = quantized * scale + min_range
```

## Narrow Range Effect

### Standard Range (narrow_range=False)
```
For signed integers:
  Integer range = [-(2^(num_bits-1)), 2^(num_bits-1) - 1]
  INT8 range = [-128, 127]  # 256 levels

For unsigned integers:
  Integer range = [0, 2^num_bits - 1]
  UINT8 range = [0, 255]  # 256 levels
```

### Narrow Range (narrow_range=True)
```
For signed integers:
  Integer range = [-(2^(num_bits-1) - 1), 2^(num_bits-1) - 1]
  INT8 range = [-127, 127]  # 255 levels (one less)

For unsigned integers:
  Integer range = [1, 2^num_bits - 1]
  UINT8 range = [1, 255]  # 255 levels (one less, reserves 0)
```

**Effect on scale**:
```
Standard:  scale = (max - min) / 255  (for INT8)
Narrow:    scale = (max - min) / 254  (for INT8, slightly finer)
```

## TensorFlow Operation Specifications

### tf.quantization.quantize()

```
Input:
  input: float32 or float64 tensor of shape (*, )
  min_range: float scalar or tensor, broadcastable with input
  max_range: float scalar or tensor, broadcastable with input
  
Parameters:
  T: output data type ∈ {qint8, qint16, quint8, quint16}
  mode: ∈ {'MIN_COMBINED', 'MIN_FIRST', 'SCALED'}
  round_mode: ∈ {'HALF_AWAY_FROM_ZERO', 'HALF_TO_EVEN'}
  narrow_range: bool
  axis: int or None
  ensure_minimum_range: float > 0
  
Output:
  output: T (quantized integer tensor)
  output_min: float (adjusted minimum)
  output_max: float (adjusted maximum)

Constraint: ensure_minimum_range ≤ (max_range - min_range)
  If violated, min_range and max_range are adjusted
```

### tf.quantization.dequantize()

```
Input:
  input: qint8, qint16, quint8, or quint16 tensor
  min_range: float scalar or tensor
  max_range: float scalar or tensor
  
Parameters:
  mode: ∈ {'MIN_COMBINED', 'MIN_FIRST', 'SCALED'}
  
Output:
  output: float32 tensor (same shape as input)
  
Property: dequantize(quantize(x)) ≈ x (within quantization error)
```

### tf.quantization.quantize_and_dequantize_v2()

```
Input:
  input: float32 or float64 tensor
  input_min: float scalar or tensor
  input_max: float scalar or tensor
  
Parameters:
  num_bits: int ∈ [1, 16], default 8
  narrow_range: bool, default False
  axis: int or None, default None
  mode: ∈ {'MIN_COMBINED', 'MIN_FIRST', 'SCALED'}, default 'MIN_COMBINED'
  round_mode: ∈ {'HALF_AWAY_FROM_ZERO', 'HALF_TO_EVEN'}
  
Output:
  output: float32 (same shape as input)
  
Semantics: output = dequantize(quantize(input))
  Gradients: Straight-through estimator
    ∂output/∂input = 1 where input ∈ [input_min, input_max]
    ∂output/∂input = 0 where input out of range (clipped)
```

## Gradient Flow (For Training)

### Straight-Through Estimator (STE)

In `quantize_and_dequantize_v2()`, gradients are computed as:

```
Forward pass: output = dequantize(quantize(input))

Backward pass (gradient):
  ∂L/∂input = {
    ∂L/∂output,  if input ∈ [input_min, input_max]
    0,           if input < input_min or input > input_max
  }
```

**Rationale**: Round and clip operations are non-differentiable, so STE assumes gradient flows straight through except where clipped.

**Mathematical representation**:
```
Let Q(x) = quantize(x) and D(x) = dequantize(x)
Forward: y = D(Q(x))

Backward gradient:
  dy/dx ≈ 1  (ignoring rounding non-differentiability)
  
With clipping:
  dy/dx = {1 if x ∈ [min, max]; 0 otherwise}
```

## Data Type Details

### TensorFlow Integer Types

**tf.qint8** (Quantized Int8):
- Range: [-128, 127]
- Bits: 8-bit signed integer
- TensorFlow specific type (not standard NumPy)
- Used for quantized tensors
- One-to-one correspondence with int8 for bitwise operations

**tf.qint16** (Quantized Int16):
- Range: [-32768, 32767]
- Bits: 16-bit signed integer
- Higher precision variant
- Used for intermediate computations or higher-precision quantization

**tf.quint8** (Quantized Unsigned Int8):
- Range: [0, 255]
- Bits: 8-bit unsigned integer
- For non-negative values only
- Useful for activation functions (ReLU output)

**tf.quint16** (Quantized Unsigned Int16):
- Range: [0, 65535]
- Bits: 16-bit unsigned integer
- High-precision unsigned quantization

### Type Conversions

```python
# TensorFlow provides conversion utilities
qint8_value = tf.cast(float_value, tf.qint8)       # Float to quantized
float_value = tf.cast(qint8_value, tf.float32)     # Quantized to float

# These are raw casts without dequantization scaling
# Use tf.quantization.dequantize() for proper reconstruction
```

## Numerical Stability Considerations

### Handling Small Ranges
```
If (max_range - min_range) < ensure_minimum_range:
  TensorFlow automatically expands range
  
Example (INT8):
  Input range: [0.001, 0.002]
  ensure_minimum_range: 0.01 (default)
  
  Expanded to: [0.001 - 0.0045, 0.002 + 0.0045]
             = [-0.0035, 0.0065]
  
  Ensures minimum 0.01 range for quantization stability
```

### Handling Zero Crossing
```
If min_range < 0 < max_range (zero-crossing):
  Standard quantization works correctly
  Zero-point offset ensures 0.0 is representable
  
If min_range ≥ 0 (all positive):
  Using tf.quint8 preferable (unsigned)
  Scale = (max - min) / 255
  
If max_range ≤ 0 (all negative):
  Use tf.qint8 with adjusted range
  scale = (|min| - |max|) / 255
```

## Performance Characteristics

### Computational Complexity

**Quantization** (per element):
```
Operations: 1 subtraction, 1 division, 1 rounding
Time: O(1) per element
Total: O(n) for n elements
GPU: Highly parallelizable
```

**Dequantization** (per element):
```
Operations: 1 multiplication, 1 addition
Time: O(1) per element
Total: O(n) for n elements
GPU: Highly parallelizable
```

**Per-channel vs Per-tensor**:
```
Per-tensor: (n_outputs) scales to compute
Per-channel: (n_outputs * n_inputs) if per-filter

Memory: Negligible (scales fit in cache)
Compute: Per-channel minimal overhead (<2% typically)
```

### Hardware Acceleration

**CPU (x86, ARM)**:
- INT8: Native 8-bit arithmetic available
- Speedup: 2-4x on CPUs with Int8 support
- Operations: VNNI (AVX-512), NEON (ARM)

**GPU (NVIDIA)**:
- INT8: Tensor Cores support INT8 operations
- Speedup: 3-8x depending on architecture
- Kernels: cuDNN, TensorRT support INT8

**TPU**:
- INT8/INT16: Native support
- Speedup: 4-10x
- Recommended: Per-channel quantization

## Verification and Testing

### Correctness Testing
```
1. Quantize: q = quantize(x)
2. Dequantize: x_approx = dequantize(q)
3. Verify: ||x - x_approx|| ≤ quantization_error

Quantization error bound:
  max_error = scale / 2 = (max - min) / (2 * (2^num_bits - 1))
```

### Numerical Testing
```
Test cases:
1. Zero-point: quantize(0) should map near 128 for INT8 in [-1, 1]
2. Extremes: quantize(min_range) = 0, quantize(max_range) = 255
3. Outliers: quantize(x > max_range) = 255 (clipped)
4. Symmetry: Check HALF_AWAY_FROM_ZERO behavior at 0.5
```

---

**Reference Implementation Location**: `/tensorflow/python/ops/math_ops.py` and `/tensorflow/core/kernels/quantize_and_dequantize_op.cc`
