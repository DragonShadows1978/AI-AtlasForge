# PTX and SASS: Practical Guide for Extraction and Analysis

## Quick Start: From CUDA Code to SASS Analysis

### Workflow 1: Compile Source → PTX → SASS → Analysis

```bash
# Step 1: Create CUDA kernel file
cat > kernel.cu << 'EOF'
__global__ void matmul(float *A, float *B, float *C, int N) {
    int row = blockIdx.y * blockDim.y + threadIdx.y;
    int col = blockIdx.x * blockDim.x + threadIdx.x;
    
    if (row < N && col < N) {
        float sum = 0.0f;
        for (int k = 0; k < N; ++k) {
            sum += A[row * N + k] * B[k * N + col];
        }
        C[row * N + col] = sum;
    }
}
EOF

# Step 2: Generate PTX (intermediate representation)
nvcc -ptx -arch=compute_86 kernel.cu -o kernel.ptx

# Step 3: Assemble PTX to SASS (native GPU assembly)
ptxas -arch=sm_86 kernel.ptx -o kernel.cubin

# Step 4: Disassemble SASS for inspection
nvdisasm -b SM_86 kernel.cubin > kernel.sass

# Step 5: Extract detailed build information
ptxas -v -arch=sm_86 kernel.ptx -o kernel.cubin 2>&1 | tee build.log

# View the results
echo "=== PTX Code ===" && head -50 kernel.ptx
echo "=== SASS Code ===" && head -50 kernel.sass
echo "=== Build Stats ===" && grep -E "register|memory|instruction" build.log
```

### Workflow 2: Extract from Compiled Binary

```bash
# If you only have a compiled executable or library
binary=my_application

# Extract all PTX from the compiled binary
cuobjdump -ptx $binary > extracted.ptx

# Extract all SASS
cuobjdump -sass $binary > extracted.sass

# Get symbol information
cuobjdump -symbols $binary > symbols.txt

# Get all information at once
cuobjdump -all $binary > complete_analysis.txt

# Extract for specific GPU architecture
cuobjdump -ptx -arch=all $binary > all_architectures.ptx
```

## Understanding PTX Code

### PTX Instruction Set Categories

#### Memory Operations
```ptx
# Load from global memory
ld.global.f32    %f0, [%rd2]           // Load 32-bit float from address in %rd2

# Store to global memory
st.global.f32    [%rd2], %f0           // Store %f0 to address in %rd2

# Shared memory (thread-block local)
ld.shared.f32    %f1, [%r3]            // Load from shared memory
st.shared.f32    [%r3], %f1            // Store to shared memory

# Constant memory
ld.const.f32     %f2, [%rd4]           // Load from constant memory
```

#### Arithmetic Operations
```ptx
# Basic arithmetic
add.f32          %f3, %f0, %f1         // f3 = f0 + f1
mul.f32          %f4, %f0, %f1         // f4 = f0 * f1
fma.f32          %f5, %f0, %f1, %f2    // f5 = f0*f1 + f2 (fused multiply-add)

# Integer operations
add.s32          %r5, %r3, %r4         // 32-bit integer addition
mul.wide.s32     %rd10, %r5, %r6       // Wide multiply (result in 64-bit)
```

#### Control Flow
```ptx
# Conditional branching
setp.gt.f32      %p1, %f0, %f1         // Compare: p1 = (f0 > f1)
@%p1 bra target_label                  // Branch if predicate true

# Synchronization
bar.sync         0                      // Synchronize all threads in block

# Function calls
call (%f0), kernel_func, (%r0, %r1)   // Call function with args
```

#### Special Register Access
```ptx
# Thread and block indices
mov.u32          %r0, %tid.x           // Get thread ID in x dimension
mov.u32          %r1, %ctaid.x         // Get block ID in x dimension

# Thread limits
mov.u32          %r2, %ntid.x          // Get threads per block in x
mov.u32          %r3, %nctaid.x        // Get grid dimension in x
```

### Analyzing PTX for Optimization

```bash
# Count instruction types
grep "^[[:space:]]*[a-z]" kernel.ptx | sed 's/\.[^[:space:]]*//' | sort | uniq -c | sort -rn

# Find memory operations
grep -E "ld\.|st\." kernel.ptx | wc -l

# Find arithmetic operations
grep -E "add\.|mul\.|fma\." kernel.ptx | wc -l

# Find synchronization barriers
grep "bar\.sync" kernel.ptx | wc -l

# Find register usage pattern
grep -E "%(f|r|rd)[0-9]" kernel.ptx | sed 's/.*%//' | sed 's/[^0-9a-z].*//' | sort | uniq -c
```

## Understanding SASS Code

### SASS Instruction Format (Ada/Ampere Example)

SASS code disassembly shows GPU native instructions with format:
```
<address>  <opcode>  <operands>  ; <scheduling info>  ; <predicate info>
```

Example SASS instruction:
```sass
0x00 : MOV R1, R0 ;                    // Move register 0 to register 1
0x04 : LDS.128 R0, [R4] ;              // Load 128 bits from shared memory
0x08 : FADD R2, R0, R1 ;               // Floating-point add
```

### Key SASS Instructions

#### Data Movement
```sass
MOV R1, R2           # Move between registers
LDS R0, [R2]         # Load from shared/local memory
STS [R2], R0         # Store to shared/local memory
LDG R0, [R2]         # Load from global memory
STG [R2], R0         # Store to global memory
```

#### Arithmetic
```sass
FADD R0, R1, R2      # FP32 addition
FMUL R0, R1, R2      # FP32 multiplication  
FFMA R0, R1, R2, R3  # Fused multiply-add
IMAD R0, R1, R2, R3  # Integer multiply-add
```

#### Control Flow
```sass
SETP P0, R1, R2      # Set predicate (comparison)
JCAL L1              # Jump with call (branch)
BRA L2               # Branch to label
SYNC                 # Thread synchronization
```

#### Special Operations
```sass
LDGSTS R0, [R1], [R2]  # Global-to-shared transfer
BFE R0, R1, R2         # Bit field extract
BFI R0, R1, R2, R3     # Bit field insert
SHFL R0, R1, R2        # Warp shuffle
```

### Analyzing SASS for Performance

```bash
# Count instruction frequency
grep "^[[:space:]][0-9a-f]" kernel.sass | \
  awk '{print $2}' | \
  sort | uniq -c | sort -rn

# Find memory operations (usually performance bottleneck)
grep -E "LDS|STS|LDG|STG|LDGSTS" kernel.sass | wc -l

# Identify register pressure
nvdisasm -ptxregcount kernel.cubin

# Find warp shuffle operations (communication overhead)
grep "SHFL" kernel.sass | wc -l

# Identify critical path instructions
grep -E "FFMA|IMAD|FMUL" kernel.sass | head -20
```

## Side-by-Side PTX and SASS Comparison

### Manual Comparison Script

```python
#!/usr/bin/env python3
"""Compare PTX and SASS instructions side-by-side"""

import re
import subprocess
import sys

def extract_ptx_instructions(ptx_file):
    """Extract main PTX instructions (simplified)"""
    instructions = []
    with open(ptx_file) as f:
        for line in f:
            if re.match(r'\s+[a-z]+\.', line):
                instructions.append(line.strip())
    return instructions

def extract_sass_instructions(cubin_file, arch="SM_86"):
    """Extract SASS instructions using nvdisasm"""
    result = subprocess.run(
        ['nvdisasm', '-b', arch, cubin_file],
        capture_output=True,
        text=True
    )
    instructions = []
    for line in result.stdout.split('\n'):
        if re.match(r'\s*[0-9a-f]+\s*:', line):
            instructions.append(line.strip())
    return instructions

def analyze_instruction_expansion(ptx_file, cubin_file):
    """Show how PTX instructions expand to SASS"""
    ptx_insts = extract_ptx_instructions(ptx_file)
    sass_insts = extract_sass_instructions(cubin_file)
    
    print("=== PTX Instruction Count ===")
    print(f"Total PTX instructions: {len(ptx_insts)}")
    
    # Count instruction types in PTX
    ptx_types = {}
    for inst in ptx_insts:
        inst_type = inst.split('.')[0]
        ptx_types[inst_type] = ptx_types.get(inst_type, 0) + 1
    
    print("\nPTX Instruction Breakdown:")
    for inst_type, count in sorted(ptx_types.items(), key=lambda x: -x[1])[:10]:
        print(f"  {inst_type}: {count}")
    
    print(f"\n=== SASS Instruction Count ===")
    print(f"Total SASS instructions: {len(sass_insts)}")
    
    # Count instruction types in SASS
    sass_types = {}
    for inst in sass_insts:
        # Extract main opcode (simplified)
        match = re.search(r'\s([A-Z]+)', inst)
        if match:
            inst_type = match.group(1)
            sass_types[inst_type] = sass_types.get(inst_type, 0) + 1
    
    print("\nSASS Instruction Breakdown (top 10):")
    for inst_type, count in sorted(sass_types.items(), key=lambda x: -x[1])[:10]:
        print(f"  {inst_type}: {count}")
    
    print(f"\n=== Expansion Ratio ===")
    if len(ptx_insts) > 0:
        ratio = len(sass_insts) / len(ptx_insts)
        print(f"SASS-to-PTX ratio: {ratio:.2f}x")

if __name__ == '__main__':
    if len(sys.argv) != 3:
        print("Usage: python3 compare_ptx_sass.py <kernel.ptx> <kernel.cubin>")
        sys.exit(1)
    
    ptx_file = sys.argv[1]
    cubin_file = sys.argv[2]
    
    analyze_instruction_expansion(ptx_file, cubin_file)
```

Usage:
```bash
python3 compare_ptx_sass.py kernel.ptx kernel.cubin
```

## Using Nsight Compute for Performance Correlation

### Basic Profiling Workflow

```bash
# Step 1: Compile with optimizations
nvcc -O3 -arch=sm_86 -o my_kernel my_kernel.cu

# Step 2: Run Nsight Compute profiling
ncu --export profile.ncu-rep ./my_kernel

# Step 3: Analyze in GUI
ncu-ui profile.ncu-rep
```

### Command-Line Analysis

```bash
# Profile specific kernels only
ncu --kernel regex:matmul ./my_kernel

# Set profiling scope
ncu --set full ./my_kernel

# Export detailed CSV data
ncu --export profile.ncu-rep --csv ./my_kernel

# View specific metrics
ncu --metrics gpu__smsp_cycle_active ./my_kernel
```

### Key Metrics for PTX/SASS Correlation
- **GPU Active Cycles**: Time spent executing instructions
- **Stall Reasons**: Why threads are stalled (memory, instruction dependencies, etc.)
- **Instruction Throughput**: Instructions per cycle
- **Memory Throughput**: Actual vs theoretical bandwidth
- **Register Efficiency**: Wasted register allocation

## Advanced Debugging with CUDA-GDB

### Setting up GDB for Disassembly Inspection

```bash
# Compile with debug symbols
nvcc -g -G -arch=sm_86 kernel.cu -o kernel_debug

# Launch debugger
cuda-gdb ./kernel_debug

# Within GDB:
(cuda-gdb) break kernel_name
(cuda-gdb) run
(cuda-gdb) info breakpoints
(cuda-gdb) cuda kernel block thread          # Select specific thread
(cuda-gdb) disassemble                       # Show current SASS disassembly
(cuda-gdb) x/10i $pc                         # Show instructions at program counter
(cuda-gdb) info registers                    # Show all registers
(cuda-gdb) print %rd0                        # Print specific register
```

### Correlating Source to SASS

```bash
# In CUDA-GDB with source available
(cuda-gdb) list                              # Show source code
(cuda-gdb) disassemble /m                    # Disassembly with source lines
(cuda-gdb) step                              # Step one source line
(cuda-gdb) stepi                             # Step one SASS instruction
```

## Performance Analysis Workflow

### 1. Identify Bottleneck

```bash
# Profile to find hot kernels
ncu --metrics gpu__smsp_cycle_active ./my_app

# Identify if memory or compute bound
ncu --metrics gpu__smsp_inst_executed,gpu__dram_throughput ./my_app
```

### 2. Extract Code

```bash
# Get PTX for analysis
cuobjdump -ptx ./my_app > app.ptx

# Find your kernel in PTX
grep -A 50 "\.entry kernel_name" app.ptx > kernel.ptx
```

### 3. Generate SASS

```bash
# Compile PTX to SASS
ptxas -arch=sm_86 -v kernel.ptx -o kernel.cubin 2>&1 | tee build.log

# Disassemble
nvdisasm -b SM_86 kernel.cubin > kernel.sass
```

### 4. Analyze Instruction Patterns

```bash
# Memory access patterns
grep -E "LDS|STG|LDG" kernel.sass | head -20

# Critical loop operations
grep -E "FFMA|IMAD" kernel.sass | head -10

# Register usage
ptxas -v -arch=sm_86 kernel.ptx 2>&1 | grep register
```

### 5. Optimize and Recompile

- Modify source code based on findings
- Regenerate PTX and SASS
- Compare instruction counts and resource usage
- Re-profile to validate improvements

## Resource Usage Information

### Interpreting ptxas Output

```bash
$ ptxas -v -arch=sm_86 kernel.ptx -o kernel.cubin

# Output example:
# ptxas info    : 0 bytes gmem
# ptxas info    : Compiling entry function '_Z7kernelPfii'
# ptxas info    : Function properties for '_Z7kernelPfii'
#     0 bytes stack frame, 0 bytes spill stores, 0 bytes spill loads
# ptxas info    : Used 16 registers, 2048 bytes smem

# Breakdown:
# - Used 16 registers per thread
# - 2048 bytes shared memory per block
# - 0 bytes stack (no local arrays)
# - 0 spill (good - fits in registers)
```

### Register Pressure Analysis

```bash
# High register usage limits blocks per SM
echo "Max 256 registers per thread (Ampere)"
echo "Max 128 registers per thread (Maxwell)"
echo "Max 64 registers per thread (Volta)"

# For example, with 16 registers and 256-register limit:
# 256 / 16 = 16 warps per block max (each warp = 32 threads)
# 16 warps * 32 threads = 512 threads per block possible
```

## Tools Comparison Table

| Task | Best Tool | Command |
|------|-----------|---------|
| Extract PTX from binary | cuobjdump | `cuobjdump -ptx app > app.ptx` |
| View SASS | nvdisasm | `nvdisasm -b SM_86 kernel.cubin` |
| Generate PTX from source | nvcc | `nvcc -ptx kernel.cu` |
| Assemble PTX to SASS | ptxas | `ptxas -arch=sm_86 kernel.ptx` |
| Debug SASS execution | cuda-gdb | `cuda-gdb ./kernel` |
| Profile & correlate | Nsight Compute | `ncu --export report.ncu-rep ./app` |
| Binary analysis | Radare2 | `r2 ./app` |
| Resource analysis | ptxas -v | `ptxas -v kernel.ptx` |

## Common Pitfalls and Solutions

### Pitfall 1: Architecture Mismatch
```bash
# Wrong: Disassemble with incorrect architecture
nvdisasm kernel.cubin  # Assumes default arch

# Right: Specify target architecture
nvdisasm -b SM_86 kernel.cubin  # Specify your GPU

# Find your GPU:
nvidia-smi --query-gpu=compute_cap --format=csv,noheader
```

### Pitfall 2: Missing Debug Information
```bash
# Wrong: Compile without debug info
nvcc kernel.cu -o kernel

# Right: Include debug symbols
nvcc -g -G kernel.cu -o kernel
# Then disassemble with source mapping:
nvdisasm -c -l kernel.cubin
```

### Pitfall 3: Optimization Hiding Real Code
```bash
# Wrong: Analyze optimized code
nvcc -O3 kernel.cu

# Right: Start with less aggressive optimization
nvcc -O0 kernel.cu  # See actual code structure

# Then compare:
nvcc -O3 kernel.cu  # See what optimizer does
```

### Pitfall 4: Not Considering Memory Hierarchy
```bash
# Bad: Analyzing only register usage
ptxas -v kernel.ptx

# Good: Check all memory usage
ptxas -v kernel.ptx 2>&1 | grep -E "register|gmem|smem|spill"
```

## Integration with Build Systems

### CMake Integration

```cmake
# CMakeLists.txt
find_package(CUDA REQUIRED)

add_executable(my_kernel kernel.cu)
target_compile_options(my_kernel PRIVATE $<$<COMPILE_LANGUAGE:CUDA>: -ptx>)

# Generate PTX and SASS in build directory
add_custom_command(TARGET my_kernel POST_BUILD
    COMMAND nvcc -ptx -arch=compute_86 ${CMAKE_CURRENT_SOURCE_DIR}/kernel.cu
            -o ${CMAKE_CURRENT_BINARY_DIR}/kernel.ptx
    COMMAND ptxas -arch=sm_86 ${CMAKE_CURRENT_BINARY_DIR}/kernel.ptx
            -o ${CMAKE_CURRENT_BINARY_DIR}/kernel.cubin
    COMMAND nvdisasm -b SM_86 ${CMAKE_CURRENT_BINARY_DIR}/kernel.cubin
            > ${CMAKE_CURRENT_BINARY_DIR}/kernel.sass
)
```

### Makefile Integration

```makefile
CUDA_PATH ?= /usr/local/cuda
NVCC = $(CUDA_PATH)/bin/nvcc
PTXAS = $(CUDA_PATH)/bin/ptxas
NVDISASM = $(CUDA_PATH)/bin/nvdisasm

ARCH = -arch=compute_86
SM = SM_86

all: kernel.sass

kernel.ptx: kernel.cu
	$(NVCC) -ptx $(ARCH) $< -o $@

kernel.cubin: kernel.ptx
	$(PTXAS) -arch=sm_86 $< -o $@

kernel.sass: kernel.cubin
	$(NVDISASM) -b $(SM) $< > $@

clean:
	rm -f kernel.ptx kernel.cubin kernel.sass
```

---

**Note**: GPU architecture codes (SM_50, SM_60, SM_70, SM_75, SM_80, SM_86, SM_87, SM_89, SM_90) change with each generation. Use `nvidia-smi --query-gpu=compute_cap` to find your GPU's compute capability.
