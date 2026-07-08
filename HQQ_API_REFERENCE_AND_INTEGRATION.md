# HQQ (Half-Quadratic Quantization) - Complete API Reference & Integration Patterns

## Document Overview

This document provides:
1. **Complete API Reference** - All HQQ function signatures and parameters
2. **Integration Patterns** - How to integrate HQQ with different frameworks
3. **Configuration Guide** - Quantization configuration options
4. **Troubleshooting** - Common issues and solutions
5. **Performance Tips** - Optimization strategies

**Target Audience**: ML Engineers, Systems Designers, Quantization Specialists
**Last Updated**: 2026-07-06

---

## Part 1: Core API Reference

### Module: `hqq.core`

The main module containing HQQLinear and core quantization functions.

#### Class: HQQLinear

```python
class HQQLinear(torch.nn.Module):
    """
    Quantized linear layer using Half-Quadratic Quantization.
    
    This layer replaces standard nn.Linear with a quantized version
    that maintains numerical precision while reducing memory footprint
    and improving inference speed.
    
    Attributes:
        weight_data (torch.Tensor): Packed quantized weights (bit-compressed)
        scales (torch.Tensor): Per-group quantization scales
        zeros (torch.Tensor): Per-group zero-point offsets
        bias (torch.Tensor or None): Layer bias (not quantized)
        quant_config (dict): Configuration dictionary used for quantization
    """
    
    def __init__(
        self,
        module: torch.nn.Linear,
        quant_config: dict,
        compute_dtype: torch.dtype = torch.float32,
        **kwargs
    ):
        """
        Initialize HQQLinear from a standard Linear layer.
        
        Args:
            module (nn.Linear): 
                The linear layer to quantize. Must be nn.Linear.
            
            quant_config (dict): 
                Quantization configuration with keys:
                - 'nbits' (int): Bit-width [1, 2, 4, 8]
                - 'group_size' (int): Weight grouping size (e.g., 64)
                - 'axis' (int): Quantization axis (0=rows, 1=columns)
                - 'offload_meta' (bool): Move metadata to CPU if True
                - 'compute_dtype' (torch.dtype): Computation dtype
                - 'pack_now' (bool): Pack weights immediately
                - 'inplace' (bool): Modify module in-place
            
            compute_dtype (torch.dtype): 
                Data type for computations (default: torch.float32)
            
            **kwargs: 
                Additional arguments (reserved for future use)
        
        Returns:
            HQQLinear instance ready for inference or fine-tuning
        
        Example:
            >>> import torch.nn as nn
            >>> linear = nn.Linear(768, 3072)
            >>> hqq_linear = HQQLinear(
            ...     linear,
            ...     quant_config={
            ...         'nbits': 4,
            ...         'group_size': 64,
            ...         'axis': 0
            ...     }
            ... )
        """
        pass
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Forward pass with automatic dequantization.
        
        The forward pass:
        1. Dequantizes weights from packed representation
        2. Performs standard linear computation
        3. Returns full-precision output
        
        Args:
            x (torch.Tensor): 
                Input tensor of shape (batch_size, in_features)
        
        Returns:
            torch.Tensor: 
                Output tensor of shape (batch_size, out_features)
        
        Notes:
            - Fully differentiable (gradients flow through dequantization)
            - Backward pass updates quantized weights via scale/zero_point
            - Performance: Slightly slower than standard Linear due to 
              dequantization overhead, but faster overall due to smaller memory
        
        Example:
            >>> x = torch.randn(32, 768)
            >>> y = hqq_linear(x)
            >>> print(y.shape)  # torch.Size([32, 3072])
        """
        pass
    
    @staticmethod
    def quantize_module(
        module: torch.nn.Module,
        quant_config: dict = None,
        compute_dtype: torch.dtype = torch.float32
    ) -> 'HQQLinear':
        """
        Quantize a linear layer (static method).
        
        This is a convenience method for in-place quantization.
        
        Args:
            module (nn.Linear): Linear layer to quantize
            quant_config (dict): Quantization config
            compute_dtype (torch.dtype): Computation dtype
        
        Returns:
            HQQLinear: Quantized layer
        
        Example:
            >>> linear = nn.Linear(768, 768)
            >>> quantized = HQQLinear.quantize_module(
            ...     linear,
            ...     {'nbits': 4, 'group_size': 64}
            ... )
        """
        return HQQLinear(module, quant_config, compute_dtype)
    
    def save_pretrained(self, path: str):
        """
        Save quantized layer to disk.
        
        Args:
            path (str): Directory path to save
        
        Saves:
            - weight_data: Packed quantized weights
            - scales: Per-group scales
            - zeros: Per-group zero-points
            - bias: Layer bias
            - config: Quantization configuration
        """
        pass
    
    @staticmethod
    def from_pretrained(path: str) -> 'HQQLinear':
        """
        Load quantized layer from disk.
        
        Args:
            path (str): Directory path containing saved layer
        
        Returns:
            HQQLinear: Loaded quantized layer
        
        Example:
            >>> hqq_layer = HQQLinear.from_pretrained('./hqq_layer')
        """
        pass
    
    def to(self, device_or_dtype):
        """
        Move layer to device or convert dtype.
        
        Args:
            device_or_dtype: torch.device or torch.dtype
        
        Returns:
            self (for chaining)
        
        Example:
            >>> hqq_layer = hqq_layer.to('cuda:0')
            >>> hqq_layer = hqq_layer.to(torch.float32)
        """
        pass
```

#### Function: hqq_global_conf.initialize()

```python
def hqq_global_conf.initialize(
    nbits: int = 4,
    group_size: int = 64,
    axis: int = 0,
    offload_meta: bool = False,
    kernel: Optional[str] = None,
    **kwargs
):
    """
    Set global HQQ quantization defaults.
    
    Args:
        nbits (int): Default bit-width [1, 2, 4, 8]
        group_size (int): Default group size
        axis (int): Quantization axis
        offload_meta (bool): Offload metadata to CPU
        kernel (str): Custom kernel selection
    
    Notes:
        - These defaults are used when creating HQQLinear without explicit config
        - Can be overridden per-layer with explicit quant_config
    
    Example:
        >>> from hqq.core import hqq_global_conf
        >>> hqq_global_conf.initialize(nbits=4, group_size=64)
    """
    pass
```

### Module: `hqq.backends`

Hardware-specific backends for optimized quantization/dequantization.

#### Available Backends

```python
# CUDA kernel backend (requires CUDA-capable GPU)
from hqq.backends.cuda import CUDABackend

# PyTorch native backend (CPU/GPU fallback)
from hqq.backends.pytorch import PyTorchBackend

# ONNX backend (for ONNX Runtime deployment)
from hqq.backends.onnx import ONNXBackend
```

---

## Part 2: Quantization Configuration Reference

### Configuration Dictionary Format

```python
quant_config = {
    # ===== REQUIRED PARAMETERS =====
    
    'nbits': int,
    # Quantization bit-width
    # Options: 1, 2, 4, 8
    # Default: 4
    # Recommendation: Use 4-bit as sweet spot
    
    'group_size': int,
    # Quantization granularity (weights per group)
    # Options: 32, 64, 128, 256
    # Default: 64
    # Impact: Smaller = finer granularity = better accuracy, slower
    #         Larger = coarser granularity = faster, potential accuracy drop
    
    'axis': int,
    # Quantization axis
    # 0: Per-row (default for weights)
    # 1: Per-column
    # Default: 0
    
    # ===== OPTIONAL PARAMETERS =====
    
    'offload_meta': bool,
    # Move quantization metadata (scales, zeros) to CPU
    # Useful if GPU memory is extremely tight
    # Trade-off: Slower inference due to PCIe transfers
    # Default: False
    
    'compute_dtype': torch.dtype,
    # Computation data type
    # Default: torch.float32
    # Options: torch.float32, torch.float16, torch.bfloat16
    
    'pack_now': bool,
    # Pack weights immediately upon initialization
    # If False, packing deferred until first forward pass
    # Default: True
    
    'inplace': bool,
    # Modify module in-place (memory efficient)
    # Default: False
    
    'kernel': Optional[str],
    # Custom kernel selection
    # Options: None, 'cuda', 'pytorch', 'onnx'
    # Default: None (auto-detect best available)
}
```

### Configuration Presets

```python
# 8-bit configuration (minimal accuracy loss, modest speed gain)
CONFIG_8BIT = {
    'nbits': 8,
    'group_size': 128,
    'axis': 0,
    'offload_meta': False
}

# 4-bit configuration (recommended, good balance)
CONFIG_4BIT = {
    'nbits': 4,
    'group_size': 64,
    'axis': 0,
    'offload_meta': False
}

# 2-bit configuration (aggressive, research-focused)
CONFIG_2BIT = {
    'nbits': 2,
    'group_size': 32,
    'axis': 0,
    'offload_meta': False
}

# Mobile/edge configuration (extremely tight memory)
CONFIG_MOBILE = {
    'nbits': 4,
    'group_size': 32,
    'axis': 0,
    'offload_meta': True
}

# Accuracy-critical configuration (minimal speed trade-off)
CONFIG_ACCURATE = {
    'nbits': 8,
    'group_size': 256,
    'axis': 0,
    'offload_meta': False
}
```

---

## Part 3: Integration Patterns

### Pattern 1: Hugging Face Transformers

```python
from transformers import AutoModelForCausalLM
from hqq.core import HQQLinear
import torch.nn as nn

def quantize_hf_model(model_name: str, nbits: int = 4):
    """Quantize Hugging Face model pattern"""
    model = AutoModelForCausalLM.from_pretrained(model_name, device_map="auto")
    
    for name, module in model.named_modules():
        if isinstance(module, nn.Linear):
            # Skip output layer
            if 'lm_head' in name:
                continue
            
            parent = model
            for attr in name.rsplit('.', 1)[0].split('.'):
                parent = getattr(parent, attr)
            
            layer_name = name.rsplit('.', 1)[1]
            quantized = HQQLinear(module, {'nbits': nbits, 'group_size': 64})
            setattr(parent, layer_name, quantized)
    
    return model
```

### Pattern 2: Custom PyTorch Models

```python
import torch.nn as nn
from hqq.core import HQQLinear

class MyModel(nn.Module):
    def __init__(self):
        super().__init__()
        # Use HQQLinear directly
        self.layer1 = HQQLinear(nn.Linear(768, 768), {'nbits': 4, 'group_size': 64})
        self.layer2 = HQQLinear(nn.Linear(768, 768), {'nbits': 4, 'group_size': 64})
    
    def forward(self, x):
        x = self.layer1(x)
        x = self.layer2(x)
        return x
```

### Pattern 3: Mixed-Precision Strategy

```python
def apply_mixed_precision(model, layer_config: dict):
    """
    Apply different quantization levels to different layer types
    
    Example layer_config:
    {
        'attention': {'nbits': 8, 'group_size': 128},
        'mlp': {'nbits': 4, 'group_size': 64},
        'embedding': {'nbits': 8, 'group_size': 256}
    }
    """
    for name, module in model.named_modules():
        if not isinstance(module, nn.Linear):
            continue
        
        nbits = 4
        group_size = 64
        
        for pattern, config in layer_config.items():
            if pattern in name:
                nbits = config['nbits']
                group_size = config['group_size']
                break
        
        # Apply quantization
        parent_name, layer_name = name.rsplit('.', 1)
        parent = model
        for attr in parent_name.split('.'):
            parent = getattr(parent, attr)
        
        quantized = HQQLinear(
            module,
            {'nbits': nbits, 'group_size': group_size}
        )
        setattr(parent, layer_name, quantized)
```

### Pattern 4: Inference Server Integration

```python
class HQQInferenceServer:
    def __init__(self, model_name: str, device: str = 'cuda'):
        self.device = device
        self.model = self._load_quantized_model(model_name)
        self.model.eval()
    
    def _load_quantized_model(self, model_name):
        # Load pre-quantized model from Hugging Face
        # e.g., mobiusml/Llama-2-7b-hqq
        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            device_map=self.device
        )
        return model
    
    def generate(self, prompt: str, max_tokens: int = 100):
        tokenizer = AutoTokenizer.from_pretrained(
            model_name.rsplit('-', 1)[0]
        )
        inputs = tokenizer(prompt, return_tensors="pt").to(self.device)
        
        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                do_sample=True,
                top_p=0.95
            )
        
        return tokenizer.decode(outputs[0], skip_special_tokens=True)
```

---

## Part 4: Practical Troubleshooting Guide

### Issue 1: OutOfMemoryError During Quantization

**Symptoms**: CUDA out of memory when quantizing large models

**Solutions**:
```python
# Solution A: Use smaller batch size
quant_config = {
    'nbits': 4,
    'group_size': 32,  # Smaller group = less memory needed
    'offload_meta': True  # Move metadata to CPU
}

# Solution B: Quantize layer-by-layer instead of all at once
for name, module in model.named_modules():
    if isinstance(module, nn.Linear):
        # Quantize one layer, then move to CPU
        quantized = HQQLinear(module, quant_config)
        # ... insert back into model ...
        torch.cuda.empty_cache()

# Solution C: Use mixed precision (8-bit for large layers)
if 'attention' in name:
    config = {'nbits': 8, 'group_size': 128}
else:
    config = {'nbits': 4, 'group_size': 64}
```

### Issue 2: Accuracy Drop Too High

**Symptoms**: Model perplexity increases significantly after quantization

**Solutions**:
```python
# Solution A: Use higher bit-width
config = {'nbits': 8, 'group_size': 64}  # Instead of 4-bit

# Solution B: Use finer quantization granularity
config = {'nbits': 4, 'group_size': 32}  # Smaller groups

# Solution C: Fine-tune with QAT
# Use train_quantized_model() from earlier examples

# Solution D: Skip quantizing critical layers
if 'lm_head' in name or 'embedding' in name:
    continue  # Don't quantize
```

### Issue 3: Slow Inference

**Symptoms**: Dequantization overhead causing slow inference

**Solutions**:
```python
# Solution A: Move to GPU for faster dequantization
model = model.to('cuda')

# Solution B: Use larger group size (trade accuracy for speed)
config = {'nbits': 4, 'group_size': 128}  # Larger = faster

# Solution C: Use inference frameworks optimized for quantized models
# - VLLM with HQQ support
# - TensorRT with HQQ backend
# - ONNXRuntime with HQQ quantization
```

### Issue 4: Import/Installation Errors

```python
# Install from source if PyPI package outdated
git clone https://github.com/mobiusml/HQQ.git
cd HQQ
pip install -e .

# Check installation
import hqq
print(hqq.__version__)
from hqq.core import HQQLinear
```

---

## Part 5: Performance Optimization Tips

### Tip 1: Batch Size Optimization

```python
# Different batch sizes have different performance characteristics
batch_sizes = [1, 4, 8, 16, 32]
for bs in batch_sizes:
    x = torch.randn(bs, 768).to('cuda')
    start = time.time()
    y = model(x)
    elapsed = time.time() - start
    print(f"Batch {bs}: {elapsed:.3f}s")
```

### Tip 2: Memory Monitoring

```python
import torch

def monitor_memory(model, input_size=(32, 768)):
    x = torch.randn(*input_size).to('cuda')
    
    torch.cuda.reset_peak_memory_stats()
    torch.cuda.empty_cache()
    
    start_mem = torch.cuda.memory_allocated()
    y = model(x)
    peak_mem = torch.cuda.max_memory_allocated()
    
    used_mem = peak_mem - start_mem
    print(f"Memory used: {used_mem / 1024**3:.2f} GB")
```

### Tip 3: Combining HQQ with Other Optimizations

```python
import torch
from torch.optim.optimizer import Optimizer

# HQQ works well with:
# 1. Mixed precision (torch.autocast)
with torch.autocast('cuda'):
    y = model(x)

# 2. Gradient checkpointing (for training)
model.gradient_checkpointing_enable()

# 3. Compile (PyTorch 2.0+)
compiled_model = torch.compile(model)

# 4. Quantized inference backends (TensorRT, etc.)
```

---

## Part 6: Pre-Quantized Models & Resources

### Available Pre-Quantized Models

**Hugging Face Model Hub:**
```
mobiusml/hqq-7b-0        # LLaMA-2-7B HQQ 4-bit
mobiusml/hqq-13b-0       # LLaMA-2-13B HQQ 4-bit
mobiusml/hqq-70b-0       # LLaMA-2-70B HQQ 4-bit
mobiusml/Mistral-7B-hqq  # Mistral-7B HQQ 4-bit
```

### Loading Pre-Quantized Models

```python
from transformers import AutoModelForCausalLM

# These models are pre-quantized and ready to use
model = AutoModelForCausalLM.from_pretrained(
    "mobiusml/hqq-7b-0",
    device_map="auto"
)

# No additional quantization needed
output = model.generate("Hello world", max_length=100)
```

---

## Summary: Quick Reference Table

| Task | Function/Class | Key Parameters |
|------|----------------|-----------------|
| Quantize Linear Layer | `HQQLinear()` | `nbits`, `group_size` |
| Quantize Model | `HQQLinear.quantize_module()` | `quant_config` |
| Set Defaults | `hqq_global_conf.initialize()` | `nbits`, `group_size` |
| Save/Load | `save_pretrained()`, `from_pretrained()` | `path` |
| Fine-tune | Standard PyTorch training | `learning_rate`, `epochs` |
| Benchmark | Custom timing code | Input size, batch size |

---

**Document Status**: Complete API Reference  
**Last Review**: 2026-07-06  
**Maintenance**: Update when HQQ releases new versions or APIs change
