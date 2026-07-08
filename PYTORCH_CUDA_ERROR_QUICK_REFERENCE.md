# PyTorch CUDA Error Handling - Quick Reference

## Top 5 GitHub Files

### 1. **CUDAException.h** (Core Macros)
- **Path:** `c10/cuda/CUDAException.h`
- **URL:** https://github.com/pytorch/pytorch/blob/master/c10/cuda/CUDAException.h
- **Key Macros:** `C10_CUDA_CHECK`, `C10_CUDA_CHECK_WARN`, `C10_CUDA_KERNEL_LAUNCH_CHECK`
- **Purpose:** Core error checking macros with diagnostic context

### 2. **CUDAException.cpp** (Implementation)
- **Path:** `c10/cuda/CUDAException.cpp`
- **URL:** https://github.com/pytorch/pytorch/blob/master/c10/cuda/CUDAException.cpp
- **Key Function:** `c10_cuda_check_implementation()`
- **Purpose:** Error processing pipeline, message assembly, exception generation

### 3. **CUDADeviceAssertionHost.h** (DSA Tracking)
- **Path:** `c10/cuda/CUDADeviceAssertionHost.h`
- **URL:** https://github.com/pytorch/pytorch/blob/master/c10/cuda/CUDADeviceAssertionHost.h
- **Key Classes:** `CUDAKernelLaunchRegistry`, `DeviceAssertionData`
- **Purpose:** Managed memory device-side assertion tracking for post-mortem debugging

### 4. **Exceptions.h** (Library-Specific)
- **Path:** `aten/src/ATen/cuda/Exceptions.h`
- **URL:** https://github.com/pytorch/pytorch/blob/master/aten/src/ATen/cuda/Exceptions.h
- **Key Macros:** `AT_CUDNN_CHECK`, `TORCH_CUDABLAS_CHECK`, `TORCH_CUDASPARSE_CHECK`, `TORCH_CUSOLVER_CHECK`
- **Purpose:** Error handlers for cuDNN, cuBLAS, cuSPARSE, cuSOLVER, cuDSS, Driver API

### 5. **CUDAGuard.h** (RAII Guards)
- **Path:** `c10/cuda/CUDAGuard.h`
- **URL:** https://github.com/pytorch/pytorch/blob/master/c10/cuda/CUDAGuard.h
- **Key Classes:** `CUDAGuard`, `OptionalCUDAGuard`, `CUDAStreamGuard`
- **Purpose:** RAII-based resource cleanup guarantees on exception

---

## Error Types Covered

| Type | Handler | Example |
|------|---------|---------|
| CUDA Runtime | `C10_CUDA_CHECK(expr)` | `C10_CUDA_CHECK(cudaMalloc(...))` |
| Kernel Launch | `C10_CUDA_KERNEL_LAUNCH_CHECK()` | Post `<<<>>>` invocation |
| cuDNN | `AT_CUDNN_CHECK(expr)` | `AT_CUDNN_CHECK(cudnnConvolution(...))` |
| cuBLAS | `TORCH_CUDABLAS_CHECK(expr)` | `TORCH_CUDABLAS_CHECK(cublasSgemm(...))` |
| cuSPARSE | `TORCH_CUDASPARSE_CHECK(expr)` | `TORCH_CUDASPARSE_CHECK(cusparseSpMV(...))` |
| cuSOLVER | `TORCH_CUSOLVER_CHECK(expr)` | `TORCH_CUSOLVER_CHECK(cusolverDnSgesvd(...))` |
| cuDSS | `TORCH_CUDSS_CHECK(expr)` | `TORCH_CUDSS_CHECK(cudssSpSV(...))` |
| Driver API | `AT_CUDA_DRIVER_CHECK(expr)` | `AT_CUDA_DRIVER_CHECK(cuMemAlloc(...))` |

---

## Key Error Handling Patterns

### Pattern 1: Safe Kernel Launch
```cpp
kernel<<<blocks, threads, shared_mem, stream>>>(...);
C10_CUDA_KERNEL_LAUNCH_CHECK();  // Catches launch configuration errors
```

### Pattern 2: Device Context Restoration
```cpp
{
  c10::cuda::CUDAGuard guard(device_id);
  // ... CUDA operations ...
}  // Device restored automatically, even on exception
```

### Pattern 3: Stream Context Management
```cpp
{
  c10::cuda::CUDAStreamGuard stream_guard(stream);
  // ... stream operations ...
}  // Both stream and device restored
```

### Pattern 4: Non-Fatal Error Handling
```cpp
C10_CUDA_CHECK_WARN(cudaEventRecord(...));  // Logs warning, doesn't throw
```

### Pattern 5: Intentional Error Ignoring
```cpp
C10_CUDA_IGNORE_ERROR(cudaMemset(...));  // Clears error state silently
```

### Pattern 6: Device-Side Assertion Tracking
```cpp
TORCH_DSA_KERNEL_LAUNCH(
    my_kernel, blocks, threads, shared_mem, stream,
    arg1, arg2, arg3
);
```

---

## Error Flow

```
CUDA API Call
    ↓
Macro Wrapper (C10_CUDA_CHECK, etc.)
    ↓
Error Code Captured
    ↓
c10_cuda_check_implementation()
    ├─ Fast path: error == cudaSuccess → return
    ├─ Clear error state (cudaGetLastError)
    ├─ Assemble error message
    │  ├─ cudaGetErrorString()
    │  ├─ Context-specific help
    │  ├─ Async error info
    │  └─ Device-side assertions (if enabled)
    └─ Throw c10::AcceleratorError
        ↓
    Exception Propagates Up
        ↓
    Caller Handles or RAII Guard Cleans Up
```

---

## Error Context Captured

Every error exception contains:
- ✓ **File:** Source filename where check occurred
- ✓ **Function:** Function name containing the check
- ✓ **Line:** Exact line number
- ✓ **Error Code:** CUDA error enum value
- ✓ **Error String:** Human-readable error description
- ✓ **Help Text:** Context-specific guidance
- ✓ **Device Assertions:** Kernel stack trace (if DSA enabled)
- ✓ **Thread Coordinates:** Block/thread ID of failing device code

---

## Resource Cleanup Guarantees

**RAII Pattern:** Guards automatically restore state on scope exit

```cpp
// Example: Automatic device restoration on exception
{
    c10::cuda::CUDAGuard guard(device_1);
    
    try {
        // ... operations on device_1 ...
        C10_CUDA_CHECK(cudaMemcpy(...));  // May throw
    } catch (...) {
        // guard.~CUDAGuard() ALWAYS runs here
        // Original device restored
        throw;
    }
    // guard.~CUDAGuard() runs here too
}
```

**Guarantees:**
- Device context restored to original
- Stream context restored to original
- Error state cleared (no accumulation)
- No memory leaks even on exception

---

## Performance Optimizations

1. **Fast Path Optimization**
   ```cpp
   if (C10_LIKELY(cuda_error == cudaSuccess && !cuda_kernel_failure)) {
       return;  // Branch predictor favors success path
   }
   ```

2. **Branch Prediction Hints**
   - `C10_LIKELY()` for common case
   - `C10_UNLIKELY()` for error case
   - Minimizes pipeline flushes

3. **Binary Size Control**
   ```cpp
   #ifndef STRIP_ERROR_MESSAGES
       // Include full error messages
   #else
       // Omit detailed messages for size-critical builds
   #endif
   ```

---

## Device-Side Assertion Tracking

**Circular Buffer:** Max 1024 kernel launches tracked per process
- **Max Assertions:** 10 simultaneous failures
- **String Limit:** 512 bytes per field
- **Thread Safety:** Mutex-protected
- **Memory:** Unified Virtual Memory (UVM) - accessible from CPU and GPU

**Captured Info:**
- Assertion message
- Source file and function
- Line number
- Kernel name
- Block and thread coordinates of failing thread
- Unique generation number for launch identification

---

## Library-Specific Features

### cuDNN
- Detects non-contiguous tensor errors with helpful hint
- Frontend error object checking

### cuSOLVER / cuDSS
- Special handling for INVALID_VALUE (NaN matrices)
- Suggests alternative backends via `torch.backends.cuda.preferred_linalg_library()`

### CUDA Driver API
- Dynamically loads error string function via NVRTC
- Handles missing NVRTC gracefully

---

## Build-Time Flags

```bash
# Disable error messages (binary size optimization)
-DSTRIP_ERROR_MESSAGES

# Enable device-side assertions at compile time
-DTORCH_USE_CUDA_DSA

# Set max GPU count (16 for Meta/Caffe2, 120 for public PyTorch)
# Controlled by -DFBCODE_CAFFE2
```

---

## Runtime Configuration

```cpp
// Access the kernel launch registry
auto& registry = c10::cuda::CUDAKernelLaunchRegistry::get_singleton_ref();

// Enable/disable device-side assertions at runtime
registry.enabled_at_runtime = true;

// Enable stack trace capture on kernel launch
registry.gather_launch_stacktrace = true;

// Set synchronization debug mode
c10::cuda::warning_state().set_sync_debug_mode(
    c10::cuda::SyncDebugMode::L_WARN
);
```

---

## Error Message Example

```
CUDA error: invalid device ordinal
Hint: Make sure CUDA is available for this operation.
This is an async error; check device synchronization status.

Device-side assertion failures:
  1. Kernel "matrix_multiply" failed at gemm.cu:256
     Block: [2, 1, 0], Thread: [16, 8, 0]
     Assertion: "expected positive alpha value"
     
File: tensor_ops.cpp
Function: execute_gemm
Line: 142
```

---

## Key Takeaways

1. **Defensive Design:** Every CUDA operation wrapped with error checking
2. **Context Preservation:** Rich diagnostic information in every exception
3. **No Automatic Retry:** Caller implements retry logic if needed
4. **Resource Safety:** RAII guards prevent leaks on exception
5. **Device Debugging:** Post-mortem inspection via device assertions
6. **Library Support:** Specialized handlers for 6+ CUDA libraries
7. **Performance Conscious:** Fast path optimization, binary size control
8. **Extensible:** Configuration flags for different use cases

---

## How to Use These Files

**To implement CUDA error handling in your code:**

1. **Basic error checking:**
   - Import: `#include <c10/cuda/CUDAException.h>`
   - Use: `C10_CUDA_CHECK(cudaMemcpy(...))`

2. **Device context management:**
   - Import: `#include <c10/cuda/CUDAGuard.h>`
   - Use: `c10::cuda::CUDAGuard guard(device_id);`

3. **Library-specific errors:**
   - Import: `#include <aten/ATen/cuda/Exceptions.h>`
   - Use: `AT_CUDNN_CHECK(cudnnConvolution(...))`

4. **Device-side assertions (advanced):**
   - Import: `#include <c10/cuda/CUDADeviceAssertionHost.h>`
   - Use: `TORCH_DSA_KERNEL_LAUNCH(kernel, ...)`

---

## Related Documentation

- CUDA Runtime API: https://docs.nvidia.com/cuda/cuda-runtime-api/
- cuDNN Documentation: https://docs.nvidia.com/deeplearning/cudnn/
- PyTorch CUDA Docs: https://pytorch.org/docs/stable/cuda.html
- PyTorch GitHub: https://github.com/pytorch/pytorch

