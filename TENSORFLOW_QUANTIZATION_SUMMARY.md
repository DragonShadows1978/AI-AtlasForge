# TensorFlow Quantization - Quick Reference Summary

## Document Repository
**Main Research Document**: `/mnt/ForgeRealm/AI-AtlasForge/TENSORFLOW_QUANTIZATION_RESEARCH.md`

## Critical API Functions at a Glance

### For Training (Simulated Quantization)
```python
# Recommended: Quantization-aware training
tf.quantization.quantize_and_dequantize_v2(
    input, input_min, input_max,
    num_bits=8,
    narrow_range=False,
    axis=None,  # None=per-tensor, int=per-channel
    mode='MIN_COMBINED',  # or 'MIN_FIRST', 'SCALED'
    round_mode='HALF_AWAY_FROM_ZERO'  # or 'HALF_TO_EVEN'
)
# Returns: float32 tensor with simulated quantization

# Learnable bounds variant
tf.quantization.fake_quant_with_min_max_vars(
    inputs, min, max,
    num_bits=8,
    narrow_range=False
)
```

### For Deployment (Actual Quantization)
```python
# Quantize to integers
quantized, min_out, max_out = tf.quantization.quantize(
    input, min_range, max_range,
    T=tf.qint8,  # tf.qint16, tf.quint8, tf.quint16
    mode='MIN_COMBINED',
    narrow_range=False,
    axis=None  # axis=0 for per-channel
)
# Returns: (quantized_tensor, min_range, max_range)

# Dequantize back to float
dequantized = tf.quantization.dequantize(
    input=quantized,
    min_range=min_out,
    max_range=max_out,
    mode='MIN_COMBINED'
)
# Returns: float32 tensor (reconstructed)
```

## Mathematical Core Formulas

### Scale Factor
```
scale = (max_range - min_range) / (2^num_bits - 1)

Examples:
- INT8 (num_bits=8): scale = (max - min) / 255
- INT4 (num_bits=4): scale = (max - min) / 15
```

### Quantization Formula
```
quantized_value = round((clipped_input - min_range) / scale)

Where: clipped_input = clip(input, min_range, max_range)
```

### Dequantization Formula
```
dequantized_value = quantized_value * scale + min_range
```

### Zero-Point (Asymmetric Quantization)
```
zero_point = round(-min_range / scale)

Purpose: Maps float value 0.0 to integer space
```

## Quantization Type Comparison

| Type | Range | Compression | Use Case | Hardware |
|------|-------|-------------|----------|----------|
| INT8 | [-128, 127] | 4x | Standard inference | Excellent |
| INT16 | [-32768, 32767] | 2x | Intermediate precision | Good |
| UINT8 | [0, 255] | 4x | Positive-bias weights | Excellent |
| INT4 | [-8, 7] | 8x | Extreme compression | Limited |

## Quantization Strategy Comparison

| Strategy | Training | Per-Channel | Accuracy | Speed | Effort |
|----------|----------|-------------|----------|-------|--------|
| Post-Training | No | Optional | Good | Fast | Low |
| QAT v2 | Yes | Optional | Better | Slower | Medium |
| Fake Quant Vars | Yes | Yes | Best | Slower | High |
| Mixed-Precision | Yes | Yes | Excellent | Medium | High |

## Per-Tensor vs Per-Channel

### Per-Tensor (axis=None)
```python
# Single scale for entire tensor
min_val = tf.reduce_min(weights)          # Scalar
max_val = tf.reduce_max(weights)          # Scalar
scale = (max_val - min_val) / 255         # Scalar

quantized = tf.quantization.quantize(
    weights, min_val, max_val,
    axis=None  # Per-tensor
)
```
- Simpler computation
- All hardware supports
- May lose precision for heterogeneous data
- 0% accuracy overhead

### Per-Channel (axis=dimension)
```python
# Separate scale per channel
min_vals = tf.reduce_min(weights, axis=1, keepdims=True)  # Shape: (64, 1)
max_vals = tf.reduce_max(weights, axis=1, keepdims=True)  # Shape: (64, 1)
# scale shape: (64, 1) - one per output channel

quantized = tf.quantization.quantize(
    weights, min_vals, max_vals,
    axis=0  # Per-channel along dimension 0
)
```
- Better precision (each channel optimized)
- 2-5% accuracy improvement typical
- Supported by modern hardware
- Essential for heterogeneous data

## Quantization Modes

### MIN_COMBINED (Default)
- Asymmetric quantization
- Full range: [0, 2^num_bits - 1]
- Rounding: HALF_AWAY_FROM_ZERO
- Use case: General purpose, most common
- Formula: `q = round((x - min) / scale)`

### MIN_FIRST
- Finds min first, then quantizes
- Symmetric around zero when possible
- Use case: Data with natural zero-point
- Formula: Variant where min is determined first

### SCALED
- Powers-of-2 scaling
- scale = 2^(-shift)
- Rounding: HALF_TO_EVEN (banker's rounding)
- Use case: Hardware with efficient power-of-2 division

## Common Implementation Patterns

### Pattern 1: Post-Training Quantization (PTQ)
```python
# Step 1: Load trained model
model = tf.keras.models.load_model('model.h5')

# Step 2: Prepare representative dataset
def representative_dataset():
    for data in calibration_data.batch(1):
        yield (tf.cast(data, tf.float32),)

# Step 3: Convert
converter = tf.lite.TFLiteConverter.from_keras_model(model)
converter.optimizations = [tf.lite.Optimize.DEFAULT]
converter.representative_dataset = representative_dataset
converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
quantized_model = converter.convert()

# Compression: 4x (float32 → INT8)
# Accuracy loss: 1-3% (typical)
# Training time: None (fast)
```

### Pattern 2: Quantization-Aware Training (QAT)
```python
import tensorflow_model_optimization as tfmot

# Step 1: Create baseline model
model = create_model()

# Step 2: Apply quantization awareness
q_model = tfmot.quantization.keras.quantize_model(model)

# Step 3: Train with quantization
q_model.compile(optimizer='adam', loss='categorical_crossentropy')
q_model.fit(train_data, epochs=10)

# Step 4: Convert
converter = tf.lite.TFLiteConverter.from_keras_model(q_model)
quantized_model = converter.convert()

# Compression: 4x (float32 → INT8)
# Accuracy loss: 0.5-1.5% (better than PTQ)
# Training time: 10-20% longer than baseline
```

### Pattern 3: Per-Channel with Custom Quantization
```python
# For weight quantization with per-channel
def quantize_weights_per_channel(weights):
    # Shape: (out_channels, in_channels, ...)
    # Quantize per output channel (axis=0)
    
    min_vals = tf.reduce_min(weights, axis=range(1, len(weights.shape)))
    max_vals = tf.reduce_max(weights, axis=range(1, len(weights.shape)))
    
    # Reshape for broadcasting
    min_vals = tf.reshape(min_vals, [-1] + [1]*(len(weights.shape)-1))
    max_vals = tf.reshape(max_vals, [-1] + [1]*(len(weights.shape)-1))
    
    quantized, min_out, max_out = tf.quantization.quantize(
        weights,
        min_vals, max_vals,
        T=tf.qint8,
        axis=0
    )
    
    return quantized, min_out, max_out
```

## Integer Range Selection (narrow_range Parameter)

### narrow_range=False (Default)
```
For signed integers:  [-(2^(num_bits-1)), 2^(num_bits-1) - 1]
For INT8:            [-128, 127]    (256 levels)

For unsigned integers: [0, 2^num_bits - 1]
For UINT8:           [0, 255]       (256 levels)
```

### narrow_range=True
```
For signed integers:  [-(2^(num_bits-1)), 2^(num_bits-1) - 1]
For INT8:           [-127, 127]    (255 levels, one less)

For unsigned integers: [1, 2^num_bits - 1]
For UINT8:          [1, 255]       (255 levels, reserves 0)
```

Use `narrow_range=True` for asymmetric quantization where you want an extra level.

## Rounding Mode Impact

### HALF_AWAY_FROM_ZERO
```
0.5 → 1
-0.5 → -1
1.5 → 2
Bias: Can introduce slight positive bias
Use: MIN_COMBINED, MIN_FIRST modes
```

### HALF_TO_EVEN (Banker's Rounding)
```
0.5 → 0 (even)
1.5 → 2 (even)
2.5 → 2 (even)
Bias: Balanced, less biased
Use: SCALED mode
```

## Source Code Locations

### Python API
- **File**: `/tensorflow/python/ops/math_ops.py`
- **GitHub**: https://github.com/tensorflow/tensorflow/blob/master/tensorflow/python/ops/math_ops.py
- **Functions**: quantize_and_dequantize, quantize_and_dequantize_v2 (lines ~3800-3950)

### C++ Operations
- **File**: `/tensorflow/core/ops/quantization_ops.cc`
- **GitHub**: https://github.com/tensorflow/tensorflow/blob/master/tensorflow/core/ops/quantization_ops.cc
- **Ops Defined**: Quantize, Dequantize, QuantizeAndDequantize

### Kernels
- **Files**: `/tensorflow/core/kernels/quantize_*_op.cc`
- **Contain**: GPU/CPU kernel implementations
- **Directory**: https://github.com/tensorflow/tensorflow/tree/master/tensorflow/core/kernels

### TensorFlow Lite
- **Directory**: `/tensorflow/lite/quantization/`
- **File**: `/tensorflow/lite/quantization/quantization.cc`

## Key Academic Papers

### Primary Reference
**Title**: "Quantization and Training of Neural Networks for Efficient Integer-Arithmetic Only Inference"
- **Authors**: Jacob et al. (Google)
- **Year**: 2018
- **URL**: https://arxiv.org/abs/1806.08342
- **Citation**: CVPR 2018
- **Content**: Defines asymmetric INT8 quantization scheme used by TensorFlow

### Secondary References
- **"Quantizing Deep Convolutional Networks for Efficient Inference: A Whitepaper"** - Krishnamoorthi, Google, 2018
- **"A Survey on Methods and Theories of Quantized Neural Networks"** - Guo, 2021 - https://arxiv.org/abs/2106.08295
- **"Integer Quantization for Deep Learning Inference"** - Jacob et al., 2020 - https://arxiv.org/abs/2004.09602

## Official TensorFlow Documentation

### Main Guides
- **Quantization Overview**: https://www.tensorflow.org/guide/quantization
- **TFLite Quantization**: https://www.tensorflow.org/lite/performance/post_training_quantization
- **QAT Guide**: https://www.tensorflow.org/lite/performance/quantization_aware_training
- **Model Optimization**: https://www.tensorflow.org/model_optimization

### API References
- **tf.quantization module**: https://www.tensorflow.org/api_docs/python/tf/quantization
- **tf.lite.TFLiteConverter**: https://www.tensorflow.org/api_docs/python/tf/lite/TFLiteConverter
- **tf.keras.quantization**: https://www.tensorflow.org/api_docs/python/tf/keras/quantization

## Quick Troubleshooting

### Problem: Large accuracy drop after quantization
- **Cause**: Poor calibration dataset or incorrect min/max ranges
- **Solution**: Collect representative data, use per-channel, try QAT

### Problem: Extreme values dominating quantization
- **Cause**: Outliers in weights/activations
- **Solution**: Use per-channel quantization, percentile-based clipping

### Problem: Quantized model doesn't match float baseline
- **Cause**: Rounding errors accumulate, different hardware behavior
- **Solution**: Expected 0.5-2% difference, use validation dataset

### Problem: INT4 training unstable
- **Cause**: Only 16 levels, extreme quantization
- **Solution**: Use QAT, learnable bounds, per-channel, start with INT8

## Performance Expectations

### Latency (Time per Inference)
```
Model: ResNet50 on CPU
- Float32:    200ms
- INT8 PTQ:   50-75ms (2.7-4x faster)
- INT8 QAT:   50-75ms (2.7-4x faster)
- INT4:       30-50ms (4-6.7x faster, if supported)
```

### Model Size
```
ResNet50:
- Float32:    102 MB
- INT8:       25.5 MB (4x compression)
- INT4:       12.75 MB (8x compression)
```

### Accuracy Trade-off
```
ResNet50 on ImageNet:
- Float32 baseline: 76.5% top-1
- INT8 PTQ:         76.1% top-1 (0.4% loss)
- INT8 QAT:         76.3% top-1 (0.2% loss)
- INT4 + QAT:       75.8% top-1 (0.7% loss)
```

---

**For comprehensive details**, see: `/mnt/ForgeRealm/AI-AtlasForge/TENSORFLOW_QUANTIZATION_RESEARCH.md`
