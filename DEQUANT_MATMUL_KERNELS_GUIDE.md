# Comprehensive Technical Guide: Dequant-Matmul Kernels

## Overview

This guide synthesizes best practices, mathematical foundations, and implementation patterns for building efficient dequantization + matrix multiplication (dequant-matmul) kernels. This is a critical bottleneck in quantized LLM inference, where weights are stored at 1-4 bits but must be expanded to full precision for computation.

**Key insight**: The fastest systems fuse dequantization with matmul to minimize memory bandwidth—the main constraint in weight-bound computations.

---

## 1. Bit-Unpacking Fundamentals

### 1.1 Core Concepts

Quantized weights are stored as packed integers to reduce memory footprint:
- **1-bit**: 32 values per 32-bit word
- **2-bit**: 16 values per 32-bit word
- **3-bit**: ~10 values per 32-bit word (with padding)
- **4-bit**: 8 values per 32-bit word
- **8-bit**: 4 values per 32-bit word

### 1.2 Bit Extraction Patterns

#### Basic Masking and Shifting

For **4-bit** values packed 2 per byte:

```cuda
// Input: uint8 packed_value (two 4-bit values)
// Extract both 4-bit values
uint8_t lo_nibble = packed_value & 0x0F;        // Bits [3:0]
uint8_t hi_nibble = (packed_value >> 4) & 0x0F; // Bits [7:4]
```

For **2-bit** values packed 4 per byte:

```cuda
uint8_t val0 = (packed_value >> 0) & 0x03;
uint8_t val1 = (packed_value >> 2) & 0x03;
uint8_t val2 = (packed_value >> 4) & 0x03;
uint8_t val3 = (packed_value >> 6) & 0x03;
```

For **1-bit** values (32 per 32-bit word):

```cuda
// Using 32-bit word
uint32_t packed = *(uint32_t*)data;
for (int i = 0; i < 32; i++) {
    uint32_t bit = (packed >> i) & 0x1;
}
```

#### Vectorized Extraction

Most efficient approach using **32-bit or 64-bit SIMD loads**:

```python
# Pseudocode for 4-bit extraction
def unpack_4bit(packed_array):
    """Extract 4-bit values from uint8 array"""
    # Read two 4-bit values per byte
    lo = packed_array & 0x0F
    hi = (packed_array >> 4) & 0x0F
    return stack_interleaved(lo, hi)
```

### 1.3 Design Principles

1. **Minimize bit operations**: Use shifts and masks efficiently; avoid loops when possible
2. **Load-to-unpack ratio**: Read larger chunks (32/64-bit words) and unpack multiple values
3. **Register pressure**: Cache unpacked values in registers for immediate dequant math
4. **Vectorization**: Use SIMD intrinsics or vector types for parallel unpacking

---

## 2. Affine Quantization Math

### 2.1 Forward (Quantization) Formula

Given a floating-point value `x`, quantize to `n`-bit integer:

```
q = clamp(round((x - zero) / scale), 0, 2^n - 1)
```

Where:
- `scale` = (max - min) / (2^n - 1)
- `zero` = min
- `n` = number of bits (typically 2, 4, or 8)

### 2.2 Inverse (Dequantization) Formula

The critical operation for inference:

```
x_deq = (q * scale) + zero
```

Or equivalently:

```
x_deq = (q - zero_point) * scale + zero
```

**In practice**, zero-point is stored as `zero` (the minimum value), not as an integer offset.

### 2.3 Quantization Approaches

#### Post-Training Quantization (PTQ)

- **When**: After training is complete
- **Method**: Find min/max per group, compute scale/zero offline
- **Precision trade-off**: Quick but may lose ~1-3% accuracy
- **Used by**: vLLM, llama.cpp, ExLlamaV2 (most production systems)

**Formula for per-group PTQ**:

```python
def quantize_per_group(weights, group_size=128, bits=4):
    """Quantize weights to `bits` bits using per-group statistics"""
    groups = weights.reshape(-1, group_size)
    scales = (groups.max(axis=1) - groups.min(axis=1)) / (2**bits - 1)
    zeros = groups.min(axis=1)
    
    # Normalize and round
    normalized = (groups - zeros[:, None]) / scales[:, None]
    quantized = np.clip(np.round(normalized), 0, 2**bits - 1).astype(np.uint8)
    
    return quantized, scales, zeros
```

#### Quantization-Aware Training (QAT)

- **When**: During training or fine-tuning
- **Method**: Simulate quantization in forward pass, backprop through fake-quant
- **Precision trade-off**: Better accuracy (often <0.5% loss)
- **Cost**: Requires retraining

**Fake-quantization during training**:

```python
def fake_quantize(x, scale, zero, bits=4):
    """Simulate quantization for gradient flow during training"""
    max_val = 2**bits - 1
    q = torch.clamp(torch.round((x - zero) / scale), 0, max_val)
    x_deq = (q * scale) + zero
    # Gradient flows through fake_quant operation
    return x_deq
```

### 2.4 Granularity Trade-offs

| Granularity | Formula | Accuracy | Memory | Kernel Complexity |
|------------|---------|----------|--------|------------------|
| **Per-tensor** | Single scale/zero for entire weight matrix | Poor | Minimal | Simplest |
| **Per-row** | Scale/zero per output row | Better | 1/in_features overhead | Simple |
| **Per-group** | Scale/zero per 128-element group | Good | 1/128 overhead | Standard |
| **Per-channel** | Scale/zero per input channel | Best | Overhead depends on layout | Complex |

**Most common in production**: Per-group with group_size=128 (balance of accuracy and kernel simplicity).

### 2.5 Typical Range Analysis

For **4-bit quantization** of FP16 weights:

- Min/max range: [-10.0, +8.5] (typical for LLM weights)
- Scale factor: ~0.1 (range 16.5 / 15 levels)
- Quantization noise: ~0.1 (uniform distribution over scale)
- Acceptable for inference: Yes, with careful calibration

---

## 3. Kernel Architecture Patterns

### 3.1 Memory Hierarchy

CUDA kernels must efficiently use:

1. **Registers** (fastest, limited): Cache unpacked values
2. **Shared memory** (fast, limited): Cache tiles of quantized input
3. **Global memory** (slowest): Main data residence

### 3.2 Typical Kernel Structure

```cuda
__global__ void dequant_matmul_kernel(
    const uint8_t* packed_weights,  // Quantized weights
    const half* scales,              // Per-group scales
    const half* zeros,               // Per-group zero points
    const half* input,               // Activation: (M, K)
    half* output,                    // Result: (M, N)
    int M, int K, int N, int group_size
) {
    // 1. Thread mapping
    int bx = blockIdx.x;  // Block over output rows
    int by = blockIdx.y;  // Block over output columns
    int tx = threadIdx.x; // Thread within block
    int ty = threadIdx.y;
    
    // 2. Shared memory allocation
    __shared__ half shared_packed[TILE_K];      // Quantized input tile
    __shared__ float shared_accum[TILE_M][TILE_N]; // Accumulation buffer
    
    // 3. Per-thread local arrays
    float local_accum[ACCUM_M][ACCUM_N] = {0};
    
    // 4. Main computation loop over K dimension
    for (int k_base = 0; k_base < K; k_base += TILE_K) {
        // 4a. Load quantized data into shared memory
        int k_idx = k_base + ty;
        if (k_idx < K) {
            int packed_idx = k_idx / 2;  // 4-bit: 2 per byte
            uint8_t packed = packed_weights[packed_idx];
            
            // Unpack and dequantize
            half val0 = dequantize_nibble(packed & 0x0F, scales[...], zeros[...]);
            half val1 = dequantize_nibble(packed >> 4, scales[...], zeros[...]);
            shared_packed[ty * 2] = val0;
            shared_packed[ty * 2 + 1] = val1;
        }
        __syncthreads();
        
        // 4b. Compute: read from shared, accumulate in registers
        for (int kk = 0; kk < TILE_K; kk++) {
            half w_val = shared_packed[kk];
            for (int mm = 0; mm < ACCUM_M; mm++) {
                half a_val = input[...];  // Coalesced read
                for (int nn = 0; nn < ACCUM_N; nn++) {
                    local_accum[mm][nn] += (float)a_val * (float)w_val;
                }
            }
        }
        __syncthreads();
    }
    
    // 5. Store results
    for (int mm = 0; mm < ACCUM_M; mm++) {
        for (int nn = 0; nn < ACCUM_N; nn++) {
            output[...] = (half)local_accum[mm][nn];
        }
    }
}
```

### 3.3 Shared Memory Layout for Quantized Data

Efficient layout to avoid bank conflicts:

```cuda
// Standard: padding to avoid bank conflicts
#define SHARED_MEMORY_BANKS 32
#define BANK_WIDTH 4  // bytes

// For 4-bit unpacked to FP16 (2 bytes per value)
__shared__ half shared_data[TILE_K + TILE_K % SHARED_MEMORY_BANKS];

// Access pattern (coalesced):
// Thread i reads shared_data[i]
// Threads read consecutive values → 32-byte transaction
```

### 3.4 Thread Layout Strategies

#### Warp-per-row Strategy

- One warp (32 threads) handles one output row
- Each thread computes partial sum for different output columns
- Suitable for: Tall-thin matrices (M >> N)

```cuda
// 32 threads, each processes one output column
int row = blockIdx.x * blockDim.y + threadIdx.y;
int col = threadIdx.x;  // 0-31

float accum = 0.0f;
for (int k = 0; k < K; k++) {
    half w = dequantize(packed_weights[row][k]);
    float a = (float)input[row][k];
    accum += a * (float)w;
}
output[row][col] = accum;
```

#### Block-per-tile Strategy

- One block computes a 2D tile of output (TILE_M x TILE_N)
- More flexible, better for balanced matrices
- Better for tensor core utilization

```cuda
// Block computes TILE_M x TILE_N output tile
int row_base = blockIdx.x * TILE_M;
int col_base = blockIdx.y * TILE_N;

// TILE_M x TILE_N threads cooperatively compute the tile
float accum[TILE_M/THREADS_PER_ROW][TILE_N/THREADS_PER_COL];
```

### 3.5 Memory Coalescing Principles

**Rule**: Global memory reads/writes must be:
- **Aligned**: First address is multiple of transaction size (32, 64, or 128 bytes)
- **Contiguous**: Threads in a warp read contiguous memory

For **dequant-matmul**:

```cuda
// GOOD: Coalesced read of quantized data
// Thread i reads packed_weights[base + i]
// All 32 threads in warp read 16 bytes (4-bit packed) → 32-byte transaction

// GOOD: Coalesced write of output
// Thread i writes output[row][base_col + i]
// All 32 threads write to consecutive output columns → 128-byte transaction

// BAD: Strided access
// Thread i reads packed_weights[base + i * STRIDE]
// Non-contiguous → multiple transactions, poor utilization
```

---

## 4. Fused Dequant-Matmul Design

### 4.1 Why Fusion Matters

**Separate operations** (dequant then matmul):

```
Memory traffic = size(quantized_weights) + size(dequantized_weights) + size(output)
               = (K*N/8) + (K*N*2) + (M*N*2)  [bytes, 4-bit quantization]
```

**Fused operation** (dequant during matmul):

```
Memory traffic = size(quantized_weights) + size(output)
               = (K*N/8) + (M*N*2)
```

**Savings**: ~16x less intermediate data in memory.

### 4.2 Fusion Strategy

Load quantized data → unpack in registers → use immediately in matmul:

```cuda
__global__ void fused_dequant_matmul(
    const uint8_t* packed_weights,
    const half* scales,
    const half* zeros,
    const half* activations,
    half* output,
    int M, int K, int N, int group_size
) {
    // Per-thread accumulator
    float local_sum = 0.0f;
    
    // Streaming loop: minimal buffering
    for (int k = 0; k < K; k++) {
        // 1. Load and unpack single weight value
        uint8_t packed = packed_weights[k / 2];
        uint8_t nibble = (k % 2 == 0) ? (packed & 0x0F) : (packed >> 4);
        
        // 2. Dequantize immediately
        int group_idx = k / group_size;
        half scale = scales[group_idx];
        half zero = zeros[group_idx];
        half w_val = ((half)nibble * scale) + zero;
        
        // 3. Multiply and accumulate
        half a_val = activations[k];
        local_sum += (float)a_val * (float)w_val;
    }
    
    // Store result
    output[threadIdx.x] = (half)local_sum;
}
```

### 4.3 Production Implementation Examples

#### vLLM Approach

vLLM fuses dequant-matmul by:

1. **Tiling**: Process weights in 128-element groups (group_size=128)
2. **Shared memory cache**: Load one group of scales/zeros into shared memory
3. **Streaming unpack**: Unpack quantized values on-the-fly as needed
4. **Immediate matmul**: Use unpacked values in matmul without storing

Reference: `vllm/kernels/quantization/` (e.g., `q4_gemm.cu`)

#### llama.cpp Approach

llama.cpp uses **vectorized unpacking**:

1. **Load 4 quantized values at once** (from uint8 → 4 nibbles)
2. **Dequantize all 4 in parallel** (vector FMA)
3. **Accumulate in vector registers**
4. **Minimal shared memory** (only for scales/zeros)

Reference: `ggml-cuda.cu` - `ggml_cuda_op_dequantize_mul_mat_vec`

#### ExLlamaV2 Approach

ExLlamaV2 focuses on **4-bit** specifically:

1. **Custom unpacking kernel** optimized for 4-bit grouping
2. **Dual-buffer shared memory**: One buffer for load, one for compute
3. **Tensor core acceleration**: Use fp32 for accumulation
4. **Warp-grouped reduction**: Efficient output writeback

Reference: `q4_matmul_kernel_gptq_4bit.cu`

### 4.4 Bandwidth Analysis

For **Gemma-7B** (5.8B weights) with **4-bit quantization**:

| Scenario | Bandwidth Requirement | Feasibility on A100 |
|----------|-------|---------|
| Unfused (dequant then matmul) | 900+ GB/s | Infeasible |
| Fused dequant-matmul | 200 GB/s | Feasible (A100 = 2TB/s) |
| With tensor cores + fusion | 100 GB/s | Feasible + high utilization |

---

## 5. Optimization Techniques

### 5.1 Vectorized Loads

Use **128-bit (16-byte) loads** to maximize throughput:

```cuda
// Load 4x uint32 words at once (128 bits)
uint4 packed = *(uint4*)pointer;

// Unpack all 16 4-bit values from 4 bytes
uint32_t w0 = packed.x;
uint32_t w1 = packed.y;
uint32_t w2 = packed.z;
uint32_t w3 = packed.w;

// Extract nibbles (4 values per word = 16 total)
for (int i = 0; i < 4; i++) {
    uint32_t w = ((i == 0) ? w0 : (i == 1) ? w1 : (i == 2) ? w2 : w3);
    for (int j = 0; j < 8; j++) {
        uint8_t nibble = (w >> (j * 4)) & 0x0F;
        // Use nibble in computation
    }
}
```

### 5.2 Tensor Core Integration

NVIDIA tensor cores provide 8x speedup for matrix multiply:

```cuda
// Use half-precision (FP16) tensors
__shared__ half shared_A[16][16];
__shared__ half shared_B[16][16];
float shared_C[16][16] = {0};

// Use tensor core intrinsics
nvcuda::wmma::load_matrix_sync(frags_a, shared_A, 16);
nvcuda::wmma::load_matrix_sync(frags_b, shared_B, 16);
nvcuda::wmma::mma_sync(frags_c, frags_a, frags_b, frags_c);

// Dequantization happens before loading into tensor core
// or is fused into custom matrix instructions
```

### 5.3 Register Blocking

Maximize register reuse by processing multiple output elements per thread:

```cuda
__shared__ half shared_weights[BLOCK_K];

float accum[BLOCK_M][BLOCK_N] = {0};  // Register array

for (int k_block = 0; k_block < K; k_block += BLOCK_K) {
    // Load quantized weights
    for (int i = 0; i < BLOCK_K; i += blockDim.x) {
        if (i + threadIdx.x < BLOCK_K) {
            uint8_t packed = packed_weights[k_block + i + threadIdx.x];
            shared_weights[i + threadIdx.x] = dequantize(packed);
        }
    }
    __syncthreads();
    
    // Compute: high register reuse
    for (int i = 0; i < BLOCK_M; i++) {
        for (int j = 0; j < BLOCK_K; j++) {
            float a_val = input[i][k_block + j];
            for (int k = 0; k < BLOCK_N; k++) {
                accum[i][k] += a_val * (float)shared_weights[j];
            }
        }
    }
    __syncthreads();
}
```

### 5.4 Instruction-Level Optimizations

1. **Inline PTX for bit ops**: Faster than C++ for complex bit manipulation
   ```cuda
   // Inline PTX for extracting nibbles (compiled to single instruction)
   asm("bfe.u32 %0, %1, %2, %3;" : "=r"(result) : "r"(data), "r"(start), "r"(width));
   ```

2. **FMA (Fused Multiply-Add)**: Combine multiply and add in single instruction
   ```cuda
   accum = __fma_rn(a, b, accum);  // Fused: a*b + accum
   ```

3. **Warp-level reductions**: Minimize synchronization
   ```cuda
   float sum = __shfl_down_sync(0xffffffff, accum, 1);
   sum += __shfl_down_sync(0xffffffff, accum, 2);
   sum += __shfl_down_sync(0xffffffff, accum, 4);
   // Result in register
   ```

### 5.5 Double Buffering

Overlap compute with next load:

```cuda
__shared__ half buffer_A[2][TILE_SIZE];
__shared__ half buffer_B[2][TILE_SIZE];

int curr = 0;

// Load first tile
load_tile(buffer_A[0], buffer_B[0], ...);
__syncthreads();

for (int tile = 1; tile < num_tiles; tile++) {
    // Start loading next tile while computing current
    load_tile_async(buffer_A[1-curr], buffer_B[1-curr], ...);
    
    // Compute with current tile
    compute(buffer_A[curr], buffer_B[curr], ...);
    
    __syncthreads();
    curr = 1 - curr;
}
```

---

## 6. Triton Implementation Patterns

### 6.1 Triton Advantages

- **Higher-level abstraction**: No manual thread management
- **Automatic optimization**: Memory coalescing, shared memory, synchronization
- **Portability**: Compile to different GPU architectures
- **Rapid development**: Simpler syntax, fewer bugs

### 6.2 Basic Dequant-Matmul in Triton

```python
import triton
import triton.language as tl

@triton.jit
def dequant_matmul_kernel(
    # Pointers to matrices
    a_ptr, b_ptr, c_ptr,
    # Quantization metadata
    scales_ptr, zeros_ptr,
    # Matrix dimensions
    M, N, K, group_size,
    # Block sizes
    BLOCK_M: tl.constexpr, BLOCK_N: tl.constexpr, BLOCK_K: tl.constexpr,
):
    # Identify this block's position
    pid_m = tl.program_id(axis=0)
    pid_n = tl.program_id(axis=1)
    
    # Compute starting indices
    rm = pid_m * BLOCK_M + tl.arange(0, BLOCK_M)
    rn = pid_n * BLOCK_N + tl.arange(0, BLOCK_N)
    
    # Initialize accumulator
    acc = tl.zeros((BLOCK_M, BLOCK_N), dtype=tl.float32)
    
    # Loop over K dimension in blocks
    for k in range(0, K, BLOCK_K):
        rk = k + tl.arange(0, BLOCK_K)
        
        # Load activation tile A (M x K)
        a = tl.load(a_ptr + rm[:, None] * K + rk[None, :])
        
        # Load quantized weight tile B (K x N)
        # Unpack 4-bit values on-the-fly
        b_packed_idx = rk[:, None] * (N // 2) + rn[None, :] // 2
        b_packed = tl.load(b_ptr + b_packed_idx)
        
        # Extract nibble based on position
        b_nibble_idx = rn[None, :] % 2
        b = tl.where(
            b_nibble_idx == 0,
            b_packed & 0x0F,
            (b_packed >> 4) & 0x0F
        ).to(tl.float32)
        
        # Dequantize
        group_idx = rk[:, None] // group_size
        scales = tl.load(scales_ptr + group_idx)
        zeros = tl.load(zeros_ptr + group_idx)
        b = (b * scales + zeros)
        
        # Accumulate: A @ B^T
        acc += tl.dot(a, b)
    
    # Store result C (M x N)
    c = acc.to(c_ptr.dtype.element_ty)
    tl.store(c_ptr + rm[:, None] * N + rn[None, :], c)
```

### 6.3 Blockwise Quantization in Triton

For **per-group quantization** (most common):

```python
@triton.jit
def quantize_kernel(
    x_ptr, scale_ptr, zero_ptr, q_ptr,
    N, group_size,
    BLOCK_SIZE: tl.constexpr,
):
    """Quantize input x to 4-bit, compute scale and zero"""
    idx = tl.program_id(axis=0) * BLOCK_SIZE + tl.arange(0, BLOCK_SIZE)
    
    # Load block
    x = tl.load(x_ptr + idx)
    
    # Compute statistics for group
    group_id = idx // group_size
    group_mask = (idx // group_size == group_id)
    
    # Min/max reduction within group
    x_min = tl.min(tl.where(group_mask, x, float('inf')))
    x_max = tl.max(tl.where(group_mask, x, float('-inf')))
    
    # Compute scale and zero
    scale = (x_max - x_min) / 15.0
    zero = x_min
    
    # Quantize
    q = tl.clamp(tl.round((x - zero) / scale), 0, 15).to(tl.uint8)
    
    # Pack into nibbles (2 values per byte)
    packed_idx = idx // 2
    q_packed = tl.where(
        idx % 2 == 0,
        q,
        q << 4
    )
    
    # Store with reduction
    tl.atomic_or(q_ptr + packed_idx, q_packed)
    tl.store(scale_ptr + group_id, scale)
    tl.store(zero_ptr + group_id, zero)
```

### 6.4 Performance Tuning in Triton

Key parameters:

```python
# Tune block sizes for different matrix shapes
@triton.autotune(
    configs=[
        triton.Config({'BLOCK_M': 128, 'BLOCK_N': 256, 'BLOCK_K': 64}),
        triton.Config({'BLOCK_M': 64, 'BLOCK_N': 128, 'BLOCK_K': 128}),
        triton.Config({'BLOCK_M': 256, 'BLOCK_N': 64, 'BLOCK_K': 32}),
    ],
    key=['M', 'N', 'K']
)
@triton.jit
def dequant_matmul_tuned(...):
    # Kernel code
    pass
```

### 6.5 Comparison: Triton vs CUDA

| Aspect | Triton | CUDA |
|--------|--------|------|
| **Development speed** | Fast (Python) | Slower (C++) |
| **Optimization complexity** | Automatic | Manual |
| **Control** | Limited | Complete |
| **Performance ceiling** | 85-95% of hand-tuned | 100% |
| **Debugging** | Easier | Harder (PTX, profiles) |
| **Portability** | Better (multiple architectures) | Vendor-locked |

---

## 7. Production Examples and References

### 7.1 vLLM Implementation

**File**: `vllm/kernels/quantization/`

Key insight: Separate **prefill** and **decode** kernels optimized for different access patterns.

```
- q4_gemm.cu: Fused dequant-GEMM for prefill
- q4_matmul_kernel.cu: Optimized for decode phase
- qkvpacked.cu: Fused QKV dequant for attention
```

**Performance**: Achieves 60-70% of peak FP16 matmul throughput on A100.

### 7.2 llama.cpp Implementation

**File**: `ggml-cuda.cu`

Key insight: Single-threaded unpacking + vectorized accumulation.

```cuda
// Pseudocode from ggml-cuda.cu (simplified)
__global__ void dequantize_mul_mat_vec_q4(
    const uint8_t* __restrict__ vx,
    const uint8_t* __restrict__ vy,
    float* __restrict__ dst,
    int ncols, int nrows,
    int kquants
) {
    // Unpack and accumulate per-thread
    for (int row = 0; row < nrows; row += blockDim.y) {
        float acc = 0.0f;
        for (int col = 0; col < ncols; col += 2) {
            uint8_t packed = vx[row * stride + col / 2];
            uint8_t q0 = packed & 0x0F;
            uint8_t q1 = packed >> 4;
            
            float v0 = vy[col] * q0;
            float v1 = vy[col + 1] * q1;
            acc += v0 + v1;
        }
        dst[row] = acc;
    }
}
```

**Performance**: Portable to CPU/GPU; achieves 10-20 GB/s on RTX 4090.

### 7.3 ExLlamaV2 Implementation

**File**: `q4_matmul_kernel_gptq_4bit.cu`

Key insight: Optimized for **GPTQ** quantization (per-channel with Hessian weighting).

**Kernel features**:
- 8-warp blocks for increased occupancy
- Dual-buffer shared memory (load/compute overlap)
- Tensor core usage for FP32 accumulation
- Warp-grouped reduction

**Performance**: 70-80% of peak FP16 throughput on RTX 4090.

### 7.4 TensorRT Implementation

**File**: `tensorrt/plugins/quantizePlugin/` (closed source, but documented)

**Approach**:
1. **Quantized weights cached** in constant memory
2. **Custom kernel per quantization scheme** (GPTQ, AWQ, etc.)
3. **Graph fusion** with attention and normalization
4. **Auto-tuning** based on batch size and hardware

**Availability**: Part of TensorRT SDK; provides best-in-class performance for production deployments.

### 7.5 Reference Links

| Project | URL | Focus |
|---------|-----|-------|
| **vLLM** | https://github.com/vllm-project/vllm | Multi-quant support, prefill/decode separation |
| **llama.cpp** | https://github.com/ggerganov/llama.cpp | Portability, CPU-efficient unpacking |
| **ExLlamaV2** | https://github.com/turboderp/exllamav2 | GPTQ optimization, max throughput |
| **TensorRT-LLM** | https://github.com/NVIDIA/TensorRT-LLM | Production deployment, graph fusion |
| **Auto-GPTQ** | https://github.com/PanQiWei/AutoGPTQ | QAT and PTQ reference implementations |

---

## 8. Implementation Checklist

### 8.1 Core Kernel Development

- [ ] **Bit unpacking**: Test extraction for 1-bit, 2-bit, 4-bit with synthetic data
- [ ] **Dequant math**: Verify against reference (numpy or PyTorch)
- [ ] **Memory coalescing**: Profile with `nsys` to confirm aligned, contiguous access
- [ ] **Shared memory**: Confirm no bank conflicts (use `ncu` profiler)
- [ ] **Synchronization**: Verify `__syncthreads()` placement is correct
- [ ] **Tensor core integration**: If using, confirm proper memory layouts

### 8.2 Performance Optimization

- [ ] **Vectorized loads**: Use 128-bit (uint4) loads for quantized data
- [ ] **Register blocking**: Measure register pressure vs throughput trade-off
- [ ] **Warp reductions**: Replace `__syncthreads()` with warp-level ops where possible
- [ ] **Double buffering**: Overlap compute with next load
- [ ] **Occupancy analysis**: Aim for 50-80% occupancy (depends on architecture)

### 8.3 Testing & Validation

- [ ] **Correctness**: Test against reference dequant-matmul (PyTorch, NumPy)
- [ ] **Precision**: Check for numerical stability (FP16 vs FP32 accumulation)
- [ ] **Edge cases**: Test with non-divisible matrix sizes
- [ ] **Quantization schemes**: Validate with GPTQ, AWQ, RTN, and custom schemes
- [ ] **Batch sizes**: Test from 1 (decode) to 256+ (prefill)

### 8.4 Production Deployment

- [ ] **GPU compatibility**: Test on target architectures (A100, H100, RTX 4090, etc.)
- [ ] **Precision modes**: Support FP16, BF16, FP32 for activations and scales
- [ ] **Batch inference**: Optimize for batched matmul (better hardware utilization)
- [ ] **Memory profiling**: Confirm minimal intermediate buffers
- [ ] **Benchmarking**: Compare against vLLM, llama.cpp baselines

---

## 9. Troubleshooting Guide

### Issue: Incorrect Results

**Symptoms**: Output differs from reference (NumPy/PyTorch)

**Checklist**:
1. Verify bit extraction is correct for your quantization format
2. Check scale/zero-point computation and application order
3. Confirm dequant formula: `(q * scale) + zero` vs `(q - zero) / scale`
4. Ensure accumulation is FP32 (not FP16) to avoid precision loss
5. Test with small matrices (8x8) to isolate issues

### Issue: Poor Performance (<50% of peak)

**Symptoms**: Kernel throughput is much lower than expected

**Checklist**:
1. **Memory bandwidth**: Profile with `nsys` to check effective bandwidth
   - If <50% of peak: likely memory access pattern issue
   - Check coalescing ratio in `ncu` ("Global Hit Rate" metric)
2. **Register spilling**: High register pressure can reduce occupancy
   - Reduce BLOCK_M/BLOCK_N or local_accum size
3. **Shared memory bank conflicts**: Use `ncu` metric "SMEM Bank Conflict %"
   - If >5%: increase padding in shared mem declarations
4. **Synchronization overhead**: Too many `__syncthreads()`
   - Try double-buffering or warp-level synchronization

### Issue: NaN or Inf in Output

**Symptoms**: Dequantization produces invalid values

**Checklist**:
1. Check scale factors: if `scale == 0`, quantization failed
2. Verify zero-point is correct (should be min value after calibration)
3. Ensure nibble values are in [0, 15] for 4-bit (not [-8, 7])
4. Check for overflow in intermediate accumulation (use FP32, not FP16)

---

## 10. Further Reading

### Academic Papers

1. **"Quantization and Training of Neural Networks for Efficient Integer-Arithmetic Only Inference"** (Jacob et al., 2018)
   - Foundational affine quantization with per-channel scales

2. **"GPTQ: Accurate Post-Training Quantization for Generative Pre-trained Transformers"** (Frantar et al., 2023)
   - Hessian-weighted quantization for extreme compression

3. **"AWQ: Activation-aware Weight Quantization for LLM Compression and Acceleration"** (Lin et al., 2023)
   - Outlier-aware quantization strategy

### Technical Documentation

- **NVIDIA CUDA C++ Programming Guide**: Memory hierarchy, shared memory, synchronization
- **NVIDIA Turing and Ampere Architecture Whitepapers**: Tensor core architecture, memory bandwidth
- **Triton Documentation**: `https://triton-lang.org/`

### Open-Source References

- vLLM quantization module: Core production reference
- llama.cpp quantization: Portable, educational reference
- ExLlamaV2: Maximum performance reference

---

## Conclusion

Efficient dequant-matmul kernels are the backbone of quantized LLM inference. The key principles are:

1. **Fuse operations** to minimize memory bandwidth
2. **Understand your hardware**: Memory hierarchy, instruction throughput, tensor cores
3. **Profile thoroughly**: Use `nsys`, `ncu`, and custom timing to guide optimization
4. **Balance trade-offs**: Register pressure vs occupancy, precision vs speed
5. **Test extensively**: Correctness first, then optimize

Start with Triton for rapid prototyping, then optimize critical paths with CUDA kernels for production.

