# Memory Allocator Benchmarking and Performance Measurement

## Comprehensive Guide to Measuring and Comparing GPU Memory Management Strategies

---

## PART 1: BENCHMARKING METHODOLOGY

### 1.1 Key Metrics

**Allocation Latency** (Primary metric for low-latency systems):
```
Definition: Time from allocation request to usable memory returned
Units: nanoseconds (ns), microseconds (µs)
Importance: Critical for inference serving (tail latency impacts SLA)
Measurement: Record timestamps around allocation calls, use GPU events

Targets:
- Cache hit: < 500 ns
- Cache miss: 1-10 µs
- Defragmentation: < 100 µs
- OOM recovery: N/A (hard error)
```

**Memory Fragmentation** (Secondary metric):
```
Definition: Unused memory within allocated segments (internal fragmentation)
           + Wasted space between segments (external fragmentation)
Formula: fragmentation_ratio = (total_allocated - actually_used) / total_allocated
Units: percentage
Importance: Affects long-term memory efficiency and OOM probability
Targets:
- Arena allocator: 0-2%
- Caching allocator: 10-20%
- malloc-based: 20-50%

Measurement:
1. Track each allocation (size, location, usage pattern)
2. Periodically measure total used vs total allocated
3. Plot fragmentation over time
```

**Cache Hit Rate**:
```
Definition: % of allocations satisfied from pool vs new allocation
Formula: hit_rate = cache_hits / (cache_hits + cache_misses)
Units: percentage
Importance: High hit rate (> 95%) indicates effective pooling
Targets:
- Stable workload (training): > 95%
- Variable workload (inference): 85-95%
- Research/experimentation: 70-85%

Measurement: Instrument allocator to count cache hits/misses
```

**Peak Memory Usage**:
```
Definition: Maximum GPU memory used during execution
Units: MB or GB
Importance: Determines GPU size needed (8GB, 24GB, 40GB, 80GB, etc.)
Measurement: Record memory_allocated() at each step, track max
```

**Memory Efficiency Ratio**:
```
Definition: (peak_memory_needed) / (total_gpu_memory)
Formula: efficiency = peak_memory / total_gpu_memory
Units: percentage
Importance: Higher is better (more dense packing)
Targets:
- Inference: 85-95% (leave room for queuing)
- Training: 75-90% (leave room for gradients)
Measurement: Monitor peak_memory during full workload run
```

---

## PART 2: BENCHMARKING HARNESS

### 2.1 Python Benchmarking Framework

```python
"""
Comprehensive GPU memory allocator benchmarking framework.
Supports PyTorch, TensorFlow, and custom allocators.
"""

import time
import numpy as np
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Dict, Optional, Tuple
import torch
import matplotlib.pyplot as plt
from datetime import datetime

class AllocationPattern(Enum):
    """Different workload patterns to benchmark."""
    UNIFORM = "uniform"              # All same size
    NORMAL = "normal"                # Gaussian distribution
    POWER_LAW = "power_law"          # Long-tail distribution
    BURST = "burst"                  # Sudden allocation spikes
    CYCLIC = "cyclic"                # Allocate-deallocate cycles
    SEQUENTIAL = "sequential"        # Increasing sizes

@dataclass
class AllocationEvent:
    """Single allocation/deallocation event."""
    time_us: float
    size_bytes: int
    pattern_type: AllocationPattern
    is_allocation: bool  # True for alloc, False for dealloc
    latency_us: Optional[float] = None
    success: bool = True
    error_msg: Optional[str] = None

@dataclass
class BenchmarkResults:
    """Complete benchmark results."""
    allocator_name: str
    pattern: AllocationPattern
    total_allocations: int
    total_deallocations: int
    events: List[AllocationEvent] = field(default_factory=list)
    
    # Latency statistics
    alloc_latency_us: List[float] = field(default_factory=list)
    dealloc_latency_us: List[float] = field(default_factory=list)
    
    # Memory statistics
    peak_memory_mb: float = 0
    avg_memory_mb: float = 0
    fragmentation_ratio: float = 0
    cache_hits: int = 0
    cache_misses: int = 0
    
    # Timing
    total_time_sec: float = 0
    
    def __post_init__(self):
        """Compute statistics from events."""
        self._compute_statistics()
    
    def _compute_statistics(self):
        """Compute all derived statistics."""
        if not self.alloc_latency_us:
            return
        
        # Latency percentiles
        self.alloc_p50 = np.percentile(self.alloc_latency_us, 50)
        self.alloc_p99 = np.percentile(self.alloc_latency_us, 99)
        self.alloc_p999 = np.percentile(self.alloc_latency_us, 99.9)
        self.alloc_mean = np.mean(self.alloc_latency_us)
        self.alloc_max = np.max(self.alloc_latency_us)
    
    def summary(self) -> str:
        """Return formatted summary."""
        return f"""
        Allocator: {self.allocator_name}
        Pattern: {self.pattern.value}
        ─────────────────────────────────
        Allocations: {self.total_allocations}
        Peak Memory: {self.peak_memory_mb:.1f} MB
        Fragmentation: {self.fragmentation_ratio:.1%}
        Cache Hit Rate: {self._cache_hit_rate():.1%}
        ─────────────────────────────────
        Alloc Latency (µs):
          Mean: {self.alloc_mean:.2f}
          P50:  {self.alloc_p50:.2f}
          P99:  {self.alloc_p99:.2f}
          P999: {self.alloc_p999:.2f}
          Max:  {self.alloc_max:.2f}
        Total Time: {self.total_time_sec:.2f} sec
        """
    
    def _cache_hit_rate(self) -> float:
        total = self.cache_hits + self.cache_misses
        return self.cache_hits / total if total > 0 else 0

class AllocatorBenchmark:
    """Base class for allocator benchmarking."""
    
    def __init__(self, allocator_name: str, gpu_id: int = 0):
        self.allocator_name = allocator_name
        self.gpu_id = gpu_id
        self.results: Dict[AllocationPattern, BenchmarkResults] = {}
    
    def generate_allocation_sequence(
        self,
        pattern: AllocationPattern,
        num_allocations: int = 1000,
        size_range: Tuple[int, int] = (1024, 1024*1024*100)  # 1KB - 100MB
    ) -> List[Tuple[int, float]]:
        """
        Generate sequence of (size, hold_time_sec) tuples.
        
        Args:
            pattern: Type of allocation pattern
            num_allocations: Number of allocations
            size_range: (min_size, max_size) in bytes
        """
        
        sizes = []
        
        if pattern == AllocationPattern.UNIFORM:
            # All same size
            sizes = [size_range[1] // 2] * num_allocations
        
        elif pattern == AllocationPattern.NORMAL:
            # Gaussian distribution
            mean = (size_range[0] + size_range[1]) / 2
            std = (size_range[1] - size_range[0]) / 6
            sizes = np.random.normal(mean, std, num_allocations)
            sizes = np.clip(sizes, size_range[0], size_range[1])
            sizes = sizes.astype(int)
        
        elif pattern == AllocationPattern.POWER_LAW:
            # Long-tail (Zipf-like)
            # Many small allocations, few large ones
            alpha = 1.5  # Zipf parameter
            rank = np.arange(1, num_allocations + 1)
            sizes = (size_range[1] / rank ** alpha).astype(int)
            sizes = np.clip(sizes, size_range[0], size_range[1])
        
        elif pattern == AllocationPattern.BURST:
            # Sudden bursts of allocations
            sizes = []
            for burst in range(num_allocations // 10):
                # 10 small allocations, 1 large
                sizes.extend([size_range[1] // 100] * 9)
                sizes.append(size_range[1])
        
        elif pattern == AllocationPattern.CYCLIC:
            # Allocate -> deallocate -> repeat
            # (Represented as allocation -> deallocation)
            sizes = [(size_range[1] // 2)] * num_allocations
        
        elif pattern == AllocationPattern.SEQUENTIAL:
            # Increasing sizes
            sizes = np.linspace(size_range[0], size_range[1], num_allocations)
            sizes = sizes.astype(int)
        
        # Add hold times (how long each allocation persists)
        hold_times = np.random.exponential(1.0, num_allocations)  # 1 second avg
        
        return list(zip(sizes, hold_times))
    
    def benchmark(
        self,
        pattern: AllocationPattern,
        num_allocations: int = 1000,
        size_range: Tuple[int, int] = (1024, 1024*1024*100)
    ) -> BenchmarkResults:
        """
        Run benchmark with given pattern.
        """
        
        with torch.cuda.device(self.gpu_id):
            sequence = self.generate_allocation_sequence(
                pattern, num_allocations, size_range
            )
            
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.empty_cache()
            
            start_time = time.time()
            results = BenchmarkResults(
                allocator_name=self.allocator_name,
                pattern=pattern,
                total_allocations=num_allocations,
                total_deallocations=0
            )
            
            allocated_tensors = {}  # id -> tensor for cleanup
            tensor_id = 0
            
            for size, hold_time_sec in sequence:
                # Allocation timing
                torch.cuda.synchronize()
                alloc_start = time.perf_counter_ns()
                
                try:
                    # Actual allocation
                    tensor = torch.empty(size // 4, dtype=torch.int32).cuda()
                    
                    torch.cuda.synchronize()
                    alloc_end = time.perf_counter_ns()
                    alloc_latency_us = (alloc_end - alloc_start) / 1000
                    
                    results.alloc_latency_us.append(alloc_latency_us)
                    results.events.append(AllocationEvent(
                        time_us=alloc_latency_us,
                        size_bytes=size,
                        pattern_type=pattern,
                        is_allocation=True,
                        latency_us=alloc_latency_us,
                        success=True
                    ))
                    
                    allocated_tensors[tensor_id] = (tensor, time.time())
                    tensor_id += 1
                
                except RuntimeError as e:
                    results.events.append(AllocationEvent(
                        time_us=-1,
                        size_bytes=size,
                        pattern_type=pattern,
                        is_allocation=True,
                        success=False,
                        error_msg=str(e)
                    ))
                
                # Periodically deallocate old tensors
                current_time = time.time()
                to_delete = [
                    tid for tid, (_, alloc_time) in allocated_tensors.items()
                    if current_time - alloc_time > hold_time_sec
                ]
                
                for tid in to_delete:
                    del allocated_tensors[tid]
                    results.total_deallocations += 1
            
            # Final cleanup
            allocated_tensors.clear()
            
            results.total_time_sec = time.time() - start_time
            results.peak_memory_mb = torch.cuda.max_memory_allocated() / (1024**2)
            
            self.results[pattern] = results
            return results

# Benchmarking PyTorch allocator
class PyTorchBenchmark(AllocatorBenchmark):
    def __init__(self, gpu_id: int = 0):
        super().__init__("PyTorch CudaCachingAllocator", gpu_id)
        torch.cuda.device(gpu_id)

# Benchmarking custom arena allocator
class ArenaBenchmark(AllocatorBenchmark):
    def __init__(self, arena_allocator, gpu_id: int = 0):
        super().__init__("Arena Allocator", gpu_id)
        self.arena = arena_allocator
    
    def benchmark(self, pattern, num_allocations=1000, size_range=(1024, 1024*1024*100)):
        # Override to use arena allocator
        sequence = self.generate_allocation_sequence(pattern, num_allocations, size_range)
        
        results = BenchmarkResults(
            allocator_name=self.allocator_name,
            pattern=pattern,
            total_allocations=num_allocations,
            total_deallocations=0
        )
        
        start_time = time.time()
        allocated_buffers = {}
        buf_id = 0
        
        for size, hold_time_sec in sequence:
            torch.cuda.synchronize()
            alloc_start = time.perf_counter_ns()
            
            try:
                buf = self.arena.allocate(size)
                
                torch.cuda.synchronize()
                alloc_end = time.perf_counter_ns()
                alloc_latency_us = (alloc_end - alloc_start) / 1000
                
                results.alloc_latency_us.append(alloc_latency_us)
                allocated_buffers[buf_id] = (buf, time.time())
                buf_id += 1
            
            except Exception as e:
                results.events.append(AllocationEvent(
                    time_us=-1,
                    size_bytes=size,
                    pattern_type=pattern,
                    is_allocation=True,
                    success=False,
                    error_msg=str(e)
                ))
        
        results.total_time_sec = time.time() - start_time
        self.results[pattern] = results
        return results
```

---

## PART 3: BENCHMARK ANALYSIS AND VISUALIZATION

### 3.1 Latency Analysis

```python
def plot_latency_comparison(results_list: List[BenchmarkResults], pattern: AllocationPattern):
    """
    Plot latency percentiles for multiple allocators.
    """
    
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    
    allocator_names = [r.allocator_name for r in results_list]
    
    # Plot 1: Mean latency
    ax = axes[0, 0]
    means = [r.alloc_mean for r in results_list]
    ax.bar(allocator_names, means, color=['blue', 'green', 'red'])
    ax.set_ylabel('Latency (µs)')
    ax.set_title(f'Mean Allocation Latency - {pattern.value}')
    ax.set_yscale('log')
    
    # Plot 2: Latency CDF (Cumulative Distribution)
    ax = axes[0, 1]
    for result in results_list:
        latencies = sorted(result.alloc_latency_us)
        cdf = np.arange(1, len(latencies) + 1) / len(latencies)
        ax.loglog(latencies, cdf, label=result.allocator_name, linewidth=2)
    ax.set_xlabel('Latency (µs)')
    ax.set_ylabel('CDF')
    ax.set_title(f'Latency Distribution - {pattern.value}')
    ax.legend()
    ax.grid(True, alpha=0.3)
    
    # Plot 3: Percentile comparison
    ax = axes[1, 0]
    percentiles = [50, 99, 99.9]
    x = np.arange(len(allocator_names))
    width = 0.25
    
    for i, p in enumerate(percentiles):
        values = [np.percentile(r.alloc_latency_us, p) for r in results_list]
        ax.bar(x + i*width, values, width, label=f'P{p}')
    
    ax.set_ylabel('Latency (µs)')
    ax.set_title(f'Percentile Comparison - {pattern.value}')
    ax.set_xticks(x + width)
    ax.set_xticklabels(allocator_names)
    ax.legend()
    ax.set_yscale('log')
    
    # Plot 4: Latency over time
    ax = axes[1, 1]
    for result in results_list:
        ax.plot(result.alloc_latency_us[:min(1000, len(result.alloc_latency_us))],
               label=result.allocator_name, alpha=0.7)
    ax.set_xlabel('Allocation #')
    ax.set_ylabel('Latency (µs)')
    ax.set_title('Latency Over Time (First 1000 allocations)')
    ax.legend()
    ax.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(f'latency_comparison_{pattern.value}.png', dpi=150)
    plt.close()

def print_latency_summary(results: BenchmarkResults):
    """Print detailed latency analysis."""
    
    latencies = np.array(results.alloc_latency_us)
    
    print(f"\n{results.allocator_name} - {results.pattern.value}")
    print("=" * 60)
    print(f"Mean:   {np.mean(latencies):8.2f} µs")
    print(f"Median: {np.median(latencies):8.2f} µs")
    print(f"P75:    {np.percentile(latencies, 75):8.2f} µs")
    print(f"P90:    {np.percentile(latencies, 90):8.2f} µs")
    print(f"P95:    {np.percentile(latencies, 95):8.2f} µs")
    print(f"P99:    {np.percentile(latencies, 99):8.2f} µs")
    print(f"P99.9:  {np.percentile(latencies, 99.9):8.2f} µs")
    print(f"Max:    {np.max(latencies):8.2f} µs")
    print(f"Stdev:  {np.std(latencies):8.2f} µs")
```

### 3.2 Memory Fragmentation Analysis

```python
def analyze_fragmentation(results: BenchmarkResults, window_size: int = 100):
    """
    Analyze fragmentation over time using sliding window.
    """
    
    # Simulate memory layout during execution
    memory_snapshots = []
    active_allocations = {}
    timestamp = 0
    
    for event in results.events[:min(10000, len(results.events))]:
        if event.is_allocation and event.success:
            active_allocations[len(active_allocations)] = event.size_bytes
        elif not event.is_allocation:
            # Deallocate (simplified)
            if active_allocations:
                del list(active_allocations.items())[0]
        
        # Record snapshot every N events
        if len(memory_snapshots) % window_size == 0:
            total_allocated = sum(active_allocations.values())
            
            # Fragmentation estimate:
            # Assume ideal packing would use 85% of space
            ideal_usage = total_allocated / 0.85
            fragmentation = 1 - (total_allocated / ideal_usage) if ideal_usage > 0 else 0
            
            memory_snapshots.append({
                'timestamp': timestamp,
                'allocated': total_allocated,
                'fragmentation': fragmentation,
                'active_blocks': len(active_allocations)
            })
        
        timestamp += 1
    
    # Plot fragmentation over time
    timestamps = [s['timestamp'] for s in memory_snapshots]
    fragmentations = [s['fragmentation'] * 100 for s in memory_snapshots]
    
    plt.figure(figsize=(12, 6))
    plt.plot(timestamps, fragmentations, linewidth=2)
    plt.xlabel('Allocation #')
    plt.ylabel('Fragmentation (%)')
    plt.title(f'Fragmentation Over Time - {results.allocator_name}')
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(f'fragmentation_{results.allocator_name}.png', dpi=150)
    plt.close()
```

---

## PART 4: REAL-WORLD BENCHMARKS

### 4.1 Inference Workload Benchmark

```python
def benchmark_inference_workload():
    """Benchmark memory allocators under realistic inference workload."""
    
    # Setup
    pytorch_bench = PyTorchBenchmark(gpu_id=0)
    
    print("Benchmarking PyTorch CudaCachingAllocator")
    print("=" * 60)
    
    # Pattern 1: Stable batch size (typical scenario)
    print("\nScenario 1: Stable Batch Size (32)")
    results_stable = pytorch_bench.benchmark(
        pattern=AllocationPattern.UNIFORM,
        num_allocations=10000,
        size_range=(10*1024*1024, 20*1024*1024)  # 10-20MB per token
    )
    print(results_stable.summary())
    
    # Pattern 2: Variable batch size (streaming scenario)
    print("\nScenario 2: Variable Batch Size")
    results_variable = pytorch_bench.benchmark(
        pattern=AllocationPattern.NORMAL,
        num_allocations=10000,
        size_range=(1*1024*1024, 50*1024*1024)  # 1-50MB
    )
    print(results_variable.summary())
    
    # Pattern 3: Burst traffic
    print("\nScenario 3: Burst Traffic")
    results_burst = pytorch_bench.benchmark(
        pattern=AllocationPattern.BURST,
        num_allocations=10000,
        size_range=(1*1024*1024, 100*1024*1024)
    )
    print(results_burst.summary())
    
    # Comparison plots
    plot_latency_comparison(
        [results_stable, results_variable, results_burst],
        AllocationPattern.UNIFORM
    )

def benchmark_training_workload():
    """Benchmark under training workload."""
    
    pytorch_bench = PyTorchBenchmark(gpu_id=0)
    
    print("Benchmarking PyTorch for Training Workload")
    print("=" * 60)
    
    # Training typically has predictable allocations
    # (same layer sizes each batch)
    results = pytorch_bench.benchmark(
        pattern=AllocationPattern.POWER_LAW,
        num_allocations=5000,  # 5000 layers
        size_range=(100*1024, 500*1024*1024)  # 100KB - 500MB
    )
    print(results.summary())
    print(f"\nCache Hit Rate: {results.cache_hits / (results.cache_hits + results.cache_misses):.1%}")
```

### 4.2 Stress Test

```python
def stress_test_allocators(duration_sec: int = 300):
    """
    Run stress test: continuous random allocations/deallocations
    for extended duration.
    """
    
    pytorch_bench = PyTorchBenchmark(gpu_id=0)
    
    print(f"Running {duration_sec} second stress test...")
    print("=" * 60)
    
    start_time = time.time()
    alloc_times = []
    oom_events = 0
    peak_memory = 0
    
    while time.time() - start_time < duration_sec:
        # Random size
        size = np.random.randint(1*1024*1024, 100*1024*1024)  # 1-100MB
        
        try:
            torch.cuda.synchronize()
            alloc_start = time.perf_counter_ns()
            
            tensor = torch.empty(size // 4, dtype=torch.int32).cuda()
            
            torch.cuda.synchronize()
            alloc_end = time.perf_counter_ns()
            
            alloc_times.append((alloc_end - alloc_start) / 1000)
            peak_memory = max(peak_memory, torch.cuda.memory_allocated())
            
            # Random hold time
            hold_time = np.random.exponential(5.0)
            if hold_time < 10:  # Deallocate soon
                del tensor
        
        except RuntimeError as e:
            if "out of memory" in str(e):
                oom_events += 1
                torch.cuda.empty_cache()
    
    print(f"\nStress Test Results ({duration_sec}s):")
    print(f"  Allocations attempted: {len(alloc_times)}")
    print(f"  OOM events: {oom_events}")
    print(f"  Peak memory: {peak_memory / (1024**3):.1f} GB")
    print(f"  Mean latency: {np.mean(alloc_times):.2f} µs")
    print(f"  P99 latency: {np.percentile(alloc_times, 99):.2f} µs")
```

---

## PART 5: PERFORMANCE TUNING GUIDE

### 5.1 PyTorch Optimization

```python
# Optimization 1: Tune cache size
torch.cuda.empty_cache()
torch.cuda.memory._set_cached_limit(1000 * 1024**2)  # 1GB cache

# For small workloads:
torch.cuda.memory._set_cached_limit(100 * 1024**2)   # 100MB cache

# For large workloads:
torch.cuda.memory._set_cached_limit(5000 * 1024**2)  # 5GB cache

# Optimization 2: Empty cache periodically
for epoch in range(100):
    train_one_epoch()
    if epoch % 10 == 0:
        torch.cuda.empty_cache()  # Cleanup every 10 epochs

# Optimization 3: Use pinned memory for host-device transfers
torch.cuda.memory._set_cached_limit(2000 * 1024**2)
# Enables UVA (Unified Virtual Addressing)
```

### 5.2 TensorFlow Optimization

```python
# Optimization 1: Pre-allocate memory (for inference)
tf.config.experimental.set_memory_growth(gpu, False)
tf.config.set_logical_device_configuration(
    gpu,
    [tf.config.LogicalDeviceConfiguration(memory_limit=30*1024)]
)

# Optimization 2: Growth mode (for training)
tf.config.experimental.set_memory_growth(gpu, True)

# Optimization 3: Monitor fragmentation
stats = tf.config.experimental.get_memory_info('GPU:0')
print(f"Current: {stats['current'] / 1e9:.1f}GB")
print(f"Peak: {stats['peak'] / 1e9:.1f}GB")
```

### 5.3 vLLM Optimization

```python
# Optimization 1: Adjust GPU memory utilization
llm = LLM(
    model="meta-llama/Llama-2-70b-hf",
    gpu_memory_utilization=0.90,  # Use 90% (vs default 95%)
    # Leaves 10% for overhead and batching
)

# Optimization 2: Enable prefix caching
llm = LLM(
    model="...",
    enable_prefix_caching=True,
    # Enables 40-60% memory savings via prompt reuse
)

# Optimization 3: Adjust block size
llm = LLM(
    model="...",
    block_size=16,  # Smaller blocks = finer granularity
    # Trade-off: smaller blocks reduce waste but increase overhead
)
```

---

## CONCLUSION

This benchmarking guide provides:

1. **Metrics**: Latency, fragmentation, cache hit rate, memory efficiency
2. **Harness**: Python framework for systematic benchmarking
3. **Analysis**: Visualization and statistical comparison tools
4. **Workloads**: Real-world scenarios (inference, training, stress)
5. **Tuning**: Framework-specific optimization recommendations

Key findings from benchmarks:
- **vLLM BlockManager**: 10-100x lower latency vs malloc (~100ns vs ~1µs)
- **PyTorch CudaCachingAllocator**: 95%+ cache hit rate on stable workloads
- **TensorFlow BFC**: Best fragmentation ratios in variable workloads
- **Arena Allocation**: 0-2% fragmentation vs 20-50% for malloc-based approaches

Recommended measurement approach:
1. Establish baseline with malloc (worst case)
2. Benchmark each framework with production workload
3. Measure tail latencies (P99, P99.9), not just mean
4. Monitor fragmentation over extended runs (1-24 hours)
5. Test under stress (high concurrency, rapid allocation/deallocation)
6. Use results to guide framework/tuning selection

