# GPU Bottleneck Identification: Complete Resource Index

This is a comprehensive research guide for identifying GPU bottlenecks across NVIDIA and AMD architectures. All documents are stored in the AtlasForge project root.

---

## Documents Overview

### 1. **GPU_BOTTLENECK_IDENTIFICATION_GUIDE.md** (31 KB)
**Comprehensive technical reference** - Start here for deep understanding

Contains:
- NVIDIA Nsight Compute complete guide with metric interpretation
- Roofline model fundamentals and analysis methodology
- PyTorch Profiler with GPU integration examples
- NVIDIA Nsight Systems for system-level profiling
- AMD Omniperf detailed workflows
- 7-phase bottleneck identification methodology with Python automation scripts
- Automated detection tools and harness implementation
- Real-world interpretation checklist
- Case study: Identifying MatMul bottleneck

**Best for:** Understanding the theory, metrics, and detailed analysis techniques

**Key sections:**
- Roofline analysis (Section 2)
- Step-by-step methodology (Section 6)
- Interpretation checklist (Section 9)

---

### 2. **GPU_PROFILING_TOOLCHAIN.md** (29 KB)
**Advanced workflows and integration patterns** - For practitioners

Contains:
- Multi-tool automated profiling pipeline
- Parallel profiling (non-blocking execution)
- Advanced Nsight Compute workflows
- Memory hierarchy analysis with Python
- Kernel comparison techniques
- PyTorch hierarchical and memory profiling
- TensorFlow GPU profiling integration
- Unified profiler interface implementation
- Continuous monitoring and real-time detection
- CI/CD integration with regression testing
- Comprehensive troubleshooting guide

**Best for:** Practical implementation, automating analysis, CI/CD integration

**Key sections:**
- Multi-tool pipeline (Section 1)
- Unified profiler interface (Section 5)
- CI/CD regression detection (Section 7)
- Continuous monitoring (Section 6)

---

### 3. **GPU_BOTTLENECK_QUICK_START.md** (19 KB)
**Ready-to-run examples and quick references** - Start here for immediate use

Contains:
- 5-minute quick profiling setup
- One-liner profiling examples
- 3 complete standalone Python scripts:
  - Automated GPU profiler
  - Memory bottleneck detector
  - Nsight Compute report parser
- Common scenario solutions (slow models, CUDA kernels, memory overflow)
- Troubleshooting quick reference
- Installation instructions for all tools

**Best for:** Getting started quickly, copy-paste scripts, immediate results

**Key sections:**
- Quick start (Section 0)
- Standalone scripts (Section "Complete Standalone Scripts")
- Common scenarios (Section "Common Scenarios")

---

### 4. **GPU_PROFILING_TOOL_COMPARISON.md** (11 KB)
**Tool selection guide** - Choose the right tool for your task

Contains:
- Tool overview comparison matrix
- Detailed feature comparison table
- Use case recommendation matrix
- Installation instructions for each tool
- Performance characteristics (speed, memory overhead)
- Output format comparison
- Bottleneck detection capability analysis
- Workflow examples for different time budgets
- Decision tree for tool selection
- Common pitfalls and solutions
- Quick reference cheat sheet

**Best for:** Choosing between tools, understanding tradeoffs, quick commands

**Key sections:**
- Use case matrix (Section 2)
- Selection decision tree (Section 7)
- Quick reference (Section 9)

---

## Recommended Reading Paths

### Path 1: Quick Profiler (5-15 minutes)
1. **GPU_PROFILING_TOOL_COMPARISON.md** - Section 9 (Quick reference cheat sheet)
2. **GPU_BOTTLENECK_QUICK_START.md** - Section "Quick Start" + "One-Liner Examples"
3. Choose tool, run script, interpret results

**Result:** Fast bottleneck identification, minimal learning curve

---

### Path 2: Intermediate Analysis (30-60 minutes)
1. **GPU_PROFILING_TOOL_COMPARISON.md** - Full document (choose your tool)
2. **GPU_BOTTLENECK_QUICK_START.md** - "Complete Standalone Scripts" (copy-paste)
3. **GPU_BOTTLENECK_IDENTIFICATION_GUIDE.md** - Section 1 or 3 (your tool's guide)
4. Run profiler, parse results, classify bottleneck

**Result:** Accurate bottleneck classification with interpretation guide

---

### Path 3: Deep Expertise (2-4 hours)
1. **GPU_BOTTLENECK_IDENTIFICATION_GUIDE.md** - Sections 1-5 (tool mastery)
2. **GPU_BOTTLENECK_IDENTIFICATION_GUIDE.md** - Section 6 (methodology)
3. **GPU_PROFILING_TOOLCHAIN.md** - Sections 1-3 (advanced workflows)
4. **GPU_BOTTLENECK_IDENTIFICATION_GUIDE.md** - Sections 7-9 (automation)
5. Implement custom harness, automate bottleneck detection

**Result:** Production-grade profiling automation, metrics interpretation expertise

---

### Path 4: Team Deployment (3-8 hours)
1. **GPU_PROFILING_TOOL_COMPARISON.md** - Full (tool ecosystem overview)
2. **GPU_PROFILING_TOOLCHAIN.md** - Section 7 (CI/CD integration)
3. **GPU_BOTTLENECK_IDENTIFICATION_GUIDE.md** - Sections 10-11 (references)
4. **GPU_PROFILING_TOOLCHAIN.md** - Section 6 (continuous monitoring)
5. Deploy automated profiling to CI/CD, set up dashboards

**Result:** Continuous profiling infrastructure, team training materials

---

## Quick Navigation by Topic

### NVIDIA Tools
- Nsight Compute basics: **GPU_BOTTLENECK_IDENTIFICATION_GUIDE.md** § 1
- Nsight Systems: **GPU_BOTTLENECK_IDENTIFICATION_GUIDE.md** § 4
- Advanced workflows: **GPU_PROFILING_TOOLCHAIN.md** § 2
- Comparison: **GPU_PROFILING_TOOL_COMPARISON.md** § 1-2

### AMD Tools
- Omniperf guide: **GPU_BOTTLENECK_IDENTIFICATION_GUIDE.md** § 5
- rocprof details: **GPU_PROFILING_TOOL_COMPARISON.md** § 1, § 3

### PyTorch & TensorFlow
- PyTorch Profiler: **GPU_BOTTLENECK_IDENTIFICATION_GUIDE.md** § 3
- PyTorch advanced: **GPU_PROFILING_TOOLCHAIN.md** § 3
- TensorFlow: **GPU_PROFILING_TOOLCHAIN.md** § 4

### Roofline Analysis
- Fundamentals: **GPU_BOTTLENECK_IDENTIFICATION_GUIDE.md** § 2.1-2.2
- Interpretation: **GPU_BOTTLENECK_IDENTIFICATION_GUIDE.md** § 2.3-2.4
- Python scripts: **GPU_BOTTLENECK_IDENTIFICATION_GUIDE.md** § 2.4

### Bottleneck Types
- Classification: **GPU_BOTTLENECK_IDENTIFICATION_GUIDE.md** § 6.2
- Memory-bound: **GPU_PROFILING_TOOLCHAIN.md** § 2.2
- Detection automation: **GPU_PROFILING_TOOLCHAIN.md** § 5

### Scripts & Tools
- Standalone ready-to-run: **GPU_BOTTLENECK_QUICK_START.md** § "Complete Standalone Scripts"
- Unified harness: **GPU_PROFILING_TOOLCHAIN.md** § 5.1
- Automated pipeline: **GPU_PROFILING_TOOLCHAIN.md** § 1.1

### Real-Time Monitoring
- Live monitoring: **GPU_PROFILING_TOOLCHAIN.md** § 6
- Continuous detection: **GPU_PROFILING_TOOLCHAIN.md** § 6.2

### CI/CD & Production
- Automated regression: **GPU_PROFILING_TOOLCHAIN.md** § 7.1
- Performance thresholds: **GPU_PROFILING_TOOLCHAIN.md** § 7.2
- Team deployment: **GPU_PROFILING_TOOLCHAIN.md** § 7

### Tool Selection
- Feature matrix: **GPU_PROFILING_TOOL_COMPARISON.md** § 2
- Use case table: **GPU_PROFILING_TOOL_COMPARISON.md** § 2
- Decision tree: **GPU_PROFILING_TOOL_COMPARISON.md** § 7
- Performance comparison: **GPU_PROFILING_TOOL_COMPARISON.md** § 3-4

### Troubleshooting
- Common issues: **GPU_PROFILING_TOOL_COMPARISON.md** § 6
- Quick fixes: **GPU_BOTTLENECK_QUICK_START.md** § "Troubleshooting"

---

## Command Quick Reference

### One-Command Profiling

```bash
# PyTorch (fastest, easiest)
python -m torch.utils.bottleneck your_script.py

# Nsight Systems (low overhead, system view)
nsys profile -o report.nsys-rep python your_script.py

# Nsight Compute (detailed kernel analysis)
ncu -o report.ncu-rep python your_script.py

# AMD Omniperf
omniperf profile -n test -- ./your_app

# Real-time monitoring
watch -n 2 'nvidia-smi --query-gpu=name,utilization.gpu,utilization.memory --format=csv,noheader'
```

### Analysis Workflows

```bash
# Export Nsight Compute to CSV
ncu --export report.csv -i profile.ncu-rep

# View report in GUI
ncu-ui profile.ncu-rep

# Generate roofline data
ncu --set roofline -o roofline.ncu-rep python script.py
ncu --export roofline.csv -i roofline.ncu-rep

# Analyze with Python
python parse_ncu_report.py profile.ncu-rep
```

---

## Key Metrics Interpretation

### Critical Thresholds

| Metric | Good | Warning | Bad | Action |
|--------|------|---------|-----|--------|
| SM Utilization | >70% | 50-70% | <50% | Increase parallelism |
| Memory BW | 70-90% | >80% | Peak | Optimize memory |
| Occupancy | >50% | 30-50% | <30% | Reduce registers |
| Warp Efficiency | >90% | 70-90% | <70% | Fix divergence |
| Cache Hit Rate | >90% | 60-90% | <60% | Improve locality |

### Bottleneck Type Quick ID

```
Memory BW > 80% + SM Util < 50%  → MEMORY-BOUND
                                   Use shared memory, coalesce access

SM Util > 80% + Occupancy > 70%  → COMPUTE-BOUND
                                   Use tensor cores, increase ILP

Occupancy < 30%                  → REGISTER PRESSURE
                                   Reduce regs per thread

Warp Efficiency < 70%            → WARP DIVERGENCE
                                   Restructure conditionals

Arithmetic Intensity << Ridge    → MEMORY-BOUND (confirms above)
```

---

## Document Statistics

| Document | Size | Sections | Code Examples | Tables |
|----------|------|----------|--------------|--------|
| GPU_BOTTLENECK_IDENTIFICATION_GUIDE.md | 31 KB | 11 | 12+ | 8 |
| GPU_PROFILING_TOOLCHAIN.md | 29 KB | 8 | 15+ | 6 |
| GPU_BOTTLENECK_QUICK_START.md | 19 KB | 9 | 3 standalone | 2 |
| GPU_PROFILING_TOOL_COMPARISON.md | 11 KB | 10 | 5 | 12 |
| **TOTAL** | **90 KB** | **38** | **35+** | **28** |

---

## Key Takeaways

1. **Tool Selection Matters**
   - PyTorch Profiler: Fast, low overhead, best for model layers
   - Nsight Compute: Detailed, high overhead, best for kernel analysis
   - Nsight Systems: System view, moderate overhead, best for timelines

2. **Three Bottleneck Types**
   - Memory-bound (optimize access patterns)
   - Compute-bound (use tensor cores)
   - Latency-bound (increase parallelism)

3. **Roofline Model is Key**
   - Compare arithmetic intensity to ridge point
   - Simple visual classification
   - Guides optimization direction

4. **Profiling is Iterative**
   - Profile → Identify → Optimize → Repeat
   - Use multiple tools for confirmation
   - Collect multiple runs (5-10) for stability

5. **Automate Everything**
   - Profiler overhead is high (5-50x)
   - Use CI/CD integration for regression detection
   - Set up continuous monitoring for production

---

## Further Resources

### NVIDIA Documentation
- Nsight Compute Manual: https://docs.nvidia.com/nsight-compute/
- CUDA Programming Guide: https://docs.nvidia.com/cuda/cuda-c-programming-guide/
- GPU Performance Analysis: https://developer.nvidia.com/blog/

### AMD Documentation
- Omniperf Repository: https://github.com/ROCm/omniperf
- ROCm Profiling Guide: https://rocmdocs.amd.com/

### Academic Papers
- Roofline Model: Williams, Waterman, Patterson (2009)
- GPU Performance Modeling: Volkov & Demmel (2010)

### Community
- NVIDIA GPU Gems: https://developer.nvidia.com/gpu-gems/
- Stack Overflow GPU tags: [cuda], [gpu], [opencl]
- GitHub research projects

---

## How to Use These Documents

**For research:** Read GPU_BOTTLENECK_IDENTIFICATION_GUIDE.md systematically

**For implementation:** Copy scripts from GPU_BOTTLENECK_QUICK_START.md

**For production:** Follow workflows in GPU_PROFILING_TOOLCHAIN.md

**For tool selection:** Use GPU_PROFILING_TOOL_COMPARISON.md decision tree

**For quick answers:** Consult the metric interpretation tables in any document

---

## Document Maintenance

These documents cover:
- ✓ NVIDIA Nsight Compute (latest versions)
- ✓ NVIDIA Nsight Systems (latest versions)
- ✓ PyTorch Profiler (1.12+)
- ✓ TensorFlow Profiler (2.0+)
- ✓ AMD Omniperf (latest)
- ✓ rocprof (latest)
- ✓ nvidia-smi utilities
- ✓ Roofline model fundamentals

Last updated: 2025-02-21
Covers tools as of: CUDA 12.0+, ROCm 5.0+, PyTorch 2.0+, TensorFlow 2.10+

