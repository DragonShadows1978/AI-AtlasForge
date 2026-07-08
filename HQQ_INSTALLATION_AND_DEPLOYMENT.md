# HQQ Installation, Setup, and Deployment Guide

## Quick Start

### Minimum Installation (5 minutes)

```bash
# 1. Create environment
python -m venv hqq_env
source hqq_env/bin/activate  # On Windows: hqq_env\Scripts\activate

# 2. Install PyTorch (CUDA 11.8 example)
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118

# 3. Install HQQ
pip install hqq

# 4. Verify installation
python -c "from hqq.core import HQQLinear; print('HQQ installed successfully')"
```

---

## Full Installation Guide

### Prerequisites

- **Python**: 3.8+ (3.10+ recommended)
- **PyTorch**: 1.9+ (2.0+ for best performance)
- **CUDA**: 11.0+ (for GPU acceleration, optional but recommended)
- **Memory**: 24GB+ for quantizing large models

### Step-by-Step Installation

#### Option 1: From PyPI (Recommended for Most Users)

```bash
# Create virtual environment
python3.10 -m venv hqq_env
source hqq_env/bin/activate

# Update pip
pip install --upgrade pip setuptools wheel

# Install PyTorch with CUDA support
# Check your CUDA version: nvidia-smi
# For CUDA 11.8:
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118

# For CPU only (slower, for testing):
pip install torch torchvision torchaudio

# Install HQQ and dependencies
pip install hqq

# Optional: Install Transformers for model loading
pip install transformers tokenizers

# Optional: Install additional utilities
pip install numpy matplotlib scikit-learn jupyter
```

#### Option 2: From Source (For Development)

```bash
# Clone repository
git clone https://github.com/mobiusml/HQQ.git
cd HQQ

# Create virtual environment
python3.10 -m venv venv
source venv/bin/activate

# Install PyTorch
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118

# Install in development mode
pip install -e .

# Run tests to verify
pytest tests/
```

#### Option 3: Docker Installation (Containerized)

```dockerfile
# Dockerfile.hqq
FROM pytorch/pytorch:2.0-cuda11.8-runtime-ubuntu22.04

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    python3-pip

# Install HQQ
RUN pip install hqq transformers datasets accelerate

# Copy your code
COPY . /app

CMD ["python", "app.py"]
```

Build and run:
```bash
docker build -f Dockerfile.hqq -t hqq_env .
docker run --gpus all -it hqq_env
```

---

## Installation Verification

### Test Basic Functionality

```python
# test_hqq_installation.py
import torch
import torch.nn as nn
from hqq.core import HQQLinear

def test_installation():
    """Verify HQQ installation works correctly"""
    
    # Test 1: Can we import?
    print("✓ HQQ imported successfully")
    
    # Test 2: Can we create a quantized layer?
    linear = nn.Linear(768, 3072)
    try:
        hqq_linear = HQQLinear(
            linear,
            quant_config={'nbits': 4, 'group_size': 64}
        )
        print("✓ HQQLinear layer created successfully")
    except Exception as e:
        print(f"✗ Failed to create HQQLinear: {e}")
        return False
    
    # Test 3: Can we run inference?
    try:
        x = torch.randn(32, 768)
        y = hqq_linear(x)
        print(f"✓ Inference successful, output shape: {y.shape}")
    except Exception as e:
        print(f"✗ Inference failed: {e}")
        return False
    
    # Test 4: Check memory usage
    params = sum(p.numel() for p in hqq_linear.parameters())
    print(f"✓ Layer parameters: {params:,}")
    
    print("\n✓✓✓ All tests passed! HQQ is ready to use.")
    return True

if __name__ == "__main__":
    test_installation()
```

Run it:
```bash
python test_hqq_installation.py
```

---

## Environment Configuration

### Virtual Environment Setup

```bash
# Create with specific Python version
python3.10 -m venv hqq_env_310

# Activate
source hqq_env_310/bin/activate  # Linux/Mac
# or
hqq_env_310\Scripts\activate  # Windows

# Verify Python version
python --version  # Should be 3.10+

# Deactivate when done
deactivate
```

### GPU Memory Configuration

```python
# Set GPU memory growth to avoid OOM errors
import torch

# Option 1: Explicit memory fraction
torch.cuda.set_per_process_memory_fraction(0.8)  # Use 80% of GPU memory

# Option 2: Disable pre-allocated memory
import os
os.environ['PYTORCH_CUDA_ALLOC_CONF'] = 'max_split_size_mb:512'

# Check GPU availability
print(f"CUDA available: {torch.cuda.is_available()}")
print(f"CUDA device: {torch.cuda.get_device_name(0)}")
print(f"CUDA device count: {torch.cuda.device_count()}")

# Get memory info
print(f"Allocated memory: {torch.cuda.memory_allocated() / 1e9:.2f} GB")
print(f"Cached memory: {torch.cuda.memory_reserved() / 1e9:.2f} GB")
```

---

## Production Deployment Patterns

### Pattern 1: Standalone Service with Flask

```python
# app.py - Flask service for HQQ model inference
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from flask import Flask, request, jsonify
import logging

app = Flask(__name__)
logger = logging.getLogger(__name__)

# Global model and tokenizer
model = None
tokenizer = None
device = 'cuda' if torch.cuda.is_available() else 'cpu'

def load_model():
    """Load pre-quantized HQQ model"""
    global model, tokenizer
    logger.info("Loading HQQ model...")
    
    model = AutoModelForCausalLM.from_pretrained(
        "mobiusml/hqq-7b-0",
        device_map=device,
        torch_dtype=torch.float32
    )
    tokenizer = AutoTokenizer.from_pretrained("meta-llama/Llama-2-7b")
    
    logger.info("Model loaded successfully")
    return model

@app.before_request
def initialize():
    """Load model on first request"""
    global model
    if model is None:
        load_model()

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'healthy'}), 200

@app.route('/generate', methods=['POST'])
def generate():
    """Generate text from prompt"""
    try:
        data = request.json
        prompt = data.get('prompt', '')
        max_length = data.get('max_length', 100)
        
        if not prompt:
            return jsonify({'error': 'No prompt provided'}), 400
        
        # Tokenize input
        inputs = tokenizer(prompt, return_tensors="pt").to(device)
        
        # Generate
        with torch.no_grad():
            output_ids = model.generate(
                **inputs,
                max_length=max_length,
                do_sample=True,
                top_p=0.95,
                temperature=0.7
            )
        
        # Decode output
        generated_text = tokenizer.decode(
            output_ids[0],
            skip_special_tokens=True
        )
        
        return jsonify({
            'prompt': prompt,
            'generated': generated_text
        }), 200
    
    except Exception as e:
        logger.error(f"Generation error: {e}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
```

Run with Gunicorn for production:
```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

### Pattern 2: Batch Processing Service

```python
# batch_processor.py - Batch inference for multiple prompts
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from typing import List
import time

class HQQBatchProcessor:
    def __init__(self, model_name: str = "mobiusml/hqq-7b-0"):
        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        self.model = AutoModelForCausalLM.from_pretrained(
            model_name,
            device_map=self.device
        )
        self.tokenizer = AutoTokenizer.from_pretrained(
            "meta-llama/Llama-2-7b"
        )
        self.model.eval()
    
    def process_batch(
        self,
        prompts: List[str],
        batch_size: int = 8,
        max_length: int = 100
    ) -> List[str]:
        """Process multiple prompts in batches"""
        results = []
        
        for i in range(0, len(prompts), batch_size):
            batch_prompts = prompts[i:i + batch_size]
            
            # Tokenize batch
            inputs = self.tokenizer(
                batch_prompts,
                return_tensors="pt",
                padding=True,
                truncation=True
            ).to(self.device)
            
            # Generate
            with torch.no_grad():
                output_ids = self.model.generate(
                    **inputs,
                    max_length=max_length,
                    num_beams=1,
                    do_sample=True,
                    top_p=0.95
                )
            
            # Decode
            batch_results = [
                self.tokenizer.decode(ids, skip_special_tokens=True)
                for ids in output_ids
            ]
            results.extend(batch_results)
        
        return results
    
    def benchmark(self, num_prompts: int = 100):
        """Benchmark throughput"""
        prompts = ["Hello world"] * num_prompts
        
        start = time.time()
        results = self.process_batch(prompts, batch_size=8)
        elapsed = time.time() - start
        
        # Estimate tokens (rough)
        total_tokens = sum(len(r.split()) for r in results)
        
        print(f"Processed {num_prompts} prompts")
        print(f"Total time: {elapsed:.2f}s")
        print(f"Throughput: {num_prompts / elapsed:.2f} prompts/sec")
        print(f"Token throughput: {total_tokens / elapsed:.2f} tokens/sec")

# Usage
if __name__ == "__main__":
    processor = HQQBatchProcessor()
    
    prompts = [
        "The future of AI is",
        "Quantization helps because",
        "Machine learning models",
    ]
    
    results = processor.process_batch(prompts, batch_size=2)
    for prompt, result in zip(prompts, results):
        print(f"Prompt: {prompt}")
        print(f"Generated: {result}\n")
```

### Pattern 3: Kubernetes Deployment

```yaml
# kubernetes.yaml - K8s deployment for HQQ service
apiVersion: apps/v1
kind: Deployment
metadata:
  name: hqq-inference-server
spec:
  replicas: 3
  selector:
    matchLabels:
      app: hqq-server
  template:
    metadata:
      labels:
        app: hqq-server
    spec:
      containers:
      - name: hqq-server
        image: hqq_env:latest
        ports:
        - containerPort: 5000
        resources:
          requests:
            memory: "16Gi"
            cpu: "4"
            nvidia.com/gpu: "1"
          limits:
            memory: "32Gi"
            cpu: "8"
            nvidia.com/gpu: "1"
        env:
        - name: CUDA_VISIBLE_DEVICES
          value: "0"
        - name: TRANSFORMERS_CACHE
          value: "/cache"
        volumeMounts:
        - name: model-cache
          mountPath: /cache
      volumes:
      - name: model-cache
        emptyDir: {}

---
apiVersion: v1
kind: Service
metadata:
  name: hqq-service
spec:
  selector:
    app: hqq-server
  ports:
  - port: 80
    targetPort: 5000
  type: LoadBalancer
```

Deploy:
```bash
kubectl apply -f kubernetes.yaml
kubectl get pods -l app=hqq-server
kubectl logs deployment/hqq-inference-server
```

---

## Common Issues & Solutions

### Issue: CUDA out of memory

```python
# Solution 1: Reduce batch size
batch_size = 1  # Instead of 8

# Solution 2: Clear cache
torch.cuda.empty_cache()

# Solution 3: Use CPU
device = 'cpu'

# Solution 4: Use smaller model
model = AutoModelForCausalLM.from_pretrained(
    "gpt2",  # Smaller than Llama-2-7B
    device_map="auto"
)
```

### Issue: Model loading is slow

```python
# Solution 1: Use local cache
import os
os.environ['HF_HOME'] = '/fast/ssd/huggingface'

# Solution 2: Pre-download model
from huggingface_hub import snapshot_download
snapshot_download("mobiusml/hqq-7b-0")

# Solution 3: Use device_map for better loading
model = AutoModelForCausalLM.from_pretrained(
    "mobiusml/hqq-7b-0",
    device_map="auto",  # Auto-split across devices
    low_cpu_mem_usage=True
)
```

### Issue: Slow inference

```python
# Solution 1: Use batch processing
# (see batch_processor.py above)

# Solution 2: Enable inference optimization
torch.backends.cudnn.benchmark = True
model = torch.compile(model)  # PyTorch 2.0+

# Solution 3: Use smaller max_length
max_length = 50  # Instead of 500
```

---

## Performance Benchmarking Script

```python
# benchmark.py - Complete benchmarking tool
import torch
import time
from transformers import AutoModelForCausalLM, AutoTokenizer

class HQQBenchmark:
    def __init__(self, model_name: str = "gpt2"):
        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        print(f"Using device: {self.device}")
        
        self.model = AutoModelForCausalLM.from_pretrained(
            model_name
        ).to(self.device).eval()
        
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
    
    def benchmark_inference(self, prompt: str = "Hello", num_iterations: int = 10):
        """Benchmark inference latency"""
        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.device)
        
        # Warmup
        with torch.no_grad():
            self.model.generate(**inputs, max_length=20)
        
        # Timed inference
        times = []
        for _ in range(num_iterations):
            start = time.perf_counter()
            with torch.no_grad():
                self.model.generate(**inputs, max_length=50)
            elapsed = time.perf_counter() - start
            times.append(elapsed)
        
        avg_time = sum(times) / len(times)
        min_time = min(times)
        max_time = max(times)
        
        print(f"Inference Benchmark ({num_iterations} iterations):")
        print(f"  Average: {avg_time*1000:.2f} ms")
        print(f"  Min: {min_time*1000:.2f} ms")
        print(f"  Max: {max_time*1000:.2f} ms")
    
    def benchmark_memory(self, batch_size: int = 8):
        """Benchmark memory usage"""
        torch.cuda.reset_peak_memory_stats()
        
        inputs = self.tokenizer(
            ["Hello"] * batch_size,
            return_tensors="pt",
            padding=True
        ).to(self.device)
        
        with torch.no_grad():
            self.model(**inputs)
        
        peak_memory = torch.cuda.max_memory_allocated() / 1e9
        print(f"\nMemory Benchmark (batch_size={batch_size}):")
        print(f"  Peak memory: {peak_memory:.2f} GB")

# Run benchmarks
if __name__ == "__main__":
    benchmark = HQQBenchmark("gpt2")
    benchmark.benchmark_inference()
    benchmark.benchmark_memory()
```

Run it:
```bash
python benchmark.py
```

---

## Troubleshooting Installation

### Python Version Issues

```bash
# Check Python version
python --version

# Use specific Python version
python3.10 -m venv venv
source venv/bin/activate

# If python3.10 not available, install:
# Ubuntu/Debian:
sudo apt-get install python3.10 python3.10-venv

# macOS:
brew install python@3.10
```

### PyTorch Version Mismatch

```bash
# Check installed PyTorch
python -c "import torch; print(torch.__version__)"

# Reinstall matching CUDA version
# Check your GPU: nvidia-smi
# Check CUDA version: nvcc --version

# Reinstall for CUDA 12.1:
pip install --upgrade torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
```

### HQQ Import Errors

```bash
# Reinstall from source
pip uninstall hqq
git clone https://github.com/mobiusml/HQQ.git
cd HQQ
pip install -e .

# Test import
python -c "from hqq.core import HQQLinear; print('Success')"
```

---

## Summary

| Task | Command |
|------|---------|
| Create environment | `python -m venv hqq_env` |
| Activate environment | `source hqq_env/bin/activate` |
| Install PyTorch | `pip install torch --index-url https://download.pytorch.org/whl/cu118` |
| Install HQQ | `pip install hqq` |
| Test installation | `python test_hqq_installation.py` |
| Run service | `gunicorn -w 4 -b 0.0.0.0:5000 app:app` |
| Deploy Kubernetes | `kubectl apply -f kubernetes.yaml` |

---

**Last Updated**: 2026-07-06  
**Status**: Production-Ready  
**Tested On**: Ubuntu 22.04, PyTorch 2.0, CUDA 11.8+
