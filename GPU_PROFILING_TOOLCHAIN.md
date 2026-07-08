# GPU Profiling Toolchain: Advanced Workflows

## Complete Integration Framework

This document provides integrated workflows combining multiple profiling tools for comprehensive GPU bottleneck analysis.

---

## 1. Multi-Tool Profiling Pipeline

### 1.1 Automated End-to-End Profiling

```bash
#!/bin/bash
# Complete GPU profiling pipeline

set -e

APP="${1:-./your_app}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_DIR="gpu_profiles_${TIMESTAMP}"
mkdir -p "$OUTPUT_DIR"

echo "GPU Bottleneck Analysis Pipeline"
echo "================================="
echo "Application: $APP"
echo "Output Directory: $OUTPUT_DIR"
echo

# 1. NVIDIA-SMI baseline
echo "[1/6] Collecting baseline system metrics..."
nvidia-smi > "$OUTPUT_DIR/nvidia-smi-baseline.txt"
nvidia-smi --query-gpu=name,driver_version,vbios_version --format=csv,noheader \
    > "$OUTPUT_DIR/gpu_info.txt"

# 2. Nsight Systems (system-level trace)
echo "[2/6] Running Nsight Systems..."
nsys profile \
    --gpu-metrics-device all \
    --cuda-memory-unit byte \
    -o "$OUTPUT_DIR/nsight_sys" \
    $APP 2>&1 | tee "$OUTPUT_DIR/nsys_output.log" || echo "Nsight Systems not available"

# 3. Nsight Compute (kernel-level analysis)
echo "[3/6] Running Nsight Compute..."
ncu --set full \
    --import none \
    --export nsight_compute \
    -o "$OUTPUT_DIR/nsight_compute.ncu-rep" \
    $APP 2>&1 | tee "$OUTPUT_DIR/ncu_output.log" || echo "Nsight Compute not available"

# 4. PyTorch Profiler (if applicable)
echo "[4/6] Running PyTorch Profiler..."
python - <<'EOF' > "$OUTPUT_DIR/pytorch_profile.txt" 2>&1 || echo "PyTorch Profiler skipped"
import torch
from torch.profiler import profile, ProfilerActivity

if torch.cuda.is_available():
    with profile(activities=[ProfilerActivity.CUDA]) as prof:
        # Application code would go here
        pass
    print(prof.key_averages().table(sort_by="cuda_time_total", row_limit=20))
EOF

# 5. Generate summary reports
echo "[5/6] Generating summary reports..."
if [ -f "$OUTPUT_DIR/nsight_compute.ncu-rep" ]; then
    ncu --export csv -i "$OUTPUT_DIR/nsight_compute.ncu-rep" \
        > "$OUTPUT_DIR/nsight_compute.csv" 2>/dev/null || true
fi

if [ -f "$OUTPUT_DIR/nsight_sys.nsys-rep" ]; then
    nsys stats -r cuda_api_sum -f csv -o "$OUTPUT_DIR/nsys_cuda_api.csv" \
        "$OUTPUT_DIR/nsight_sys.nsys-rep" 2>/dev/null || true
    nsys stats -r cuda_gpu_kern_sum -f csv -o "$OUTPUT_DIR/nsys_kernels.csv" \
        "$OUTPUT_DIR/nsight_sys.nsys-rep" 2>/dev/null || true
fi

# 6. Analysis
echo "[6/6] Running bottleneck analysis..."
python - <<'PYEOF' > "$OUTPUT_DIR/bottleneck_analysis.txt" 2>&1 || true
import json
import csv
from pathlib import Path

output_dir = "$OUTPUT_DIR"

# Parse Nsight Compute CSV
ncu_csv = Path(output_dir) / "nsight_compute.csv"
if ncu_csv.exists():
    print("=== NSIGHT COMPUTE ANALYSIS ===")
    with open(ncu_csv) as f:
        reader = csv.DictReader(f)
        for row in list(reader)[:5]:  # Top 5 kernels
            print(f"Kernel: {row.get('Kernel Name', 'Unknown')}")
            print(f"  SM Utilization: {row.get('SM %', 'N/A')}")
            print(f"  Memory Utilization: {row.get('Memory %%', 'N/A')}")
            print()

# Parse Nsight Systems kernel CSV
nsys_csv = Path(output_dir) / "nsys_kernels.csv"
if nsys_csv.exists():
    print("\n=== NSIGHT SYSTEMS KERNEL SUMMARY ===")
    with open(nsys_csv) as f:
        reader = csv.DictReader(f)
        for row in list(reader)[:5]:
            print(f"Kernel: {row.get('Kernel Name', 'Unknown')}")
            print(f"  Count: {row.get('Count', 'N/A')}")
            print(f"  Total Time: {row.get('Total Time', 'N/A')}")
            print()
PYEOF

echo
echo "================================="
echo "Analysis complete!"
echo "Output files:"
ls -lh "$OUTPUT_DIR"/ | awk '{print $9, "("$5")"}'
```

### 1.2 Parallel Profiling (Non-Blocking)

```bash
#!/bin/bash
# Run multiple profilers in parallel for faster turnaround

APP="./your_app"
OUTPUT_DIR="profiles_$(date +%s)"
mkdir -p "$OUTPUT_DIR"

# Start profilers in background
nsys profile -o "$OUTPUT_DIR/nsys" $APP &
NSYS_PID=$!

sleep 0.5  # Stagger starts

ncu -o "$OUTPUT_DIR/ncu.ncu-rep" $APP &
NCU_PID=$!

# Optional: Memory profiling
nvidia-smi dmon -s pucvmet -o N -f "$OUTPUT_DIR/metrics.csv" &
DMON_PID=$!

# Wait for completion
wait $NSYS_PID $NCU_PID
kill $DMON_PID 2>/dev/null || true

echo "Profiling complete: $OUTPUT_DIR"
```

---

## 2. Advanced Nsight Compute Workflows

### 2.1 Kernel-Specific Deep Analysis

```bash
#!/bin/bash
# Deep profile specific kernel

KERNEL_NAME="$1"  # e.g., "volta_gemm_64x64"
APP="./your_app"

# Profile with maximum detail
ncu \
    --set full \
    --kernel-name "${KERNEL_NAME}.*" \
    --launch-skip 0 \
    --launch-count 1 \
    -o report.ncu-rep \
    $APP

# Export detailed metrics
ncu --export report.csv -i report.ncu-rep

# Create roofline data
ncu --export roofline.csv --set roofline -i report.ncu-rep

echo "Analysis for $KERNEL_NAME:"
echo "  CSV: report.csv"
echo "  Roofline: roofline.csv"
```

### 2.2 Memory Hierarchy Analysis

```bash
#!/usr/bin/env python3
"""Analyze memory hierarchy from Nsight Compute data"""

import subprocess
import json
import sys
from pathlib import Path

def extract_memory_metrics(ncu_report):
    """Extract memory hierarchy metrics from NCU report"""
    
    # Export to JSON
    subprocess.run(
        ['ncu', '--export', 'temp.json', '-i', ncu_report],
        capture_output=True
    )
    
    with open('temp.json') as f:
        data = json.load(f)
    
    print("=" * 70)
    print("MEMORY HIERARCHY ANALYSIS")
    print("=" * 70)
    
    for kernel in data.get('kernels', []):
        print(f"\nKernel: {kernel.get('kernelName', 'Unknown')}")
        
        # L1 Cache
        l1_hits = kernel.get('l1tex__t_cache_hit_rate', {}).get('avg', 0)
        l1_requests = kernel.get('l1tex__t_requests_sample', {}).get('sum', 0)
        print(f"\nL1 Cache:")
        print(f"  Hit Rate: {l1_hits:.1f}%")
        print(f"  Requests: {l1_requests:.2e}")
        
        # L2 Cache
        l2_hits = kernel.get('lts__t_cache_hit_rate', {}).get('avg', 0)
        print(f"\nL2 Cache:")
        print(f"  Hit Rate: {l2_hits:.1f}%")
        
        # Global Memory
        dram_bw = kernel.get('dram__throughput.avg.pct_of_peak_sustained_elapsed', 0)
        dram_reads = kernel.get('dram__read_bytes', {}).get('sum', 0)
        dram_writes = kernel.get('dram__write_bytes', {}).get('sum', 0)
        
        print(f"\nDRAM:")
        print(f"  Bandwidth Utilization: {dram_bw:.1f}% of peak")
        print(f"  Total Reads: {dram_reads / 1e9:.2f} GB")
        print(f"  Total Writes: {dram_writes / 1e9:.2f} GB")
        
        # Shared Memory
        smem_util = kernel.get('smem__utilization.avg', 0)
        smem_bw = kernel.get('smem__throughput.avg.pct_of_peak_sustained_elapsed', 0)
        
        print(f"\nShared Memory:")
        print(f"  Utilization: {smem_util:.1f}%")
        print(f"  Bandwidth: {smem_bw:.1f}% of peak")
        
        # Memory Efficiency
        arithmetic_intensity = kernel.get('smsp__inst_executed_op_dmem_realtime', {}).get('per_cycle_elapsed', 0)
        
        print(f"\nMemory Efficiency:")
        print(f"  Arithmetic Intensity: {arithmetic_intensity:.2f} FLOPS/Byte")
        
        # Recommendations
        if l1_hits < 40:
            print("\n  ⚠ Poor L1 hit rate: Consider data reuse optimization")
        if dram_bw > 80:
            print("\n  ⚠ High DRAM bandwidth: Kernel is memory-bound")
        if arithmetic_intensity < 5:
            print("\n  ⚠ Low arithmetic intensity: Increase compute per memory access")

if __name__ == '__main__':
    ncu_report = sys.argv[1] if len(sys.argv) > 1 else 'report.ncu-rep'
    
    if not Path(ncu_report).exists():
        print(f"Error: Report file not found: {ncu_report}")
        sys.exit(1)
    
    extract_memory_metrics(ncu_report)
```

### 2.3 Comparing Kernel Versions

```bash
#!/bin/bash
# Compare performance across kernel versions

VERSIONS=("v1" "v2" "v3")
OUTPUT_CSV="kernel_comparison.csv"

echo "Kernel,SM%,Occupancy%,Memory%,TFLOPS,Status" > "$OUTPUT_CSV"

for version in "${VERSIONS[@]}"; do
    echo "Profiling $version..."
    
    ncu --set full -o "report_${version}.ncu-rep" ./app_${version}
    
    # Extract key metrics
    SM_UTIL=$(ncu -i "report_${version}.ncu-rep" --export csv 2>/dev/null | \
              grep "SM Utilization" | awk -F, '{print $NF}' | head -1)
    OCCUPANCY=$(ncu -i "report_${version}.ncu-rep" --export csv 2>/dev/null | \
                grep "Occupancy" | awk -F, '{print $NF}' | head -1)
    MEM_BW=$(ncu -i "report_${version}.ncu-rep" --export csv 2>/dev/null | \
             grep "Memory.*Util" | awk -F, '{print $NF}' | head -1)
    
    echo "$version,$SM_UTIL,$OCCUPANCY,$MEM_BW,0.0,OK" >> "$OUTPUT_CSV"
done

echo "Comparison saved to: $OUTPUT_CSV"
```

---

## 3. PyTorch Profiler Advanced Usage

### 3.1 Hierarchical Profiling

```python
import torch
from torch.profiler import profile, record_function, ProfilerActivity

def profile_with_hierarchy():
    model = create_model().cuda()
    input_data = torch.randn(32, 3, 224, 224).cuda()
    
    with profile(
        activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA],
        record_shapes=True,
        profile_memory=True,
        with_stack=True,
    ) as prof:
        with record_function("forward_pass"):
            output = model(input_data)
        
        with record_function("loss_computation"):
            loss = torch.nn.functional.cross_entropy(output, targets)
        
        with record_function("backward_pass"):
            loss.backward()
    
    # Print hierarchical view
    print(prof.key_averages(group_by_stack_n=5).table(
        sort_by="cuda_time_total",
        row_limit=20
    ))
    
    # Export for visualization
    prof.export_chrome_trace("trace.json")
```

### 3.2 Memory Profiling

```python
import torch
from torch.profiler import profile, ProfilerActivity

def analyze_memory():
    model = create_model().cuda()
    input_data = torch.randn(256, 3, 224, 224).cuda()
    
    with profile(
        activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA],
        profile_memory=True,
        record_shapes=True,
    ) as prof:
        output = model(input_data)
    
    # Memory stats
    print("=== MEMORY STATISTICS ===")
    print(prof.key_averages(group_by_stack_n=5).table(
        sort_by="self_cuda_memory_usage",
        row_limit=20
    ))
    
    # Peak memory
    cuda_memory = torch.cuda.memory_stats()
    print(f"\nPeak Memory: {cuda_memory['reserved_bytes.all.peak'] / 1e9:.2f} GB")
    print(f"Allocated: {cuda_memory['allocated_bytes.all.peak'] / 1e9:.2f} GB")
```

### 3.3 Identifying Bottleneck Layers

```python
import torch
from torch.profiler import profile, ProfilerActivity
from typing import Dict, List, Tuple

class LayerBottleneckAnalyzer:
    
    @staticmethod
    def analyze_model(model, input_shape, top_k=10):
        """Identify bottleneck layers in model"""
        
        input_data = torch.randn(*input_shape).cuda()
        
        with profile(
            activities=[ProfilerActivity.CUDA],
            record_shapes=True,
        ) as prof:
            output = model(input_data)
        
        events = prof.key_averages()
        
        # Group by layer/kernel name
        kernel_times = {}
        for event in events:
            if event.cuda_time_total > 0:
                kernel_name = event.name.split('[')[0]  # Remove shape info
                
                if kernel_name not in kernel_times:
                    kernel_times[kernel_name] = 0
                kernel_times[kernel_name] += event.cuda_time_total
        
        # Rank by time
        sorted_kernels = sorted(
            kernel_times.items(),
            key=lambda x: x[1],
            reverse=True
        )
        
        print("=== TOP BOTTLENECK LAYERS ===")
        total_time = sum(t for _, t in sorted_kernels)
        
        for i, (name, time) in enumerate(sorted_kernels[:top_k], 1):
            percent = (time / total_time * 100) if total_time > 0 else 0
            print(f"{i:2d}. {name:40s} {time/1e6:8.2f}ms ({percent:5.1f}%)")
        
        return sorted_kernels

# Usage
analyzer = LayerBottleneckAnalyzer()
bottlenecks = analyzer.analyze_model(
    model=model,
    input_shape=(1, 3, 224, 224),
    top_k=15
)
```

---

## 4. TensorFlow Profiler Integration

### 4.1 TensorFlow GPU Profiling

```python
import tensorflow as tf
from tensorflow.profiler import experimental as profexp

# Enable profiling
profexp.start('logs')

# Run model
model = create_model()
dataset = create_dataset()

for batch in dataset.take(10):
    predictions = model(batch)

profexp.stop()

# Analyze via TensorBoard
# tensorboard --logdir logs --port=6006

# Programmatic analysis
profile_data = profexp.trace('localhost:6009', 'profile', 100)
```

### 4.2 TensorFlow GPU Timeline

```python
import tensorflow as tf
from tensorflow.profiler import experimental as profexp

def profile_with_timeline():
    # Create profiler options
    options = tf.profiler.experimental.Options(
        host_tracer_level=3,
        python_tracer_level=1,
        device_tracer_level=1,
    )
    
    # Profile
    with tf.profiler.experimental.Trace('train'):
        # Model training code
        for step in range(100):
            model(input_data)
    
    # Export
    profexp.save('logs', profexp.latest())
```

---

## 5. Custom Bottleneck Detection Harness

### 5.1 Unified Profiler Interface

```python
#!/usr/bin/env python3
"""Unified GPU profiling interface"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Any, List
import subprocess
import json

class ProfilerType(Enum):
    NSIGHT_COMPUTE = "nsight_compute"
    PYTORCH = "pytorch"
    TENSORFLOW = "tensorflow"
    OMNIPERF = "omniperf"

class BottleneckType(Enum):
    COMPUTE_BOUND = "compute_bound"
    MEMORY_BOUND = "memory_bound"
    LATENCY_BOUND = "latency_bound"
    SYNCHRONIZATION = "synchronization"
    UNKNOWN = "unknown"

@dataclass
class ProfilingResult:
    """Result from profiling run"""
    profiler_type: ProfilerType
    bottleneck_type: BottleneckType
    confidence: float
    metrics: Dict[str, Any]
    recommendations: List[str]
    raw_data: Any

class GPUProfiler(ABC):
    """Abstract base class for GPU profilers"""
    
    @abstractmethod
    def profile(self, app_path: str, *args) -> ProfilingResult:
        pass
    
    @abstractmethod
    def analyze(self, result: ProfilingResult) -> Dict[str, Any]:
        pass

class NSightComputeProfiler(GPUProfiler):
    """Nsight Compute profiler implementation"""
    
    def profile(self, app_path: str, *args) -> ProfilingResult:
        # Run ncu
        result = subprocess.run(
            ['ncu', '--set', 'full', '-o', 'report.ncu-rep', app_path] + list(args),
            capture_output=True,
            text=True
        )
        
        # Parse results
        bottleneck_type, confidence = self._classify_bottleneck('report.ncu-rep')
        metrics = self._extract_metrics('report.ncu-rep')
        recommendations = self._generate_recommendations(bottleneck_type)
        
        return ProfilingResult(
            profiler_type=ProfilerType.NSIGHT_COMPUTE,
            bottleneck_type=bottleneck_type,
            confidence=confidence,
            metrics=metrics,
            recommendations=recommendations,
            raw_data='report.ncu-rep'
        )
    
    def _classify_bottleneck(self, report_path: str) -> tuple:
        """Classify bottleneck type"""
        # Implementation would parse NCU metrics
        return BottleneckType.MEMORY_BOUND, 0.85
    
    def _extract_metrics(self, report_path: str) -> Dict[str, Any]:
        """Extract key metrics from report"""
        # Implementation would extract metrics from CSV export
        return {
            'sm_utilization': 65.0,
            'memory_bandwidth': 88.0,
            'occupancy': 45.0,
        }
    
    def _generate_recommendations(self, bottleneck: BottleneckType) -> List[str]:
        """Generate optimization recommendations"""
        
        recommendations_map = {
            BottleneckType.MEMORY_BOUND: [
                "Optimize memory access patterns",
                "Use shared memory for data reuse",
                "Increase arithmetic intensity",
            ],
            BottleneckType.COMPUTE_BOUND: [
                "Use tensor cores or specialized units",
                "Increase instruction-level parallelism",
                "Optimize loop nesting",
            ],
            BottleneckType.LATENCY_BOUND: [
                "Increase independent operations",
                "Use prefetching",
                "Reduce synchronization points",
            ],
        }
        
        return recommendations_map.get(bottleneck, ["Run detailed analysis"])
    
    def analyze(self, result: ProfilingResult) -> Dict[str, Any]:
        """Deep analysis of profiling result"""
        return {
            'summary': str(result.bottleneck_type),
            'metrics': result.metrics,
            'recommendations': result.recommendations,
        }

class PyTorchProfiler(GPUProfiler):
    """PyTorch built-in profiler implementation"""
    
    def profile(self, app_path: str, *args) -> ProfilingResult:
        # Implementation would run PyTorch profiler
        # and classify bottleneck
        pass
    
    def analyze(self, result: ProfilingResult) -> Dict[str, Any]:
        pass

class UnifiedGPUProfiler:
    """Run multiple profilers and synthesize results"""
    
    def __init__(self):
        self.profilers = {
            ProfilerType.NSIGHT_COMPUTE: NSightComputeProfiler(),
            ProfilerType.PYTORCH: PyTorchProfiler(),
        }
    
    def profile_all(self, app_path: str, *args) -> List[ProfilingResult]:
        """Run all available profilers"""
        
        results = []
        for profiler_type, profiler in self.profilers.items():
            try:
                result = profiler.profile(app_path, *args)
                results.append(result)
            except Exception as e:
                print(f"Profiler {profiler_type.value} failed: {e}")
        
        return results
    
    def synthesize_results(self, results: List[ProfilingResult]) -> Dict[str, Any]:
        """Combine results from multiple profilers"""
        
        if not results:
            return {'error': 'No profiling results'}
        
        # Vote on bottleneck type
        bottleneck_votes = {}
        for result in results:
            bt = result.bottleneck_type
            bottleneck_votes[bt] = bottleneck_votes.get(bt, 0) + 1
        
        consensus_bottleneck = max(bottleneck_votes, key=bottleneck_votes.get)
        
        # Collect all recommendations
        all_recommendations = set()
        for result in results:
            all_recommendations.update(result.recommendations)
        
        # Average confidence
        avg_confidence = sum(r.confidence for r in results) / len(results)
        
        return {
            'bottleneck_type': consensus_bottleneck.value,
            'confidence': avg_confidence,
            'profilers_used': [r.profiler_type.value for r in results],
            'recommendations': list(all_recommendations),
            'detailed_results': [
                {
                    'profiler': r.profiler_type.value,
                    'bottleneck': r.bottleneck_type.value,
                    'confidence': r.confidence,
                }
                for r in results
            ]
        }

# Usage
if __name__ == '__main__':
    profiler = UnifiedGPUProfiler()
    
    results = profiler.profile_all('./gpu_app', '--batch-size', '32')
    synthesis = profiler.synthesize_results(results)
    
    print(json.dumps(synthesis, indent=2))
```

---

## 6. Continuous Monitoring

### 6.1 Real-Time Bottleneck Detector

```bash
#!/bin/bash
# Real-time GPU bottleneck monitoring

INTERVAL=2  # seconds
THRESHOLD_SM=30
THRESHOLD_MEM=80

while true; do
    # Get GPU stats
    STATS=$(nvidia-smi \
        --query-gpu=utilization.gpu,utilization.memory \
        --format=csv,noheader,nounits)
    
    SM_UTIL=$(echo $STATS | awk '{print $1}')
    MEM_UTIL=$(echo $STATS | awk '{print $2}')
    
    # Classify
    if (( $(echo "$MEM_UTIL > $THRESHOLD_MEM" | bc -l) )); then
        STATUS="MEMORY_BOUND"
    elif (( $(echo "$SM_UTIL < $THRESHOLD_SM" | bc -l) )); then
        STATUS="UNDERUTILIZED"
    else
        STATUS="NORMAL"
    fi
    
    # Print
    printf "[%s] SM:%3.0f%% MEM:%3.0f%% Status:%s\n" \
        "$(date +%H:%M:%S)" "$SM_UTIL" "$MEM_UTIL" "$STATUS"
    
    sleep $INTERVAL
done
```

### 6.2 Automated Profiling Loop

```python
#!/usr/bin/env python3
"""Continuous profiling with automatic bottleneck detection"""

import torch
import time
import threading
from collections import deque
from dataclasses import dataclass
from typing import Optional

@dataclass
class GPUSnapshot:
    timestamp: float
    sm_utilization: float
    memory_utilization: float
    memory_used: int
    memory_available: int

class ContinuousGPUMonitor:
    """Monitor GPU and detect bottlenecks in real-time"""
    
    def __init__(self, window_size=30, sample_interval=0.1):
        self.window_size = window_size
        self.sample_interval = sample_interval
        self.snapshots = deque(maxlen=window_size)
        self.running = False
        self.thread: Optional[threading.Thread] = None
    
    def start(self):
        """Start monitoring thread"""
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
    
    def stop(self):
        """Stop monitoring thread"""
        self.running = False
        if self.thread:
            self.thread.join()
    
    def _monitor_loop(self):
        """Continuous monitoring loop"""
        while self.running:
            try:
                # Get GPU stats
                torch.cuda.synchronize()
                
                props = torch.cuda.get_device_properties(0)
                mem_info = torch.cuda.mem_get_info()
                
                snapshot = GPUSnapshot(
                    timestamp=time.time(),
                    sm_utilization=self._estimate_sm_util(),
                    memory_utilization=100 * (mem_info[1] - mem_info[0]) / mem_info[1],
                    memory_used=mem_info[1] - mem_info[0],
                    memory_available=mem_info[1],
                )
                
                self.snapshots.append(snapshot)
            
            except Exception as e:
                print(f"Monitoring error: {e}")
            
            time.sleep(self.sample_interval)
    
    def _estimate_sm_util(self) -> float:
        """Estimate SM utilization (simplified)"""
        # In practice, would read from nvidia-smi or Nsight
        return 50.0  # Placeholder
    
    def get_bottleneck(self) -> Optional[str]:
        """Detect current bottleneck"""
        if len(self.snapshots) < 3:
            return None
        
        recent = list(self.snapshots)[-5:]
        avg_sm = sum(s.sm_utilization for s in recent) / len(recent)
        avg_mem = sum(s.memory_utilization for s in recent) / len(recent)
        
        if avg_mem > 80 and avg_sm < 30:
            return "MEMORY_BOUND"
        elif avg_sm < 20:
            return "UNDERUTILIZED"
        elif avg_sm > 80 and avg_mem < 40:
            return "COMPUTE_BOUND"
        
        return "NORMAL"
    
    def get_report(self) -> str:
        """Generate monitoring report"""
        if not self.snapshots:
            return "No data collected"
        
        recent = list(self.snapshots)[-10:]
        
        report = "=== CONTINUOUS GPU MONITOR REPORT ===\n"
        report += f"Samples: {len(recent)}\n"
        report += f"Avg SM Util: {sum(s.sm_utilization for s in recent) / len(recent):.1f}%\n"
        report += f"Avg Memory: {sum(s.memory_utilization for s in recent) / len(recent):.1f}%\n"
        report += f"Bottleneck: {self.get_bottleneck()}\n"
        
        return report

# Usage
monitor = ContinuousGPUMonitor()
monitor.start()

# Run your application
model(input_data)

# Check bottleneck
print(monitor.get_report())
monitor.stop()
```

---

## 7. Integration with CI/CD

### 7.1 Automated Regression Testing

```yaml
# .github/workflows/gpu-regression.yml
name: GPU Performance Regression

on: [pull_request]

jobs:
  gpu-profile:
    runs-on: gpu-runner
    
    steps:
      - uses: actions/checkout@v2
      
      - name: Setup environment
        run: |
          pip install torch nsight-compute torch-profiler
      
      - name: Profile baseline
        run: |
          ncu -o baseline.ncu-rep python benchmark.py
          python analyze_profile.py baseline.ncu-rep > baseline.txt
      
      - name: Compare with main
        run: |
          git checkout main
          ncu -o main.ncu-rep python benchmark.py
          python compare_profiles.py main.ncu-rep baseline.ncu-rep
      
      - name: Post results
        if: always()
        run: |
          python format_results.py > comment.md
          gh pr comment -F comment.md
```

### 7.2 Performance Threshold Checks

```python
#!/usr/bin/env python3
"""GPU Performance regression detection"""

import json
from pathlib import Path
from typing import Dict, Tuple

class PerformanceRegression:
    
    THRESHOLDS = {
        'sm_utilization': (-10, 0),      # min drop, max drop (%)
        'memory_bandwidth': (-15, 0),     # min drop, max drop (%)
        'occupancy': (-20, 0),            # min drop, max drop (%)
    }
    
    @staticmethod
    def compare_profiles(
        baseline: Dict,
        current: Dict,
    ) -> Tuple[bool, Dict]:
        """Compare two profiles for regressions"""
        
        regressions = []
        
        for metric, (min_threshold, max_threshold) in PerformanceRegression.THRESHOLDS.items():
            baseline_val = baseline.get(metric, 0)
            current_val = current.get(metric, 0)
            
            if baseline_val == 0:
                continue
            
            delta_percent = ((current_val - baseline_val) / baseline_val) * 100
            
            # Check for regression
            if delta_percent < min_threshold:
                regressions.append({
                    'metric': metric,
                    'baseline': baseline_val,
                    'current': current_val,
                    'delta': delta_percent,
                    'threshold': min_threshold,
                })
        
        is_regressed = len(regressions) > 0
        
        return is_regressed, {'regressions': regressions}

# Usage in CI
baseline_metrics = json.load(open('baseline.json'))
current_metrics = json.load(open('current.json'))

regressed, report = PerformanceRegression.compare_profiles(baseline_metrics, current_metrics)

if regressed:
    print("Performance regression detected!")
    for r in report['regressions']:
        print(f"  {r['metric']}: {r['delta']:.1f}% (threshold: {r['threshold']}%)")
    exit(1)
```

---

## 8. Troubleshooting Guide

| Issue | Diagnosis | Solution |
|-------|-----------|----------|
| Nsight reports unavailable metrics | GPU not fully supported | Update CUDA / Use different GPU |
| High memory usage from profiler | Profiler overhead | Reduce `--set` detail level |
| Kernel disappears in trace | Kernel too fast or fused | Add artificial work or disable fusion |
| Permission denied for Nsight | Not running with necessary permissions | Use `sudo` or add user to nvidia group |
| PyTorch profiler slow | Overhead from detailed profiling | Reduce `record_shapes` or `profile_memory` |
| Inconsistent results | Thermal throttling | Cool GPU before profiling, reduce workload |

---

## 9. Quick Reference Card

```
┌─ PROFILER SELECTION ─┐
│ Nsight Compute     → Detailed kernel analysis
│ Nsight Systems     → System-level timeline
│ PyTorch Profiler   → Model layer breakdown
│ Omniperf           → AMD GPU analysis
│ nvidia-smi         → Real-time monitoring
└─────────────────────┘

┌─ COMMON COMMANDS ─┐
│ ncu -o r.ncu-rep python app.py
│ nsys profile -o r.nsys-rep python app.py
│ python -m torch.profiler app.py
│ omniperf profile -n test -- ./app
└────────────────────┘

┌─ KEY METRICS ─┐
│ < 50% SM Util      → Parallelism issue
│ > 80% Memory BW    → Memory-bound
│ < 30% Occupancy    → Register pressure
│ < 70% Warp Eff     → Warp divergence
│ > Ridge Point      → Compute-bound
└────────────────────┘
```

