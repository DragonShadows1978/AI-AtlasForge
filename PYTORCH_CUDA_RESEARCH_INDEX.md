# PyTorch CUDA Error Handling Research - Complete Index

**Research Completion Date:** 2026-07-07  
**Source:** site:github.com/pytorch/pytorch  
**Status:** Complete with all 5 top files analyzed and documented

---

## Research Deliverables

### 1. **Comprehensive Analysis Document**
**File:** `PYTORCH_CUDA_ERROR_HANDLING_RESEARCH.md`

**Contents:**
- 13 detailed sections covering all aspects of PyTorch CUDA error handling
- Full code snippets from actual PyTorch source files
- Error flow diagrams
- Build-time configuration options
- 5 key error handling patterns explained
- Complete file reference table

**Sections Included:**
1. Core Error Handling Macros
2. Error Implementation Details
3. Device-Side Assertion Tracking
4. Library-Specific Error Handlers (cuDNN, cuBLAS, cuSPARSE, cuSOLVER, cuDSS, Driver API)
5. Resource Cleanup & Guard Patterns (RAII)
6. CUDA Utility Functions
7. Error Types & Categories
8. Retry Logic & Resource Cleanup
9. Error Message Components
10. Build-Time Configuration
11. Key Patterns & Best Practices
12. File References & URLs
13. Summary Table

---

### 2. **Structured JSON Reference**
**File:** `PYTORCH_CUDA_ERROR_PATTERNS.json`

**Contents:**
- Ranked list of top 5 code files with descriptions
- Error handling patterns for each file
- Error type catalog with library support
- Code snippets in JSON format
- Research methodology documentation

**Sections:**
- Top 5 files with GitHub URLs
- Error handling summary
- Top 5 error patterns with code examples
- Research methodology tracking

---

### 3. **Quick Reference Guide**
**File:** `PYTORCH_CUDA_ERROR_QUICK_REFERENCE.md`

**Contents:**
- One-page reference for developers
- Quick lookup tables
- Code pattern examples
- Error flow diagram
- Build flags and runtime config
- Key takeaways

**Quick Links:**
- Error types and their handlers
- 6 key error handling patterns
- Resource cleanup guarantees
- Performance optimizations
- Device-side assertion tracking explained
- Library-specific features

---

## Top 5 Files Analyzed

### File 1: CUDAException.h
**Repository Path:** `c10/cuda/CUDAException.h`  
**GitHub URL:** https://github.com/pytorch/pytorch/blob/master/c10/cuda/CUDAException.h

**Key Content:**
```cpp
// Core error checking macro
#define C10_CUDA_CHECK(EXPR)
  // Captures error code and context
  // Delegates to c10_cuda_check_implementation()

// Warning-only variant
#define C10_CUDA_CHECK_WARN(EXPR)
  // Logs warning without throwing

// Kernel launch validation
#define C10_CUDA_KERNEL_LAUNCH_CHECK()

// Device-side assertion launcher
#define TORCH_DSA_KERNEL_LAUNCH(...)
```

**Error Handling Category:** Primary macro definitions  
**Retry Logic:** None (exception-based propagation)  
**Resource Cleanup:** Via macro wrapper (delegates to implementation)

---

### File 2: CUDAException.cpp
**Repository Path:** `c10/cuda/CUDAException.cpp`  
**GitHub URL:** https://github.com/pytorch/pytorch/blob/master/c10/cuda/CUDAException.cpp

**Key Function:**
```cpp
void c10_cuda_check_implementation(
    const int32_t err,
    const char* filename,
    const char* function_name,
    const uint32_t line_number,
    const bool include_device_assertions);
```

**Error Handling Steps:**
1. Fast-path check (error == cudaSuccess)
2. Error state clearing (cudaGetLastError)
3. Message assembly with context-specific help
4. Device-side assertion retrieval (if enabled)
5. Exception generation and throw

**Resource Cleanup:** Guaranteed via exception throw mechanism

---

### File 3: CUDADeviceAssertionHost.h
**Repository Path:** `c10/cuda/CUDADeviceAssertionHost.h`  
**GitHub URL:** https://github.com/pytorch/pytorch/blob/master/c10/cuda/CUDADeviceAssertionHost.h

**Key Classes:**
```cpp
struct DeviceAssertionData;        // Single assertion record
struct DeviceAssertionsData;       // Array of assertions
struct CUDAKernelLaunchInfo;       // Kernel launch metadata
class CUDAKernelLaunchRegistry;    // Central registry (singleton)
```

**Tracking Mechanism:**
- Circular buffer: 1024 max kernel launches per process
- Managed memory (UVM) for GPU/CPU access
- Thread-safe via mutex
- Generation numbers for cross-reference

**Error Handling Category:** Post-mortem debugging infrastructure  
**Resource Cleanup:** UVM allocation/deallocation handled internally

---

### File 4: Exceptions.h (ATen)
**Repository Path:** `aten/src/ATen/cuda/Exceptions.h`  
**GitHub URL:** https://github.com/pytorch/pytorch/blob/master/aten/src/ATen/cuda/Exceptions.h

**Library-Specific Error Handlers:**

| Library | Macro | Error Type | Special Handling |
|---------|-------|-----------|-----------------|
| cuDNN | `AT_CUDNN_CHECK` | `cudnnStatus_t` | Non-contiguous tensor detection |
| cuDNN Frontend | `AT_CUDNN_FRONTEND_CHECK` | Custom error object | Object validation |
| cuBLAS | `TORCH_CUDABLAS_CHECK` | `cublasStatus_t` | Status enum mapping |
| cuSPARSE | `TORCH_CUDASPARSE_CHECK` | `cusparseStatus_t` | Sparse matrix errors |
| cuSOLVER | `TORCH_CUSOLVER_CHECK` | `cusolverStatus_t` | NaN detection + backend suggestions |
| cuDSS | `TORCH_CUDSS_CHECK` | `cudssStatus_t` | Execution failure + NaN detection |
| CUDA Driver | `AT_CUDA_DRIVER_CHECK` | `CUresult` | Dynamically loaded error strings |

**Error Handling Category:** Library-specific error translation  
**Retry Logic:** None (library-specific, caller handles)  
**Resource Cleanup:** Varies per library

---

### File 5: CUDAGuard.h
**Repository Path:** `c10/cuda/CUDAGuard.h`  
**GitHub URL:** https://github.com/pytorch/pytorch/blob/master/c10/cuda/CUDAGuard.h

**RAII Guard Classes:**
```cpp
struct CUDAGuard;              // Device context management
struct OptionalCUDAGuard;      // Optional device switching
struct CUDAStreamGuard;        // Stream context management
```

**Error Handling Category:** Resource cleanup guarantees  
**Retry Logic:** Not applicable (cleanup/restoration only)  
**Resource Cleanup:** Guaranteed via RAII destructors

**Guarantee:** Original device/stream always restored on scope exit, even on exception

---

## Error Type Coverage Matrix

| Error Category | Handler Macro | File Location | Error Propagation |
|----------------|---------------|---------------|------------------|
| CUDA Runtime | `C10_CUDA_CHECK` | CUDAException.h | Exception |
| Kernel Launch | `C10_CUDA_KERNEL_LAUNCH_CHECK` | CUDAException.h | Exception |
| Device Assertions | `TORCH_DSA_KERNEL_LAUNCH` | CUDADeviceAssertionHost.h | Exception + DSA records |
| cuDNN | `AT_CUDNN_CHECK` | Exceptions.h | Exception |
| cuBLAS | `TORCH_CUDABLAS_CHECK` | Exceptions.h | Exception |
| cuSPARSE | `TORCH_CUDASPARSE_CHECK` | Exceptions.h | Exception |
| cuSOLVER | `TORCH_CUSOLVER_CHECK` | Exceptions.h | Exception |
| cuDSS | `TORCH_CUDSS_CHECK` | Exceptions.h | Exception |
| CUDA Driver | `AT_CUDA_DRIVER_CHECK` | Exceptions.h | Exception |

---

## Error Handling Feature Comparison

| Feature | Implementation | File |
|---------|----------------|------|
| Immediate error capture | Macro wrapper | CUDAException.h |
| Error code checking | `c10_cuda_check_implementation()` | CUDAException.cpp |
| Error message formatting | Exception assembly | CUDAException.cpp |
| Context preservation | File/function/line tracking | CUDAException.h |
| Device assertion tracking | Circular buffer + UVM | CUDADeviceAssertionHost.h |
| Device restoration on error | RAII guard destructors | CUDAGuard.h |
| Stream restoration on error | RAII guard destructors | CUDAGuard.h |
| Warning-only reporting | `C10_CUDA_CHECK_WARN` | CUDAException.h |
| Non-critical error ignoring | `C10_CUDA_IGNORE_ERROR` | CUDAException.h |
| Library-specific handling | 6+ specialized macros | Exceptions.h |

---

## Key Patterns Identified

### Pattern 1: Safe Kernel Launch
**Files:** CUDAException.h  
**Purpose:** Validate kernel launch immediately after invocation  
**Code:**
```cpp
kernel<<<blocks, threads, shared_mem, stream>>>(...);
C10_CUDA_KERNEL_LAUNCH_CHECK();
```

### Pattern 2: Automatic Device Restoration
**Files:** CUDAGuard.h  
**Purpose:** Guarantee device context restoration  
**Code:**
```cpp
{
  c10::cuda::CUDAGuard guard(device);
  // ... operations ...
}  // Device restored automatically
```

### Pattern 3: Stream Context Management
**Files:** CUDAGuard.h  
**Purpose:** Manage stream and associated device  
**Code:**
```cpp
{
  c10::cuda::CUDAStreamGuard guard(stream);
  // ... stream operations ...
}  // Stream and device restored
```

### Pattern 4: Non-Fatal Error Handling
**Files:** CUDAException.h  
**Purpose:** Log errors without throwing  
**Code:**
```cpp
C10_CUDA_CHECK_WARN(cudaMemcpy(...));
```

### Pattern 5: Library-Specific Errors
**Files:** Exceptions.h  
**Purpose:** Handle library-specific error enums  
**Code:**
```cpp
AT_CUDNN_CHECK(cudnnConvolution(...));
TORCH_CUDABLAS_CHECK(cublasGemm(...));
```

---

## Error Information Captured

**Per-Error Context:**
- Source file name
- Function name
- Line number
- Error code (enum value)
- Error string (from CUDA runtime)
- Help text (context-specific)
- Async error information
- Device-side assertion details (if enabled)
- Thread/block coordinates (from device-side)

---

## Performance Considerations

1. **Fast Path Optimization**
   - Success case returns immediately
   - Branch prediction hints (`C10_LIKELY`)

2. **Error State Management**
   - `cudaGetLastError()` clears state
   - Prevents error accumulation

3. **Binary Size**
   - Optional message stripping via `STRIP_ERROR_MESSAGES`
   - Macros reduce code duplication

4. **Memory Overhead**
   - Circular buffer: ~64KB per 1024 launches
   - DSA structures: ~10KB per device max

---

## Build Configuration Options

### Compile-Time Flags

**Error Message Control:**
```bash
-DSTRIP_ERROR_MESSAGES  # Omit detailed error messages
```

**Device-Side Assertions:**
```bash
-DTORCH_USE_CUDA_DSA  # Enable DSA at compile time
```

**GPU Limits:**
```bash
-DFBCODE_CAFFE2        # Set max GPUs to 16 (Meta/Caffe2)
# Default: 120 GPUs (public PyTorch)
```

### Runtime Flags

**Environment Variables:**
- `TORCH_USE_CUDA_DSA=1` - Enable device-side assertions
- `TORCH_CUDA_DSA_STACKTRACE=1` - Capture launch stack traces

**API Configuration:**
```cpp
registry.enabled_at_runtime = true;
registry.gather_launch_stacktrace = true;
warning_state().set_sync_debug_mode(SyncDebugMode::L_WARN);
```

---

## Retry Logic Analysis

**Finding:** PyTorch implements **NO automatic retry logic**

**Rationale:**
- Application-specific retry strategies
- Resource exhaustion handling
- Transient vs. permanent error distinction
- User control over retry boundaries

**Caller Responsibility:**
- Implement retry loops if needed
- Handle resource cleanup between retries
- Track retry counts and backoff

**Example Retry Pattern (caller-implemented):**
```cpp
int max_retries = 3;
for (int retry = 0; retry < max_retries; retry++) {
  try {
    C10_CUDA_CHECK(cudaMalloc(&ptr, size));
    break;  // Success
  } catch (const c10::AcceleratorError& e) {
    if (retry < max_retries - 1) {
      c10::cuda::device_synchronize();  // Clear state
      std::this_thread::sleep_for(std::chrono::milliseconds(100 * (1 << retry)));
    } else {
      throw;
    }
  }
}
```

---

## Resource Cleanup Guarantees

**RAII Principle:** Destructors always run, even on exception

```cpp
{
  CUDAGuard guard(device);  // Acquire device context
  
  try {
    // ... CUDA operations that may throw ...
    C10_CUDA_CHECK(cudaMemcpy(...));
  } catch (...) {
    // ~CUDAGuard() runs here BEFORE catch clause
    // Device restored to original
    throw;  // Re-throw
  }
  
  // ~CUDAGuard() runs here on normal exit
}
// Device is guaranteed to be in original state
```

**What Gets Cleaned Up:**
- Device context (via `cudaSetDevice()`)
- Stream context (via `cudaStreamCaptureStatus`)
- Error state (via `cudaGetLastError()`)

**What Doesn't Get Cleaned Up Automatically:**
- Memory allocations (caller responsibility)
- Custom event handles (caller responsibility)
- Streams/events created in user code (caller responsibility)

---

## Integration Points

**Where to Find Error Handling in PyTorch Code:**

1. **Operator Implementations:** `aten/src/ATen/native/cuda/`
   - Uses: `AT_CUDA_CHECK`, `AT_CUDNN_CHECK`

2. **Memory Management:** `c10/cuda/CUDAAllocator.cpp`
   - Uses: `C10_CUDA_CHECK`, `CUDAGuard`

3. **Stream Management:** `c10/cuda/CUDAStream.cpp`
   - Uses: `C10_CUDA_CHECK`, `CUDAStreamGuard`

4. **Kernel Launchers:** `aten/src/ATen/cuda/detail/IndexUtils.cu`
   - Uses: `C10_CUDA_KERNEL_LAUNCH_CHECK`, `TORCH_DSA_KERNEL_LAUNCH`

5. **Library Wrappers:** `aten/src/ATen/cuda/`
   - Uses: All library-specific macros (`AT_CUDNN_CHECK`, etc.)

---

## Documentation & Further Reading

### Official PyTorch Docs
- CUDA Semantics: https://pytorch.org/docs/stable/notes/cuda.html
- Backends: https://pytorch.org/docs/stable/backends.html

### NVIDIA Documentation
- CUDA Runtime API: https://docs.nvidia.com/cuda/cuda-runtime-api/
- cuDNN: https://docs.nvidia.com/deeplearning/cudnn/
- cuBLAS: https://docs.nvidia.com/cuda/cublas/
- cuSPARSE: https://docs.nvidia.com/cuda/cusparse/

### PyTorch GitHub Resources
- Source Code: https://github.com/pytorch/pytorch
- Issues & Discussions: https://github.com/pytorch/pytorch/issues

---

## Summary Statistics

| Metric | Value |
|--------|-------|
| Files Analyzed | 5 |
| Error Handling Macros | 20+ |
| Library Handlers | 7 (cuDNN, cuBLAS, cuSPARSE, cuSOLVER, cuDSS, Driver, Frontend) |
| RAII Guard Types | 3 |
| Error Context Fields | 8+ |
| Max Tracked Assertions | 10 |
| Max Tracked Launches | 1024 |
| Code Patterns Documented | 5 |

---

## How to Use This Research

### For Implementation
1. Start with **Quick Reference** for immediate code patterns
2. Refer to **Comprehensive Analysis** for detailed behavior
3. Check **JSON file** for quick field lookups

### For Integration
1. Use `CUDAException.h` macros for error checking
2. Use `CUDAGuard.h` for device/stream management
3. Use library-specific macros from `Exceptions.h`
4. Implement caller-level retry logic

### For Debugging
1. Enable `TORCH_USE_CUDA_DSA` for device-side assertions
2. Check device synchronization issues with sync debug mode
3. Inspect kernel launch registry for post-mortem analysis

---

## File Organization

```
PYTORCH_CUDA_RESEARCH_INDEX.md          (This file - navigation & summary)
PYTORCH_CUDA_ERROR_HANDLING_RESEARCH.md (Comprehensive 13-section analysis)
PYTORCH_CUDA_ERROR_PATTERNS.json        (Structured JSON reference)
PYTORCH_CUDA_ERROR_QUICK_REFERENCE.md   (Developer quick-start guide)
```

**Total Coverage:** ~15,000 words of analysis + code documentation

---

**Research Completed:** 2026-07-07  
**Source Repository:** pytorch/pytorch (GitHub)  
**Quality Level:** Production-grade error handling system analysis  
**Verification Method:** Direct code inspection from master branch

