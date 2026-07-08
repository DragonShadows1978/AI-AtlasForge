# CUDA Error Propagation - Quick Reference Guide

## Error Checking Decision Tree

```
Start: Do I need error checking?

├─ YES (production code)
│  ├─ Need to identify which operation failed?
│  │  ├─ YES → Use Stream-Based Isolation (Pattern 2)
│  │  └─ NO  → Is workload repetitive/deterministic?
│  │     ├─ YES → Use CUDA Graphs (Pattern 4)
│  │     └─ NO  → Use Batch-and-Check (Pattern 1)
│  │
│  └─ Need GPU to never stall?
│     ├─ YES → Use Device-Side Accumulation (Pattern 3)
│     └─ NO  → Overhead acceptable?
│        ├─ YES (< 1%) → Use Batch-and-Check with CUDA Graphs
│        └─ NO (need < 0.5%) → Profile and optimize further
│
└─ NO (debugging/testing)
   └─ Use full synchronization with per-operation checking
```

---

## Pattern Selection Quick Reference

| Pattern | Overhead | Granularity | Implementation | Best For |
|---------|----------|-------------|-----------------|----------|
| **Batch-and-Check** | 10-20% | Batch-level | 1 check per 10-100 ops | Default choice |
| **Stream-Based** | 5-10% | Per-stream | Sync each stream separately | Task-based workloads |
| **Device-Side** | 2-5% | Operation-level | Atomic counter tracking | Never-block scenarios |
| **CUDA Graphs** | <0.5% | Graph-level | Captured as unit | High-throughput batch |

---

## Copy-Paste Code Snippets

### Minimal-Overhead Pattern
```c
#define CUDA_CHECK(ans) { \
    cudaError_t err = (ans); \
    if (err != cudaSuccess) { \
        fprintf(stderr, "CUDA Error: %s\n", cudaGetErrorString(err)); \
        exit(1); \
    } \
}

// Usage
for (int batch = 0; batch < num_batches; batch++) {
    for (int i = 0; i < 100; i++) {
        kernel<<<blocks, threads>>>(args[batch*100 + i]);
    }
    CUDA_CHECK(cudaGetLastError());  // One check per 100 ops
}
```

### Stream-Based Pattern
```c
const int NUM_STREAMS = 4;
cudaStream_t streams[NUM_STREAMS];
for (int i = 0; i < NUM_STREAMS; i++) {
    cudaStreamCreate(&streams[i]);
}

// Launch work
for (int task = 0; task < num_tasks; task++) {
    kernel<<<blocks, threads, 0, streams[task % NUM_STREAMS]>>>(args);
}

// Check each stream
for (int i = 0; i < NUM_STREAMS; i++) {
    cudaStreamSynchronize(streams[i]);
    if (cudaGetLastError() != cudaSuccess) {
        printf("Stream %d error\n", i);
    }
}
```

### Device-Side Error Tracking
```c
__global__ void kernel_with_errors(float *data, int *error_count, int n) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= n) return;
    
    if (data[idx] < 0.0f) {
        atomicAdd(error_count, 1);  // Increment error counter
    }
}

// Host
int h_errors = 0;
int *d_errors;
cudaMalloc(&d_errors, sizeof(int));
cudaMemcpy(d_errors, &h_errors, sizeof(int), cudaMemcpyHostToDevice);

kernel_with_errors<<<blocks, threads>>>(d_data, d_errors, n);

// Later: read error count
cudaMemcpy(&h_errors, d_errors, sizeof(int), cudaMemcpyDeviceToHost);
printf("Total errors: %d\n", h_errors);
```

### CUDA Graphs Pattern
```c
cudaGraph_t graph;
cudaGraphCreate(&graph, cudaGraphDefault);

// Capture operations
for (int i = 0; i < 100; i++) {
    cudaGraphAddKernelNode(..., graph, ...);
}

cudaGraphExec_t graphExec;
cudaGraphInstantiate(&graphExec, graph);

cudaGraphLaunch(graphExec, 0);
cudaStreamSynchronize(0);
if (cudaGetLastError() != cudaSuccess) {
    printf("Graph execution error\n");
}

cudaGraphExecDestroy(graphExec);
cudaGraphDestroy(graph);
```

---

## Error Checking Overhead by Kernel Type

```
Kernel Type          Duration    Per-Op Check Overhead    With Batch-100 Overhead
───────────────────────────────────────────────────────────────────────────────
Tiny kernel          < 1μs       30% overhead             0.5%
Fast kernel          1-10ms      10% overhead             0.2%
Medium kernel        10-100ms    5% overhead              0.1%
Heavy kernel         > 100ms     1% overhead              < 0.05%
```

**Lesson**: Batch checking is most important for fast kernels

---

## Common Mistakes and Fixes

### Mistake 1: Error Lost Due to Multiple Calls
```c
// WRONG
kernel<<<blocks, threads>>>(args);
// Some library code calls cudaGetLastError()
// Now error is cleared!
cudaError_t err = cudaGetLastError();  // Returns cudaSuccess

// CORRECT
kernel<<<blocks, threads>>>(args);
cudaError_t err = cudaGetLastError();  // Save immediately
// Now safe - error is saved even if others clear device state
```

### Mistake 2: Checking Before Async Completion
```c
// WRONG
cudaMemcpyAsync(d_dst, h_src, size, cudaMemcpyHostToDevice, stream);
cudaError_t err = cudaGetLastError();  // Too early - memcpy not started yet

// CORRECT
cudaMemcpyAsync(d_dst, h_src, size, cudaMemcpyHostToDevice, stream);
cudaStreamSynchronize(stream);  // Wait for completion
cudaError_t err = cudaGetLastError();  // Now safe
```

### Mistake 3: Checking Default Stream with Multiple Threads
```c
// WRONG - in multi-threaded code
// Thread 1
kernel1<<<blocks, threads>>>(args);  // Default stream

// Thread 2
kernel2<<<blocks, threads>>>(args);  // Default stream
cudaError_t err = cudaGetLastError();  // May be Thread 1's error!

// CORRECT - use explicit streams
cudaStream_t stream;
cudaStreamCreate(&stream);
kernel<<<blocks, threads, 0, stream>>>(args);
cudaStreamSynchronize(stream);
cudaError_t err = cudaGetLastError();
```

### Mistake 4: Forgetting to Clear Error State
```c
// WRONG
if (cudaGetLastError() != cudaSuccess) {
    printf("Error 1\n");
}
if (cudaGetLastError() != cudaSuccess) {
    printf("Error 2\n");  // Won't print - state was cleared by first call
}

// CORRECT
cudaError_t err = cudaGetLastError();
if (err != cudaSuccess) {
    printf("Error: %s\n", cudaGetErrorString(err));
}
// Error state now cleared, ready for next check
```

---

## Quick Debugging Checklist

When errors aren't being detected:

- [ ] Is `cudaGetLastError()` being called AFTER the operation completes?
- [ ] Are synchronization points in the right place?
- [ ] Is error state being cleared before it's read?
- [ ] Is the default stream synchronizing unintentionally?
- [ ] Are multiple host threads sharing device?
- [ ] Is error code being interpreted correctly?

```c
// Debug helper
void print_cuda_state() {
    cudaError_t err = cudaPeekAtLastError();  // Don't clear
    printf("Current CUDA error: %s\n", cudaGetErrorString(err));
    
    int device;
    cudaGetDevice(&device);
    printf("Current device: %d\n", device);
    
    int threads_available;
    cudaGetDeviceProperties(&prop, device);
    printf("Compute capability: %d.%d\n", prop.major, prop.minor);
}
```

---

## Performance Tuning Tips

### Tip 1: Batch Size Selection
```c
// Measure optimal batch size
for (int batch_size = 1; batch_size <= 1000; batch_size *= 10) {
    timer_start();
    for (int i = 0; i < 10000; i++) {
        kernel<<<blocks, threads>>>(args);
        if (i % batch_size == 0) {
            cudaGetLastError();  // Check every batch_size ops
        }
    }
    cudaDeviceSynchronize();
    printf("Batch size %d: %.2f ms\n", batch_size, timer_end());
}
// Choose batch_size with best throughput
```

### Tip 2: Stream Count Optimization
```c
// Profile with different stream counts
for (int num_streams = 1; num_streams <= 32; num_streams *= 2) {
    // Create streams, run workload, measure throughput
    printf("Streams: %d, Throughput: %.2f ops/sec\n", 
           num_streams, measured_throughput);
}
```

### Tip 3: Selective Stream Synchronization
```c
// Don't sync all streams - sync only critical ones
cudaStream_t critical, background;

kernel_critical<<<blocks, threads, 0, critical>>>(args);
kernel_background<<<blocks, threads, 0, background>>>(args);

// Only sync critical path
cudaStreamSynchronize(critical);
if (cudaGetLastError() != cudaSuccess) {
    // Handle critical error immediately
}

// Background errors checked later
```

---

## CUDA Graphs Checklists

### When CUDA Graphs Help
- [ ] Workload is deterministic (same operations every iteration)
- [ ] Need to launch 50+ operations per cycle
- [ ] Overhead is measured and unacceptable (>1%)
- [ ] Available CUDA 11.0+
- [ ] Memory layout doesn't change between graph executions

### When CUDA Graphs Don't Help
- [ ] Workload is data-dependent (branching)
- [ ] Operations change dynamically
- [ ] Only launching 5-10 operations per cycle
- [ ] Memory pointers change frequently
- [ ] Need fine-grained error reporting

---

## Key CUDA Documentation Links

| Topic | URL |
|-------|-----|
| Error Handling | https://docs.nvidia.com/cuda/cuda-c-programming-guide/#error-handling |
| Streams | https://docs.nvidia.com/cuda/cuda-c-programming-guide/#streams-and-events |
| CUDA Graphs | https://docs.nvidia.com/cuda/cuda-c-programming-guide/#cuda-graphs |
| API Reference | https://docs.nvidia.com/cuda/cuda-runtime-api/ |

---

## Error Codes Quick Reference

```c
// Most common error codes
cudaSuccess                    // No error (0)
cudaErrorInvalidValue          // Invalid parameter
cudaErrorMemoryAllocation      // Out of device memory
cudaErrorNotInitialized        // Driver not initialized
cudaErrorInvalidDevice         // Invalid device index
cudaErrorInvalidResourceHandle // Invalid resource handle
cudaErrorUnmapBufferObjectFailed // Cannot unmap memory

// Check error name (v11.0+)
const char *name = cudaGetErrorName(err);
const char *str = cudaGetErrorString(err);
```

---

## Production Deployment Template

```c
#ifndef NDEBUG
    #define CUDA_CHECK(call) { \
        cudaError_t err = (call); \
        if (err != cudaSuccess) { \
            fprintf(stderr, "[%s:%d] CUDA error: %s (%d)\n", \
                    __FILE__, __LINE__, cudaGetErrorString(err), err); \
            exit(EXIT_FAILURE); \
        } \
    }
    #define ERROR_CHECK_INTERVAL 1  // Check after every op
#else
    #define CUDA_CHECK(call) (call)
    #define ERROR_CHECK_INTERVAL 100  // Check after every 100 ops
#endif

// Usage
int op_count = 0;
for (int batch = 0; batch < num_batches; batch++) {
    for (int i = 0; i < ops_per_batch; i++) {
        kernel<<<blocks, threads>>>(args);
        if (++op_count % ERROR_CHECK_INTERVAL == 0) {
            CUDA_CHECK(cudaGetLastError());
        }
    }
}
```

---

## Summary Table

| Aspect | Details |
|--------|---------|
| **Error State** | Single per device, not per-stream |
| **Best Practice** | Batch-and-check at algorithm phase boundaries |
| **Overhead** | 0.5-20% depending on pattern and kernel size |
| **Most Common Error** | Checking too frequently or too late |
| **Production Recommendation** | 1% overhead target with batch checking |
| **Fastest Approach** | CUDA Graphs with single check per graph |
| **Most Flexible** | Device-side error accumulation with atomics |
| **Easiest to Debug** | Full sync with per-operation checking (dev only) |

