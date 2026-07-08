# GPU Bottleneck Identification: Quick-Start Guide

Practical scripts and ready-to-run examples for identifying GPU bottlenecks.

---

## Quick Start: 5-Minute Profiling

### Step 1: Install Tools

```bash
# NVIDIA CUDA Toolkit (includes Nsight Compute)
# Download from: https://developer.nvidia.com/cuda-downloads
# Or via package manager:
sudo apt-get install nvidia-cuda-toolkit

# Verify installation
ncu --version
nsys --version
nvidia-smi

# PyTorch Profiler (if using PyTorch)
pip install torch --index-url https://download.pytorch.org/whl/cu118
```

### Step 2: Run Basic Profile

```bash
# Option A: Profile a Python script directly
ncu -o profile.ncu-rep python your_script.py

# Option B: Profile with Nsight Systems (lower overhead)
nsys profile -o profile.nsys-rep python your_script.py

# Option C: Profile with PyTorch built-in
python -m torch.utils.bottleneck your_script.py
```

### Step 3: Analyze Results

```bash
# View report in GUI (if X11 available)
ncu-ui profile.ncu-rep

# Or export to CSV and analyze
ncu --export report.csv -i profile.ncu-rep
head -20 report.csv

# Quick summary
ncu -i profile.ncu-rep --export summary
```

---

## One-Liner Examples

### Profile with Everything

```bash
# Complete profiling (requires ~30 seconds)
bash -c '
mkdir -p gpu_profile_$(date +%s)
cd gpu_profile_$(date +%s)
echo "[1/3] Running Nsight Systems..."
nsys profile -o nsys python ../app.py
echo "[2/3] Running Nsight Compute..."
ncu -o ncu.ncu-rep python ../app.py
echo "[3/3] Exporting data..."
ncu --export csv -i ncu.ncu-rep > metrics.csv
echo "Done! Check $(pwd) for results"
'
```

### Quick Memory Analysis

```bash
# Identify memory bottleneck
python3 -c "
import torch
from torch.profiler import profile, ProfilerActivity
model = torch.nn.Linear(1000, 1000).cuda()
x = torch.randn(100, 1000).cuda()
with profile(activities=[ProfilerActivity.CUDA], profile_memory=True) as p:
    model(x)
print(p.key_averages().table(sort_by='self_cuda_memory_usage', row_limit=10))
"
```

### Real-Time GPU Monitor

```bash
# Watch GPU metrics every 2 seconds
watch -n 2 'nvidia-smi --query-gpu=name,utilization.gpu,utilization.memory,memory.used,memory.total --format=csv,noheader'
```

---

## Complete Standalone Scripts

### Script 1: Automated Profiler

Save as `profile_gpu.py`:

```python
#!/usr/bin/env python3
"""Automated GPU profiler - run and get bottleneck classification"""

import subprocess
import sys
import os
from pathlib import Path

def run_profiler(app_path, args=None):
    """Run comprehensive GPU profiling"""
    
    if args is None:
        args = []
    
    # Check if app exists
    if not os.path.exists(app_path):
        print(f"Error: Application not found: {app_path}")
        sys.exit(1)
    
    output_dir = Path(f"gpu_profile_{Path(app_path).stem}")
    output_dir.mkdir(exist_ok=True)
    
    cmd = app_path if os.path.isabs(app_path) else f"./{app_path}"
    full_cmd = [cmd] + args
    
    print(f"Profiling: {' '.join(full_cmd)}")
    print(f"Output: {output_dir}")
    print()
    
    # Run profilers
    profilers = [
        ("Nsight Systems", ["nsys", "profile", "-o", str(output_dir / "nsys"), cmd] + args),
        ("Nsight Compute", ["ncu", "-o", str(output_dir / "ncu.ncu-rep"), cmd] + args),
    ]
    
    for name, cmd_list in profilers:
        try:
            print(f"Running {name}...")
            result = subprocess.run(cmd_list, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                print(f"  ⚠ {name} had issues (tool may not be installed)")
            else:
                print(f"  ✓ {name} complete")
        except subprocess.TimeoutExpired:
            print(f"  ⚠ {name} timed out")
        except FileNotFoundError:
            print(f"  ⚠ {name} not found")
        print()
    
    # Analyze
    print("Generating analysis...")
    analyze_profile(output_dir)

def analyze_profile(output_dir):
    """Analyze profiling results"""
    
    ncu_report = output_dir / "ncu.ncu-rep"
    
    if not ncu_report.exists():
        print("No NCU report found")
        return
    
    try:
        # Export CSV
        csv_path = output_dir / "metrics.csv"
        subprocess.run(
            ["ncu", "--export", "csv", "-i", str(ncu_report)],
            capture_output=True,
            cwd=output_dir,
            check=True
        )
        
        # Parse and classify
        with open(csv_path) as f:
            lines = f.readlines()
        
        print("\n" + "="*70)
        print("BOTTLENECK CLASSIFICATION")
        print("="*70)
        
        # Simple classification logic
        metrics = {}
        for line in lines[1:]:
            parts = line.strip().split(',')
            if len(parts) >= 2:
                key = parts[0]
                val = parts[1]
                try:
                    metrics[key] = float(val)
                except ValueError:
                    pass
        
        sm_util = metrics.get('SM Utilization', 0)
        mem_bw = metrics.get('Memory Utilization', 0)
        occupancy = metrics.get('Occupancy', 0)
        
        print(f"\nKey Metrics:")
        print(f"  SM Utilization:     {sm_util:.1f}%")
        print(f"  Memory Utilization: {mem_bw:.1f}%")
        print(f"  Occupancy:          {occupancy:.1f}%")
        
        # Classify
        if mem_bw > 80 and sm_util < 50:
            bottleneck = "MEMORY-BOUND"
            recommendation = "Optimize memory access patterns (coalescing, caching)"
        elif sm_util > 80 and mem_bw < 50:
            bottleneck = "COMPUTE-BOUND"
            recommendation = "Use tensor cores or increase instruction parallelism"
        elif occupancy < 30:
            bottleneck = "REGISTER PRESSURE"
            recommendation = "Reduce registers per thread or simplify kernel"
        else:
            bottleneck = "BALANCED"
            recommendation = "No obvious bottleneck - look at specific kernel metrics"
        
        print(f"\nBottleneck Type: {bottleneck}")
        print(f"Recommendation: {recommendation}")
        print("="*70)
    
    except Exception as e:
        print(f"Analysis failed: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <app_path> [args...]")
        print(f"Example: {sys.argv[0]} python script.py --batch-size 32")
        sys.exit(1)
    
    app_path = sys.argv[1]
    args = sys.argv[2:]
    run_profiler(app_path, args)
```

Usage:
```bash
python profile_gpu.py python my_model.py --batch-size 64
```

### Script 2: Memory Bottleneck Detector

Save as `detect_memory_bottleneck.py`:

```python
#!/usr/bin/env python3
"""Detect if kernel is memory-bound"""

import torch
import torch.cuda
from torch.profiler import profile, ProfilerActivity
from typing import Tuple

def measure_arithmetic_intensity(model, input_shape, num_runs=10) -> float:
    """
    Calculate arithmetic intensity (FLOPS / Byte)
    
    High intensity (>ridge point) = compute-bound
    Low intensity (<ridge point) = memory-bound
    """
    
    # Create input
    input_tensor = torch.randn(*input_shape, device='cuda')
    
    # Warmup
    for _ in range(3):
        model(input_tensor)
    
    torch.cuda.synchronize()
    
    # Profile with detailed metrics
    with profile(
        activities=[ProfilerActivity.CUDA],
        record_shapes=True,
    ) as prof:
        for _ in range(num_runs):
            model(input_tensor)
    
    # Extract metrics
    events = prof.key_averages()
    
    total_ops = 0
    total_memory = 0
    
    for event in events:
        # Count operations (approximation)
        if 'gemm' in event.name.lower() or 'matmul' in event.name.lower():
            # For matrix multiply: 2 * m * n * k operations
            total_ops += event.cuda_time_total * 1000  # Rough estimate
        
        # Count memory (from shapes if available)
        total_memory += event.cuda_time_total / 1000  # Rough estimate
    
    # Calculate intensity
    intensity = total_ops / total_memory if total_memory > 0 else 0
    
    return intensity

def classify_kernel_memory_bound(model, input_shape) -> str:
    """
    Classify if kernel is memory-bound
    
    Returns: "MEMORY_BOUND", "COMPUTE_BOUND", "UNKNOWN"
    """
    
    # GPU-dependent ridge points (FLOPS per byte)
    ridge_points = {
        'A100': 10300,      # FP32
        'A10': 2600,
        'V100': 5200,
        'T4': 1300,
    }
    
    # Measure intensity
    intensity = measure_arithmetic_intensity(model, input_shape)
    
    # Get GPU name
    props = torch.cuda.get_device_properties(0)
    gpu_name = props.name
    
    # Determine ridge point (default to V100)
    ridge = ridge_points.get('V100', 5200)
    for known_gpu, known_ridge in ridge_points.items():
        if known_gpu.lower() in gpu_name.lower():
            ridge = known_ridge
            break
    
    # Classify
    if intensity < ridge:
        return "MEMORY_BOUND"
    elif intensity > ridge * 2:
        return "COMPUTE_BOUND"
    else:
        return "BALANCED"

def memory_optimization_suggestions(classification: str) -> list:
    """Get optimization suggestions based on classification"""
    
    suggestions = {
        "MEMORY_BOUND": [
            "✓ Use shared memory for data reuse",
            "✓ Coalesce memory access patterns",
            "✓ Use memory-efficient data types (fp16, int8)",
            "✓ Reduce memory traffic with loop fusion",
            "✓ Use fast reductions with shared memory",
        ],
        "COMPUTE_BOUND": [
            "✓ Use tensor cores (Nsight recommends)",
            "✓ Increase arithmetic operations per memory access",
            "✓ Optimize instruction-level parallelism",
            "✓ Reduce instruction latency",
            "✓ Use mixed-precision computation",
        ],
        "BALANCED": [
            "✓ Profile individual kernels for targeted optimization",
            "✓ Look for synchronization bottlenecks",
            "✓ Check for memory coalescing issues",
            "✓ Analyze warp efficiency",
        ],
    }
    
    return suggestions.get(classification, ["Run detailed Nsight Compute analysis"])

# Example usage
if __name__ == "__main__":
    # Simple test model
    model = torch.nn.Sequential(
        torch.nn.Linear(1024, 2048),
        torch.nn.ReLU(),
        torch.nn.Linear(2048, 1024),
    ).cuda()
    
    print("Memory Bottleneck Detector")
    print("="*50)
    
    classification = classify_kernel_memory_bound(model, (32, 1024))
    print(f"\nClassification: {classification}")
    
    print("\nRecommendations:")
    for suggestion in memory_optimization_suggestions(classification):
        print(f"  {suggestion}")
```

Usage:
```bash
python detect_memory_bottleneck.py
```

### Script 3: Nsight Compute Report Parser

Save as `parse_ncu_report.py`:

```python
#!/usr/bin/env python3
"""Parse and summarize Nsight Compute reports"""

import subprocess
import csv
import sys
from pathlib import Path
from collections import defaultdict

class NCUReportParser:
    """Parse NCU CSV export"""
    
    def __init__(self, report_path: str):
        self.report_path = Path(report_path)
        self.metrics = self._parse_report()
    
    def _parse_report(self) -> dict:
        """Parse NCU report to CSV and extract metrics"""
        
        # Export to CSV
        csv_path = self.report_path.parent / "temp_export.csv"
        
        try:
            subprocess.run(
                ["ncu", "--export", "csv", "-i", str(self.report_path)],
                cwd=self.report_path.parent,
                capture_output=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("Error: Could not export NCU report to CSV")
            print("Ensure Nsight Compute is installed and report file is valid")
            sys.exit(1)
        
        # Parse CSV
        metrics = defaultdict(dict)
        
        try:
            with open(csv_path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    kernel_name = row.get('Kernel Name', 'unknown')
                    for key, value in row.items():
                        try:
                            metrics[kernel_name][key] = float(value)
                        except (ValueError, TypeError):
                            metrics[kernel_name][key] = value
        except FileNotFoundError:
            print("Error: CSV export failed")
            sys.exit(1)
        finally:
            csv_path.unlink(missing_ok=True)
        
        return metrics
    
    def print_summary(self):
        """Print summary of all kernels"""
        
        print("\n" + "="*80)
        print("NSIGHT COMPUTE REPORT SUMMARY")
        print("="*80)
        print(f"Report: {self.report_path}")
        print(f"Kernels analyzed: {len(self.metrics)}")
        print()
        
        for kernel_name, kernel_metrics in self.metrics.items():
            print(f"Kernel: {kernel_name}")
            
            # Key metrics
            key_fields = [
                'SM Utilization (%)',
                'Memory Utilization (%)',
                'Occupancy (%)',
                'Warp Efficiency (%)',
                'Memory Bandwidth (GB/s)',
            ]
            
            for field in key_fields:
                # Try multiple field name variations
                for key in kernel_metrics:
                    if field.split()[0].lower() in key.lower():
                        val = kernel_metrics[key]
                        if isinstance(val, (int, float)):
                            print(f"  {field}: {val:.1f}")
                        break
            
            # Bottleneck classification
            sm_util = kernel_metrics.get('SM Utilization (%)', 0)
            mem_bw = kernel_metrics.get('Memory Utilization (%)', 0)
            
            if isinstance(sm_util, (int, float)) and isinstance(mem_bw, (int, float)):
                if mem_bw > 80 and sm_util < 50:
                    print("  ⚠ Bottleneck: MEMORY-BOUND")
                elif sm_util > 80:
                    print("  ⚠ Bottleneck: COMPUTE-BOUND")
            
            print()
    
    def get_bottleneck_kernels(self, threshold_sm=50, threshold_mem=70) -> list:
        """Get list of problematic kernels"""
        
        bottlenecks = []
        
        for kernel_name, metrics in self.metrics.items():
            sm_util = metrics.get('SM Utilization (%)', 0)
            mem_bw = metrics.get('Memory Utilization (%)', 0)
            
            if isinstance(sm_util, (int, float)) and isinstance(mem_bw, (int, float)):
                if sm_util < threshold_sm or mem_bw > threshold_mem:
                    bottlenecks.append({
                        'kernel': kernel_name,
                        'sm_util': sm_util,
                        'mem_bw': mem_bw,
                    })
        
        return sorted(bottlenecks, key=lambda x: x['mem_bw'], reverse=True)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <ncu_report.ncu-rep>")
        sys.exit(1)
    
    report_path = sys.argv[1]
    
    parser = NCUReportParser(report_path)
    parser.print_summary()
    
    bottlenecks = parser.get_bottleneck_kernels()
    if bottlenecks:
        print("\n" + "="*80)
        print("PROBLEMATIC KERNELS")
        print("="*80)
        for b in bottlenecks:
            print(f"{b['kernel']:40s} SM:{b['sm_util']:6.1f}% MEM:{b['mem_bw']:6.1f}%")
```

Usage:
```bash
python parse_ncu_report.py report.ncu-rep
```

---

## Common Scenarios

### Scenario 1: Slow PyTorch Model

```bash
# Step 1: Identify slow layer
python -c "
import torch
from torch.profiler import profile, ProfilerActivity

model = torch.hub.load('pytorch/vision:v0.10.0', 'resnet50', pretrained=True).cuda()
x = torch.randn(1, 3, 224, 224).cuda()

with profile(activities=[ProfilerActivity.CUDA]) as p:
    model(x)

print(p.key_averages().table(sort_by='cuda_time_total', row_limit=10))
"

# Step 2: Profile slow layer with Nsight
# (Wrap the slow layer in a standalone script and profile with ncu)

# Step 3: Interpret results
# If SM util < 50%: increase parallelism
# If Memory BW > 80%: optimize memory patterns
```

### Scenario 2: Custom CUDA Kernel Underperforming

```bash
# Profile custom kernel
ncu --set=full -o profile.ncu-rep ./my_cuda_app

# View detailed report
ncu-ui profile.ncu-rep

# Check roofline
ncu --export roofline.csv -i profile.ncu-rep
python -c "
import csv
with open('roofline.csv') as f:
    r = list(csv.DictReader(f))
    print(f\"Arithmetic Intensity: {r[0].get('Arithmetic Intensity')}\")
    print(f\"Ridge Point: ~10,300 FLOPS/Byte (for A100 FP32)\")
"
```

### Scenario 3: Memory Overflow

```bash
# Profile memory usage
python -m torch.utils.bottleneck your_script.py

# Or detailed memory profiling
python -c "
import torch
from torch.profiler import profile, ProfilerActivity

with profile(
    activities=[ProfilerActivity.CUDA],
    profile_memory=True
) as prof:
    # Your code here
    pass

print(prof.key_averages().table(sort_by='self_cuda_memory_usage', row_limit=15))
"
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `ncu: command not found` | Install CUDA Toolkit: https://developer.nvidia.com/cuda-downloads |
| `Permission denied` | Run with `sudo` or add user to nvidia group: `sudo usermod -a -G video $USER` |
| Report shows no data | Application may be too fast; add loop: `for _ in range(100): model(x)` |
| Profiler overhead is too high | Use `--set default` instead of `--set full` |
| Can't visualize traces | Install NSight UI or use Chrome trace viewer |
| Kernels don't appear in trace | Kernels may be fused; disable with `DISABLE_KERNEL_FUSION=1` |

---

## Further Learning

1. **NVIDIA Documentation**
   - Nsight Compute Manual: https://docs.nvidia.com/nsight-compute/
   - GPU Performance Analysis: https://developer.nvidia.com/blog/

2. **Roofline Model**
   - Original paper: "Roofline: An Insightful Visual Performance Model for Floating-Point Programs"
   - Interactive tool: https://crd.lbl.gov/departments/computer-science/par/research/roofline/

3. **Community Resources**
   - NVIDIA GPU Gems: https://developer.nvidia.com/gpu-gems/
   - GPU Optimization: https://stackoverflow.com/questions/tagged/cuda+performance

