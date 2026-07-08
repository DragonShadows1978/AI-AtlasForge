# AQLM Integration Guide - Technical Implementation

**Version:** 1.0  
**Last Updated:** 2026-07-06  
**Status:** Production-Ready

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [vLLM Integration](#vllm-integration)
3. [HuggingFace Transformers](#huggingface-transformers)
4. [Model Selection & Loading](#model-selection--loading)
5. [Performance Tuning](#performance-tuning)
6. [Troubleshooting](#troubleshooting)

---

## Quick Start

### Installation Matrix

```bash
# vLLM (Production)
pip install vllm

# HuggingFace Transformers (Development)
pip install aqlm[cuda]  # or [cpu] for CPU-only

# Alternative: Rust/WASM (Edge)
# See AQLM.rs section below
```

### Minimum Working Example

**vLLM** (10 lines):
```python
from vllm import LLM, SamplingParams

llm = LLM("ISTA-DASLab/Llama-2-7b-AQLM-2Bit-1x16-hf")
outputs = llm.generate(
    ["The future of AI is"],
    SamplingParams(temperature=0.7, max_tokens=100)
)
print(outputs[0].outputs[0].text)
```

**HuggingFace Transformers** (10 lines):
```python
from transformers import AutoTokenizer, AutoModelForCausalLM

tokenizer = AutoTokenizer.from_pretrained("ISTA-DASLab/Llama-2-7b-AQLM-2Bit-1x16-hf")
model = AutoModelForCausalLM.from_pretrained(
    "ISTA-DASLab/Llama-2-7b-AQLM-2Bit-1x16-hf",
    trust_remote_code=True
).cuda()

inputs = tokenizer("The future of AI is", return_tensors="pt")
outputs = model.generate(**inputs, max_new_tokens=100)
print(tokenizer.decode(outputs[0]))
```

---

## vLLM Integration

### Setup

```bash
# Install vLLM
pip install vllm

# Verify installation
python -c "from vllm import LLM; print('vLLM ready')"
```

### Basic Usage

```python
from vllm import LLM, SamplingParams

# Load AQLM-quantized model
model_name = "ISTA-DASLab/Llama-2-7b-AQLM-2Bit-1x16-hf"
llm = LLM(model=model_name)

# Define sampling parameters
sampling_params = SamplingParams(
    temperature=0.7,
    top_p=0.95,
    max_tokens=256
)

# Generate completions
prompts = [
    "What is the capital of France?",
    "Explain quantum computing:",
]
outputs = llm.generate(prompts, sampling_params)

# Print results
for output in outputs:
    print(f"Prompt: {output.prompt}")
    print(f"Generated: {output.outputs[0].text}\n")
```

### Advanced Features

#### Tensor Parallelism (Multi-GPU)

```python
from vllm import LLM

# Distribute across 2 GPUs
llm = LLM(
    model="ISTA-DASLab/Llama-70b-AQLM-2Bit-1x16-hf",
    tensor_parallel_size=2,  # Use 2 GPUs
    dtype="auto"
)

# Same API, automatically parallelized
outputs = llm.generate(prompts, sampling_params)
```

#### GPU Memory Optimization

```python
llm = LLM(
    model="ISTA-DASLab/Llama-70b-AQLM-2Bit-1x16-hf",
    gpu_memory_utilization=0.9,  # Use 90% of GPU VRAM
    max_model_len=4096,           # Limit context length
    tensor_parallel_size=2        # Distribute if needed
)
```

#### Batch Processing

```python
# Efficient batch inference
prompts = [f"Question {i}: ..." for i in range(100)]
sampling_params = SamplingParams(
    temperature=0.7,
    max_tokens=128
)

outputs = llm.generate(prompts, sampling_params)
# vLLM automatically batches and optimizes throughput
```

### OpenAI-Compatible Server

```bash
# Start inference server
python -m vllm.entrypoints.openai.api_server \
    --model ISTA-DASLab/Llama-2-7b-AQLM-2Bit-1x16-hf \
    --tensor-parallel-size 1 \
    --port 8000

# In another terminal, use like OpenAI API
python << 'EOF'
import openai

openai.api_base = "http://localhost:8000/v1"
openai.api_key = "token-abc123"

response = openai.ChatCompletion.create(
    model="ISTA-DASLab/Llama-2-7b-AQLM-2Bit-1x16-hf",
    messages=[{"role": "user", "content": "Hello!"}]
)
print(response.choices[0].message.content)
EOF
```

### Performance Tuning for vLLM

| Setting | Recommendation | Trade-off |
|---------|---|---|
| `gpu_memory_utilization` | 0.85-0.95 | Higher = more throughput, lower headroom |
| `max_model_len` | 4096 (default) | Higher context requires more memory |
| `tensor_parallel_size` | 1-4 (per GPU) | More = lower per-GPU latency |
| `dtype` | "auto" | Respects model's native precision |

---

## HuggingFace Transformers

### Setup

```bash
# Install AQLM with CUDA support
pip install aqlm[cuda]

# For CPU-only:
pip install aqlm

# Verify
python -c "import aqlm; print(f'AQLM version: {aqlm.__version__}')"
```

### Basic Loading

```python
from transformers import AutoTokenizer, AutoModelForCausalLM

# Model and tokenizer
model_id = "ISTA-DASLab/Llama-2-7b-AQLM-2Bit-1x16-hf"

# Load tokenizer
tokenizer = AutoTokenizer.from_pretrained(model_id)

# Load model (AQLM quantized)
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    trust_remote_code=True,  # Required for custom quantization code
    torch_dtype="auto",      # Respects model's precision
    device_map="auto"        # Auto GPU/CPU placement
)

# Generate text
prompt = "The future of AI is"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=100)
print(tokenizer.decode(outputs[0]))
```

### Fine-tuning with LoRA

```python
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import get_peft_model, LoraConfig, TaskType

# Load base quantized model
model_id = "ISTA-DASLab/Llama-2-7b-AQLM-2Bit-1x16-hf"
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    trust_remote_code=True,
    device_map="auto"
)
tokenizer = AutoTokenizer.from_pretrained(model_id)

# Configure LoRA
lora_config = LoraConfig(
    task_type=TaskType.CAUSAL_LM,
    r=8,
    lora_alpha=32,
    lora_dropout=0.1,
    bias="none"
)

# Apply LoRA
model = get_peft_model(model, lora_config)
model.print_trainable_parameters()

# Training code (using transformers Trainer)
from transformers import Trainer, TrainingArguments

training_args = TrainingArguments(
    output_dir="./aqlm_finetuned",
    per_device_train_batch_size=4,
    per_device_eval_batch_size=4,
    num_train_epochs=3,
    logging_steps=10,
    save_steps=100,
)

trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=train_dataset,
    eval_dataset=eval_dataset,
)

trainer.train()
```

### CPU Inference

```python
from transformers import AutoModelForCausalLM, AutoTokenizer

model_id = "ISTA-DASLab/Llama-2-7b-AQLM-2Bit-1x16-hf"
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    trust_remote_code=True,
    device_map="cpu",  # Force CPU
    torch_dtype="float32"  # CPU uses float32
)
tokenizer = AutoTokenizer.from_pretrained(model_id)

# Generate (will be slower than GPU)
inputs = tokenizer("Hello", return_tensors="pt")
outputs = model.generate(**inputs, max_new_tokens=50)
print(tokenizer.decode(outputs[0]))
```

### Quantization (Custom Models)

```python
# This is computationally expensive - use pre-quantized models instead
# For reference only:

from aqlm import QuantizationConfig, quantize_model
from transformers import AutoModelForCausalLM

model_id = "meta-llama/Llama-2-7b"
model = AutoModelForCausalLM.from_pretrained(model_id)

# Configure AQLM
quant_config = QuantizationConfig(
    bits=2,
    group_size=64,
    out_features=None
)

# Quantize (expensive operation)
quantized_model = quantize_model(model, quant_config)

# Save
quantized_model.save_pretrained("llama-2-7b-aqlm-2bit")
```

---

## Model Selection & Loading

### Available Pre-quantized Models

#### Llama-2 Family

```python
from transformers import AutoModelForCausalLM

# 7B Model (2-bit)
model = AutoModelForCausalLM.from_pretrained(
    "ISTA-DASLab/Llama-2-7b-AQLM-2Bit-1x16-hf",
    trust_remote_code=True,
    device_map="auto"
)

# 7B Model (1-bit, newer)
model = AutoModelForCausalLM.from_pretrained(
    "ISTA-DASLab/Llama-2-7b-AQLM-1Bit-1x8-hf",
    trust_remote_code=True,
    device_map="auto"
)

# 70B Model (2-bit)
model = AutoModelForCausalLM.from_pretrained(
    "ISTA-DASLab/Llama-2-70b-AQLM-2Bit-1x16-hf",
    trust_remote_code=True,
    device_map="auto"
)
```

#### Llama-3 Family

```python
# Llama-3-70B with AQLM-PV tuning
model = AutoModelForCausalLM.from_pretrained(
    "ISTA-DASLab/Llama-3-70b-AQLM-PV-2bit",
    trust_remote_code=True,
    device_map="auto"
)
```

#### Mixtral Family

```python
# Mixtral-8x7B (AQLM)
model = AutoModelForCausalLM.from_pretrained(
    "ISTA-DASLab/Mixtral-8x7b-AQLM-2Bit-1x16-hf",
    trust_remote_code=True,
    device_map="auto"
)
```

### Model Compatibility Matrix

| Model | Size | Bits | Memory (GPU) | Framework | Status |
|-------|------|------|--------------|-----------|--------|
| Llama-2-7B | 7B | 2-bit | 2-3GB | vLLM, HF | Available |
| Llama-2-7B | 7B | 1-bit | 1-2GB | vLLM, HF | Available (Apr 2025) |
| Llama-2-70B | 70B | 2-bit | 22GB | vLLM, HF | Available |
| Llama-3-70B | 70B | 2-bit | 22GB | vLLM, HF | Available (PV) |
| Mixtral-8x7B | 56B | 2-bit | 14GB | vLLM, HF | Available |

---

## Performance Tuning

### GPU Memory Optimization

#### Monitor Memory Usage

```python
import torch
from transformers import AutoModelForCausalLM

model = AutoModelForCausalLM.from_pretrained(
    "ISTA-DASLab/Llama-2-7b-AQLM-2Bit-1x16-hf",
    device_map="auto"
)

# Check memory
print(f"GPU Memory Used: {torch.cuda.memory_allocated() / 1e9:.2f}GB")
print(f"GPU Max Allocated: {torch.cuda.max_memory_allocated() / 1e9:.2f}GB")
```

#### 8-bit KV Cache Quantization (Additional Optimization)

```python
# Further reduce memory by quantizing KV cache
# Combine with AQLM for maximum compression
model = AutoModelForCausalLM.from_pretrained(
    "ISTA-DASLab/Llama-2-7b-AQLM-2Bit-1x16-hf",
    load_in_8bit=True,  # Quantize KV cache
    trust_remote_code=True,
    device_map="auto"
)
```

### Inference Throughput Optimization

#### Batch Size Tuning

```python
# For HuggingFace Transformers
prompts = [f"Question {i}?" for i in range(32)]  # Batch of 32

inputs = tokenizer(
    prompts,
    return_tensors="pt",
    padding=True,
    truncation=True,
    max_length=512
).to(model.device)

outputs = model.generate(
    **inputs,
    max_new_tokens=128,
    do_sample=True,
    temperature=0.7
)

# vLLM handles batching automatically
```

#### Reduce Context Length

```python
# Shorter context = faster inference
model.config.max_position_embeddings = 2048  # Default: 4096

# Or in generation
outputs = model.generate(
    inputs,
    max_new_tokens=128,
    max_length=2048  # Limit total length
)
```

### Multi-GPU Setup

#### vLLM Tensor Parallelism

```bash
# 2 GPUs
python -m vllm.entrypoints.openai.api_server \
    --model ISTA-DASLab/Llama-70b-AQLM-2Bit-1x16-hf \
    --tensor-parallel-size 2

# 4 GPUs
python -m vllm.entrypoints.openai.api_server \
    --model ISTA-DASLab/Llama-70b-AQLM-2Bit-1x16-hf \
    --tensor-parallel-size 4
```

#### HuggingFace Distributed Setup

```python
from transformers import AutoModelForCausalLM
import torch.distributed as dist

# Enable distributed mode
dist.init_process_group("nccl")

model = AutoModelForCausalLM.from_pretrained(
    "ISTA-DASLab/Llama-70b-AQLM-2Bit-1x16-hf",
    device_map="auto",
    trust_remote_code=True
)

# Model is automatically sharded across processes
```

---

## Troubleshooting

### Common Issues & Solutions

#### Issue 1: Out of Memory (OOM)

```python
# Solution: Reduce model size or batch size

# Option A: Use smaller model
model_name = "ISTA-DASLab/Llama-2-7b-AQLM-2Bit-1x16-hf"  # 2-3GB

# Option B: Reduce batch size
batch_size = 1  # Start with 1

# Option C: Reduce max context length
max_length = 1024

# Option D: Use 1-bit quantization for even smaller footprint
model_name = "ISTA-DASLab/Llama-2-7b-AQLM-1Bit-1x8-hf"
```

#### Issue 2: `trust_remote_code=True` Warning

```python
# This is normal for AQLM - the quantization kernels require it
from transformers import AutoModelForCausalLM

model = AutoModelForCausalLM.from_pretrained(
    "ISTA-DASLab/Llama-2-7b-AQLM-2Bit-1x16-hf",
    trust_remote_code=True  # Required for AQLM kernels
)
```

#### Issue 3: Slow Inference on CPU

```python
# AQLM kernels are optimized for GPU
# If on CPU, expect 10-100x slower than GPU

# Solution: Use GPU-accelerated inference via vLLM
# or wait for AQLM.rs (Rust implementation) if CPU is required
```

#### Issue 4: Python Version Mismatch

```bash
# AQLM requires Python 3.10+
python --version  # Should be 3.10, 3.11, or 3.12

# If using older Python
conda create -n aqlm python=3.11
conda activate aqlm
pip install aqlm[cuda]
```

#### Issue 5: CUDA/Triton Issues (Windows)

```bash
# Triton may not be essential for all AQLM configs
# Workaround: Use CPU mode or containers

# Option A: Docker (Linux)
docker run --gpus all -it nvcr.io/nvidia/cuda:12.1.0-devel-ubuntu22.04

# Option B: WSL2 on Windows
wsl --install -d Ubuntu-22.04
```

### Performance Diagnostics

```python
import torch
import time
from transformers import AutoModelForCausalLM, AutoTokenizer

# Load model
model_id = "ISTA-DASLab/Llama-2-7b-AQLM-2Bit-1x16-hf"
model = AutoModelForCausalLM.from_pretrained(model_id, trust_remote_code=True, device_map="auto")
tokenizer = AutoTokenizer.from_pretrained(model_id)

# Warm up
for _ in range(3):
    inputs = tokenizer("test", return_tensors="pt").to(model.device)
    _ = model.generate(**inputs, max_new_tokens=10)

# Benchmark
prompt = "The future of artificial intelligence"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

start = time.time()
with torch.no_grad():
    outputs = model.generate(**inputs, max_new_tokens=100)
elapsed = time.time() - start

tokens_generated = outputs.shape[1] - inputs.input_ids.shape[1]
tokens_per_sec = tokens_generated / elapsed

print(f"Time: {elapsed:.2f}s")
print(f"Tokens/sec: {tokens_per_sec:.2f}")
print(f"Memory: {torch.cuda.memory_allocated() / 1e9:.2f}GB")
```

---

## Advanced Deployment

### Production Checklist

- [ ] Select appropriate model size (7B, 70B, etc.)
- [ ] Choose bit-width (1-bit, 2-bit, 3-bit, 4-bit)
- [ ] Test on target hardware (GPU type/memory)
- [ ] Benchmark baseline performance
- [ ] Set up monitoring (memory, throughput, latency)
- [ ] Implement rate limiting / queue management
- [ ] Add logging and error handling
- [ ] Test failure recovery
- [ ] Document deployment procedure

### Recommended Hardware

| Use Case | GPU | VRAM | Notes |
|----------|-----|------|-------|
| 7B Model | RTX 3090 | 24GB | Fits 2-3 concurrent inferences |
| 7B Model | A100 | 80GB | Fits 10+ concurrent inferences |
| 70B Model | L40S | 48GB | Single inference only |
| 70B Model | A100 80GB | 80GB | Best cost/perf for 70B |
| 70B Model | H100 | 80GB | Fastest inference (~2x A100) |

---

## References

- **Official GitHub**: https://github.com/Vahe1994/AQLM
- **HuggingFace Docs**: https://huggingface.co/docs/transformers/main/en/quantization/aqlm
- **vLLM Docs**: https://docs.vllm.ai/
- **AQLM Paper**: https://arxiv.org/pdf/2401.06118.pdf
- **PV-Tuning Paper**: https://arxiv.org/abs/2405.14852

