# CUDA Error Propagation: Advanced Technical Details

## Advanced Topics and Implementation Considerations

### 1. Per-Thread vs Per-Device Error State Clarification

**Common Misconception**: Some developers assume `cudaGetLastError()` is per-thread.

**Reality**: 
- Error state is **per-device**, not per-thread
- If multiple host threads access the same device concurrently, they share error state
- Thread A launches kernel, Thread B calls `cudaGetLastError()` → B may get A's kernel error

**Implication for Multi-Threaded Code**:
```c
// Thread 1
cudaStream_t stream1;
cudaStreamCreate(&stream1);
kernel1<<<blocks, threads, 0, stream1>>>(args);

// Thread 2
cudaStream_t stream2;
cudaStreamCreate(&stream2);
kernel2<<<blocks, threads, 0, stream2>>>(args);
cudaError_t err = cudaGetLastError();  // May return error from kernel1 (Thread 1)!

// Solution: Use per-stream error checking with synchronization
cudaStreamSynchronize(stream2);
cudaError_t err2 = cudaGetLastError();  // More likely to be stream2 error
```

**Recommendation**: 
- Use one device thread per GPU (most common pattern)
- Or use explicit stream synchronization before error checks
- Or use device-side error accumulation (atomic counters)

---

### 2. cudaGetLastError() vs cudaPeekAtLastError()

**cudaGetLastError()**
```c
cudaError_t err = cudaGetLastError();  // Reads error AND clears to cudaSuccess
```
- Retrieves error code
- **CLEARS error state** to cudaSuccess
- Next call returns cudaSuccess (unless new error occurs)

**cudaPeekAtLastError()** (CUDA 5.0+)
```c
cudaError_t err = cudaPeekAtLastError();  // Reads error WITHOUT clearing
cudaError_t err2 = cudaPeekAtLastError();  // Returns same error again
```
- Retrieves error code
- **DOES NOT clear** error state
- Useful for multiple checks without losing error info

**Pattern for Robust Error Handling**:
```c
// Check if error exists without modifying state
if (cudaPeekAtLastError() != cudaSuccess) {
    // Log error details
    fprintf(stderr, "Error: %s\n", cudaGetErrorString(cudaPeekAtLastError()));
    
    // Now clear it
    cudaError_t err = cudaGetLastError();
    
    // Recovery logic...
}
```

---

### 3. Stream Semantics in Detail

#### Default Stream Behavior (Pre-CUDA 7.0)

In CUDA < 7.0, default stream (stream 0) behaves as **serializing stream**:
- Synchronizes with ALL other streams
- Host API calls on default stream wait for GPU
- Very expensive for performance

```c
// CUDA < 7.0: Implicit synchronization with all streams
kernel<<<blocks, threads>>>(args);  // On default stream
// Implicitly waits for stream1, stream2, stream3 to complete!
```

#### Per-Thread Default Stream (CUDA 7.0+, Kepler+)

With `-default-stream per-thread` flag:
- Each host thread gets its own default stream
- No synchronization between threads' default streams
- Reduces implicit sync overhead

```bash
# Compile with per-thread default stream
nvcc -default-stream per-thread mycode.cu
```

```c
// Thread 1
kernel1<<<blocks, threads>>>(args);  // On thread 1's default stream

// Thread 2  
kernel2<<<blocks, threads>>>(args);  // On thread 2's default stream
// No implicit sync between them
```

#### Explicit Streams (Always Independent)

```c
cudaStream_t stream1, stream2;
cudaStreamCreate(&stream1);
cudaStreamCreate(&stream2);

kernel1<<<blocks, threads, 0, stream1>>>(args);  // Stream 1, independent
kernel2<<<blocks, threads, 0, stream2>>>(args);  // Stream 2, independent

// Both execute in parallel, no synchronization
```

---

### 4. Error State Clearing and Re-Detection

**Problem**: Error state can be accidentally cleared before error is handled.

```c
kernel1<<<blocks, threads>>>(args);  // Fails but error isn't checked yet

// Some library code somewhere:
cudaGetLastError();  // ERROR CLEARED!

// Later, application tries to check:
cudaError_t err = cudaGetLastError();  // Returns cudaSuccess - error lost!
```

**Solution: Save Error State Immediately**

```c
kernel1<<<blocks, threads>>>(args);
cudaError_t saved_error = cudaGetLastError();

// Even if library clears error state...
// We still have the saved copy
if (saved_error != cudaSuccess) {
    handle_error(saved_error);
}
```

**Prevention Pattern**:
```c
// Always save immediately after critical operations
#define CUDA_LAUNCH_KERNEL(kernel, blocks, threads, args) { \
    (kernel) <<<(blocks), (threads)>>> args; \
    g_last_error = cudaGetLastError(); \
    if (g_last_error != cudaSuccess) { \
        fprintf(stderr, "Kernel launch failed: %s\n", \
                cudaGetErrorString(g_last_error)); \
        goto error_handler; \
    } \
}
```

---

### 5. Stream Callbacks and Error Handling

**Stream Callbacks** (CUDA 3.1+) enable asynchronous error handling:

```c
void error_callback(cudaStream_t stream, cudaError_t status, void *userData) {
    if (status != cudaSuccess) {
        struct ErrorContext *ctx = (struct ErrorContext *)userData;
        printf("Stream %d error: %s\n", ctx->stream_id, 
               cudaGetErrorString(status));
        ctx->has_error = true;
    }
}

// Register callback BEFORE kernel
struct ErrorContext ctx = {stream_id: 1, has_error: false};
cudaStreamAddCallback(stream, error_callback, &ctx, 0);

// Launch kernel
kernel<<<blocks, threads, 0, stream>>>(args);

// Callback fires automatically when stream completes
// No explicit synchronization needed
```

**Advantages**:
- Error detected asynchronously
- No explicit sync call required
- Callback executes in driver context

**Limitations**:
- Callback executes on driver thread, not GPU
- Cannot be used to recover GPU state
- Limited to post-execution error notification

---

### 6. Event-Based Error Detection

**CUDA Events** provide lightweight synchronization points:

```c
cudaEvent_t event;
cudaEventCreate(&event);

kernel<<<blocks, threads>>>(args);
cudaEventRecord(event);  // Records event after kernel launch

// Check if event is ready (kernel completed)
cudaError_t status = cudaEventQuery(event);
if (status == cudaErrorNotReady) {
    printf("Kernel still running\n");
} else if (status != cudaSuccess) {
    printf("Kernel error: %s\n", cudaGetErrorString(status));
}

// For definitive result, synchronize
cudaEventSynchronize(event);
cudaGetLastError();  // Clear any error state
```

**Performance Characteristics**:
- `cudaEventQuery()`: Non-blocking, minimal overhead
- `cudaEventSynchronize()`: Blocking until event ready
- CPU can poll without forced synchronization

**Overhead**: ~1-2% compared to per-kernel checking

---

### 7. Advanced: CUDA Error Codes and Recovery

**Recoverable Errors**:
```c
if (err == cudaErrorMemoryAllocation) {
    printf("Out of memory - can try to free and retry\n");
    cudaDeviceReset();  // Clear memory
    return RETRY_OPERATION;
}
```

**Non-Recoverable Errors**:
```c
if (err == cudaErrorUnmapBufferObjectFailed) {
    printf("Fatal GPU state error - must abort\n");
    exit(1);
}
```

**Common Error Codes**:
- `cudaErrorMemoryAllocation` - Out of device memory (sometimes recoverable)
- `cudaErrorInvalidValue` - Invalid parameter (not recoverable)
- `cudaErrorInvalidPitchValue` - Memory alignment issue (not recoverable)
- `cudaErrorInvalidSymbol` - Symbol lookup failed (not recoverable)
- `cudaErrorUnmapBufferObjectFailed` - GPU state corrupted (fatal)

**Recovery Pattern**:
```c
const int MAX_RETRIES = 3;
for (int retry = 0; retry < MAX_RETRIES; retry++) {
    kernel<<<blocks, threads>>>(args);
    cudaError_t err = cudaGetLastError();
    
    if (err == cudaErrorMemoryAllocation) {
        if (retry < MAX_RETRIES - 1) {
            cudaDeviceReset();
            continue;  // Retry
        }
    } else if (err != cudaSuccess) {
        fprintf(stderr, "Non-recoverable error: %s\n", cudaGetErrorString(err));
        return false;
    } else {
        return true;  // Success
    }
}
return false;  // All retries exhausted
```

---

### 8. Memory Copy Error Checking

Async memory copies have special error semantics:

```c
// Asynchronous copy - returns immediately
cudaMemcpyAsync(d_dst, h_src, size, cudaMemcpyHostToDevice, stream);
cudaError_t err1 = cudaGetLastError();  // May return cudaSuccess (copy not started)

// Synchronize stream
cudaStreamSynchronize(stream);
cudaError_t err2 = cudaGetLastError();  // Now contains any memcpy errors

// Async copy with event
cudaMemcpyAsync(d_dst, h_src, size, cudaMemcpyHostToDevice, stream);
cudaEvent_t event;
cudaEventCreate(&event);
cudaEventRecord(event, stream);

// Later...
cudaEventSynchronize(event);
cudaError_t memcpy_err = cudaGetLastError();
```

**Important**: Async memcpy can have **host memory errors** that aren't caught until synchronization:
```c
float *bad_ptr = NULL;
cudaMemcpyAsync(d_data, bad_ptr, size, cudaMemcpyHostToDevice, stream);
// No error yet - copy hasn't started

cudaStreamSynchronize(stream);  // NOW the error is detected
cudaError_t err = cudaGetLastError();  // cudaErrorInvalidValue
```

---

### 9. Compilation Flags for Error Detection

**Debug Symbols**:
```bash
# Include debug info for better error messages
nvcc -g -G mycode.cu

# -g: Generate host debug info
# -G: Generate device debug info (slower execution)
```

**Error Checking Macros**:
```c
#ifndef NDEBUG
    #define CUDA_CHECK(call) { \
        cudaError_t err = call; \
        if (err != cudaSuccess) { \
            fprintf(stderr, "CUDA error at %s:%d code=%d(%s) '%s' \n", \
                    __FILE__, __LINE__, err, cudaGetErrorName(err), \
                    cudaGetErrorString(err)); \
            exit(EXIT_FAILURE); \
        } \
    }
    
    #define CUDA_SYNC_CHECK() { \
        cudaDeviceSynchronize(); \
        cudaError_t err = cudaGetLastError(); \
        if (err != cudaSuccess) { \
            fprintf(stderr, "CUDA sync error at %s:%d\n", __FILE__, __LINE__); \
            exit(EXIT_FAILURE); \
        } \
    }
#else
    #define CUDA_CHECK(call) call
    #define CUDA_SYNC_CHECK()
#endif
```

Compile with debug flag to enable checking:
```bash
nvcc mycode.cu                    # CUDA_CHECK disabled
nvcc -DDEBUG mycode.cu            # CUDA_CHECK enabled
```

---

### 10. Profiling Error Checking Overhead

**Methodology**:
```c
#include <time.h>

clock_t start = clock();
for (int i = 0; i < 1000; i++) {
    kernel<<<blocks, threads>>>(args);
    cudaGetLastError();  // With checking
}
cudaDeviceSynchronize();
clock_t end = clock();

printf("Time with checking: %f ms\n", (end - start) * 1000.0 / CLOCKS_PER_SEC);

// Compare to:
start = clock();
for (int i = 0; i < 1000; i++) {
    kernel<<<blocks, threads>>>(args);
    // No checking
}
cudaDeviceSynchronize();
end = clock();

printf("Time without checking: %f ms\n", (end - start) * 1000.0 / CLOCKS_PER_SEC);
```

**Expected Results** (per-kernel overhead):
- Batch kernels (< 1ms each): 5-30% overhead per check
- Heavy kernels (100ms+): < 1% overhead per check

**Lesson**: Error checking overhead is most significant for small, fast kernels

---

### 11. CUDA Compute Capability and Error Detection

Different compute capabilities have different error detection support:

| Capability | Error Support | Notes |
|-----------|-------|-------|
| 1.0-2.1 | Basic | Limited async error detection |
| 3.0-3.5 | Good | Full async support, events |
| 5.0+ | Excellent | Stream callbacks, better diagnostics |
| 7.0+ | Best | Unified memory errors, detailed reports |

**Check Compute Capability**:
```c
int device;
cudaGetDevice(&device);
cudaDeviceProp props;
cudaGetDeviceProperties(&props, device);
printf("Compute capability: %d.%d\n", props.major, props.minor);

if (props.major < 3) {
    printf("Warning: Old GPU with limited error detection\n");
}
```

---

### 12. Unified Memory and Error Propagation

**Unified Memory** (CUDA 6.0+) has different error semantics:

```c
float *managed_data;
cudaMallocManaged(&managed_data, size);

kernel<<<blocks, threads>>>(managed_data);
// Kernel may fault on first access, not during launch

cudaDeviceSynchronize();
cudaError_t err = cudaGetLastError();  // NOW error is detected
```

**Automatic Pagefaults**:
```c
float *managed_data;
cudaMallocManaged(&managed_data, size);

// Access on host before GPU
managed_data[0] = 1.0f;  // Pagefault might occur here

kernel<<<blocks, threads>>>(managed_data);  // Accesses may pagefault here too
cudaDeviceSynchronize();

// Pagefaults can happen at unpredictable times
```

**Key Difference**: Unified memory errors can be deferred to any synchronization point, not just after kernel launch.

---

### 13. Production Deployment Checklist

When deploying CUDA code with error handling:

- [ ] Error checking strategy selected (batch-check, streams, device-side)
- [ ] Compile-time flags for debug vs production checking
- [ ] Timeout mechanisms for hung kernels
- [ ] Logging of all error codes and stack traces
- [ ] Error recovery procedures documented
- [ ] Per-stream error handling tested
- [ ] Multi-GPU scenarios tested (if applicable)
- [ ] Overhead measured and acceptable
- [ ] Documentation of error codes and meanings
- [ ] Monitoring/alerting on error rates

---

## Summary

Advanced CUDA error handling requires understanding:
1. Error state is device-wide, not per-thread
2. Multiple mechanisms for deferred checking (streams, events, callbacks)
3. Trade-offs between error granularity and performance
4. Recovery strategies for recoverable vs fatal errors
5. Platform-specific error detection capabilities

The key to production reliability is choosing the right strategy for your workload and thoroughly testing error paths.
