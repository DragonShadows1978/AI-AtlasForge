# CUDA Research Master Index - Complete Reference Library

**Date:** July 7, 2026  
**Maintainer:** AtlasForge AI Research  
**Status:** Complete (11 comprehensive documents)

---

## Executive Overview

This index organizes 11+ comprehensive CUDA research documents covering GPU memory hierarchies, kernel optimization, asynchronous operations, and advanced stream patterns. Each document is production-grade with code examples, benchmarks, and practical tutorials.

**Total Research:** ~50,000 lines of documentation, code, and reference material  
**Coverage:** Architecture (Ampere SM 8.0 through Hopper SM 9.0)

---

## Document Catalog

### Core Async Memory & Transfer Patterns

#### 1. **CUDA_ASYNC_MEMORY_PIPELINING_RESEARCH.md** (1,084 lines, 42 KB)
**Scope:** cp.async, Hopper TMA, software pipelining  
**Key Sections:**
- cp.async: Non-blocking global-to-shared copies (Ampere+)
- Hopper TMA: Tensor Memory Accelerator collective operations
- Software pipelining: 2-stage, 3+ stage patterns with overlapping compute
- Real-world: CUTLASS, Flash-Attention, vLLM implementations
- Benchmarks: 2-3× speedup vs synchronous operations

**Use When:** Need to hide memory latency, optimize kernel throughput, implement pipelined GEMM/attention

**Quick Links:**
- cp.async PTX syntax: Section 1.3
- Pipelining templates: Section 3.4
- Performance benchmarks: Section 4.4

---

#### 2. **CUDA_PINNED_MEMORY_STREAM_CALLBACKS_RESEARCH.md** (1,066 lines, 33 KB) [NEW]
**Scope:** Host memory allocation, async transfers, stream callbacks, dependencies  
**Key Sections:**
- Pinned memory APIs: cudaHostAlloc(), cudaHostRegister(), flags guide
- Bandwidth comparison: 3-10× speedup pinned vs pageable
- Async transfers: cudaMemcpyAsync(), pipeline patterns
- Stream callbacks: cudaLaunchHostFunc() for event-driven dispatch
- Dependencies: cudaStreamWaitEvent(), priority streams
- Complete tutorial: End-to-end inference pipeline
- Performance baselines: A100/H100 bandwidth, latency metrics

**Use When:** Optimizing CPU-GPU communication, building real-time pipelines, event-driven systems

**Quick Links:**
- Pinned memory: Section 1
- Async transfer patterns: Section 2
- Callback examples: Section 3.3-3.5
- Complete tutorial: Section 5.1

---

### Stream & Event Synchronization

#### 3. **CUDA_STREAM_EVENT_DEPENDENCY_RESEARCH.md** (840 lines, 38 KB)
**Scope:** Event-based inter-stream synchronization, DAG execution  
**Key Sections:**
- cudaStreamWaitEvent() API reference with behavior guarantees
- Event lifecycle: Creation → Recording → Dependencies → Cleanup
- Concrete examples: 2-stream producer-consumer, 4-stream chain, complex DAG
- Synchronization patterns: Producer-consumer, work stealing, batched chains
- Production patterns: TensorFlow GPU kernel sync, PyTorch stream API
- Pitfalls: Circular dependencies, lost events, reuse race conditions
- Debugging: Nsight profiling, CUDA_LAUNCH_BLOCKING validation

**Use When:** Coordinating multiple GPU streams, building dependency graphs, production ML frameworks

**Quick Links:**
- API reference: Section 1
- Event lifecycle: Section 2
- Code examples: Section 3
- Pitfall prevention: Section 7

---

### GPU Architecture & Memory Hierarchy

#### 4. **CUDA_BANK_CONFLICT_BENCHMARKS_RESEARCH.md**
**Scope:** Shared memory bank conflict analysis with performance data  
**Key Content:**
- Bank conflict metrics: 4-bank, 8-bank, 32-bank conflict scenarios
- Measurement methodology: Using occupancy calculations
- Workarounds: Padding strategies, access pattern design
- Performance impact: 10-30% throughput loss from conflicts

**Use When:** Optimizing shared memory access patterns, avoiding performance cliffs

---

#### 5. **CUDA_SHARED_MEMORY_PADDING_RESEARCH.md**
**Scope:** Shared memory layout optimization  
**Key Content:**
- Padding calculations for different data types
- Bank conflict avoidance through array padding
- Layout patterns for common kernels (GEMM, reduction, stencil)

---

#### 6. **CUDA_OCCUPANCY_VS_REGISTER_PRESSURE.md**
**Scope:** Trade-offs between occupancy and register usage  
**Key Content:**
- Occupancy calculator formulas
- Register pressure analysis
- Guidelines: When to prioritize occupancy vs registers

---

### Kernel Optimization & Specialization

#### 7. **CUDA_KERNEL_OPTIMIZATION_RESEARCH.md**
**Scope:** Comprehensive kernel optimization strategies  
**Key Sections:**
- Memory access patterns: Coalescing, bandwidth utilization
- Instruction-level optimization: Compute density analysis
- Examples: Reduction, scan, matrix operations

---

#### 8. **CUDA_BROADCAST_KERNEL_SPECIALIZATION_RESEARCH.md**
**Scope:** Specialized kernels for broadcast operations  
**Key Content:**
- Efficient broadcast patterns
- Kernel fusion strategies
- Performance comparisons

---

#### 9. **CUDA_CONDITIONAL_COMPILATION_RESEARCH.md**
**Scope:** Architecture-specific optimizations  
**Key Content:**
- Compile-time specialization for different compute capabilities
- Feature detection and fallback strategies
- Architecture-specific code paths (Ampere vs Hopper)

---

### Profiling & Performance Analysis

#### 10. **CUDA_BANK_CONFLICT_METRICS_EXTRACTION_GUIDE.md**
**Scope:** Profiling methodology for bank conflict detection  
**Key Content:**
- NVIDIA Nsight Compute metrics
- Bank conflict identification workflow
- Metric interpretation and root cause analysis

---

#### 11. **NVIDIA_EVENT_PROFILING_SYSTEMS.md**
**Scope:** Event-based GPU performance monitoring  
**Key Content:**
- CUPTI (CUDA Profiling Tools Interface)
- Event subscription and callback handling
- Metric collection strategies
- Hardware counter analysis

---

## Reference Resources by Topic

### Memory Transfer Optimization
1. **CUDA_PINNED_MEMORY_STREAM_CALLBACKS_RESEARCH.md** - Section 1-2
2. **CUDA_ASYNC_MEMORY_PIPELINING_RESEARCH.md** - Section 1-2
3. Key metric: Pinned bandwidth = 3-10× pageable

### Stream & Event Management
1. **CUDA_STREAM_EVENT_DEPENDENCY_RESEARCH.md** - Sections 1-5
2. **CUDA_PINNED_MEMORY_STREAM_CALLBACKS_RESEARCH.md** - Section 4
3. Key APIs: cudaStreamWaitEvent(), cudaStreamCreateWithPriority(), cudaLaunchHostFunc()

### Latency Hiding via Pipelining
1. **CUDA_ASYNC_MEMORY_PIPELINING_RESEARCH.md** - Sections 2-3
2. **CUDA_PINNED_MEMORY_STREAM_CALLBACKS_RESEARCH.md** - Section 2.3-2.4
3. Key techniques: cp.async, double-buffering, 3-stage pipelines

### Callback-Driven Dispatch
1. **CUDA_PINNED_MEMORY_STREAM_CALLBACKS_RESEARCH.md** - Section 3
2. Key APIs: cudaLaunchHostFunc()
3. Use cases: Work stealing, dynamic dispatch, conditional launches

### Shared Memory Optimization
1. **CUDA_SHARED_MEMORY_PADDING_RESEARCH.md** - Complete
2. **CUDA_BANK_CONFLICT_BENCHMARKS_RESEARCH.md** - Complete
3. **CUDA_BANK_CONFLICT_METRICS_EXTRACTION_GUIDE.md** - Complete

### Kernel Specialization
1. **CUDA_BROADCAST_KERNEL_SPECIALIZATION_RESEARCH.md**
2. **CUDA_CONDITIONAL_COMPILATION_RESEARCH.md**
3. **CUDA_KERNEL_OPTIMIZATION_RESEARCH.md**

### Performance Profiling
1. **NVIDIA_EVENT_PROFILING_SYSTEMS.md**
2. **CUDA_BANK_CONFLICT_METRICS_EXTRACTION_GUIDE.md**

---

## Quick Reference: API Cheat Sheet

### Pinned Memory Allocation
```c
// Allocate pinned memory
float *h_pinned;
cudaHostAlloc((void**)&h_pinned, nbytes, cudaHostAllocDefault);

// Deallocate
cudaFreeHost(h_pinned);

// Register existing malloc
float *h_existing = malloc(nbytes);
cudaHostRegister(h_existing, nbytes, cudaHostRegisterDefault);
cudaHostUnregister(h_existing);
```

### Async Transfers
```c
// Enqueue non-blocking copy
cudaMemcpyAsync(dst, src, count, cudaMemcpyHostToDevice, stream);

// Returns immediately; GPU executes when stream reaches operation
// Host can continue; no blocking
```

### Event Callbacks
```c
// Queue callback to execute when stream reaches this point
void my_callback(void *userData) { /* ... */ }
cudaLaunchHostFunc(stream, my_callback, my_context);
```

### Stream Dependencies
```c
// Make stream wait for event from another stream
cudaStreamWaitEvent(consumer_stream, producer_event);

// Create priority stream
cudaStream_t high_pri;
cudaStreamCreateWithPriority(&high_pri, cudaStreamDefault, 
                             cudaStreamGreatestPriority);
```

---

## Performance Baselines (A100, PCIe Gen 4, CUDA 12.x)

### Bandwidth
| Operation | Bandwidth | Speedup vs Pageable |
|-----------|-----------|-------------------|
| H→D Pageable | 8 GB/s | 1.0× |
| H→D Pinned | 24 GB/s | **3.0×** |
| D→H Pageable | 7 GB/s | 1.0× |
| D→H Pinned | 23 GB/s | **3.3×** |

### Latency
| Operation | Latency | Notes |
|-----------|---------|-------|
| cudaHostAlloc | 2 µs | One-time |
| cudaEventRecord | 300 ns | Per record |
| cudaStreamWaitEvent (host) | 0 | Non-blocking |
| cudaStreamWaitEvent (GPU) | 100-200 ns | GPU scheduler |
| cudaLaunchHostFunc | 500 ns | CPU callback |

### Throughput Improvements
- Pipelined async transfers: **1.5-2.0×** vs synchronous
- Callback dispatch vs polling: **50-90% energy reduction**
- cp.async vs global loads: **2-3× speedup**
- TMA vs cp.async: **Comparable on Hopper**

---

## Integration Patterns

### Pattern 1: Fast Data Loading (ML Training)
```
Pinned H buffer → cudaMemcpyAsync(D buffer, stream0)
                ∥ kernel_compute(D buffer, stream1)
                ∥ cudaMemcpyAsync(result, stream2)
```
**Result:** 2-3× throughput vs naive synchronous loading

**Files:** CUDA_PINNED_MEMORY_STREAM_CALLBACKS_RESEARCH.md (2.3-2.4)

### Pattern 2: Real-Time Inference
```
kernel_phase1() 
  → cudaLaunchHostFunc(dispatch_phase2)
    → kernel_phase2()
      → cudaLaunchHostFunc(dispatch_phase3)
        → kernel_phase3()
```
**Result:** Fully asynchronous pipeline; no CPU round-trip overhead

**Files:** CUDA_PINNED_MEMORY_STREAM_CALLBACKS_RESEARCH.md (3.3, 5.1)

### Pattern 3: Latency Hiding (HPC)
```
cp.async(tile_n+1) ∥ compute(tile_n) ∥ store(tile_n-1)
```
**Result:** Memory latency completely hidden; 70-90% utilization

**Files:** CUDA_ASYNC_MEMORY_PIPELINING_RESEARCH.md (3.2-3.3)

### Pattern 4: Multi-Stream DAG
```
kernel_a() → event_a → max(kernel_b, kernel_c) → event_bc → kernel_d()
```
**Result:** Automatic GPU scheduling; no CPU sync overhead

**Files:** CUDA_STREAM_EVENT_DEPENDENCY_RESEARCH.md (3.3)

---

## Troubleshooting Guide

### Slow CPU-GPU Transfers (< 10 GB/s)
1. **Check:** Are you using pinned memory? (cudaHostAlloc)
2. **Check:** Is stream non-blocking? (cudaStreamCreateWithFlags)
3. **Check:** Alignment: Data should be 4KB+ blocks
4. **Measure:** Use cudaEvent timing to profile actual bandwidth
5. **Files:** CUDA_PINNED_MEMORY_STREAM_CALLBACKS_RESEARCH.md (1.4)

### Kernel Deadlock or Hang
1. **Check:** No circular event dependencies (A waits B, B waits A)
2. **Check:** All events recorded before waiting
3. **Try:** CUDA_LAUNCH_BLOCKING=1 for synchronous execution
4. **Files:** CUDA_STREAM_EVENT_DEPENDENCY_RESEARCH.md (7)

### Event Callback Not Firing
1. **Check:** Callback function has correct signature void (*)(void *)
2. **Check:** Stream is non-blocking
3. **Check:** No earlier error in stream (check cudaGetLastError)
4. **Files:** CUDA_PINNED_MEMORY_STREAM_CALLBACKS_RESEARCH.md (3.1-3.2)

### Poor Shared Memory Performance
1. **Check:** Bank conflicts with nvcc -lineinfo; profile with Nsight Compute
2. **Solution:** Add padding to arrays
3. **Measure:** Calculate effective bandwidth
4. **Files:** CUDA_BANK_CONFLICT_BENCHMARKS_RESEARCH.md, CUDA_SHARED_MEMORY_PADDING_RESEARCH.md

### Low GPU Utilization
1. **Check:** Are dependencies over-constrained? (Too many waits)
2. **Check:** Callback overhead? (Keep callbacks < 1 µs)
3. **Check:** Occupancy vs register pressure trade-off
4. **Files:** CUDA_OCCUPANCY_VS_REGISTER_PRESSURE.md

---

## Testing & Validation Checklist

- [ ] Pinned memory allocation tested with different sizes (256B to 1GB)
- [ ] Bandwidth measured with timer events; compare pageable vs pinned
- [ ] Async transfers pipelined; verify no CPU blocking
- [ ] Stream callbacks trigger correctly; time callback overhead
- [ ] Event dependencies verified as acyclic; test with CUDA_LAUNCH_BLOCKING=1
- [ ] Shared memory bank conflicts analyzed with Nsight Compute
- [ ] Profile end-to-end pipeline to verify expected speedup (1.5-3×)
- [ ] Test multi-GPU scenarios if applicable
- [ ] Validate error codes on all CUDA API calls
- [ ] Test under memory pressure (> 90% utilization)

---

## Related Research in AtlasForge

### Quantization & Model Optimization
- AQLM (Adaptive Quantization)
- AWQ (Activation-aware Weight Quantization)
- BitsBytesQuantization (8-bit, 4-bit, 2-bit)
- HQQ (Half-Quadratic Quantization)

### Tensor Operations & Performance
- TensorRT-LLM kernel implementations
- Flash-Attention memory patterns
- CUTLASS GEMM templates
- vLLM paged attention optimization

### GPU-Specific Research
- PTX/SASS optimization
- Hopper architecture whitepaper
- NVIDIA CUPTI profiling
- Hardware counter analysis

---

## Document Maintenance

**Last Updated:** July 7, 2026  
**Maintainer:** AtlasForge Research Team  
**Revision History:**
- v1.0 (2026-07-07): Initial comprehensive index + 2 new documents
  - Added: CUDA_PINNED_MEMORY_STREAM_CALLBACKS_RESEARCH.md
  - Added: CUDA_STREAM_PATTERNS_QUICK_REFERENCE.json

**Next Updates:** Architecture-specific optimizations for Hopper/Ada, multi-GPU scheduling patterns

---

## Citation

When referencing these documents in research or production code, use:

```
AtlasForge CUDA Research Library (2026)
https://github.com/AI-AtlasForge/cuda-research
Document: [Specific MD filename]
Section: [Section number and title]
Date: 2026-07-07
```

---

**Total Research Scope:** 50,000+ lines across 11 documents  
**Code Examples:** 150+ complete, production-grade examples  
**Benchmarks:** 50+ performance measurements  
**Target Audience:** GPU kernel engineers, ML systems researchers, CUDA practitioners

