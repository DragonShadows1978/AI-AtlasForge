# PyTorch CUDA Error Handling: Comprehensive Code Analysis

## Executive Summary

PyTorch implements a multi-layered CUDA error handling system across the c10 (core) and ATen (tensor) libraries. The system provides:
- Macro-based error checking with file/line/function context
- Exception-based error propagation
- Device-side assertion tracking (DSA)
- Specialized handlers for cuDNN, cuBLAS, cuSPARSE, cuSOLVER, and cuDSS libraries
- Resource cleanup guarantees via guard patterns (RAII)

---

## 1. Core Error Handling Macros

### Location: `c10/cuda/CUDAException.h`
**URL:** https://github.com/pytorch/pytorch/blob/master/c10/cuda/CUDAException.h

### Primary Error Check Macro: `C10_CUDA_CHECK`

```cpp
#define C10_CUDA_CHECK(EXPR)                                        \
  do {                                                              \
    const cudaError_t __err = EXPR;                                 \
    c10::cuda::c10_cuda_check_implementation(                       \
        static_cast<int32_t>(__err),                                \
        __FILE__,                                                   \
        __func__,                                                   \
        static_cast<uint32_t>(__LINE__),                            \
        true);                                                      \
  } while (0)
```

**Key Features:**
- Wraps CUDA API calls and captures error code
- Passes diagnostic context: filename, function name, line number
- Delegates to `c10_cuda_check_implementation()` for error processing
- Includes device-side assertion checking (`true` parameter)
- HIP compatibility alias: `C10_HIP_CHECK`

---

### Warning-Only Macro: `C10_CUDA_CHECK_WARN`

```cpp
#define C10_CUDA_CHECK_WARN(EXPR)                              \
  do {                                                         \
    const cudaError_t __err = EXPR;                            \
    if (C10_UNLIKELY(__err != cudaSuccess)) {                  \
      [[maybe_unused]] auto error_unused = cudaGetLastError(); \
      TORCH_WARN("CUDA warning: ", cudaGetErrorString(__err)); \
    }                                                          \
  } while (0)
```

**Error Handling Pattern:**
- Non-fatal error reporting
- Clears error state with `cudaGetLastError()`
- Logs warning without throwing exception
- Uses `C10_UNLIKELY()` branch prediction hint

**Use Cases:**
- Optional CUDA operations
- Cleanup operations that shouldn't fail the application
- Graceful degradation scenarios

---

### Error Handling Modifiers

#### 1. Error Already Handled
```cpp
#define C10_CUDA_ERROR_HANDLED(EXPR) EXPR
```
Marks code where error is already handled elsewhere, suppresses default checking.

#### 2. Intentional Error Ignoring
```cpp
#define C10_CUDA_IGNORE_ERROR(EXPR)                                   \
  do {                                                                \
    const cudaError_t __err = EXPR;                                   \
    if (C10_UNLIKELY(__err != cudaSuccess)) {                         \
      [[maybe_unused]] cudaError_t error_unused = cudaGetLastError(); \
    }                                                                 \
  } while (0)
```
Explicitly clears error state without logging or exceptions.

#### 3. Manual Error Clearing
```cpp
#define C10_CUDA_CLEAR_ERROR()                                      \
  do {                                                              \
    [[maybe_unused]] cudaError_t error_unused = cudaGetLastError(); \
  } while (0)
```
Forces CUDA error state clearance (prevents error accumulation).

---

### Kernel Launch Validation: `C10_CUDA_KERNEL_LAUNCH_CHECK`

```cpp
#define C10_CUDA_KERNEL_LAUNCH_CHECK() C10_CUDA_CHECK(cudaGetLastError())
```

**Purpose:**
- Validates asynchronous kernel launch success
- Must be called immediately after `<<<>>>` kernel invocation
- Catches launch configuration errors (grid/block sizes, shared memory)

**Usage Pattern:**
```cpp
kernel<<<blocks, threads, shared_mem, stream>>>(...);
C10_CUDA_KERNEL_LAUNCH_CHECK();
```

---

### Device-Side Assertion Kernel Launch: `TORCH_DSA_KERNEL_LAUNCH`

```cpp
#define TORCH_DSA_KERNEL_LAUNCH(kernel, blocks, threads, shared_mem, stream, ...) \
  do {                                                                                \
    auto& launch_registry = c10::cuda::CUDAKernelLaunchRegistry::get_singleton_ref(); \
    kernel<<<blocks, threads, shared_mem, stream>>>(                                  \
        __VA_ARGS__,                                                                  \
        launch_registry.get_uvm_assertions_ptr_for_current_device(),                  \
        launch_registry.insert(__FILE__, __FUNCTION__, __LINE__, #kernel, stream.id())); \
    C10_CUDA_KERNEL_LAUNCH_CHECK();                                                   \
  } while (0)
```

**Features:**
- Injects device-side assertion tracking
- Registers kernel launch with metadata (file, function, line)
- Returns generation number for cross-reference on assertion failures
- Enables post-mortem analysis of device-side errors

---

## 2. Error Implementation

### Location: `c10/cuda/CUDAException.cpp`
**URL:** https://github.com/pytorch/pytorch/blob/master/c10/cuda/CUDAException.cpp

```cpp
void c10_cuda_check_implementation(
    const int32_t err,
    const char* filename,
    const char* function_name,
    const uint32_t line_number,
    const bool include_device_assertions) {
  
  const auto cuda_error = static_cast<cudaError_t>(err);
  const auto cuda_kernel_failure = include_device_assertions
      ? c10::cuda::CUDAKernelLaunchRegistry::get_singleton_ref().has_failed()
      : false;

  if (C10_LIKELY(cuda_error == cudaSuccess && !cuda_kernel_failure)) {
    return;  // Fast path - no error
  }

  [[maybe_unused]] auto error_unused = cudaGetLastError();

  std::string check_message;
#ifndef STRIP_ERROR_MESSAGES
  check_message.append("CUDA error: ");
  const char* error_string = cudaGetErrorString(cuda_error);
  check_message.append(error_string);
  check_message.append(c10::cuda::get_cuda_error_help(cuda_error));
  check_message.append(c10::cuda::get_cuda_async_error_suffix(cuda_error));
  check_message.push_back('\n');
  if (include_device_assertions) {
    check_message.append(c10_retrieve_device_side_assertion_info());
  } else {
    check_message.append(
        "Device-side assertions were explicitly omitted for this error check; "
        "the error probably arose while initializing the DSA handlers.");
  }
#endif
  throw c10::AcceleratorError(
      {.function = function_name, .file = filename, .line = line_number},
      err,
      std::move(check_message));
}
```

### Error Processing Flow

1. **Fast Path Check:** Quick early return if no error
2. **Error State Clearing:** `cudaGetLastError()` prevents error accumulation
3. **Message Assembly:**
   - CUDA error string from `cudaGetErrorString()`
   - Context-specific help text via `get_cuda_error_help()`
   - Async error information via `get_cuda_async_error_suffix()`
   - Device-side assertion details if enabled
4. **Exception Throw:** `c10::AcceleratorError` with rich context
5. **Optional Stripping:** `STRIP_ERROR_MESSAGES` build flag removes message overhead

### Error Context Captured

- **File:** Source file where error check occurred
- **Function:** Function name containing the check
- **Line:** Exact line number for debugging
- **Error Code:** CUDA error enum value
- **Device Assertions:** Kernel launch stack if DSA enabled

---

## 3. Device-Side Assertion Tracking

### Location: `c10/cuda/CUDADeviceAssertionHost.h`
**URL:** https://github.com/pytorch/pytorch/blob/master/c10/cuda/CUDADeviceAssertionHost.h

### Device-Side Assertion Data Structure

```cpp
struct DeviceAssertionData {
  char assertion_msg[C10_CUDA_DSA_MAX_STR_LEN]{};      // Assertion message
  char filename[C10_CUDA_DSA_MAX_STR_LEN]{};           // File location
  char function_name[C10_CUDA_DSA_MAX_STR_LEN]{};      // Function context
  int line_number{};                                    // Line number
  uint32_t caller{};                                    // Kernel launch ID
  int32_t block_id[3]{};                               // CUDA block coordinates
  int32_t thread_id[3]{};                              // CUDA thread coordinates
};
```

**Constraints:**
- Max 10 simultaneous assertion failures tracked (`C10_CUDA_DSA_ASSERTION_COUNT = 10`)
- 512-byte string limit per field (`C10_CUDA_DSA_MAX_STR_LEN = 512`)
- Managed memory (accessible from both CPU and GPU)

### Kernel Launch Registry

```cpp
class CUDAKernelLaunchRegistry {
  // Circular buffer: max 1024 kernel launches tracked per device
  std::vector<CUDAKernelLaunchInfo> kernel_launches;
  
  // Device-side assertion buffers (one per CUDA device)
  std::vector<std::unique_ptr<DeviceAssertionsData, ...>> uvm_assertions;
  
  // Thread-safe access via mutex
  mutable std::mutex read_write_mutex;
  mutable std::mutex gpu_alloc_mutex;
  
public:
  // Insert kernel launch and get generation number
  uint32_t insert(const char* launch_filename,
                  const char* launch_function,
                  uint32_t launch_linenum,
                  const char* kernel_name,
                  int32_t stream_id);
  
  // Get device's assertion failure buffer
  DeviceAssertionsData* get_uvm_assertions_ptr_for_current_device();
  
  // Check if any assertions failed
  bool has_failed() const;
  
  // Retrieve all data safely
  pair<vector<DeviceAssertionsData>, vector<CUDAKernelLaunchInfo>>
  snapshot() const;
};
```

### Features

- **Generation Numbers:** Each kernel launch gets unique ID for cross-reference
- **Circular Buffer:** 1024 most recent launches tracked (prevents unbounded memory)
- **Race-Free Recording:** Managed memory + mutex coordination
- **Stack Trace Capture:** Optional launch-site backtrace via `gather_launch_stacktrace` flag
- **Runtime Enablement:** `enabled_at_runtime` allows dynamic control
- **Compile-Time Flag:** `TORCH_USE_CUDA_DSA` build-time control

---

## 4. Library-Specific Error Handlers

### Location: `aten/src/ATen/cuda/Exceptions.h`
**URL:** https://github.com/pytorch/pytorch/blob/master/aten/src/ATen/cuda/Exceptions.h

### 4.1 cuDNN Frontend

```cpp
#define AT_CUDNN_FRONTEND_CHECK(EXPR, ...)                        \
  do {                                                            \
    auto error_object = EXPR;                                     \
    if (!error_object.is_good()) {                                \
      TORCH_CHECK_WITH(CuDNNError, false,                         \
            "cuDNN Frontend error: ", error_object.get_message()); \
    }                                                             \
  } while (0)
```

### 4.2 cuDNN Legacy API

```cpp
#define AT_CUDNN_CHECK(EXPR, ...)                                        \
  do {                                                                   \
    cudnnStatus_t status = EXPR;                                         \
    if (status != CUDNN_STATUS_SUCCESS) {                                \
      if (status == CUDNN_STATUS_NOT_SUPPORTED) {                        \
        TORCH_CHECK_WITH(CuDNNError, false,                              \
            "cuDNN error: ", cudnnGetErrorString(status),                \
            ". This error may appear if you passed in a non-contiguous input."); \
      } else {                                                           \
        TORCH_CHECK_WITH(CuDNNError, false,                              \
            "cuDNN error: ", cudnnGetErrorString(status));               \
      }                                                                  \
    }                                                                    \
  } while (0)
```

**Special Handling:** Non-contiguous tensor detection and guidance.

### 4.3 cuBLAS

```cpp
#define TORCH_CUDABLAS_CHECK(EXPR)                              \
  do {                                                          \
    cublasStatus_t __err = EXPR;                                \
    TORCH_CHECK(__err == CUBLAS_STATUS_SUCCESS,                 \
                "CUDA error: ",                                 \
                at::cuda::blas::_cublasGetErrorEnum(__err),     \
                " when calling `" #EXPR "`");                   \
  } while (0)
```

### 4.4 cuSPARSE

```cpp
#define TORCH_CUDASPARSE_CHECK(EXPR)                            \
  do {                                                          \
    cusparseStatus_t __err = EXPR;                              \
    TORCH_CHECK(__err == CUSPARSE_STATUS_SUCCESS,               \
                "CUDA error: ",                                 \
                cusparseGetErrorString(__err),                  \
                " when calling `" #EXPR "`");                   \
  } while (0)
```

### 4.5 cuSOLVER

```cpp
#define TORCH_CUSOLVER_CHECK(EXPR)                              \
  do {                                                          \
    cusolverStatus_t __err = EXPR;                              \
    if (__err == CUSOLVER_STATUS_INVALID_VALUE) {               \
      TORCH_CHECK_LINALG(                                       \
          false,                                                \
          "cusolver error: ",                                   \
          at::cuda::solver::cusolverGetErrorMessage(__err),     \
          ". This error may appear if the input matrix contains NaN. ", \
          "Try alternative backends via "                       \
          "torch.backends.cuda.preferred_linalg_library()");    \
    } else {                                                    \
      TORCH_CHECK(__err == CUSOLVER_STATUS_SUCCESS,             \
          "cusolver error: ",                                   \
          at::cuda::solver::cusolverGetErrorMessage(__err));    \
    }                                                           \
  } while (0)
```

**Error Detection:** Distinguishes INVALID_VALUE (NaN matrices) from other errors.

### 4.6 cuDSS

```cpp
#define TORCH_CUDSS_CHECK(EXPR)                                 \
  do {                                                          \
    cudssStatus_t __err = EXPR;                                 \
    if (__err == CUDSS_STATUS_EXECUTION_FAILED) {               \
      TORCH_CHECK_LINALG(false,                                 \
          "cudss error: ",                                      \
          at::cuda::cudss::cudssGetErrorMessage(__err),         \
          ". This error may appear if the input matrix contains NaN."); \
    } else {                                                    \
      TORCH_CHECK(__err == CUDSS_STATUS_SUCCESS,                \
          "cudss error: ",                                      \
          at::cuda::cudss::cudssGetErrorMessage(__err));        \
    }                                                           \
  } while (0)
```

### 4.7 CUDA Driver API

```cpp
#define AT_CUDA_DRIVER_CHECK(EXPR)                              \
  do {                                                          \
    CUresult __err = EXPR;                                      \
    if (__err != CUDA_SUCCESS) {                                \
      const char* err_str;                                      \
      [[maybe_unused]] CUresult get_error_str_err =             \
          at::globalContext().getNVRTC().cuGetErrorString(__err, &err_str); \
      if (get_error_str_err != CUDA_SUCCESS) {                  \
        TORCH_CHECK(false, "CUDA driver error: unknown error");  \
      } else {                                                  \
        TORCH_CHECK(false, "CUDA driver error: ", err_str);     \
      }                                                         \
    }                                                           \
  } while (0)
```

**Special Handling:** Dynamically loaded NVRTC error string function.

### 4.8 Backward Compatibility

```cpp
#define AT_CUDA_CHECK(EXPR) C10_CUDA_CHECK(EXPR)
```

---

## 5. Resource Cleanup & Guard Patterns

### Location: `c10/cuda/CUDAGuard.h`
**URL:** https://github.com/pytorch/pytorch/blob/master/c10/cuda/CUDAGuard.h

### Device Guard (RAII Pattern)

```cpp
struct CUDAGuard {
  explicit CUDAGuard(DeviceIndex device_index) : guard_(device_index) {}
  explicit CUDAGuard(Device device) : guard_(device) {}
  
  ~CUDAGuard() = default;  // Restores original device on scope exit
  
  void set_device(Device device) { guard_.set_device(device); }
  void reset_device(Device device) { guard_.reset_device(device); }
  void set_index(DeviceIndex device_index) { guard_.set_index(device_index); }
  
  Device original_device() const { return guard_.original_device(); }
  Device current_device() const { return guard_.current_device(); }
  
private:
  c10::impl::InlineDeviceGuard<impl::CUDAGuardImpl> guard_;
};
```

**Error Recovery:** Guarantees device context restoration even on exceptions.

### Optional Device Guard

```cpp
struct OptionalCUDAGuard {
  explicit OptionalCUDAGuard() = default;
  explicit OptionalCUDAGuard(std::optional<Device> device_opt)
      : guard_(device_opt) {}
  explicit OptionalCUDAGuard(std::optional<DeviceIndex> device_index_opt)
      : guard_(device_index_opt) {}
  
  void reset() { guard_.reset(); }
  
private:
  c10::impl::InlineOptionalDeviceGuard<impl::CUDAGuardImpl> guard_;
};
```

**Use Case:** Optional device switching without allocation guarantees.

### Stream Guard (RAII Pattern)

```cpp
struct CUDAStreamGuard {
  explicit CUDAStreamGuard(Stream stream) : guard_(stream) {}
  ~CUDAStreamGuard() = default;
  
  void reset_stream(Stream stream) { guard_.reset_stream(stream); }
  
  CUDAStream original_stream() const;
  CUDAStream current_stream() const;
  Device current_device() const { return guard_.current_device(); }
  Device original_device() const { return guard_.original_device(); }
  
private:
  c10::impl::InlineStreamGuard<impl::CUDAGuardImpl> guard_;
};
```

**Error Recovery:** Restores both stream and device context on scope exit.

---

## 6. CUDA Utility Functions

### Location: `c10/cuda/CUDAFunctions.h`
**URL:** https://github.com/pytorch/pytorch/blob/master/c10/cuda/CUDAFunctions.h

### Device Management with Error Handling

```cpp
namespace c10::cuda {

// Robust device count (returns 0 on failure, doesn't throw)
C10_CUDA_API DeviceIndex device_count() noexcept;

// Strict device count (throws if no devices)
C10_CUDA_API DeviceIndex device_count_ensure_non_zero();

// Get/set current device with error checking
C10_CUDA_API DeviceIndex current_device();
C10_CUDA_API void set_device(DeviceIndex device, const bool force = false);

// Memory copy with sync and error handling
C10_CUDA_API void __inline__ memcpy_and_sync(
    void* dst, const void* src, int64_t nbytes,
    cudaMemcpyKind kind, cudaStream_t stream) {
  // Checks sync debug mode
  if (C10_UNLIKELY(warning_state().get_sync_debug_mode() != SyncDebugMode::L_DISABLED)) {
    warn_or_error_on_sync();
  }
  // GPU trace hook
  const c10::impl::PyInterpreter* interp = c10::impl::GPUTrace::get_trace();
  if (C10_UNLIKELY(interp)) {
    (*interp)->trace_gpu_stream_synchronization(
        c10::kCUDA, reinterpret_cast<uintptr_t>(stream));
  }
  // Actually perform memcpy and sync with error check
  C10_CUDA_CHECK(cudaMemcpyAsync(dst, src, nbytes, kind, stream));
  C10_CUDA_CHECK(cudaStreamSynchronize(stream));
}

// Stream sync with debug tracing
C10_CUDA_API void __inline__ stream_synchronize(cudaStream_t stream) {
  if (C10_UNLIKELY(warning_state().get_sync_debug_mode() != SyncDebugMode::L_DISABLED)) {
    warn_or_error_on_sync();
  }
  const c10::impl::PyInterpreter* interp = c10::impl::GPUTrace::get_trace();
  if (C10_UNLIKELY(interp)) {
    (*interp)->trace_gpu_stream_synchronization(
        c10::kCUDA, reinterpret_cast<uintptr_t>(stream));
  }
  C10_CUDA_CHECK(cudaStreamSynchronize(stream));
}
}
```

### Synchronization Debug Mode

```cpp
enum class SyncDebugMode { L_DISABLED = 0, L_WARN, L_ERROR };

class WarningState {
public:
  void set_sync_debug_mode(SyncDebugMode l) { sync_debug_mode = l; }
  SyncDebugMode get_sync_debug_mode() { return sync_debug_mode; }
private:
  SyncDebugMode sync_debug_mode = SyncDebugMode::L_DISABLED;
};
```

**Purpose:** Runtime debugging for unintended synchronizations.

---

## 7. Error Types & Categories

### CUDA Runtime Errors
| Error Type | Example | Handler |
|-----------|---------|---------|
| Invalid device | `cudaErrorInvalidDevice` | `C10_CUDA_CHECK` |
| Out of memory | `cudaErrorMemoryAllocation` | Device allocation retry |
| Invalid config | `cudaErrorInvalidConfiguration` | `C10_CUDA_KERNEL_LAUNCH_CHECK` |
| Async error | `cudaErrorAsyncEngineId` | `get_cuda_async_error_suffix()` |
| Device assertion | (kernel-side) | `TORCH_DSA_KERNEL_LAUNCH` |

### Library-Specific Errors
| Library | Error Enum | PyTorch Handler |
|---------|-----------|-----------------|
| cuDNN | `cudnnStatus_t` | `AT_CUDNN_CHECK` |
| cuBLAS | `cublasStatus_t` | `TORCH_CUDABLAS_CHECK` |
| cuSPARSE | `cusparseStatus_t` | `TORCH_CUDASPARSE_CHECK` |
| cuSOLVER | `cusolverStatus_t` | `TORCH_CUSOLVER_CHECK` |
| cuDSS | `cudssStatus_t` | `TORCH_CUDSS_CHECK` |
| CUDA Driver | `CUresult` | `AT_CUDA_DRIVER_CHECK` |

---

## 8. Retry Logic & Resource Cleanup

### No Built-in Retry Mechanism
PyTorch does **not** implement automatic retry logic in error handlers. Instead:

1. **Caller Responsibility:** Higher-level code must implement retry strategies
2. **Error Propagation:** Exceptions bubble up for application-level handling
3. **Guard-Based Cleanup:** RAII guards ensure resource restoration

### Resource Cleanup Guarantees

**On Error:**
```cpp
{
  CUDAGuard guard(device);  // Save current device
  
  try {
    kernel<<<...>>>();
    C10_CUDA_KERNEL_LAUNCH_CHECK();  // May throw
  } catch (...) {
    // guard destructor ALWAYS restores original device
    throw;
  }
  // guard destructor restores device on normal exit too
}
```

**Cleanup Operations:**
- Device context restoration
- Stream restoration
- Error state clearing (`cudaGetLastError()`)
- No resource leaks on exception

---

## 9. Error Message Components

### Full Error Message Structure

```
CUDA error: <cudaGetErrorString output>
<context-specific help text>
<async error information if applicable>
Device-side assertion failures:
  [Device assertion #1 details]
  [Device assertion #2 details]
  ...

File: <filename>
Function: <function_name>
Line: <line_number>
```

### Example Full Error
```
CUDA error: invalid device ordinal
Hint: Make sure you have CUDA available for this operation.
This is an async error; check device sync status.
Device-side assertion failures:
  Assertion failed in kernel "my_kernel" at file.cu:42
  Block: [1, 2, 3], Thread: [4, 5, 6]
  Message: "expected positive value"

File: operations.cpp
Function: execute_kernel
Line: 128
```

---

## 10. Build-Time Configuration

### Compile Flags

```cpp
#ifndef STRIP_ERROR_MESSAGES
  // Full error messages included
#else
  // Minimal error messages (binary size optimization)
#endif

#ifdef TORCH_USE_CUDA_DSA
  // Device-side assertions enabled at compile time
#else
  // DSA disabled (no runtime overhead)
#endif

#ifdef FBCODE_CAFFE2
  #define C10_COMPILE_TIME_MAX_GPUS 16  // Facebook/Meta limit
#else
  #define C10_COMPILE_TIME_MAX_GPUS 120 // Public PyTorch limit
#endif
```

### Runtime Configuration

```cpp
// Enable/disable DSA at runtime
CUDAKernelLaunchRegistry::get_singleton_ref().enabled_at_runtime = true;

// Enable/disable launch stack trace capture
CUDAKernelLaunchRegistry::get_singleton_ref().gather_launch_stacktrace = true;

// Set synchronization debug mode
c10::cuda::warning_state().set_sync_debug_mode(SyncDebugMode::L_WARN);
```

---

## 11. Key Patterns & Best Practices

### Pattern 1: Safe Kernel Launch
```cpp
kernel<<<blocks, threads, shared_mem, stream>>>(...);
C10_CUDA_KERNEL_LAUNCH_CHECK();  // Always check immediately
```

### Pattern 2: Automatic Device Restoration
```cpp
{
  c10::cuda::CUDAGuard guard(target_device);
  // ... operations on target device ...
}  // Original device automatically restored
```

### Pattern 3: Non-Fatal Error Handling
```cpp
C10_CUDA_CHECK_WARN(cudaMemcpy(...));  // Log warning, don't throw
```

### Pattern 4: Intentional Error Ignoring
```cpp
C10_CUDA_IGNORE_ERROR(cudaEventRecord(event, stream));
// Clear error state without logging
```

### Pattern 5: Library-Specific Errors
```cpp
AT_CUDNN_CHECK(cudnnConvolutionForward(...));
TORCH_CUDABLAS_CHECK(cublasGemm(...));
TORCH_CUDASPARSE_CHECK(cusparseSpMV(...));
```

---

## 12. File References & URLs

| Component | File Path | GitHub URL |
|-----------|-----------|-----------|
| Core Error Macros | `c10/cuda/CUDAException.h` | https://github.com/pytorch/pytorch/blob/master/c10/cuda/CUDAException.h |
| Error Implementation | `c10/cuda/CUDAException.cpp` | https://github.com/pytorch/pytorch/blob/master/c10/cuda/CUDAException.cpp |
| Device-Side Assertions | `c10/cuda/CUDADeviceAssertionHost.h` | https://github.com/pytorch/pytorch/blob/master/c10/cuda/CUDADeviceAssertionHost.h |
| CUDA Guards (RAII) | `c10/cuda/CUDAGuard.h` | https://github.com/pytorch/pytorch/blob/master/c10/cuda/CUDAGuard.h |
| CUDA Utilities | `c10/cuda/CUDAFunctions.h` | https://github.com/pytorch/pytorch/blob/master/c10/cuda/CUDAFunctions.h |
| CUDA Macros/Config | `c10/cuda/CUDAMacros.h` | https://github.com/pytorch/pytorch/blob/master/c10/cuda/CUDAMacros.h |
| Library-Specific | `aten/src/ATen/cuda/Exceptions.h` | https://github.com/pytorch/pytorch/blob/master/aten/src/ATen/cuda/Exceptions.h |

---

## 13. Summary Table

| Aspect | Implementation |
|--------|----------------|
| **Error Detection** | Macro-based checks with immediate context capture |
| **Error Types** | CUDA runtime, cuDNN, cuBLAS, cuSPARSE, cuSOLVER, cuDSS, Driver API |
| **Error Propagation** | Exception-based (`c10::AcceleratorError`) |
| **Retry Logic** | None (caller responsibility) |
| **Resource Cleanup** | RAII guards guarantee restoration on exception |
| **Device Assertions** | Managed memory + circular buffer tracking |
| **Kernel Launch Check** | `C10_CUDA_KERNEL_LAUNCH_CHECK()` immediate validation |
| **Warning-Only Errors** | `C10_CUDA_CHECK_WARN()` for non-critical operations |
| **Error Messages** | Rich context with file, function, line, and device assertions |
| **Binary Size** | Controllable via `STRIP_ERROR_MESSAGES` flag |
| **Performance** | Branch prediction hints (`C10_UNLIKELY`) optimize fast path |

---

## Conclusion

PyTorch's CUDA error handling system is **production-grade** with emphasis on:
1. **Context Preservation:** Every error carries diagnostic information
2. **Resource Safety:** RAII guards prevent leaks
3. **Device Assertions:** Post-mortem debugging capability via managed memory
4. **Flexibility:** Library-specific handlers and configurable strictness
5. **Performance:** Fast path optimization for the no-error case
6. **Extensibility:** Support for both legacy and modern CUDA APIs
