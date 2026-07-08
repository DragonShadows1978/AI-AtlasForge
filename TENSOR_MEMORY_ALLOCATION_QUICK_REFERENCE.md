# Tensor Memory Allocation: Quick Reference & Performance Guide

**Date:** July 7, 2026  
**Purpose:** Fast lookup table, decision tree, and performance benchmarks  
**Audience:** Engineers choosing allocation strategies for new projects

---

## Decision Tree: Choosing an Allocator

```
START: What's your primary workload?

├─ GPU Inference (most common)
│  ├─ Small model (<8GB)?
│  │  └─ Use: PyTorch CUDACachingAllocator (default)
│  │     Why: Proven, handles variable batch sizes, minimal fragmentation
│  │
│  └─ Large model (>8GB)?
│     ├─ Many small kernels?
│     │  └─ Use: cudaMemPool (CUDA 11.2+)
│     │     Why: Async alloc/free, zero stall overhead
│     │
│     └─ Few large kernels?
│        └─ Use: CUDA Graphs + memory pools
│           Why: Persistent memory across iterations
│
├─ GPU Training (gradients, optimizer state)
│  ├─ Single GPU?
│  │  └─ Use: PyTorch default + gradient checkpointing
│  │     Why: CUDACachingAllocator handles gradient lifecycle
│  │
│  └─ Multi-GPU (distributed)?
│     └─ Use: Memory pooling per GPU + ring buffers
│        Why: Predictable allocation, avoid cross-GPU sync
│
├─ CPU-side Tensor Allocations
│  ├─ Latency critical (<1 µs)?
│  │  └─ Use: jemalloc or mimalloc
│  │     Why: 10-50 cycle allocation, fine-grained bins
│  │
│  └─ Throughput focused?
│     └─ Use: Arena allocator + periodic reset
│        Why: 2-3 cycle allocation, no fragmentation
│
├─ Temporary/Scratch Buffers (kernel internals)
│  └─ Use: Bump/Linear allocator (shared memory or device)
│     Why: 1-cycle allocation, perfect for function scope
│
└─ Custom ML Library (building from scratch)
   ├─ Start with: Arena allocator (foundation)
   ├─ Add: Size-class binning (jemalloc model)
   ├─ Implement: Caching layer (PyTorch model)
   ├─ Deploy: Memory pooling (CUDA 11.2+)
   └─ Monitor: Fragmentation metrics

```

---

## Performance Quick Reference

### Allocation Latency (CPU cycles / microseconds)

| Operation | Latency (cycles) | Latency (µs) | Context |
|-----------|------------------|--------------|---------|
| **Bump allocate** | 2-3 | 0.001-0.002 | Fast path, linear arena |
| **jemalloc (cache hit)** | 10-50 | 0.01-0.05 | Thread-local bin, no lock |
| **cudaMalloc** | N/A | 5-15 | Includes GPU sync, slow |
| **PyTorch cached (hit)** | 50-200 | 0.05-0.2 | Hash table lookup + link |
| **cudaMemPool (async)** | N/A | 0.1-1 | No host-device sync |
| **CUDA Graph launch** | N/A | 1-10 | Entire pipeline |

**Rule of Thumb:**
- Cache hit: 100× faster than cache miss
- Async operations: No additional cost if pipelined
- Fragmentation doubles alloc latency for large objects

---

## Memory Overhead Comparison

| Allocator | Overhead | Fragmentation | Use Case |
|-----------|----------|----------------|----------|
| Bump | 0% | 0% (deterministic) | Temporaries, scope-limited |
| Size-class (jemalloc) | 0.5-2% | 3-5% | General purpose, CPU |
| Caching (PyTorch) | 0.1-0.5% | 5-15% (varies) | GPU inference |
| Memory pool | 0% | 0-1% (if sized right) | GPU, predictable workload |
| No allocator (malloc) | 0% | 10-30%+ | Baseline, fragmented |

---

## GPU Memory Budget Worksheet

For planning your GPU memory allocation:

```
GPU Model: _____________ Total Memory: _______ GB

Model weights:
  Parameters: _________ (billions)
  Precision (2B or 4B): _____
  Weights size: _________ GB
  
Batch size: _______ Sequence length: _______

Activations (forward pass):
  Hidden dim: ______ Layers: ______
  Est. activation size: _______ GB
  
KV Cache (if inference):
  Per-layer KV: _______ GB
  Total KV: _______ GB
  
Optimizer State (if training):
  Momentum: _______ GB
  Variance: _______ GB
  
Temporary Buffers:
  Attention output: _______ GB
  MLP intermediate: _______ GB
  Gradients (training): _______ GB
  
Total Core: _______ GB
Allocator overhead (1.2×): _______ GB
Peak memory needed: _______ GB

Your GPU: _______ GB available
Remaining: _______ GB for safety margin
```

---

## Size Class Reference

### Standard PyTorch Binning (matching tensor shapes)

```
Bin Range         | Count | Common Tensor Shape Examples
------------------|-------|----------------------------------
8B - 512B        | 64    | Embeddings, small dense layers
512B - 16KB      | 240   | Attention scores, token outputs
16KB - 256KB     | 256   | Batch activations, gradients
256KB - 4MB      | 256   | Layer activations, batched tensors
4MB - 64MB       | 256   | Large batches, big layers
64MB+            | ~     | Full model activations
```

### jemalloc Size Classes (CPU reference)

```
Bin          | Bin Size | Typical Objects
-------------|----------|--------------------
1            | 8B       | Metadata, indices
2-16         | 16-128B  | Small structs
17-32        | 256B     | Cache lines
33-48        | 512B-2KB | Small arrays
49-64        | 4KB-8KB  | Regular arrays
65-80        | 16KB+    | Large allocations
```

---

## Fragmentation Prevention Checklist

When designing an allocator for your use case:

- [ ] Size classes aligned to common tensor dimensions
- [ ] Free list sorted by size for best-fit allocation
- [ ] Coalescing of adjacent free blocks (GC)
- [ ] Separate pools for different lifetime categories
- [ ] Per-thread arenas to reduce contention
- [ ] Periodic defragmentation (off-critical-path)
- [ ] Monitoring of fragmentation ratio
- [ ] Aging-based eviction (unused blocks)
- [ ] Statistics tracking (hits, misses, coalescences)

---

## PyTorch Memory Tuning

### Enable Memory Efficient Operations

```python
# Gradient checkpointing (trade compute for memory)
torch.utils.checkpoint.checkpoint(module, input)

# Activation checkpointing
from torch.utils.checkpoint import checkpoint

# Mixed precision (reduce model footprint)
with torch.cuda.amp.autocast():
    output = model(input)

# Memory pooling (CUDA 11.2+)
torch.cuda.memory._set_pool_config(
    torch.cuda.default_generators[0].device,
    fraction_of_free_memory=0.75
)

# Benchmark allocator overhead
torch.cuda.reset_peak_memory_stats()
result = model(input)
peak = torch.cuda.max_memory_allocated()

# Profile allocations
from torch.profiler import profile, record_function, ProfilerActivity
with profile(activities=[ProfilerActivity.CUDA], 
             profile_memory=True) as prof:
    output = model(input)
```

### Measure Fragmentation

```python
def measure_fragmentation(device=0):
    allocated = torch.cuda.memory_allocated(device)
    reserved = torch.cuda.memory_reserved(device)
    
    fragmentation = (reserved - allocated) / allocated
    efficiency = allocated / reserved
    
    print(f"Allocated: {allocated/1e9:.2f} GB")
    print(f"Reserved: {reserved/1e9:.2f} GB")
    print(f"Fragmentation ratio: {fragmentation:.2f}")
    print(f"Efficiency: {efficiency:.1%}")
```

---

## Common Mistakes & Fixes

### Mistake 1: Frequent Small Allocations
```python
# BAD: Allocates 1000 times
for i in range(1000):
    x = torch.randn(100, requires_grad=True)
    output = model(x)

# GOOD: Allocate once, reuse
x = torch.randn(1000, 100, requires_grad=True)
for i in range(1000):
    output = model(x[i:i+1])
```

### Mistake 2: Not Resetting Temporary Buffers
```python
# BAD: Temp buffer grows indefinitely
class Model(nn.Module):
    def __init__(self):
        self.temp_alloc = TemporaryBufferAllocator()
    
    def forward(self, x):
        temp = self.temp_alloc.allocate_tensor((1024, 1024))
        # ... use temp ...
        return result
        # Forgot to reset!

# GOOD: Reset at end of forward
def forward(self, x):
    self.temp_alloc.reset()  # Clear previous temporaries
    temp = self.temp_alloc.allocate_tensor((1024, 1024))
    # ... use temp ...
    self.temp_alloc.reset()  # Reset for next forward
    return result
```

### Mistake 3: Holding References to Freed Tensors
```python
# BAD: Tensor lifetime unclear
def process_batch(batch):
    embeddings = embed_layer(batch)
    # embeddings reference might still exist in closure
    attention = attn_layer(embeddings)  # Depends on GC
    return attention

# GOOD: Explicit lifetime management
def process_batch(batch):
    embeddings = embed_layer(batch)
    attention = attn_layer(embeddings)
    del embeddings  # Force immediate deallocation
    return attention
```

### Mistake 4: Allocating Too Much at Once
```python
# BAD: Entire dataset on GPU
batch_size = 10000
x = torch.randn(batch_size, 1024, device='cuda')  # May exceed GPU memory

# GOOD: Reasonable batch size
batch_size = min(256, len(dataset))
dataloader = DataLoader(dataset, batch_size=batch_size)
for batch_x in dataloader:
    x = batch_x.to('cuda')  # Allocate one batch at a time
```

---

## Performance Profiling Script

```python
#!/usr/bin/env python3
"""Profile memory allocation performance"""

import torch
import time
from statistics import mean, stdev

def benchmark_allocations(sizes, repeats=100, device=0):
    """Benchmark allocation latency for various sizes"""
    
    results = {}
    
    for size in sizes:
        latencies = []
        
        for _ in range(repeats):
            torch.cuda.synchronize(device)
            start = time.perf_counter()
            
            x = torch.randn(size, device=f'cuda:{device}')
            
            torch.cuda.synchronize(device)
            elapsed = (time.perf_counter() - start) * 1e6  # Convert to µs
            
            latencies.append(elapsed)
            del x
        
        results[size] = {
            'mean_us': mean(latencies),
            'stdev_us': stdev(latencies) if len(latencies) > 1 else 0,
            'min_us': min(latencies),
            'max_us': max(latencies),
        }
    
    return results

if __name__ == '__main__':
    sizes = [
        256,           # Small tensor
        4096,          # Medium tensor
        65536,         # Large tensor
        1048576,       # 1 MB
        10485760,      # 10 MB
        104857600,     # 100 MB
    ]
    
    print("GPU Memory Allocation Benchmarks")
    print("=" * 70)
    print(f"{'Size':<12} {'Mean (µs)':<12} {'Stdev (µs)':<12} {'Min (µs)':<12} {'Max (µs)':<12}")
    print("-" * 70)
    
    results = benchmark_allocations(sizes, repeats=50)
    
    for size, stats in results.items():
        size_str = f"{size / 1e6:.1f}MB" if size >= 1e6 else f"{size / 1e3:.1f}KB"
        print(f"{size_str:<12} {stats['mean_us']:<12.2f} {stats['stdev_us']:<12.2f} "
              f"{stats['min_us']:<12.2f} {stats['max_us']:<12.2f}")
```

---

## Implementation Timeline

For a new tensor library project:

```
Week 1: Foundation
├─ Basic arena allocator
├─ Unit tests
└─ Benchmark: ~2-3 µs per allocation

Week 2: Binning
├─ Add size classes (16-32 classes)
├─ Segregated fit strategy
└─ Benchmark: ~1-2 µs per allocation (small), 5-10 µs (large)

Week 3: Caching
├─ Implement free list per bin
├─ Add coalescing logic
├─ Basic GC trigger
└─ Benchmark: 0.1-0.5 µs (cache hit), 5-15 µs (miss)

Week 4: GPU Integration
├─ Integrate with CUDA memory pooling
├─ Async allocation/deallocation
├─ Event-based cleanup
└─ Benchmark: async ops invisible to latency profile

Week 5: Optimization
├─ Profile real workload fragmentation
├─ Tune size classes for your workload
├─ Separate pools by lifetime
└─ 30-50% memory savings possible

Week 6+: Production
├─ Monitoring/observability
├─ Auto-tuning of pool sizes
├─ Advanced techniques (lifecycle analysis)
└─ Maintain <5% memory overhead
```

---

## Further Reading

**Papers:**
- "jemalloc: A Scalable Concurrent malloc(3) Implementation" - Evans, 2006
- "mimalloc: Free List Sharding by Cardinality" - Leijen et al., 2019
- "Unified GPU Memory Access for Faster Kernel Launches" - NVIDIA, 2020

**Documentation:**
- PyTorch Memory Management: https://pytorch.org/docs/stable/cuda.html
- CUDA C++ Programming Guide (sec 3.2 Memory): nvidia.com
- TensorFlow Memory Optimization: tensorflow.org/guide/memory_optimization

**Tools:**
- `nvidia-smi` - GPU memory monitoring
- `torch.cuda.memory_stats()` - Detailed PyTorch memory analysis
- NVIDIA Nsight Systems - GPU profiling with memory traces

---

## Cheat Sheet

```python
# Quick memory operations in PyTorch

# Allocate and track
x = torch.randn(1024, 1024, device='cuda')

# Check memory
allocated = torch.cuda.memory_allocated(0)      # Current use
reserved = torch.cuda.memory_reserved(0)        # Reserved from OS
peak = torch.cuda.max_memory_allocated(0)       # Peak during session

# Reset tracking
torch.cuda.reset_peak_memory_stats(0)

# Force cleanup
torch.cuda.empty_cache()

# Detailed stats
stats = torch.cuda.memory_stats(0)

# Profile
with torch.profiler.profile(profile_memory=True) as prof:
    output = model(input)

# Memory efficient forward
from torch.utils.checkpoint import checkpoint
output = checkpoint(model, input)

# Mixed precision
with torch.cuda.amp.autocast():
    output = model(input)

# Async operations (CUDA 11.2+)
torch.cuda.memory._set_pool_config(
    torch.cuda.default_generators[0].device,
    fraction_of_free_memory=0.75
)
```

---

## Summary Matrix

Choose based on your primary constraint:

| Constraint | Strategy | Expected Result |
|-----------|----------|-----------------|
| **Allocation latency** | Bump allocator for temp buffers | <1 µs overhead |
| **Fragmentation** | Separate pools by lifetime | <5% waste |
| **GPU stalls** | Async memory pool + graphs | 0 alloc stalls |
| **Variable sizes** | Size-class binning | 0.5-2% overhead |
| **Memory efficiency** | Lifetime analysis + coalescing | 30-50% reduction |
| **Simplicity** | PyTorch default | Proven, works |

Start simple, measure, optimize where bottlenecks appear.

