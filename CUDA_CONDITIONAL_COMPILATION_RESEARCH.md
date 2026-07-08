# CUDA Conditional Compilation Patterns with __CUDA_ARCH__

## Executive Summary

`__CUDA_ARCH__` is a predefined preprocessor macro in CUDA that enables architecture-specific conditional compilation. It represents the GPU compute capability (architecture version) and is set at compile time per PTX target. This research document synthesizes best practices, common patterns, and implementation strategies from NVIDIA libraries and the broader CUDA ecosystem.

---

## 1. Fundamentals of __CUDA_ARCH__

### 1.1 What is __CUDA_ARCH__?

- **Definition**: A preprocessor macro that encodes the GPU compute capability as an integer
- **Format**: Numeric representation of SM (Streaming Multiprocessor) architecture version
- **Examples**:
  - SM 5.0 (Maxwell) → `__CUDA_ARCH__ = 500`
  - SM 6.0 (Pascal) → `__CUDA_ARCH__ = 600`
  - SM 7.0 (Volta) → `__CUDA_ARCH__ = 700`
  - SM 8.0 (Ampere) → `__CUDA_ARCH__ = 800`
  - SM 9.0 (Hopper) → `__CUDA_ARCH__ = 900`

### 1.2 Key Characteristics

| Aspect | Details |
|--------|---------|
| **Scope** | Device-side code only (kernel functions) |
| **Set During** | Compilation (per target architecture) |
| **Type** | Preprocessor macro (compile-time constant) |
| **Host Code** | Not available; use `cudaGetDeviceProperties()` instead |
| **Varies By** | `--gpu-architecture` or `-arch=sm_XX` compilation flag |

### 1.3 Compute Capability Versions

```
Architecture          Compute Capability    __CUDA_ARCH__
Maxwell               5.0, 5.2, 5.3         500, 520, 530
Pascal                6.0, 6.1, 6.2         600, 610, 620
Volta                 7.0, 7.2              700, 720
Turing                7.5                   750
Ampere                8.0, 8.6, 8.7         800, 860, 870
Ada Lovelace          8.9                   890
Hopper                9.0                   900
```

---

## 2. Basic Conditional Compilation Patterns

### 2.1 Simple Architecture Checks

**Pattern 1: Single Architecture Threshold**
```cuda
__global__ void kernel() {
    #if __CUDA_ARCH__ >= 700
        // Volta and newer: Use advanced features
        __syncwarp();  // Available since Volta
    #else
        // Maxwell/Pascal: Use compatible fallback
        __syncthreads();
    #endif
}
```

**Pattern 2: Specific Architecture Range**
```cuda
__global__ void compute() {
    #if __CUDA_ARCH__ >= 600 && __CUDA_ARCH__ < 700
        // Pascal-specific optimization
        int warp_sum = __shfl_down_sync(0xffffffff, value, 1);
    #elif __CUDA_ARCH__ >= 700
        // Volta+: Enhanced shuffle operations
        int warp_sum = __shfl_down_sync(0xffffffff, value, 1);
    #else
        // Maxwell fallback
        int warp_sum = __shfl_down(value, 1);
    #endif
}
```

**Pattern 3: Multiple Architecture Levels**
```cuda
__device__ float atomic_add_precision(float *addr, float val) {
    #if __CUDA_ARCH__ >= 600
        // Pascal+: Direct atomic add for float
        return atomicAdd(addr, val);
    #elif __CUDA_ARCH__ >= 500
        // Maxwell: Use CAS-based implementation
        float old = *addr, assumed;
        do {
            assumed = old;
            old = atomicCAS((unsigned int*)addr, 
                            __float_as_int(assumed), 
                            __float_as_int(assumed + val));
        } while(__int_as_float(old) != assumed);
        return old;
    #endif
}
```

### 2.2 Feature-Level Conditionals

**Pattern 4: Capability-Based Feature Gates**
```cuda
__global__ void advanced_kernel() {
    #if __CUDA_ARCH__ >= 800  // Ampere
        // Tensor Float 32 (TF32)
        // Sparse Tensor Cores
        // Asynchronous Copy (cp.async)
    #elif __CUDA_ARCH__ >= 700  // Volta
        // Tensor Cores (INT8, FP32, FP64)
        // Independent Thread Scheduling
    #elif __CUDA_ARCH__ >= 600  // Pascal
        // Compute Capability 6.x features
    #endif
}
```

**Pattern 5: Hardware Instruction Availability**
```cuda
__device__ int fast_popcount(unsigned int x) {
    #if __CUDA_ARCH__ >= 500
        // Maxwell+: Hardware popcnt instruction
        return __popc(x);
    #else
        // Older: Software implementation
        return __builtin_popcount(x);
    #endif
}
```

---

## 3. Common Patterns in NVIDIA Libraries

### 3.1 CUTLASS (CUDA Templates for Linear Algebra Subroutines)

CUTLASS extensively uses `__CUDA_ARCH__` for architecture-specific optimizations:

```cuda
// From CUTLASS gemm kernels
namespace cutlass {
namespace gemm {
namespace kernel {

template <typename Mma>
__global__ void Gemm(GemmCoord problem_size) {
    // Shared memory staging patterns differ by architecture
    #if __CUDA_ARCH__ >= 700  // Volta
        // Use double-buffering with async pipeline
        cutlass::cp_async::copy_tile<...>();
    #elif __CUDA_ARCH__ >= 600  // Pascal
        // Use explicit shared memory copies
        __syncthreads();
    #endif
}

}}}
```

**Key CUTLASS Patterns**:
- SM-specific tile sizes (32x32 vs 16x16)
- Async copy support (SM 80+)
- Shared memory optimization strategies
- Warp-level instruction selection

### 3.2 cuDNN (CUDA Deep Neural Network Library)

cuDNN uses architecture detection for:

```cuda
// Pseudo-code from cuDNN patterns
__global__ void conv_forward() {
    #if __CUDA_ARCH__ == 800  // Ampere-specific
        // Sparse tensor optimizations
        // Enhanced memory bandwidth utilization
    #elif __CUDA_ARCH__ >= 700
        // Tensor Core-based operations
        // Mixed-precision compute
    #endif
}
```

**Key cuDNN Patterns**:
- Tensor Core vs non-Tensor Core execution paths
- Memory coalescing strategies per architecture
- Occupancy-optimized thread block sizes
- Precision-specific implementations (FP32, FP16, INT8)

### 3.3 cuBLAS (CUDA Basic Linear Algebra Subroutines)

```cuda
// Architecture-aware matrix operations
__global__ void matrix_multiply() {
    #if __CUDA_ARCH__ >= 700
        // Volta: Use Tensor Cores for better throughput
        // Tile: 128x128 or 256x256
    #elif __CUDA_ARCH__ >= 500
        // Maxwell: Traditional CUDA cores
        // Tile: 64x64 or 128x128
    #endif
}
```

### 3.4 TensorRT (NVIDIA Inference Engine)

TensorRT uses `__CUDA_ARCH__` for:
- Choosing optimal kernel implementations
- FP32 vs FP16 vs INT8 code paths
- Memory layout optimizations
- Warp utilization patterns

---

## 4. Code Organization Strategies

### 4.1 Header-Only Library Pattern

```cuda
// cuda_arch_utils.h
#pragma once

namespace cuda_arch {

// Architecture-agnostic interface
__device__ inline float reduce_warp(float val) {
    #if __CUDA_ARCH__ >= 700
        // Volta: Optimized shuffle
        return __shfl_down_sync(0xffffffff, val, 16) +
               __shfl_down_sync(0xffffffff, val, 8) +
               __shfl_down_sync(0xffffffff, val, 4) +
               __shfl_down_sync(0xffffffff, val, 2) +
               __shfl_down_sync(0xffffffff, val, 1);
    #else
        // Maxwell: Legacy shuffle
        return __shfl_down(val, 16) +
               __shfl_down(val, 8) +
               __shfl_down(val, 4) +
               __shfl_down(val, 2) +
               __shfl_down(val, 1);
    #endif
}

__device__ inline int popcnt(unsigned int x) {
    #if __CUDA_ARCH__ >= 500
        return __popc(x);
    #else
        return __builtin_popcount(x);
    #endif
}

}  // namespace cuda_arch
```

### 4.2 Separate Compilation Units Pattern

**Structure**:
```
kernel_generic.cu      // Fallback implementation
kernel_sm60.cu         // Pascal-specific
kernel_sm70.cu         // Volta-specific
kernel_sm80.cu         // Ampere-specific
kernel_dispatch.cu     // Runtime dispatch
```

**Implementation**:
```cuda
// kernel_generic.cu - compile with -arch=sm_50
__global__ void kernel_impl(float *data, int n) {
    // Generic implementation
}

// kernel_sm80.cu - compile with -arch=sm_80
__global__ void kernel_impl(float *data, int n) {
    // Ampere-optimized implementation
}

// kernel_dispatch.cu
void launch_kernel(float *data, int n, cudaStream_t stream) {
    int device;
    cudaGetDevice(&device);
    cudaDeviceProp props;
    cudaGetDeviceProperties(&props, device);
    
    if (props.major >= 8) {
        // Launch Ampere version
        kernel_impl_sm80<<<grid, block, 0, stream>>>(data, n);
    } else if (props.major >= 7) {
        // Launch Volta version
        kernel_impl_sm70<<<grid, block, 0, stream>>>(data, n);
    } else {
        // Launch generic version
        kernel_impl_generic<<<grid, block, 0, stream>>>(data, n);
    }
}
```

### 4.3 Template Specialization Pattern

```cuda
template<int ArchVersion>
struct KernelTraits;

template<>
struct KernelTraits<500> {
    static constexpr int BLOCK_SIZE = 256;
    static constexpr int TILE_M = 64;
    static constexpr int TILE_N = 64;
    static constexpr bool HAS_SYNC_WARP = false;
};

template<>
struct KernelTraits<700> {
    static constexpr int BLOCK_SIZE = 256;
    static constexpr int TILE_M = 128;
    static constexpr int TILE_N = 128;
    static constexpr bool HAS_SYNC_WARP = true;
};

template<>
struct KernelTraits<800> {
    static constexpr int BLOCK_SIZE = 256;
    static constexpr int TILE_M = 256;
    static constexpr int TILE_N = 256;
    static constexpr bool HAS_SYNC_WARP = true;
    static constexpr bool HAS_ASYNC_COPY = true;
};

template<typename Traits>
__global__ void compute_kernel(float *output, const float *input, int n) {
    __shared__ float smem[Traits::BLOCK_SIZE];
    
    #if __CUDA_ARCH__ >= 700
        if constexpr (Traits::HAS_SYNC_WARP) {
            __syncwarp();
        }
    #endif
}
```

### 4.4 Multi-Architecture Compilation Configuration

**CMakeLists.txt**:
```cmake
# Compile for multiple architectures
set(CUDA_ARCHITECTURES 
    50    # Maxwell (fallback)
    60    # Pascal
    70    # Volta
    75    # Turing
    80    # Ampere
    90    # Hopper
)

# Enable architecture-specific compilation
set(CMAKE_CUDA_FLAGS "${CMAKE_CUDA_FLAGS} -Xcompiler -fPIC")

# Create architecture-specific targets
foreach(ARCH ${CUDA_ARCHITECTURES})
    add_library(kernel_sm${ARCH} OBJECT kernel.cu)
    target_compile_options(kernel_sm${ARCH} PRIVATE 
        $<$<COMPILE_LANGUAGE:CUDA>:-arch=sm_${ARCH}>
    )
    list(APPEND KERNEL_OBJECTS $<TARGET_OBJECTS:kernel_sm${ARCH}>)
endforeach()

# Link all versions
add_library(cuda_kernels STATIC ${KERNEL_OBJECTS})
```

---

## 5. Preprocessor Directives and Syntax

### 5.1 Standard Preprocessor Operations

| Directive | Usage | Example |
|-----------|-------|---------|
| `#if` | Conditional compilation | `#if __CUDA_ARCH__ >= 700` |
| `#elif` | Else-if clause | `#elif __CUDA_ARCH__ >= 600` |
| `#else` | Default fallback | `#else` |
| `#endif` | End conditional block | `#endif` |
| `#ifdef` | Check macro definition | `#ifdef __CUDA_ARCH__` |
| `#ifndef` | Check macro undefined | `#ifndef __CUDA_ARCH__` |
| `#defined()` | Function-like check | `#if defined(__CUDA_ARCH__)` |

### 5.2 Combining Conditions

```cuda
// Multiple conditions with logical operators
#if __CUDA_ARCH__ >= 700 && __CUDA_ARCH__ < 800
    // Volta-specific code
#elif (__CUDA_ARCH__ >= 600 && __CUDA_ARCH__ < 700) || __CUDA_ARCH__ >= 900
    // Pascal or Hopper
#endif

// Architecture range checking
#if __CUDA_ARCH__ >= 500 && __CUDA_ARCH__ != 510
    // Maxwell except SM 5.1 (special handling)
#endif

// Feature-based checks
#if __CUDA_ARCH__ >= 600
    #define HAS_ATOMIC_FLOAT 1
#else
    #define HAS_ATOMIC_FLOAT 0
#endif
```

### 5.3 Common Pitfalls

```cuda
// WRONG: Using runtime values
int major = props.major;
if (major >= 7) {  // This is NOT the same as __CUDA_ARCH__
    // This is RUNTIME, not compile-time
}

// CORRECT: Use compile-time __CUDA_ARCH__
#if __CUDA_ARCH__ >= 700
    // This is compile-time
#endif

// WRONG: Referencing outside kernel
void host_function() {
    #if __CUDA_ARCH__ >= 700  // Error! Not in device code
    #endif
}

// CORRECT: Only in device functions
__global__ void kernel() {
    #if __CUDA_ARCH__ >= 700
        // OK - kernel is device code
    #endif
}

// WRONG: Comparing with floating point
#if __CUDA_ARCH__ >= 7.0  // Syntax error
#endif

// CORRECT: Integer comparison only
#if __CUDA_ARCH__ >= 700
#endif
```

---

## 6. Performance Implications and Optimization Strategies

### 6.1 Compilation Overhead

| Strategy | Compilation Time | Binary Size | Flexibility |
|----------|------------------|-------------|-------------|
| Single architecture | Very Fast | Smallest | Low |
| Fat binary | Moderate | Large (2-5x) | High |
| JIT (PTX) | Slow (first run) | Small | Very High |
| Separate .cu files | Slow (parallel) | Medium | High |

### 6.2 Runtime Cost vs. Compile-Time Benefit

**Compile-Time Conditionals** (using `__CUDA_ARCH__`):
- Cost: None at runtime (code is already selected)
- Benefit: Smallest binaries, optimal per-architecture code
- Use case: Production deployments with known GPUs

```cuda
__global__ void optimized_kernel() {
    #if __CUDA_ARCH__ >= 800
        // This branch completely compiled out for older GPUs
        // Zero runtime overhead
    #endif
}
```

**Runtime Conditionals** (using `cudaGetDeviceProperties`):
- Cost: Small branch overhead per kernel launch
- Benefit: Single binary supports all architectures
- Use case: General-purpose applications

```cuda
void launch_kernel() {
    int device;
    cudaGetDevice(&device);
    cudaDeviceProp props;
    cudaGetDeviceProperties(&props, device);
    
    if (props.major >= 8) {
        // Runtime decision - small cost per launch
        kernel_sm80<<<grid, block>>>();
    }
}
```

### 6.3 Code Size Reduction Techniques

```cuda
// Technique 1: Use __CUDA_ARCH__ to avoid dead code
#if __CUDA_ARCH__ >= 700
__device__ void use_volta_feature() {
    __syncwarp();  // Compiled out for Maxwell
}
#endif

// Technique 2: Combine related conditionals
#if __CUDA_ARCH__ >= 600
    #define SHUFFLE_OPCODE(v, d) __shfl_down_sync(0xffffffff, v, d)
#else
    #define SHUFFLE_OPCODE(v, d) __shfl_down(v, d)
#endif

// Technique 3: Extract common code to shared headers
// Headers use macros, each .cu file compiled with specific -arch flag
```

### 6.4 Instruction-Level Optimizations

**Example: Reduction Pattern**

```cuda
// Generic reduction - works on all architectures
__device__ float warp_reduce_generic(float val) {
    val += __shfl_down(val, 16);
    val += __shfl_down(val, 8);
    val += __shfl_down(val, 4);
    val += __shfl_down(val, 2);
    val += __shfl_down(val, 1);
    return val;
}

// Optimized for Volta with __syncwarp()
#if __CUDA_ARCH__ >= 700
__device__ float warp_reduce_optimized(float val) {
    const int MASK = 0xffffffff;
    val += __shfl_down_sync(MASK, val, 16);
    val += __shfl_down_sync(MASK, val, 8);
    val += __shfl_down_sync(MASK, val, 4);
    val += __shfl_down_sync(MASK, val, 2);
    val += __shfl_down_sync(MASK, val, 1);
    return val;
}
#else
#define warp_reduce_optimized warp_reduce_generic
#endif
```

---

## 7. Best Practices Summary

### 7.1 DO's

1. **Use `__CUDA_ARCH__` for truly incompatible features**
   ```cuda
   #if __CUDA_ARCH__ >= 600
       atomicAdd(ptr, val);  // Native float atomic add
   #endif
   ```

2. **Organize multi-architecture support systematically**
   - Use header libraries with `#if __CUDA_ARCH__` guards
   - Document which architectures each code path targets
   - Test on representative hardware

3. **Keep conditionals for significant optimizations**
   ```cuda
   // Worth the extra code path
   #if __CUDA_ARCH__ >= 800
       // Ampere: Async copy with cp.async
   #endif
   ```

4. **Document architecture-specific behavior**
   ```cuda
   // SM 7.0+: __syncwarp() has O(1) latency
   // SM 5.x-6.x: __syncwarp() may have higher latency or unavailable
   __device__ void sync_safe() {
       #if __CUDA_ARCH__ >= 700
           __syncwarp();
       #else
           __syncthreads();  // Fallback with higher overhead
       #endif
   }
   ```

5. **Profile both code paths**
   - Compile for each target architecture
   - Measure performance differences
   - Verify correctness across all paths

### 7.2 DON'Ts

1. **Don't overuse conditionals for minor differences**
   ```cuda
   // Unnecessary - unlikely to matter
   #if __CUDA_ARCH__ >= 600
       int x = 32;
   #else
       int x = 31;
   #endif
   ```

2. **Don't confuse compile-time and runtime decisions**
   ```cuda
   // WRONG
   int compute_capability = props.major * 100 + props.minor;
   #if compute_capability >= 700  // This doesn't work!
   #endif
   
   // CORRECT - use separate kernel functions or runtime dispatch
   ```

3. **Don't forget to test older architectures**
   ```cuda
   // Many developers only test on latest GPU
   // Must verify fallback paths work correctly
   ```

4. **Don't use `__CUDA_ARCH__` in host code**
   ```cuda
   // WRONG - __CUDA_ARCH__ undefined in host code
   void host_function() {
       #if __CUDA_ARCH__ >= 700  // Compile error or unexpected behavior
       #endif
   }
   ```

5. **Don't create unmaintainable conditional spirals**
   ```cuda
   // Hard to maintain and understand
   #if __CUDA_ARCH__ >= 500
       #if __CUDA_ARCH__ >= 600
           #if __CUDA_ARCH__ >= 700
               // Complex nesting
           #endif
       #endif
   #endif
   
   // Better: Cleaner structure
   #if __CUDA_ARCH__ >= 700
       // Best
   #elif __CUDA_ARCH__ >= 600
       // Better
   #else
       // Fallback
   #endif
   ```

---

## 8. Real-World Examples from NVIDIA Libraries

### 8.1 cuDNN Snippet Pattern

```cuda
// Tensor Core utilization based on architecture
namespace cudnn {

__global__ void ConvolutionForward(...) {
    #if __CUDA_ARCH__ >= 700
        // Volta Tensor Cores (float16, float32, float64)
        // Thread block organization: 128x128 or 256x256 tiles
        // Async copy for data staging (SM 80+)
    #elif __CUDA_ARCH__ >= 600
        // Pascal: Optimized memory access
        // Thread block: 64x64 or 128x128 tiles
        // Explicit __syncthreads() for synchronization
    #else
        // Maxwell fallback
        // Thread block: 32x32 or 64x64 tiles
        // Legacy shuffle operations
    #endif
}

}  // namespace cudnn
```

### 8.2 CUTLASS Tile Selection

```cuda
namespace cutlass {

template<int kArchVersion>
struct GemmConfig;

template<>
struct GemmConfig<500> {
    // Maxwell
    static constexpr int kBlockM = 64;
    static constexpr int kBlockN = 64;
    static constexpr int kBlockK = 16;
};

template<>
struct GemmConfig<700> {
    // Volta
    static constexpr int kBlockM = 128;
    static constexpr int kBlockN = 128;
    static constexpr int kBlockK = 32;
};

template<>
struct GemmConfig<800> {
    // Ampere
    static constexpr int kBlockM = 256;
    static constexpr int kBlockN = 256;
    static constexpr int kBlockK = 64;
};

template<typename Config>
__global__ void GemmKernel(...) {
    __shared__ float smem[Config::kBlockM * Config::kBlockK];
    // Implementation uses Config::kBlockM, etc.
}

}  // namespace cutlass
```

### 8.3 TensorRT Pattern

```cuda
namespace tensorrt {

class KernelSelector {
public:
    KernelFunction SelectKernel(const GPUProperties& props) {
        if (props.major == 8 && props.minor == 0) {
            // Ampere A100
            return kernel_sm80_a100;
        } else if (props.major == 8) {
            // Ampere A10/A30
            return kernel_sm80_generic;
        } else if (props.major == 7) {
            // Volta
            return kernel_sm70;
        } else {
            // Fallback
            return kernel_sm50_fallback;
        }
    }
};

}  // namespace tensorrt
```

---

## 9. Compilation Flags and Configuration

### 9.1 Key Compilation Flags

```bash
# Single target architecture
nvcc -arch=sm_80 kernel.cu -o kernel

# Multiple architectures in fat binary
nvcc -arch=sm_50 -arch=sm_60 -arch=sm_70 -arch=sm_80 kernel.cu -o kernel

# PTX JIT compilation
nvcc -arch=compute_80 kernel.cu -o kernel

# Generate both cubin and PTX
nvcc -gencode arch=compute_80,code=sm_80 -gencode arch=compute_80,code=compute_80 kernel.cu

# With CMake
cmake -DCMAKE_CUDA_ARCHITECTURES="60;70;80" ..
```

### 9.2 CMake Integration

```cmake
# Option 1: Set architectures globally
set(CMAKE_CUDA_ARCHITECTURES 60 70 80)

# Option 2: Per-target configuration
set_target_properties(my_kernel PROPERTIES 
    CUDA_ARCHITECTURES "60;70;80"
)

# Option 3: Architecture-specific sources
set_source_files_properties(kernel_sm80.cu PROPERTIES 
    CUDA_ARCHITECTURES "80"
)
```

---

## 10. Troubleshooting and Debugging

### 10.1 Verifying Architecture Selection

```cuda
#include <stdio.h>

__global__ void check_arch() {
    #if __CUDA_ARCH__ >= 800
        printf("Compiled for Ampere or newer (SM 8.0+)\n");
    #elif __CUDA_ARCH__ >= 700
        printf("Compiled for Volta or newer (SM 7.0+)\n");
    #elif __CUDA_ARCH__ >= 600
        printf("Compiled for Pascal or newer (SM 6.0+)\n");
    #else
        printf("Compiled for Maxwell or older (SM 5.x)\n");
    #endif
}
```

### 10.2 Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| Code not executing expected path | Compiled for different arch | Use `check_arch()` kernel to verify |
| Undefined function error | Function unavailable on target arch | Add conditional compilation gate |
| Binary too large | All architectures compiled in fat binary | Use separate compilation units |
| Unexpected performance | Code path not optimized for GPU | Profile and check `__CUDA_ARCH__` selection |

### 10.3 Debugging Strategies

```cuda
// Strategy 1: Add debug kernel
__global__ void debug_config() {
    if (threadIdx.x == 0) {
        printf("SM Version: %d.%d\n", __CUDA_ARCH__ / 100, (__CUDA_ARCH__ % 100) / 10);
    }
}

// Strategy 2: Create version-specific output
#if __CUDA_ARCH__ >= 800
    #define ARCH_STR "Ampere"
#elif __CUDA_ARCH__ >= 700
    #define ARCH_STR "Volta"
#else
    #define ARCH_STR "Maxwell/Pascal"
#endif

__global__ void report_version() {
    if (threadIdx.x == 0) {
        printf("Running kernel compiled for: %s\n", ARCH_STR);
    }
}

// Strategy 3: Compile-time assertions
#if __CUDA_ARCH__ < 600
    #error "This kernel requires Pascal (SM 6.0) or newer"
#endif
```

---

## 11. Summary and Recommendations

### 11.1 When to Use `__CUDA_ARCH__`

**Use conditionals when**:
- Different architectures have fundamentally different instruction sets
- Significant performance gains (>10%) from architecture-specific code
- Features unavailable on older architectures (e.g., `__syncwarp()`)
- Memory access patterns differ substantially

**Avoid when**:
- The difference is negligible (<5% performance impact)
- Adds complexity without clear benefit
- Can achieve same result with architecture-independent code

### 11.2 Recommended Architecture Support Matrix

For new projects:
- **Minimum**: SM 7.0 (Volta, 2017) - covers most modern data centers
- **Recommended**: SM 7.0, 8.0, 9.0 - spans 7 years of hardware
- **Optional**: SM 6.0-6.2 (Pascal) - if broader compatibility needed

For legacy projects:
- **Maintain**: SM 5.0 fallback as generic reference
- **Optimize**: SM 7.0+ with architecture-specific paths
- **Test**: All paths on representative hardware

---

## 12. References and Further Reading

### Official NVIDIA Documentation
- **CUDA C++ Programming Guide**: https://docs.nvidia.com/cuda/cuda-c-programming-guide/
- **CUDA Compiler Driver nvcc**: https://docs.nvidia.com/cuda/cuda-compiler-driver-nvcc/
- **Compute Capability Documentation**: https://docs.nvidia.com/cuda/cuda-c-programming-guide/#compute-capabilities

### Open-Source Libraries (See Code)
- **CUTLASS**: https://github.com/NVIDIA/cutlass - Linear algebra kernels with architecture-specific optimizations
- **cuDNN**: Source available in CUDA Toolkit - Deep learning primitives
- **Modern CUDA**: https://github.com/moderngl/moderngl - Real-world CUDA usage patterns

### Performance Analysis
- **NVIDIA Nsight Systems**: Profile actual execution paths
- **Nsight Compute**: Analyze kernel performance per architecture
- **PTX Assembly**: Use `nvcc -ptx` to verify generated code

---

## Conclusion

`__CUDA_ARCH__` is a powerful tool for architecture-specific optimization in CUDA. Key takeaways:

1. **Precision**: Use for compile-time decisions only; represents SM architecture version
2. **Scope**: Kernel code and device functions; unavailable in host code
3. **Patterns**: Simple thresholds, feature gates, and multi-level conditionals are most common
4. **Organization**: Header-only libraries and separate compilation units scale best
5. **Performance**: Compile-time overhead is zero; careful design prevents binary bloat
6. **Best practice**: Only use for significant optimizations; document carefully

When used properly, `__CUDA_ARCH__` enables optimal performance across multiple GPU generations while maintaining clean, understandable code.
