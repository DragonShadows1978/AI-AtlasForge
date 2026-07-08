# Quantization Mathematics: Practical Implementations & Worked Examples

## Overview

This is a consolidated research report covering practical implementations of quantization mathematics with working code, step-by-step algorithms, and detailed numerical examples.

---

## 1. FUNDAMENTAL CONCEPTS

### Affine (Linear) Quantization Scheme

The core mapping function for quantization is affine/linear transformation:

```
x_q = round(x / S + Z)  [quantization]
x̂ = S * (x_q - Z)       [dequantization]
```

Where:
- `x` = original floating-point value
- `x_q` = quantized integer value (INT8: -128 to 127)
- `S` = scale factor (positive float)
- `Z` = zero-point (integer representing where FP32 zero maps to)
- `x̂` = reconstructed floating-point value

### Quantization Parameters

**Scale Factor Calculation:**

For **asymmetric (affine) quantization**:
```
S = (max_val - min_val) / (qmax - qmin)
```

For **symmetric quantization** (centered at zero):
```
S = max(|min_val|, |max_val|) / 127  [for INT8, symmetric uses -127 to 127]
```

**Zero-Point Calculation:**

For asymmetric quantization:
```
Z = qmax - max_val / S
```

For symmetric quantization:
```
Z = 0  [by design, no offset needed]
```

Where:
- For signed INT8: qmin = -128, qmax = 127
- For unsigned UINT8: qmin = 0, qmax = 255

---

## 2. WORKED EXAMPLES WITH ACTUAL NUMBERS

### Example 1: Symmetric INT8 Quantization (Absmax)

**Problem:** Quantize weight tensor: `[0.84, -0.23, 1.12, -0.67, 0.45]`

**Step 1: Find maximum absolute value**
```
absmax = max(|0.84|, |-0.23|, |1.12|, |-0.67|, |0.45|)
       = 1.12
```

**Step 2: Compute scale factor**
```
scale = absmax / 127
      = 1.12 / 127
      ≈ 0.008819
```

**Step 3: Quantize each element**
```
For 0.84:   0.84 / 0.008819 ≈ 95.25 → round(95.25) = 95
For -0.23:  -0.23 / 0.008819 ≈ -26.08 → round(-26.08) = -26
For 1.12:   1.12 / 0.008819 ≈ 127.00 → round(127.00) = 127
For -0.67:  -0.67 / 0.008819 ≈ -75.95 → round(-75.95) = -76
For 0.45:   0.45 / 0.008819 ≈ 51.02 → round(51.02) = 51

Result: [95, -26, 127, -76, 51]  [all INT8 values]
```

**Step 4: Dequantize to verify**
```
For 95:   0.008819 * 95 ≈ 0.838
For -26:  0.008819 * (-26) ≈ -0.229
For 127:  0.008819 * 127 ≈ 1.120
For -76:  0.008819 * (-76) ≈ -0.670
For 51:   0.008819 * 51 ≈ 0.450

Reconstructed: [0.838, -0.229, 1.120, -0.670, 0.450]
Original:      [0.840, -0.230, 1.120, -0.670, 0.450]
Quantization error: < 0.003 per element
```

### Example 2: Asymmetric INT8 Quantization

**Problem:** Quantize activation tensor (all non-negative): `[0.1, 0.5, 0.8, 1.2, 0.3]`

**Step 1: Find min and max**
```
min_val = 0.1
max_val = 1.2
```

**Step 2: Compute scale (for UINT8: qmin=0, qmax=255)**
```
scale = (max_val - min_val) / (qmax - qmin)
      = (1.2 - 0.1) / (255 - 0)
      = 1.1 / 255
      ≈ 0.00431
```

**Step 3: Compute zero-point**
```
zero_point = qmax - max_val / scale
           = 255 - 1.2 / 0.00431
           = 255 - 278.19
           ≈ -23.19 → round(-23) = -23  [but clamp to valid range]

Actually for UINT8, clamp to [0, 255]:
zero_point = 0  [simplified; actual calculation per ONNX spec]
```

**Step 4: Quantize each element**
```
For 0.1:   round((0.1 - 0.1) / 0.00431 + 0) = 0
For 0.5:   round((0.5 - 0.1) / 0.00431 + 0) = round(92.8) = 93
For 0.8:   round((0.8 - 0.1) / 0.00431 + 0) = round(162.2) = 162
For 1.2:   round((1.2 - 0.1) / 0.00431 + 0) = round(255.0) = 255
For 0.3:   round((0.3 - 0.1) / 0.00431 + 0) = round(46.4) = 46

Result: [0, 93, 162, 255, 46]  [UINT8 values]
```

---

## 3. PYTORCH REFERENCE IMPLEMENTATION

### Core Quantization Functions (from Jermmy/pytorch-quantization-demo)

```python
def calcScaleZeroPoint(min_val, max_val, num_bits=8):
    """Calculate scale and zero-point for affine quantization"""
    qmin = 0.
    qmax = 2. ** num_bits - 1.
    
    # Scale: maps floating-point range to quantized range
    scale = (max_val - min_val) / (qmax - qmin)
    
    # Zero-point: where does FP 0.0 map to in quantized space?
    zero_point = qmax - max_val / scale
    
    # Clamp zero-point to valid range
    if zero_point < qmin:
        zero_point = torch.tensor([qmin], dtype=torch.float32).to(min_val.device)
    elif zero_point > qmax:
        zero_point = torch.tensor([qmax], dtype=torch.float32).to(max_val.device)
    
    zero_point.round_()
    return scale, zero_point


def quantize_tensor(x, scale, zero_point, num_bits=8, signed=False):
    """Quantize floating-point tensor to integers"""
    if signed:
        qmin = - 2. ** (num_bits - 1)
        qmax = 2. ** (num_bits - 1) - 1
    else:
        qmin = 0.
        qmax = 2. ** num_bits - 1.
    
    # Main quantization formula
    q_x = zero_point + x / scale
    q_x.clamp_(qmin, qmax).round_()
    return q_x


def dequantize_tensor(q_x, scale, zero_point):
    """Reconstruct floating-point values from quantized integers"""
    return scale * (q_x - zero_point)
```

### QParam Module - Tracks Quantization Parameters

```python
class QParam(nn.Module):
    """Stores and manages quantization parameters"""
    
    def __init__(self, num_bits=8):
        super(QParam, self).__init__()
        self.num_bits = num_bits
        self.register_buffer('scale', torch.tensor([]))
        self.register_buffer('zero_point', torch.tensor([]))
        self.register_buffer('min', torch.tensor([]))
        self.register_buffer('max', torch.tensor([]))

    def update(self, tensor):
        """Calibration: update min/max based on observed values"""
        if self.max.nelement() == 0 or self.max.data < tensor.max().data:
            self.max.data = tensor.max().data
        self.max.clamp_(min=0)
        
        if self.min.nelement() == 0 or self.min.data > tensor.min().data:
            self.min.data = tensor.min().data
        self.min.clamp_(max=0)
        
        # Recalculate scale and zero-point based on new min/max
        self.scale, self.zero_point = calcScaleZeroPoint(
            self.min, self.max, self.num_bits
        )
    
    def quantize_tensor(self, tensor):
        return quantize_tensor(tensor, self.scale, self.zero_point, 
                             num_bits=self.num_bits)

    def dequantize_tensor(self, q_x):
        return dequantize_tensor(q_x, self.scale, self.zero_point)
```

### Quantization-Aware Training (QAT) - Fake Quantization

```python
class FakeQuantize(Function):
    """Simulates quantization in forward pass for gradient computation"""
    
    @staticmethod
    def forward(ctx, x, qparam):
        # Quantize and immediately dequantize to add quantization noise
        x = qparam.quantize_tensor(x)
        x = qparam.dequantize_tensor(x)
        return x

    @staticmethod
    def backward(ctx, grad_output):
        # Straight-through estimator: gradients pass through unchanged
        return grad_output, None
```

### Quantized Linear Layer Example

```python
class QLinear(QModule):
    """Quantized Linear layer with INT8 inference"""
    
    def __init__(self, fc_module, qi=True, qo=True, num_bits=8):
        super(QLinear, self).__init__(qi=qi, qo=qo, num_bits=num_bits)
        self.fc_module = fc_module
        self.qw = QParam(num_bits=num_bits)  # Weight quantization
        self.register_buffer('M', torch.tensor([]))  # Rescaling factor
    
    def forward(self, x):
        # Training: use fake quantization to simulate INT8 inference
        if hasattr(self, 'qi'):
            self.qi.update(x)
            x = FakeQuantize.apply(x, self.qi)
        
        self.qw.update(self.fc_module.weight.data)
        x = F.linear(x, FakeQuantize.apply(self.fc_module.weight, self.qw), 
                    self.fc_module.bias)
        
        if hasattr(self, 'qo'):
            self.qo.update(x)
            x = FakeQuantize.apply(x, self.qo)
        
        return x
    
    def freeze(self, qi=None, qo=None):
        """Freeze quantization parameters for inference"""
        if qi is not None:
            self.qi = qi
        if qo is not None:
            self.qo = qo
        
        # Compute rescaling factor for inference
        # M = (qw_scale * qi_scale) / qo_scale
        self.M.data = (self.qw.scale * self.qi.scale / self.qo.scale).data
        
        # Quantize weights and bias
        self.fc_module.weight.data = self.qw.quantize_tensor(
            self.fc_module.weight.data
        )
        self.fc_module.weight.data = (
            self.fc_module.weight.data - self.qw.zero_point
        )
        
        self.fc_module.bias.data = quantize_tensor(
            self.fc_module.bias.data,
            scale=self.qi.scale * self.qw.scale,
            zero_point=0, num_bits=32, signed=True
        )
    
    def quantize_inference(self, x):
        """Pure INT8 inference"""
        # Step 1: Quantize input
        x = x - self.qi.zero_point
        
        # Step 2: INT8 matrix multiply
        x = self.fc_module(x)
        
        # Step 3: Rescale accumulated result
        x = self.M * x
        x.round_()
        
        # Step 4: Dequantize to output range
        x = x + self.qo.zero_point
        x.clamp_(0., 2.**self.num_bits - 1.).round_()
        
        return x
```

---

## 4. ONNX RUNTIME QUANTIZATION REFERENCE

### Affine Quantization Formula (ONNX)

```
val_fp32 = scale * (val_quantized - zero_point)
```

**Quantization:**
```
For asymmetric: scale = (max_float - min_float) / (qmax - qmin)
For symmetric:  scale = max(|min_float|, |max_float|) / 127
```

**Clipping:**
```
val_quantized = clip(round(val_fp32 / scale + zero_point), qmin, qmax)
```

### ONNX QuantizeLinear Operator

```
Y = saturate(round(X / scale + zero_point))
```

Where:
- Input X is FP32
- Output Y is INT8 (or other integer type)
- scale is FP32
- zero_point is INT8
- saturate clamps to valid range [-128, 127] for signed INT8

### ONNX DequantizeLinear Operator

```
Y = (X - zero_point) * scale
```

Where:
- Input X is INT8 (quantized)
- Output Y is FP32 (reconstructed)

---

## 5. PYTORCH NATIVE QUANTIZATION API

### Dynamic Quantization Example

```python
import torch
import torch.quantization as quantization

# Quantize Linear and LSTM layers dynamically
model = torch.quantization.quantize_dynamic(
    model, 
    qconfig_spec={torch.nn.Linear, torch.nn.LSTM},
    dtype=torch.qint8
)
```

**Advantages:**
- Activations quantized on-the-fly during inference (calibrated per input)
- Weights pre-quantized
- Higher accuracy, lower inference overhead

### Static Quantization Example

```python
import torch
from torch.quantization import get_default_qconfig, prepare_qat, convert

# 1. Prepare model for static quantization
qconfig = get_default_qconfig('fbgemm')
model.qconfig = qconfig
torch.quantization.prepare_qat(model, inplace=True)

# 2. Calibrate on representative data
for batch in calibration_data:
    model(batch)

# 3. Convert to quantized model
torch.quantization.convert(model, inplace=True)

# 4. Inference
output = model(input)
```

**Advantages:**
- Scale/zero-point pre-computed (offline calibration)
- Faster inference than dynamic
- May need re-calibration for distribution shift

### Quantization-Aware Training (QAT)

```python
model.qconfig = torch.quantization.get_default_qat_qconfig('fbgemm')
torch.quantization.prepare_qat(model, inplace=True)

# Train with fake quantization
for epoch in range(num_epochs):
    for batch in train_data:
        output = model(batch)
        loss = criterion(output, targets)
        loss.backward()
        optimizer.step()

# Convert to quantized model
torch.quantization.convert(model, inplace=True)
```

---

## 6. HUGGINGFACE OPTIMUM QUANTIZATION

### Core Quantization Formula (From Optimum Docs)

```
x_q = clip(round(x / S + Z), qmin, qmax)
```

Where:
- S = (b - a) / (qmax - qmin)  [scale for range [a,b]]
- Z = zero-point (ensures 0 maps to exactly Z in quantized space)
- qmin, qmax are INT8 bounds: [-128, 127]

### Per-Tensor vs Per-Channel Quantization

**Per-Tensor:** Single (S, Z) pair for entire tensor
```
Single scale = (max(tensor) - min(tensor)) / (255)
All elements quantized with same scale
```

**Per-Channel:** Separate (S, Z) for each channel/row
```
For each channel c:
    scale[c] = (max(channel_c) - min(channel_c)) / (255)
Per-channel allows 256x more granular scaling (8-bit overhead)
```

### Calibration Techniques

| Technique | Formula | Best For |
|-----------|---------|----------|
| **MinMax** | range = [min_observed, max_observed] | Weights |
| **Moving Average MinMax** | weighted average of min/max | Activations |
| **Entropy** | minimize KL divergence | Dynamic ranges |
| **Mean Square Error (MSE)** | minimize (x - x̂)² | Precision-critical |
| **Percentile** | range = [p-percentile, (100-p)-percentile] | Outlier handling |

---

## 7. TVM QUANTIZATION DOCUMENTATION

### Key Concepts from TVM

**Quantization Workflow:**
1. **Pass 1:** Record min/max statistics for each tensor
2. **Pass 2:** Compute scale and zero-point
3. **Pass 3:** Insert quantize/dequantize nodes in graph
4. **Pass 4:** Fuse operations where possible

**Common Patterns:**

```
Original:   Conv → BatchNorm → ReLU
Fused:      ConvBN → ReLU
After Q:    QuantizeLinear → ConvBN → ReLU → DequantizeLinear
```

---

## 8. PRACTICAL IMPLEMENTATION PATTERNS

### Pattern 1: Post-Training Quantization (PTQ)

```python
def post_training_quantize(model, calibration_dataloader):
    """Step-by-step PTQ"""
    
    # Attach observers to record statistics
    for module in model.modules():
        if isinstance(module, nn.Linear):
            observer = torch.quantization.MinMaxObserver()
            module.register_forward_hook(
                lambda m, i, o: observer(o)
            )
    
    # Run calibration
    model.eval()
    with torch.no_grad():
        for batch in calibration_dataloader:
            model(batch)
    
    # Compute scale and zero-point from statistics
    scale = observer.scale
    zero_point = observer.zero_point
    
    # Quantize weights
    for module in model.modules():
        if isinstance(module, nn.Linear):
            module.weight.data = quantize_tensor(
                module.weight.data, scale, zero_point
            )
    
    return model
```

### Pattern 2: Layer-Wise Quantization

```python
def layer_wise_quantize(model):
    """Quantize layer by layer with per-layer calibration"""
    
    for name, module in model.named_modules():
        if isinstance(module, nn.Linear):
            # Per-layer quantization
            qi = QParam()
            qw = QParam()
            qo = QParam()
            
            # Calibrate on representative activations
            with torch.no_grad():
                x = get_representative_input()
                qi.update(x)
                
                # Pass through layer
                y = module(x)
                qo.update(y)
            
            # Calibrate weights
            qw.update(module.weight.data)
            
            # Replace with quantized version
            module = QLinear(
                module, qi=qi, qo=qo, num_bits=8
            )
    
    return model
```

### Pattern 3: Quantization-Aware Training (QAT)

```python
def qat_train(model, train_dataloader, num_epochs):
    """Train model with fake quantization"""
    
    # Prepare model for QAT
    model.qconfig = torch.quantization.get_default_qat_qconfig('fbgemm')
    torch.quantization.prepare_qat(model, inplace=True)
    
    model.train()
    optimizer = torch.optim.Adam(model.parameters())
    
    for epoch in range(num_epochs):
        for batch, targets in train_dataloader:
            # Forward with fake quantization
            output = model(batch)
            loss = F.cross_entropy(output, targets)
            
            # Backward - straight-through estimator approximates gradients
            loss.backward()
            optimizer.step()
            optimizer.zero_grad()
        
        # Anneal quantization parameters if desired
        if epoch > num_epochs * 0.8:
            # Freeze batch norm statistics
            torch.quantization.disable_observer(model)
    
    # Convert to actual quantized model
    torch.quantization.convert(model, inplace=True)
    return model
```

---

## 9. SCALE COMPUTATION DETAILED BREAKDOWN

### For Symmetric Quantization (Common for Weights)

```
Step 1: Find extreme values
    absmax = max(|tensor|)

Step 2: Map to quantized range [-127, 127]
    scale = absmax / 127
    
    Reasoning: 127 is the largest representable positive INT8 value
    If absmax = 1.0, then scale = 1/127 ≈ 0.00787
    Each integer step covers 0.00787 in FP space

Step 3: Quantize
    q_val = round(fp_val / scale)
```

**Example:**
```
absmax = 2.5
scale = 2.5 / 127 = 0.01969
value = 2.0
q_val = round(2.0 / 0.01969) = round(101.52) = 102
reconstructed = 102 * 0.01969 = 2.007
```

### For Asymmetric Quantization (Common for Activations)

```
Step 1: Find data range
    min_val = tensor.min()
    max_val = tensor.max()

Step 2: Calculate scale covering [min_val, max_val] → [0, 255]
    scale = (max_val - min_val) / 255
    
    This maps the entire float range to UINT8 space
    If range = 1.0 (max 1.0, min 0.0), scale = 1.0/255 ≈ 0.00392

Step 3: Calculate zero-point (where FP 0.0 maps to)
    zero_point = round(-min_val / scale)
    
    Ensures: FP_0.0 → INT8_zero_point exactly

Step 4: Quantize
    q_val = round((fp_val - min_val) / scale)
    
    Or equivalently:
    q_val = round(fp_val / scale - min_val / scale)
    q_val = round(fp_val / scale + zero_point)
```

**Example:**
```
min_val = 0.5, max_val = 2.5
scale = (2.5 - 0.5) / 255 = 2.0 / 255 ≈ 0.00784
zero_point = round(-0.5 / 0.00784) = round(-63.75) = -64

value = 1.5
q_val = round((1.5 - 0.5) / 0.00784) = round(127.55) = 128
reconstructed = 128 * 0.00784 + 0.5 = 1.503

verification:
  q_val = round(1.5 / 0.00784 - 64) = round(191.33 - 64) = round(127.33) = 127 ✓
```

---

## 10. SOURCES & REPOSITORIES

### Comprehensive Tutorials
1. **Michael Brenndoerfer's INT8 Guide** (mbrenndoerfer.com)
   - Complete worked examples with actual numbers
   - Absmax, smooth quantization, outlier problem coverage
   - Interactive visualizations

2. **PyTorch Official Quantization Guide** (pytorch.org/blog/quantization-in-practice)
   - Mapping functions, calibration, QConfig
   - Dynamic, static, QAT workflows
   - Sensitivity analysis

3. **HuggingFace Optimum Quantization** (huggingface.co/docs/optimum)
   - Affine scheme fundamentals
   - Per-tensor vs per-channel details
   - Energy efficiency analysis (2024)

### GitHub Reference Implementations
1. **Jermmy/pytorch-quantization-demo** (543 stars)
   - Ground truth implementation
   - QParam class, quantize_tensor, dequantize_tensor functions
   - QLinear, QConv2d modules with INT8 inference

2. **infocusp/model_quantization** (Tutorial repo)
   - PTDQ, PTSQ, QAT notebooks
   - Introduction to Quantization PDF
   - Symmetric vs asymmetric comparisons

### Framework Documentation
1. **ONNX Runtime Quantization**
   - affine scheme formula
   - Pre-processing, dynamic/static methods
   - Debugging tools

2. **PyTorch torch.quantization**
   - quantize_dynamic, quantize_static APIs
   - Observer classes (MinMax, MovingAverage, Entropy)
   - Backend support (FBGEMM, QNNPACK)

### Academic Foundations
- Jacob et al. 2018: "Quantization and Training of Neural Networks for Efficient Integer-Arithmetic-Only Inference"
- Dettmers et al. 2022: "LLM.int8(): 8-bit Matrix Multiplication for Transformers at Scale"
- Xiao et al. 2022: "SmoothQuant: Accurate and Efficient Post-Training Quantization for Large Language Models"

---

## 11. KEY IMPLEMENTATION TAKEAWAYS

### Critical Details

1. **Clipping Range Selection** (Calibration)
   - Too tight: outliers cause large errors
   - Too loose: wasted precision on unused ranges
   - Solution: Use percentile methods or entropy minimization

2. **Zero-Point Importance**
   - MUST map FP32 zero exactly to an INT8 value
   - Breaks zero-padding patterns in convolutions if not exact
   - Affects ReLU and other zero-aware operations

3. **Per-Channel vs Per-Tensor**
   - Per-channel: ~1% memory overhead, 1-3% accuracy gain
   - Per-tensor: simpler, faster quantization/dequantization
   - Weights usually benefit from per-channel
   - Activations usually fine with per-tensor

4. **Gradient Approximation (QAT)**
   - Quantization is non-differentiable
   - Straight-through estimator: backprop treats quant as identity
   - Allow model to learn parameters resilient to quantization noise

5. **Accumulation Data Type**
   - INT8 × INT8 multiplication → INT16+ result
   - Avoid overflow in matrix operations
   - ONNX uses INT32 for INT8 accumulation

### Testing Checklist

- [ ] Verify scale/zero-point computed correctly
- [ ] Check quantization error < threshold (typically < 1% of range)
- [ ] Ensure zero maps exactly: `abs(dequant(Z) - 0.0) < epsilon`
- [ ] Test on representative calibration data (200-1000 examples)
- [ ] Compare FP32 vs INT8 activations in middle layers
- [ ] Validate end-to-end accuracy on full test set

---

## APPENDIX: Quick Reference

### Formulas Sheet

```
SYMMETRIC QUANTIZATION:
  scale = absmax / 127
  zero_point = 0
  q_val = round(fp_val / scale)
  fp_val ≈ q_val * scale

ASYMMETRIC QUANTIZATION:
  scale = (max_val - min_val) / 255
  zero_point = round(-min_val / scale)
  q_val = round(fp_val / scale - min_val / scale)
       = round((fp_val - min_val) / scale)
  fp_val ≈ q_val * scale + min_val
       ≈ (q_val - zero_point) * scale

DEQUANTIZATION (both):
  fp_val ≈ (q_val - zero_point) * scale

INT8 INFERENCE PATTERN:
  1. q_input = quantize(input)
  2. q_output = matmul(q_weight, q_input)  [INT32 accumulation]
  3. q_output = rescale(q_output)  [M = Sq * Sa / So]
  4. output = dequantize(q_output)
```

### Typical Accuracy Impact

| Precision | Model Size | Speed | Accuracy Loss |
|-----------|-----------|-------|----------------|
| FP32 | 1.0x | 1.0x | baseline |
| FP16 | 0.5x | 1.5-2x | < 0.5% |
| INT8 (PTQ) | 0.25x | 2-4x | 0.5-2% |
| INT8 (QAT) | 0.25x | 2-4x | < 0.5% |
| INT4 (GPTQ) | 0.125x | 4-8x | 1-3% |

---

**Document Generated:** 2026-07-06
**Source:** Consolidated from Brenndoerfer, PyTorch, ONNX Runtime, HuggingFace, GitHub implementations
