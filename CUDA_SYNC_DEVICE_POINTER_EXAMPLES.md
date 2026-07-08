# CUDA Synchronization & Device Pointer: Code Examples

Runnable code patterns demonstrating synchronization costs, device-pointer bugs, and fixes.

---

## Example 1: Synchronization Cost Demonstration

### C Code: Naive vs. Optimized

```c
// naive_sync.cu - Demonstrates sync overhead

#include <cuda_runtime.h>
#include <stdio.h>
#include <time.h>

// Simple element-wise kernel
__global__ void add_kernel(float* a, float* b, float* c, int n) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx < n) c[idx] = a[idx] + b[idx];
}

// NAIVE: synchronize after every kernel
void naive_sync_pattern(float* a, float* b, float* c, int n, int iterations) {
    int block = 256;
    int grid = (n + block - 1) / block;
    
    for (int i = 0; i < iterations; i++) {
        add_kernel<<<grid, block>>>(a, b, c, n);
        cudaDeviceSynchronize();  // BLOCKS after every kernel!
    }
}

// OPTIMIZED: batch kernels, sync once
void optimized_async_pattern(float* a, float* b, float* c, int n, int iterations) {
    int block = 256;
    int grid = (n + block - 1) / block;
    
    for (int i = 0; i < iterations; i++) {
        add_kernel<<<grid, block>>>(a, b, c, n);
        // No sync; GPU continues to next iteration
    }
    cudaDeviceSynchronize();  // Single sync at end
}

// PRODUCTION: stream-based pipelining
void stream_pipeline_pattern(float* a, float* b, float* c, int n, int iterations) {
    int block = 256;
    int grid = (n + block - 1) / block;
    
    // Create multiple streams
    cudaStream_t streams[3];
    for (int s = 0; s < 3; s++) {
        cudaStreamCreate(&streams[s]);
    }
    
    for (int i = 0; i < iterations; i++) {
        int stream_idx = i % 3;
        add_kernel<<<grid, block, 0, streams[stream_idx]>>>(a, b, c, n);
        // Kernels queue to different streams; execute concurrently
    }
    
    // Sync all streams
    for (int s = 0; s < 3; s++) {
        cudaStreamSynchronize(streams[s]);
        cudaStreamDestroy(streams[s]);
    }
}

// Timing harness
int main() {
    int n = 1000000;  // 1M elements
    int iterations = 1000;
    
    float* a, * b, * c;
    cudaMalloc(&a, n * sizeof(float));
    cudaMalloc(&b, n * sizeof(float));
    cudaMalloc(&c, n * sizeof(float));
    
    clock_t start, end;
    
    // Naive pattern
    start = clock();
    naive_sync_pattern(a, b, c, n, iterations);
    cudaDeviceSynchronize();
    end = clock();
    printf("Naive (sync after each): %fms\n", (float)(end - start) / CLOCKS_PER_SEC * 1000);
    
    // Optimized pattern
    start = clock();
    optimized_async_pattern(a, b, c, n, iterations);
    end = clock();
    printf("Optimized (batch sync): %fms\n", (float)(end - start) / CLOCKS_PER_SEC * 1000);
    
    // Stream pattern
    start = clock();
    stream_pipeline_pattern(a, b, c, n, iterations);
    end = clock();
    printf("Stream pipeline: %fms\n", (float)(end - start) / CLOCKS_PER_SEC * 1000);
    
    // Expected output:
    // Naive: ~500ms (1000 syncs * 500us each)
    // Optimized: ~50ms (1 sync, kernels run concurrently)
    // Stream pipeline: ~20ms (streams run in parallel)
    
    cudaFree(a);
    cudaFree(b);
    cudaFree(c);
    return 0;
}
```

### Rust Version (Similar to Tensor-RS)

```rust
// sync_patterns.rs - Rust patterns for CUDA sync

use std::ffi::c_void;

#[link(name = "cudart")]
extern "C" {
    fn cudaMalloc(ptr: *mut *mut c_void, size: usize) -> i32;
    fn cudaFree(ptr: *mut c_void) -> i32;
    fn cudaDeviceSynchronize() -> i32;
    fn cudaStreamCreate(stream: *mut *mut c_void) -> i32;
    fn cudaStreamSynchronize(stream: *mut c_void) -> i32;
    fn cudaStreamDestroy(stream: *mut c_void) -> i32;
}

const CUDA_SUCCESS: i32 = 0;

fn check(code: i32, context: &str) -> Result<(), String> {
    if code == CUDA_SUCCESS {
        Ok(())
    } else {
        Err(format!("{}: error code {}", context, code))
    }
}

/// NAIVE: synchronize after every kernel
fn naive_sync_pattern(iterations: usize) -> Result<(), String> {
    for i in 0..iterations {
        // Queue kernel (simplified; assumes kernel_launch setup)
        unsafe { check(cudaDeviceSynchronize(), "sync")? };  // BLOCKS!
    }
    Ok(())
}

/// OPTIMIZED: batch kernels, sync once
fn optimized_async_pattern(iterations: usize) -> Result<(), String> {
    for i in 0..iterations {
        // Queue kernel (no sync)
    }
    unsafe { check(cudaDeviceSynchronize(), "final sync")? };  // BLOCKS ONCE
    Ok(())
}

/// PRODUCTION: stream-based pipelining
fn stream_pipeline_pattern(iterations: usize) -> Result<(), String> {
    let mut streams = vec![std::ptr::null_mut(); 3];
    for i in 0..3 {
        unsafe {
            check(cudaStreamCreate(&mut streams[i]), "create stream")?;
        }
    }
    
    for i in 0..iterations {
        let stream_idx = i % 3;
        // Queue kernel to streams[stream_idx]
        // No sync; kernels execute concurrently on different streams
    }
    
    for stream in &mut streams {
        unsafe {
            check(cudaStreamSynchronize(*stream), "stream sync")?;
            check(cudaStreamDestroy(*stream), "destroy stream")?;
        }
    }
    Ok(())
}
```

---

## Example 2: Error Detection Without Sync

### C Code: Detecting Kernel Failures

```c
// error_detection.cu - Shows when errors are caught

#include <cuda_runtime.h>
#include <stdio.h>

// Intentionally broken kernel to demonstrate error detection
__global__ void bad_kernel(float* ptr, int n) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx < n) {
        // This will crash if ptr is not device memory
        ptr[idx] = ptr[idx] * 2.0f;
    }
}

// WRONG: no error detection
void no_error_detection() {
    float* host_ptr = (float*)malloc(1000 * sizeof(float));
    
    // Launch kernel with host pointer (undefined behavior!)
    bad_kernel<<<32, 32>>>((float*)host_ptr, 1000);
    
    // cudaGetLastError() doesn't catch kernel crashes
    cudaError_t err = cudaGetLastError();
    if (err != cudaSuccess) {
        printf("Error: %s\n", cudaGetErrorString(err));
    } else {
        printf("No error (but kernel may have crashed on device!)\n");
    }
    // Kernel crash not detected until later
    free(host_ptr);
}

// CORRECT: error detection with synchronization
void error_detection_with_sync() {
    float* host_ptr = (float*)malloc(1000 * sizeof(float));
    
    // Launch kernel
    bad_kernel<<<32, 32>>>((float*)host_ptr, 1000);
    
    // Check for launch errors
    cudaError_t err = cudaGetLastError();
    if (err != cudaSuccess) {
        printf("Launch error: %s\n", cudaGetErrorString(err));
        free(host_ptr);
        return;
    }
    
    // Synchronize to detect execution-phase errors
    err = cudaDeviceSynchronize();
    if (err != cudaSuccess) {
        printf("Execution error: %s\n", cudaGetErrorString(err));
        // Error now caught!
    }
    
    free(host_ptr);
}

// PRODUCTION: deferred error checking with streams
void stream_error_detection() {
    cudaStream_t stream;
    cudaStreamCreate(&stream);
    
    float* dev_ptr;
    cudaMalloc(&dev_ptr, 1000 * sizeof(float));
    
    // Queue multiple kernels
    for (int i = 0; i < 10; i++) {
        bad_kernel<<<32, 32, 0, stream>>>(dev_ptr, 1000);
    }
    
    // Check for launch errors (before execution)
    cudaError_t err = cudaGetLastError();
    if (err != cudaSuccess) {
        printf("Launch error: %s\n", cudaGetErrorString(err));
        cudaStreamDestroy(stream);
        cudaFree(dev_ptr);
        return;
    }
    
    // Sync stream to detect all execution errors at once
    err = cudaStreamSynchronize(stream);
    if (err != cudaSuccess) {
        printf("Stream error: %s\n", cudaGetErrorString(err));
    }
    
    cudaStreamDestroy(stream);
    cudaFree(dev_ptr);
}

// Validation kernel: detects if pointer is valid device memory
__global__ void validate_kernel(const float* x, int n) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx < n) {
        // This read will fail if x is host memory
        float val = x[idx];
        // If we reach here, x is valid device memory
    }
}

void validate_pointer() {
    const float* suspected_ptr = (const float*)0xdeadbeef;  // Garbage pointer
    
    validate_kernel<<<32, 32>>>(suspected_ptr, 1000);
    
    cudaError_t err = cudaGetLastError();
    if (err != cudaSuccess) {
        printf("Pointer validation failed: %s\n", cudaGetErrorString(err));
    }
    
    // Must sync to detect execution error
    err = cudaDeviceSynchronize();
    if (err != cudaSuccess) {
        printf("Pointer is invalid: %s\n", cudaGetErrorString(err));
    }
}
```

### Rust Version (from Tensor-RS Pattern)

```rust
// error_detection.rs - Rust error handling patterns

#[link(name = "cudart")]
extern "C" {
    fn cudaGetLastError() -> i32;
    fn cudaDeviceSynchronize() -> i32;
    fn cudaStreamSynchronize(stream: *mut std::ffi::c_void) -> i32;
}

const CUDA_SUCCESS: i32 = 0;

// WRONG: trusts that no error occurred
fn error_detection_bad() -> Result<(), i32> {
    // After kernel launch
    let err = unsafe { cudaGetLastError() };
    if err == CUDA_SUCCESS {
        Ok(())  // False confidence; kernel may still be crashing
    } else {
        Err(err)
    }
}

// CORRECT: synchronizes before checking (Tensor-RS pattern)
fn error_detection_good() -> Result<(), i32> {
    let err = unsafe { cudaGetLastError() };
    if err != CUDA_SUCCESS {
        return Err(err);  // Launch error
    }
    
    // Wait for kernel to complete
    let err = unsafe { cudaDeviceSynchronize() };
    if err == CUDA_SUCCESS {
        Ok(())
    } else {
        Err(err)  // Execution error now detected
    }
}

// PRODUCTION: Stream-based async pattern (like vLLM)
fn stream_async_error_handling(stream: *mut std::ffi::c_void) -> Result<(), i32> {
    // Queue multiple kernels
    for _ in 0..100 {
        // kernel_launch(stream);
    }
    
    // Check for launch errors without blocking
    let err = unsafe { cudaGetLastError() };
    if err != CUDA_SUCCESS {
        return Err(err);
    }
    
    // Sync this stream (may still run other streams)
    let err = unsafe { cudaStreamSynchronize(stream) };
    if err == CUDA_SUCCESS {
        Ok(())
    } else {
        Err(err)  // All errors from this stream now caught
    }
}

// Helper: replicates Tensor-RS finish_launch pattern
fn finish_launch() -> Result<(), i32> {
    unsafe {
        let err = cudaGetLastError();
        if err != CUDA_SUCCESS {
            return Err(err);
        }
        
        let err = cudaDeviceSynchronize();
        if err == CUDA_SUCCESS {
            Ok(())
        } else {
            Err(err)
        }
    }
}
```

---

## Example 3: Device Pointer Anti-Pattern Detection

### Python: CuPy Integration with Validation

```python
# cupy_device_pointer.py - Device pointer patterns and bugs

import numpy as np
import cupy as cp
import ctypes

# WRONG: no validation that pointer is device memory
def bad_device_ptr_usage():
    x = cp.array([1.0, 2.0, 3.0], dtype=cp.float32)
    
    # Extract pointer (no checks)
    device_ptr = x.__cuda_array_interface__['data'][0]
    
    # This could be garbage!
    # Pass to C library which treats it as device memory
    # If device_ptr is actually host memory or null:
    # - Kernel reads garbage
    # - Output is wrong but no error raised
    
    return device_ptr

# CORRECT: validate before use
def good_device_ptr_usage():
    x = cp.array([1.0, 2.0, 3.0], dtype=cp.float32)
    
    interface = x.__cuda_array_interface__
    device_ptr = interface['data'][0]
    shape = interface['shape']
    dtype = interface['typestr']
    
    # Validate protocol version
    version = interface.get('version', 1)
    if version < 3:
        raise ValueError("Unsupported __cuda_array_interface__ version")
    
    # Validate properties
    if dtype != '<f4':
        raise ValueError(f"Expected float32, got {dtype}")
    
    # Validate non-null
    if device_ptr == 0:
        raise ValueError("Device pointer is null")
    
    # Now safe to use
    return device_ptr, shape

# ANTI-PATTERN: forgotten reference
def reference_forgotten_bug():
    def extract_ptr(cupy_array):
        # Extract pointer
        ptr = cupy_array.__cuda_array_interface__['data'][0]
        return ptr
    
    def process():
        x = cp.array([1.0, 2.0, 3.0], dtype=cp.float32)
        ptr = extract_ptr(x)
        # x goes out of scope here; CuPy frees the memory
        # ptr now points to freed device memory!
        return ptr
    
    freed_ptr = process()
    # Any use of freed_ptr is use-after-free

# FIXED: maintain reference
def reference_maintained_good():
    def process():
        x = cp.array([1.0, 2.0, 3.0], dtype=cp.float32)
        ptr = x.__cuda_array_interface__['data'][0]
        shape = x.shape
        
        # x still in scope; memory still valid
        result = some_cuda_operation(ptr, shape)
        
        # x only freed after function returns
        return result
    
    return process()

# ANTI-PATTERN: non-contiguous array
def non_contiguous_bug():
    x = cp.array([[1.0, 2.0], [3.0, 4.0]], dtype=cp.float32)
    x_view = x[:, 0]  # Non-contiguous view
    
    # x_view.__cuda_array_interface__['strides'] is not None!
    ptr = x_view.__cuda_array_interface__['data'][0]
    
    # Kernel expects contiguous memory, but ptr only points to first column
    # Linear indexing breaks; kernel reads wrong data
    return ptr

# FIXED: ensure contiguity
def contiguity_check_good():
    x = cp.array([[1.0, 2.0], [3.0, 4.0]], dtype=cp.float32)
    x_view = x[:, 0]
    
    interface = x_view.__cuda_array_interface__
    
    # Check if contiguous
    if interface['strides'] is not None:
        # Non-contiguous; make a copy
        x_view = cp.ascontiguousarray(x_view)
    
    # Now safe to extract pointer
    ptr = x_view.__cuda_array_interface__['data'][0]
    return ptr

# Pattern injection for validation
def pattern_injection_validation():
    """Inject known pattern to detect pointer ingestion bugs"""
    
    x = cp.zeros(1000, dtype=cp.float32)
    
    # Inject pattern
    injection_pattern = cp.arange(1000, dtype=cp.float32)
    x[:] = injection_pattern
    
    # Pass to kernel
    result = some_kernel_operation(x)
    
    # Validate output pattern
    expected = injection_pattern * 2  # Assume kernel doubles
    
    if not cp.allclose(result, expected):
        raise RuntimeError("Kernel read wrong data; pointer ingestion bug!")
    
    return result

# Address space validation (advanced)
def pointer_type_validation():
    """Use cudaPointerGetAttributes to validate pointer type"""
    
    x = cp.array([1.0, 2.0, 3.0], dtype=cp.float32)
    ptr = x.__cuda_array_interface__['data'][0]
    
    # In production code, you'd call CUDA API to validate:
    # cudaPointerAttributes attrs;
    # cudaPointerGetAttributes(&attrs, (void*)ptr);
    # if (attrs.type != cudaMemoryTypeDevice) error!
    
    # For now, just assert it's not obviously garbage
    assert ptr != 0, "Null pointer"
    assert ptr > 0x1000000, "Suspiciously small address"
    
    return ptr

# Zero-copy workflow
def zero_copy_pattern():
    """Efficient workflow with no unnecessary copies"""
    
    # Allocate once on device
    x_device = cp.random.randn(1000000, dtype=cp.float32)
    y_device = cp.zeros(1000000, dtype=cp.float32)
    
    # All operations stay on device
    for i in range(100):
        # Pass device pointers; no host involvement
        y_device = some_cuda_kernel(x_device, y_device)
    
    # Only copy result back at the very end
    result_host = cp.asnumpy(y_device)
    
    return result_host

# Anti-pattern: unnecessary copies
def unnecessary_copy_pattern():
    """WRONG: copies data back and forth"""
    
    x_gpu = cp.array([1.0, 2.0, 3.0], dtype=cp.float32)
    
    # Copy to host (unnecessary)
    x_host = cp.asnumpy(x_gpu)
    
    # Pass host array to kernel (triggers device allocation + memcpy)
    y_gpu = some_cuda_kernel(x_host)
    
    # Now we have 2 copies of the data:
    # - x_host (host)
    # - y_gpu (device)
    # Plus 2 memcpys of overhead
    
    return cp.asnumpy(y_gpu)  # Copy again!
```

### Rust/C Validation Pattern

```c
// device_ptr_validation.cu - Device pointer validation

#include <cuda_runtime.h>
#include <stdio.h>

// Kernel that validates its inputs
__global__ void validate_and_compute_kernel(
    const float* x, const float* y, float* out, int n
) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx < n) {
        // If x or y point to host memory or garbage:
        // - This read will fail with a segfault
        // - CUDA runtime detects it during synchronize()
        float val_x = x[idx];
        float val_y = y[idx];
        out[idx] = val_x + val_y;
    }
}

// Safe wrapper: validates pointers before launch
extern "C" int safe_compute(
    const float* x, const float* y, float* out, int n
) {
    // Validate that all pointers are device pointers
    cudaPointerAttributes x_attrs, y_attrs, out_attrs;
    
    cudaError_t err = cudaPointerGetAttributes(&x_attrs, x);
    if (err != cudaSuccess) return (int)err;
    if (x_attrs.type != cudaMemoryTypeDevice) {
        return 1001;  // Custom error: x not device memory
    }
    
    err = cudaPointerGetAttributes(&y_attrs, y);
    if (err != cudaSuccess) return (int)err;
    if (y_attrs.type != cudaMemoryTypeDevice) {
        return 1002;  // Custom error: y not device memory
    }
    
    err = cudaPointerGetAttributes(&out_attrs, out);
    if (err != cudaSuccess) return (int)err;
    if (out_attrs.type != cudaMemoryTypeDevice) {
        return 1003;  // Custom error: out not device memory
    }
    
    // Now safe to launch
    int block = 256;
    int grid = (n + block - 1) / block;
    validate_and_compute_kernel<<<grid, block>>>(x, y, out, n);
    
    // Sync and report errors
    err = cudaDeviceSynchronize();
    return (int)err;
}

// Pattern injection: inject known data and validate output
__global__ void pattern_check_kernel(
    const float* expected, const float* actual, int n, int* mismatch_count
) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx < n) {
        if (fabsf(expected[idx] - actual[idx]) > 1e-5f) {
            atomicAdd(mismatch_count, 1);
        }
    }
}

extern "C" int validate_pattern(
    const float* expected, const float* actual, int n
) {
    int* device_count = nullptr;
    cudaMalloc(&device_count, sizeof(int));
    cudaMemset(device_count, 0, sizeof(int));
    
    int block = 256;
    int grid = (n + block - 1) / block;
    pattern_check_kernel<<<grid, block>>>(expected, actual, n, device_count);
    
    cudaError_t err = cudaDeviceSynchronize();
    if (err != cudaSuccess) {
        cudaFree(device_count);
        return (int)err;
    }
    
    int host_count = 0;
    err = cudaMemcpy(&host_count, device_count, sizeof(int), cudaMemcpyDeviceToHost);
    cudaFree(device_count);
    
    if (err != cudaSuccess) return (int)err;
    
    // Return 0 if patterns match, mismatch_count otherwise
    return host_count;
}
```

---

## Example 4: Stream-Based Pipelining

### Complete Example: Batched SDPA Attention

```python
# stream_pipelining_attention.py - Production attention pipeline

import numpy as np
import cupy as cp
import tensor_rs

class AttentionPipeline:
    """
    Stream-based pipelined attention computation.
    Demonstrates proper stream usage for overlapping computation.
    """
    
    def __init__(self, num_streams=3):
        self.num_streams = num_streams
        # Note: In production, create CUDA streams here
        # cudaStream_t streams[num_streams];
        # for (int i = 0; i < num_streams; i++) cudaStreamCreate(&streams[i]);
    
    def process_batches(self, queries, keys, values, batch_size=64):
        """
        Process attention in batches using multiple streams.
        
        Args:
            queries: (seq_len, d_model) device array
            keys: (seq_len, d_model) device array
            values: (seq_len, d_model) device array
            batch_size: num batches to process
        
        Returns:
            output: (seq_len, d_model) attention output
        """
        
        seq_len = queries.shape[0]
        d_model = queries.shape[1]
        num_heads = 8
        d_head = d_model // num_heads
        
        # Reshape to (num_heads, seq_len, d_head) for multi-head attention
        q = queries.reshape(seq_len, num_heads, d_head)
        k = keys.reshape(seq_len, num_heads, d_head)
        v = values.reshape(seq_len, num_heads, d_head)
        
        outputs = []
        
        # NAIVE (no pipelining): process sequentially
        for i in range(0, seq_len, batch_size):
            end = min(i + batch_size, seq_len)
            # Process tokens i:end
            # Must wait for completion before next batch
            output_batch = tensor_rs.attention(
                q[i:end], k, v,
                scale=1.0 / np.sqrt(d_head)
            )
            outputs.append(output_batch)
        
        # OPTIMIZED (pipelined): queue to multiple streams
        # Pseudocode (actual implementation requires CUDA API):
        # for (int i = 0; i < seq_len; i += batch_size) {
        #     int stream_idx = (i / batch_size) % num_streams;
        #     cudaStream_t stream = streams[stream_idx];
        #     
        #     // Queue attention kernel to this stream
        #     tensor_rs_attention_on_stream(
        #         q_ptr + i, k_ptr, v_ptr, out_ptr + i,
        #         stream
        #     );
        #     // CPU continues; GPU processes in parallel
        # }
        # 
        # // Sync all streams
        # for (int s = 0; s < num_streams; s++) {
        #     cudaStreamSynchronize(streams[s]);
        # }
        
        return cp.concatenate(outputs, axis=0).reshape(seq_len, d_model)
    
    def compute_with_async_prefetch(self, queries, keys, values):
        """
        Overlap computation with data transfer using streams.
        
        Pattern:
        - Stream 0: compute attention
        - Stream 1: prefetch next batch to device
        - Stream 2: transfer results back to host
        """
        
        # This demonstrates the pipeline:
        # Stream 0: Kernel 1 → Kernel 2 → Kernel 3
        # Stream 1: Prefetch A → Prefetch B → Prefetch C
        # Stream 2: Transfer result back
        
        # All three streams execute concurrently
        
        # Pseudocode:
        # cudaStream_t compute_stream, prefetch_stream, transfer_stream;
        # 
        # for (int batch = 0; batch < num_batches; batch++) {
        #     // Stream 0: compute (all kernels queue immediately)
        #     attention_kernel_1<<<grid, block, 0, compute_stream>>>(q, k, out1);
        #     attention_kernel_2<<<grid, block, 0, compute_stream>>>(out1, v, out2);
        #     attention_kernel_3<<<grid, block, 0, compute_stream>>>(out2, scale, out);
        #     
        #     // Stream 1: prefetch next batch (doesn't wait for compute stream)
        #     cudaMemcpyAsync(host_q, next_q, size, cudaMemcpyDeviceToHost, prefetch_stream);
        #     
        #     // Stream 2: transfer result (overlaps with prefetch)
        #     cudaMemcpyAsync(result_host, out, size, cudaMemcpyDeviceToHost, transfer_stream);
        # }
        # 
        # // Wait for all streams
        # cudaStreamSynchronize(compute_stream);
        # cudaStreamSynchronize(prefetch_stream);
        # cudaStreamSynchronize(transfer_stream);
        
        pass


# Benchmark: pipelined vs. sequential
def benchmark_pipelining():
    """Compare pipelined vs. sequential processing"""
    
    seq_len = 4096
    d_model = 768
    batch_size = 256
    
    q = cp.random.randn(seq_len, d_model, dtype=cp.float32)
    k = cp.random.randn(seq_len, d_model, dtype=cp.float32)
    v = cp.random.randn(seq_len, d_model, dtype=cp.float32)
    
    pipeline = AttentionPipeline(num_streams=3)
    
    # Sequential: ~1000ms (4 batches * 250ms each)
    # Pipelined: ~300ms (250ms per stream + overlap)
    
    import time
    start = time.time()
    result = pipeline.process_batches(q, k, v, batch_size=batch_size)
    elapsed = time.time() - start
    
    print(f"Pipeline time: {elapsed*1000:.1f}ms")
    # Expected: 250-400ms for 3 concurrent streams
```

---

## Running the Examples

### Compile C CUDA Code

```bash
# core_kernels example
nvcc -O3 sync_patterns.cu error_detection.cu device_ptr_validation.cu -o cuda_examples

# Run
./cuda_examples
```

### Compile Rust Code

```bash
# Assuming Tensor-RS build setup
cd /mnt/ForgeRealm/AI-AtlasForge/workspace/Tensor_Rust_Port/mission_f860a512/tensor-rs

# Run tests with synchronization patterns
cargo test --release sync_patterns
cargo test --release stream_tests
```

### Run Python Examples

```bash
python3 cupy_device_pointer.py
python3 stream_pipelining_attention.py
```

