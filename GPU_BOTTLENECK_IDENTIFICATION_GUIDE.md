# GPU Bottleneck Identification Tools and Workflows

## Executive Summary

This comprehensive guide covers practical tools, methodologies, and command-line workflows for identifying GPU bottlenecks across NVIDIA and AMD architectures. It includes NVIDIA Nsight Compute profiling, roofline analysis, PyTorch Profiler, AMD Omniperf, and automated detection frameworks.

---

## 1. NVIDIA Nsight Compute: Comprehensive Guide

### 1.1 Installation and Setup

```bash
# NVIDIA Nsight Compute Installation
# Via CUDA Toolkit (recommended)
# Included in CUDA 11.0+

# Verify installation
ncu --version

# For older systems or standalone install
# Download from: https://developer.nvidia.com/nsight-compute
# Extract and add to PATH
export PATH=/path/to/nsight-compute/bin:$PATH
```

### 1.2 Basic Usage

```bash
# Profile a CUDA application
ncu [options] executable [executable_args]

# Profile a Python script
ncu -o report.ncu-rep python your_script.py

# Profile with output
ncu --export report.ncu-rep python your_script.py

# Profile specific kernel
ncu --kernel-name "kernel_name_regex" python your_script.py

# Profile with metrics
ncu --set=full python your_script.py
ncu --set=detailed python your_script.py
ncu --set=roofline python your_script.py
```

### 1.3 Key Metric Sets

```bash
# Preset profiles available
ncu --list-sets

# Common metric sets:
# - default: Basic metrics
# - full: Comprehensive metrics
# - detailed: Detailed analysis
# - roofline: Roofline model data
# - launch_trace: Kernel launch timeline
```

### 1.4 Output Analysis

```bash
# Generate report
ncu -o report.ncu-rep python script.py

# View report in GUI (requires X11 or remote display)
ncu-ui report.ncu-rep

# Export to CSV for analysis
ncu -o report.ncu-rep --export report.csv python script.py

# Export to JSON
ncu -o report.ncu-rep --export report.json python script.py
```

### 1.5 Interpreting Nsight Compute Metrics

Key metrics for bottleneck identification:

| Metric | Meaning | Threshold | Action |
|--------|---------|-----------|--------|
| SM Utilization (%) | Streaming Multiprocessor usage | < 50% | Increase parallelism |
| Memory Utilization (%) | DRAM bandwidth utilization | < 50% | Check memory patterns |
| Achieved Occupancy (%) | Active warps vs. max | < 50% | Reduce register pressure |
| Compute Utilization (%) | Floating-point unit usage | < 30% | Add more compute work |
| Memory Efficiency | Actual vs. theoretical bandwidth | < 60% | Align memory access patterns |
| Branch Efficiency (%) | Non-divergent branches | < 90% | Reduce warp divergence |
| Warp Efficiency (%) | Active lanes vs. 32 | < 80% | Reduce control flow |

---

## 2. Roofline Analysis with Nsight Compute

### 2.1 Roofline Model Fundamentals

The roofline model graphically identifies whether a kernel is:
- **Compute-bound**: Limited by peak floating-point throughput
- **Memory-bound**: Limited by memory bandwidth

```
Performance (FLOPS)
        ^
        |     Compute Roof (Peak FP32 TFLOPS)
        |    /
        |   / <- Compute Bound
        |  /_______ <- Roofline
        |          \
        |           \ <- Memory Bound
        |            \________
        |
        +-------------------> Arithmetic Intensity (FLOPS/Byte)
        |
      Ridge Point = Peak TFLOPS / Peak Memory Bandwidth
```

### 2.2 Generating Roofline Data

```bash
# Profile with roofline metrics
ncu --set roofline -o roofline.ncu-rep python your_script.py

# For A100 GPU (example):
# Peak FP32: 19.5 TFLOPS
# Peak Memory Bandwidth: 1.9 TB/s
# Ridge Point: ~10,300 FLOPS/Byte

# Export roofline data
ncu --export roofline.csv -i roofline.ncu-rep

# Parse CSV to extract:
# - Achieved FLOPS
# - Arithmetic Intensity
# - Bandwidth Utilization
```

### 2.3 Roofline Interpretation Examples

**Example 1: Memory-Bound Kernel (Bad)**
```
Arithmetic Intensity: 2 FLOPS/Byte (below ridge point ~10,300)
Achieved Bandwidth: 1.2 TB/s (of 1.9 TB/s available)

Action: 
- Optimize memory access patterns
- Use cache-friendly algorithms
- Increase temporal locality
- Consider memory coalescing
```

**Example 2: Compute-Bound Kernel (Good)**
```
Arithmetic Intensity: 50,000 FLOPS/Byte (above ridge point)
Achieved TFLOPS: 15.2 (of 19.5 peak)

Action:
- Optimize instruction-level parallelism
- Reduce instruction latency
- Use tensor cores (if available)
- Minimize register spills
```

### 2.4 Python Script for Roofline Analysis

```python
import subprocess
import json
import re

def extract_roofline_metrics(ncu_report_path):
    """Extract roofline metrics from Nsight Compute report"""
    
    # Export JSON from report
    result = subprocess.run(
        [
            'ncu', 
            '--export', 'report.json', 
            '-i', ncu_report_path
        ],
        capture_output=True,
        text=True
    )
    
    with open('report.json', 'r') as f:
        data = json.load(f)
    
    metrics = {}
    for kernel in data.get('kernels', []):
        kernel_name = kernel['kernelName']
        
        # Extract key metrics
        metrics[kernel_name] = {
            'arithmetic_intensity': kernel.get('sm__throughput.avg.pct_of_peak_sustained_elapsed', 0),
            'memory_bandwidth': kernel.get('dram__throughput.avg.pct_of_peak_sustained_elapsed', 0),
            'sm_utilization': kernel.get('sm__utilization.avg', 0),
            'achieved_occupancy': kernel.get('sm__occupancy.avg', 0),
        }
    
    return metrics

def classify_bottleneck(metrics, peak_tflops=19.5, peak_bandwidth=1.9):
    """Classify kernel as compute or memory bound"""
    
    for kernel, m in metrics.items():
        intensity = m['arithmetic_intensity']
        ridge_point = peak_tflops / peak_bandwidth
        
        if intensity < ridge_point:
            bottleneck = "Memory-Bound"
        else:
            bottleneck = "Compute-Bound"
        
        print(f"{kernel}: {bottleneck}")
        print(f"  Arithmetic Intensity: {intensity:.2f} FLOPS/Byte")
        print(f"  Memory BW: {m['memory_bandwidth']:.1f}%")
        print(f"  SM Utilization: {m['sm_utilization']:.1f}%")
        print()

if __name__ == '__main__':
    import sys
    report_path = sys.argv[1]
    metrics = extract_roofline_metrics(report_path)
    classify_bottleneck(metrics)
```

---

## 3. PyTorch Profiler

### 3.1 Basic PyTorch Profiling

```python
import torch
from torch.profiler import profile, record_function, ProfilerActivity

# Simple profiling
with profile(activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA]) as prof:
    # Your model code here
    output = model(input_tensor)

prof.print_stats()

# Export results
prof.export_chrome_trace("trace.json")
prof.export_stacks("stacks.txt")
```

### 3.2 Detailed GPU Profiling with Nsight Integration

```python
import torch
from torch.profiler import profile, record_function, ProfilerActivity, schedule

# Configuration for memory and bandwidth analysis
def trace_handler(prof):
    print(prof.key_averages().table(sort_by="cuda_time_total", row_limit=10))
    prof.export_chrome_trace("trace_" + str(prof.step_num) + ".json")
    prof.export_stacks("profiler_stacks_" + str(prof.step_num) + ".txt", "self_cuda_time_total")

def profile_model():
    model = create_model()
    input_tensor = torch.randn(batch_size, *input_shape).cuda()
    
    with profile(
        activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA],
        schedule=schedule(wait=1, warmup=1, active=3, repeat=2),
        on_trace_ready=trace_handler,
        record_shapes=True,
        profile_memory=True,
        with_stack=True,
    ) as prof:
        for _ in range(10):
            output = model(input_tensor)
            prof.step()

# Memory profiling
with profile(
    activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA],
    profile_memory=True,
    with_stack=True,
) as prof:
    output = model(input_tensor)

# Print memory stats
print(prof.key_averages(group_by_stack_n=5).table(
    sort_by="self_cuda_memory_usage", 
    row_limit=10
))
```

### 3.3 PyTorch Profiler + Nsight Integration

```bash
# Run PyTorch profiler to generate trace
python profile_script.py
# Generates: trace_*.json

# Import into Nsight Systems
nsys import --type pytorch trace_*.json

# Or view in Chrome
# Open chrome://tracing, drag and drop trace.json
```

---

## 4. NVIDIA Nsight Systems (System-Level Profiling)

### 4.1 Installation and Basic Usage

```bash
# Install Nsight Systems
# Part of CUDA Toolkit or standalone from:
# https://developer.nvidia.com/nsight-systems

nsys --version

# Basic profiling
nsys profile -o report.nsys-rep python your_script.py

# GPU trace
nsys profile --gpu-metrics-device all -o gpu_trace.nsys-rep python your_script.py

# Memory profiling
nsys profile --memory gpu -o memory_trace.nsys-rep python your_script.py
```

### 4.2 Interpreting Nsight Systems Output

```bash
# Generate detailed report
nsys stats -r cuda_api_sum -f csv -o - report.nsys-rep

# Show GPU memory events
nsys stats -r gpu_mem_time_sum report.nsys-rep

# Show kernel timeline
nsys stats -r cuda_gpu_kern_sum report.nsys-rep

# Export to SQLite for custom analysis
nsys export -t sqlite -o report.sqlite report.nsys-rep
```

---

## 5. AMD Omniperf

### 5.1 Installation

```bash
# For RDNA-based GPUs (MI200 series, etc.)
# Install via package manager
sudo apt-get install omniperf  # Ubuntu/Debian
sudo yum install omniperf       # RHEL/CentOS

# Or build from source
git clone https://github.com/ROCm/omniperf.git
cd omniperf
mkdir build && cd build
cmake ..
make -j$(nproc)
sudo make install
```

### 5.2 Basic Omniperf Workflow

```bash
# Profile HIP kernel
omniperf profile -n my_profile -- ./hip_app

# Analyze results
omniperf analyze -p workloads/my_profile

# GUI analysis
omniperf gui

# Roofline analysis
omniperf profile --roofline -n roofline_test -- ./hip_app
omniperf analyze -p roofline_test --roofline
```

### 5.3 Omniperf Bottleneck Analysis

```bash
# Generate bottleneck report
omniperf analyze -p workloads/profile --list

# Analyze specific kernel
omniperf analyze -p workloads/profile -k "kernel_name"

# Export results
omniperf analyze -p workloads/profile -o results.csv
```

---

## 6. Step-by-Step Bottleneck Identification Methodology

### Phase 1: Profiling (Gather Data)

```bash
#!/bin/bash
# Comprehensive profiling workflow

APP="./your_app"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# 1. Nsight Compute full profile
echo "[1/5] Running Nsight Compute..."
ncu --set full -o nsight_$TIMESTAMP.ncu-rep $APP

# 2. Nsight Systems timeline
echo "[2/5] Running Nsight Systems..."
nsys profile -o nsight_sys_$TIMESTAMP.nsys-rep $APP

# 3. PyTorch Profiler (if applicable)
if python -c "import torch"; then
    echo "[3/5] Running PyTorch Profiler..."
    python profile_pytorch.py > pytorch_$TIMESTAMP.json
fi

# 4. Memory profiling
echo "[4/5] Running memory profiling..."
ncu --set roofline -o roofline_$TIMESTAMP.ncu-rep $APP

# 5. System-level metrics
echo "[5/5] Gathering system metrics..."
nvidia-smi dmon -s pucvmet > sysmetrics_$TIMESTAMP.csv &
sleep 10 && killall nvidia-smi

echo "Profiling complete. Reports:"
ls -lh *$TIMESTAMP* | awk '{print $9, $5}'
```

### Phase 2: Data Analysis

```python
#!/usr/bin/env python3
"""GPU Bottleneck Analyzer"""

import subprocess
import json
import csv
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List

@dataclass
class KernelMetrics:
    name: str
    sm_utilization: float
    memory_utilization: float
    achieved_occupancy: float
    warp_efficiency: float
    memory_bandwidth: float
    arithmetic_intensity: float
    
    def classify_bottleneck(self) -> str:
        """Classify kernel bottleneck type"""
        
        # Memory-bound if memory bandwidth is high but occupancy is low
        if self.memory_bandwidth > 0.8 and self.achieved_occupancy < 0.5:
            return "MEMORY_BOUND"
        
        # Compute-bound if occupancy and SM util are high
        if self.achieved_occupancy > 0.7 and self.sm_utilization > 0.7:
            return "COMPUTE_BOUND"
        
        # Latency-bound if SM util low despite occupancy
        if self.achieved_occupancy > 0.5 and self.sm_utilization < 0.3:
            return "LATENCY_BOUND"
        
        # Register pressure if occupancy very low
        if self.achieved_occupancy < 0.2:
            return "REGISTER_PRESSURE"
        
        # Warp divergence if warp efficiency low
        if self.warp_efficiency < 0.5:
            return "WARP_DIVERGENCE"
        
        return "UNKNOWN"
    
    def get_recommendations(self) -> List[str]:
        """Get optimization recommendations"""
        bottleneck = self.classify_bottleneck()
        
        recommendations = {
            "MEMORY_BOUND": [
                "Optimize memory access patterns (coalescing)",
                "Increase temporal locality with tiling",
                "Use shared memory for reduction operations",
                "Consider memory compression or quantization",
            ],
            "COMPUTE_BOUND": [
                "Use tensor cores (NVIDIA) or MFMA (AMD)",
                "Optimize instruction-level parallelism",
                "Reduce instruction latency",
                "Minimize register spills",
            ],
            "LATENCY_BOUND": [
                "Increase number of independent operations",
                "Use instruction-level parallelism",
                "Prefetch data more aggressively",
                "Reduce synchronization points",
            ],
            "REGISTER_PRESSURE": [
                "Reduce registers per thread (simplify kernel)",
                "Use local memory if needed",
                "Reduce loop unrolling",
                "Decrease block size if possible",
            ],
            "WARP_DIVERGENCE": [
                "Restructure conditionals (coalesce branches)",
                "Use predication instead of branching",
                "Align thread blocks to data structure",
                "Consider data reorganization",
            ],
        }
        
        return recommendations.get(bottleneck, ["Profile with more detail"])


class NSightComputeAnalyzer:
    """Parse and analyze Nsight Compute reports"""
    
    def __init__(self, report_path: str):
        self.report_path = report_path
        self.metrics = self._parse_report()
    
    def _parse_report(self) -> Dict[str, KernelMetrics]:
        """Parse Nsight Compute JSON export"""
        
        # Export to JSON
        json_path = "temp_analysis.json"
        subprocess.run(
            ["ncu", "--export", "report.json", "-i", self.report_path],
            check=True,
            capture_output=True
        )
        
        metrics = {}
        with open("report.json") as f:
            data = json.load(f)
        
        # Parse kernels
        for kernel in data.get("kernels", []):
            name = kernel.get("kernelName", "unknown")
            
            # Extract metrics (field names may vary by NCU version)
            kernel_metrics = KernelMetrics(
                name=name,
                sm_utilization=kernel.get("sm__utilization.avg", 0) / 100,
                memory_utilization=kernel.get("dram__throughput.avg.pct_of_peak_sustained_elapsed", 0) / 100,
                achieved_occupancy=kernel.get("sm__occupancy.avg", 0) / 100,
                warp_efficiency=kernel.get("warp_efficiency", 1.0),
                memory_bandwidth=kernel.get("dram__throughput.avg.pct_of_peak_sustained_elapsed", 0) / 100,
                arithmetic_intensity=kernel.get("smsp__sass_thread_inst_executed_op_dmem_realtime.sum.per_cycle_elapsed", 0),
            )
            
            metrics[name] = kernel_metrics
        
        return metrics
    
    def generate_report(self):
        """Generate human-readable analysis report"""
        
        print("=" * 80)
        print("GPU BOTTLENECK ANALYSIS REPORT")
        print("=" * 80)
        print()
        
        for kernel_name, metrics in self.metrics.items():
            bottleneck = metrics.classify_bottleneck()
            print(f"Kernel: {kernel_name}")
            print(f"  Bottleneck Type: {bottleneck}")
            print(f"  SM Utilization: {metrics.sm_utilization*100:.1f}%")
            print(f"  Memory Utilization: {metrics.memory_utilization*100:.1f}%")
            print(f"  Achieved Occupancy: {metrics.achieved_occupancy*100:.1f}%")
            print(f"  Warp Efficiency: {metrics.warp_efficiency*100:.1f}%")
            print()
            
            print(f"  Recommendations:")
            for i, rec in enumerate(metrics.get_recommendations(), 1):
                print(f"    {i}. {rec}")
            print()


# Usage
if __name__ == "__main__":
    import sys
    
    report_path = sys.argv[1] if len(sys.argv) > 1 else "profile.ncu-rep"
    
    analyzer = NSightComputeAnalyzer(report_path)
    analyzer.generate_report()
```

### Phase 3: Targeted Optimization

```bash
#!/bin/bash
# Iterative optimization workflow

APP="./your_app"
ITERATION=1
PREVIOUS_TIME=0

while [ $ITERATION -le 5 ]; do
    echo "=== Iteration $ITERATION ==="
    
    # Profile
    ncu --set full -o iter_${ITERATION}.ncu-rep $APP
    
    # Measure execution time
    CURRENT_TIME=$(time $APP 2>&1 | grep real | awk '{print $2}')
    
    # Calculate improvement
    if [ $ITERATION -gt 1 ]; then
        IMPROVEMENT=$(echo "scale=2; (($PREVIOUS_TIME - $CURRENT_TIME) / $PREVIOUS_TIME) * 100" | bc)
        echo "Improvement: $IMPROVEMENT%"
    fi
    
    PREVIOUS_TIME=$CURRENT_TIME
    
    # Analyze bottleneck
    python analyze_bottleneck.py iter_${ITERATION}.ncu-rep > iter_${ITERATION}_analysis.txt
    
    # Apply optimization based on recommendations
    # (Manual step or automated via build system)
    
    ITERATION=$((ITERATION + 1))
    
    # Ask user before next iteration
    read -p "Continue optimizing? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        break
    fi
done
```

---

## 7. Automated Bottleneck Detection Tools

### 7.1 NVIDIA NvBlox (TensorFlow/PyTorch Integration)

```python
# TensorFlow Profiler with GPU analysis
import tensorflow as tf

tf.profiler.experimental.start('logdir')

# Your model code
model(input_data)

tf.profiler.experimental.stop()

# Analyze
tf.profiler.experimental.client.trace('localhost:6009', 'basic_profile', 100)
```

### 7.2 PyTorch BottleneckFinder (Custom Implementation)

```python
class BottleneckFinder:
    """Automated GPU bottleneck detector"""
    
    def __init__(self, model, device='cuda'):
        self.model = model
        self.device = device
        self.metrics = {}
    
    def profile_bandwidth_limited(self, input_tensor):
        """Test if kernel is bandwidth limited"""
        
        # Run with full cache
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        
        # Measure time and memory
        with torch.profiler.profile(activities=[torch.profiler.ProfilerActivity.CUDA]) as prof:
            output = self.model(input_tensor)
        
        # Analyze bandwidth usage
        for evt in prof.key_averages():
            if 'cuda_time_total' in str(evt):
                self.metrics['bandwidth_limited'] = evt.cuda_time_total > 1000000
        
        return self.metrics.get('bandwidth_limited', False)
    
    def profile_compute_limited(self, input_tensor):
        """Test if kernel is compute limited"""
        
        # Similar to bandwidth but focus on compute operations
        with torch.profiler.profile(activities=[torch.profiler.ProfilerActivity.CUDA]) as prof:
            # Run multiple times for stability
            for _ in range(10):
                output = self.model(input_tensor)
        
        events = prof.key_averages()
        compute_time = sum(e.cuda_time_total for e in events 
                          if 'gemm' in str(e).lower() or 'matmul' in str(e).lower())
        
        total_time = sum(e.cuda_time_total for e in events)
        self.metrics['compute_ratio'] = compute_time / total_time if total_time > 0 else 0
        
        return self.metrics['compute_ratio'] > 0.5
    
    def detect_register_pressure(self, input_tensor):
        """Detect if kernel has register pressure"""
        
        # Monitor occupancy changes with block size variations
        occupancies = []
        
        for block_size in [128, 256, 512]:
            # This requires kernel recompilation (pseudocode)
            # In practice, use nsys to measure
            occupancies.append(block_size)  # Placeholder
        
        # If occupancy drops significantly with increasing block size,
        # indicates register pressure
        return len(set(occupancies)) > 1
    
    def auto_detect_bottleneck(self, input_tensor):
        """Automatically detect bottleneck type"""
        
        self.profile_bandwidth_limited(input_tensor)
        self.profile_compute_limited(input_tensor)
        self.detect_register_pressure(input_tensor)
        
        if self.metrics.get('bandwidth_limited'):
            return "MEMORY_BOUND"
        elif self.metrics.get('compute_ratio', 0) > 0.5:
            return "COMPUTE_BOUND"
        else:
            return "LATENCY_BOUND"
```

### 7.3 Integrated Profiling Harness

```python
#!/usr/bin/env python3
"""Complete automated GPU bottleneck detection"""

import torch
import subprocess
import json
from pathlib import Path
from dataclasses import dataclass
from enum import Enum

class BottleneckType(Enum):
    COMPUTE_BOUND = "Compute-Bound"
    MEMORY_BOUND = "Memory-Bound"
    LATENCY_BOUND = "Latency-Bound"
    REGISTER_PRESSURE = "Register Pressure"
    WARP_DIVERGENCE = "Warp Divergence"
    UNKNOWN = "Unknown"

@dataclass
class BottleneckReport:
    bottleneck_type: BottleneckType
    confidence: float
    metrics: dict
    recommendations: list
    
    def print_report(self):
        print(f"Bottleneck Type: {self.bottleneck_type.value}")
        print(f"Confidence: {self.confidence*100:.1f}%")
        print(f"Key Metrics:")
        for k, v in self.metrics.items():
            print(f"  {k}: {v:.2f}" if isinstance(v, float) else f"  {k}: {v}")
        print(f"Recommendations:")
        for rec in self.recommendations:
            print(f"  - {rec}")

class GPUBottleneckDetector:
    
    def __init__(self, model, input_shape, device='cuda:0'):
        self.model = model
        self.input_shape = input_shape
        self.device = device
        self.metrics = {}
    
    def run_pytorch_profile(self, num_runs=5):
        """Profile with PyTorch Profiler"""
        
        input_tensor = torch.randn(*self.input_shape, device=self.device)
        
        # Warmup
        for _ in range(3):
            self.model(input_tensor)
        
        torch.cuda.synchronize()
        
        # Profile
        with torch.profiler.profile(
            activities=[torch.profiler.ProfilerActivity.CUDA],
            record_shapes=True,
        ) as prof:
            for _ in range(num_runs):
                self.model(input_tensor)
        
        # Extract key events
        events = prof.key_averages()
        total_cuda_time = sum(e.cuda_time_total for e in events)
        
        self.metrics['pytorch_profile'] = {
            'total_cuda_time': total_cuda_time,
            'num_events': len(events),
            'avg_event_time': total_cuda_time / len(events) if events else 0,
        }
        
        return prof
    
    def run_nsight_profile(self):
        """Run Nsight Compute profile"""
        
        # Create wrapper script
        wrapper = """
import torch
from model_module import get_model

model = get_model().cuda()
input_tensor = torch.randn(batch_size, *input_shape).cuda()

for _ in range(10):
    output = model(input_tensor)
"""
        
        Path('nsight_wrapper.py').write_text(wrapper)
        
        # Run Nsight Compute
        result = subprocess.run(
            ['ncu', '--set', 'full', '-o', 'nsight_profile.ncu-rep', 
             'python', 'nsight_wrapper.py'],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            self.metrics['nsight_available'] = True
            return 'nsight_profile.ncu-rep'
        else:
            self.metrics['nsight_available'] = False
            return None
    
    def analyze_metrics(self):
        """Analyze collected metrics to detect bottleneck"""
        
        # Get memory info
        device_prop = torch.cuda.get_device_properties(self.device)
        
        self.metrics['device_info'] = {
            'name': device_prop.name,
            'sm_count': device_prop.multi_processor_count,
            'max_threads_per_sm': device_prop.max_threads_per_multi_processor,
        }
        
        # Heuristic bottleneck detection
        pytorch_metrics = self.metrics.get('pytorch_profile', {})
        total_time = pytorch_metrics.get('total_cuda_time', 0)
        
        # Memory vs Compute classification
        # (Simplified - in practice would use Nsight data)
        if total_time > 1000000:  # 1ms in microseconds
            bottleneck_type = BottleneckType.MEMORY_BOUND
            confidence = 0.7
            recommendations = [
                "Use shared memory for frequent data access",
                "Increase computational intensity (FLOPS/byte)",
                "Consider kernel fusion to reduce memory pressure",
            ]
        else:
            bottleneck_type = BottleneckType.COMPUTE_BOUND
            confidence = 0.7
            recommendations = [
                "Use tensor cores or MFMA instructions",
                "Increase instruction-level parallelism",
                "Consider mixed-precision computation",
            ]
        
        return BottleneckReport(
            bottleneck_type=bottleneck_type,
            confidence=confidence,
            metrics=self.metrics,
            recommendations=recommendations,
        )
    
    def detect(self):
        """Run full detection pipeline"""
        
        print("Running PyTorch profile...")
        self.run_pytorch_profile()
        
        print("Running Nsight Compute (if available)...")
        self.run_nsight_profile()
        
        print("Analyzing metrics...")
        report = self.analyze_metrics()
        
        return report


if __name__ == "__main__":
    # Example usage
    model = torch.nn.Sequential(
        torch.nn.Linear(1024, 2048),
        torch.nn.ReLU(),
        torch.nn.Linear(2048, 1024),
    ).cuda()
    
    detector = GPUBottleneckDetector(
        model=model,
        input_shape=(32, 1024),
        device='cuda:0'
    )
    
    report = detector.detect()
    report.print_report()
```

---

## 8. Command-Line Quick Reference

### NVIDIA Profilers

```bash
# Nsight Compute
ncu -o report.ncu-rep python script.py                  # Basic profile
ncu --set roofline -o report.ncu-rep python script.py   # Roofline
ncu --export report.csv -i report.ncu-rep               # Export data

# Nsight Systems  
nsys profile -o report.nsys-rep python script.py        # System profile
nsys profile --gpu-metrics-device all python script.py  # GPU metrics
nsys stats -r cuda_gpu_kern_sum report.nsys-rep         # Kernel summary

# NVIDIA-SMI
nvidia-smi dmon -s pucvmet                              # Real-time metrics
nvidia-smi dmon -s c                                    # Clocks
nvidia-smi dmon -s m                                    # Memory
nvidia-smi pmon                                         # Process monitoring
```

### AMD Profilers

```bash
# Omniperf
omniperf profile -n test -- ./hip_app                   # Profile
omniperf analyze -p workloads/test                      # Analyze
omniperf analyze -p workloads/test --roofline           # Roofline

# rocprof (low-level)
rocprof --stats ./hip_app                               # Counters
rocprof --trace ./hip_app                               # Timeline
```

### PyTorch Profiling

```bash
# PyTorch built-in
python -m torch.profiler script.py                      # Profile with tensorboard

# TensorFlow Profiler
tensorboard --logdir=./logs --port=6006                 # Visualize
```

---

## 9. Interpretation Checklist

Use this checklist when analyzing GPU kernels:

```
[ ] SM Utilization < 50%?
    → Increase parallelism, check thread block size
    
[ ] Memory Bandwidth > 80% but Performance Low?
    → Memory-bound, optimize access patterns
    
[ ] Achieved Occupancy < 30%?
    → Register pressure or synchronization issues
    
[ ] Warp Efficiency < 70%?
    → Warp divergence, restructure conditionals
    
[ ] Branch Efficiency < 90%?
    → Use predication, coalesce control flow
    
[ ] Arithmetic Intensity << Ridge Point?
    → Memory-bound kernel
    
[ ] Arithmetic Intensity >> Ridge Point?
    → Compute-bound kernel
    
[ ] Memory Latency High but Bandwidth Low?
    → Latency bound, improve prefetching
    
[ ] IPC (Instructions Per Cycle) < 2?
    → Instruction-level parallelism opportunity
    
[ ] Bank Conflicts Detected?
    → Reorganize shared memory layout
```

---

## 10. Real-World Example: Identifying MatMul Bottleneck

```python
import torch
from torch.profiler import profile, ProfilerActivity

# Simple matrix multiplication
A = torch.randn(2048, 2048, device='cuda')
B = torch.randn(2048, 2048, device='cuda')

with profile(activities=[ProfilerActivity.CUDA], record_shapes=True) as prof:
    for _ in range(10):
        C = torch.mm(A, B)

# Analyze
print(prof.key_averages().table(sort_by="cuda_time_total", row_limit=5))

# Profile with Nsight
# ncu -o matmul.ncu-rep python matmul_profile.py

# Expected findings for FP32 MatMul:
# - Arithmetic Intensity: ~4 FLOPS/Byte (memory-bound for modern GPUs)
# - Memory Bandwidth: >80% utilization
# - SM Utilization: 70-90%
# - Achieved: 50-70% of theoretical peak TFLOPS
#
# Optimization: Use TensorFloat32 (TF32) for 3-5x speedup
#   torch.backends.cuda.matmul.allow_tf32 = True
```

---

## 11. References and Further Reading

**NVIDIA Documentation:**
- Nsight Compute User Guide: https://docs.nvidia.com/nsight-compute/
- Roofline Model: https://docs.nvidia.com/cuda/profiler-users-guide/
- Optimization Best Practices: https://docs.nvidia.com/cuda/cuda-c-programming-guide/

**Community Resources:**
- NVIDIA GPU Gems: https://developer.nvidia.com/gpu-gems
- Khronos GPU Optimization Resources: https://www.khronos.org/

**AMD Documentation:**
- Omniperf: https://github.com/ROCm/omniperf
- ROCm Profiling: https://rocmdocs.amd.com/

**Academic References:**
- Roofline: An Insightful Visual Performance Model for Floating-Point Programs (Williams, Waterman, Patterson, 2009)
- Performance Modeling and Benchmarking with Empirical Roofline Model
