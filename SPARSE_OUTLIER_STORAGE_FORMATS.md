# Sparse Outlier Matrix Storage Formats: Technical Deep Dive

## Executive Summary

Sparse outlier matrix storage is a critical optimization for quantized neural networks. This report covers compression techniques, index map maintenance strategies, and performance implications across different storage formats and hardware platforms.

**Key Insight**: Format selection depends on **sparsity level** (outlier percentage), **target hardware** (CPU/GPU), and **access patterns** (sequential vs. random). No single format wins universally.

---

## 1. Sparse Matrix Storage Formats

### 1.1 COO (Coordinate Format)

**Structure**: Three separate arrays
```
row_indices: [0, 0, 1, 2, 2, 2]
col_indices: [0, 3, 2, 0, 2, 3]
values:      [1.2, 3.4, 2.1, 5.6, 7.8, 9.0]
```

**Memory Overhead**: 
- 3 × nnz values
- Example: 1M × 1M matrix with 0.001% outliers (10K outliers) = 120 KB index overhead + 40 KB values = 160 KB total

**Characteristics**:
- **Pros**: Simple, flexible insertion, easy conversion to/from dense
- **Cons**: High index overhead for moderate sparsity, slow arithmetic ops
- **Best For**: Very sparse data (<0.1% non-zeros), I/O operations

---

### 1.2 CSR (Compressed Sparse Row)

**Structure**: Row pointers + column indices + values
```
row_ptr: [0, 2, 3, 6]      # n+1 = 4 rows
col_idx: [0, 3, 2, 0, 2, 3]
data:    [1.2, 3.4, 2.1, 5.6, 7.8, 9.0]
```

**Memory Overhead**:
- 2 × nnz + n bytes
- Example: 1M rows, 10K non-zeros = 40 KB + 4 MB = ~4 MB (much smaller than COO)

**Characteristics**:
- **Pros**: Efficient row access, compact, fast matrix ops, GPU-kernel friendly
- **Cons**: Slow column access, requires reordering for modifications
- **Best For**: GPU matrix multiplication, sparse GEMM operations

**GPU Acceleration**: NVIDIA cuSPARSE, AMD rocSPARSE implement specialized kernels for CSR GEMM, achieving 10-100× speedup over CPU sparse ops.

---

### 1.3 CSC (Compressed Sparse Column)

Mirror of CSR but column-oriented. Optimal for column-wise access patterns.

---

### 1.4 DOK (Dictionary of Keys)

**Structure**: Hash map `(row, col) -> value`

**Characteristics**:
- **Pros**: Efficient incremental construction
- **Cons**: Highest memory overhead, slow arithmetic, poor cache locality
- **Best For**: Iteratively building sparse matrices (not for computation)

---

## 2. Outlier Storage in Quantized Models

### 2.1 Dense-Sparse Hybrid Architecture

Standard approach for low-precision quantization with outlier preservation:

```
Matrix = Base Matrix (low-precision) + Outlier Matrix (full-precision)

A_original = A_quantized (int8/fp16) + A_outliers (fp32/fp16)
           = (mantissa, scale) + (index_map, outlier_values)
```

**Typical Compression**:
- 8-bit base quantization: 1 byte per element
- Outlier rate: 0.1%-5% depending on layer and model
- Outlier values: 2-4 bytes per outlier (fp32 or fp16)
- Index overhead: 0.125-1 bit per element (depending on indexing strategy)

**Total compression**: 8-16× vs. float32 (4 bytes)

### 2.2 Index Map Techniques

#### Option A: Dense Mask (Bitmap)

```python
# Pseudocode
outlier_mask = torch.zeros((m, n), dtype=torch.bool)  # 1 bit per element
outlier_mask[0, 5] = True  # Element at (0,5) is outlier
outlier_mask[2, 8] = True  # Element at (2,8) is outlier

outlier_values = torch.empty(nnz_outliers, dtype=torch.float32)
outlier_values[0] = 3.14159  # Value for (0,5)
outlier_values[1] = 2.71828  # Value for (2,8)

# Reconstruction
result = dequantize(base_matrix) 
for i in range(nnz_outliers):
    idx = outlier_mask.nonzero()[i]
    result[idx] = outlier_values[i]
```

**Overhead Calculation**:
- Mask: m × n bits = 1/(8) bytes per element
- Base (int8): 1 byte per element
- **Total: 1.125 bytes per element** (vs. 4 for float32 = 3.56× compression)

**Performance Characteristics**:
- Index lookup: O(1), ~3 CPU cycles (single bit check)
- SIMD-friendly: Check 64 mask bits in parallel (AVX2)
- GPU-friendly: Warp-parallel mask check + scatter/gather
- **Best For**: 1-10% outlier rates, predictable access patterns

#### Option B: COO Index Format

```python
# Coordinate format for outliers
outlier_row_idx = torch.tensor([0, 2], dtype=torch.int32)
outlier_col_idx = torch.tensor([5, 8], dtype=torch.int32)
outlier_values = torch.tensor([3.14159, 2.71828], dtype=torch.float32)

# Lookup function (requires binary search or hash table)
def get_outlier(row, col):
    idx = binary_search((row, col), zip(outlier_row_idx, outlier_col_idx))
    return outlier_values[idx] if idx >= 0 else None
```

**Overhead Calculation**:
- Row indices: 4 bytes × nnz outliers
- Col indices: 4 bytes × nnz outliers
- **Total: 8 bytes per outlier** (or 0.08 bytes per total element if 0.1% sparsity)

**Performance Characteristics**:
- Index lookup: O(log n) for binary search, O(1) average for hash table
- Binary search: ~10-20 CPU cycles
- Hash table: ~20-40 CPU cycles (plus branch misprediction penalty)
- **Best For**: <0.1% outlier rates (breaks even with dense mask around 0.1-1% depending on hardware)

#### Option C: CSR-like Offset Pointers

```python
# Row-major offset pointers (similar to CSR)
row_offsets = torch.tensor([0, 1, 1, 3], dtype=torch.int32)  # [0, 1, 2, 3] rows
col_indices = torch.tensor([5, 8, 4], dtype=torch.int32)     # Column indices
values = torch.tensor([3.14159, 2.71828, 1.41421], dtype=torch.float32)

# Fast row-wise iteration
for row in range(num_rows):
    for i in range(row_offsets[row], row_offsets[row+1]):
        col = col_indices[i]
        val = values[i]
```

**Overhead Calculation**:
- Row offsets: 4 bytes × (m + 1)
- Col indices: 4 bytes × nnz
- **Total: 4*(m+1) + 4*nnz bytes**

**Performance Characteristics**:
- Row iteration: O(nnz) with cache-friendly sequential access
- Column access: O(n) worst case (scan full row)
- **Best For**: Row-wise or block-wise processing

#### Option D: Hash Table Indexing

```python
# Dictionary-based lookup
outlier_dict = {
    (0, 5): 3.14159,
    (2, 8): 2.71828
}

def get_outlier(row, col):
    return outlier_dict.get((row, col), default_value)
```

**Overhead Calculation**:
- Hash table load factor: typically 0.75
- Bytes per entry: ~40-60 bytes (pointer, hash, collision resolution)
- **Total: 40-60 bytes per outlier** (or 0.4-0.6 bytes per total element if 0.1% sparsity)

**Performance Characteristics**:
- Lookup: O(1) average, but 20-40 CPU cycles
- L1 cache misses: Common due to random access
- Branch misprediction: ~10-20% of entries cause pipeline stalls
- **Best For**: Variable size, incremental updates (NOT production inference)

---

## 3. Index Compression Techniques

### 3.1 Delta Encoding

Store differences between consecutive indices instead of absolute values.

```python
# Original indices: [100, 101, 102, 105, 200, 201]
# Delta encoding:   [100, 1, 1, 3, 95, 1]
# Savings: Many deltas < 256, fit in single byte (varint encoding)

def encode_delta(indices):
    deltas = [indices[0]] + [indices[i] - indices[i-1] for i in range(1, len(indices))]
    return encode_varint(deltas)

def decode_delta(encoded):
    deltas = decode_varint(encoded)
    indices = [deltas[0]]
    for d in deltas[1:]:
        indices.append(indices[-1] + d)
    return indices
```

**Compression Ratio**: 2-4× reduction in index storage for naturally ordered data.

### 3.2 Variable-Length Integer Encoding (Varint)

Encode small integers in fewer bytes.

```python
# Fixed size: 100, 101, 102 → 12 bytes (3 × 4 bytes)
# Varint:     100 (1 byte) + 101 (1 byte) + 102 (1 byte) = 3 bytes

# Typical encoding:
# 0-127:        1 byte   (values < 2^7)
# 128-16383:    2 bytes  (values < 2^14)
# 16384+:       3+ bytes (continuation bit approach)
```

**Compression Ratio**: 2-3× for typical index distributions in NLP models.

### 3.3 Bit Packing

Pack multiple small indices into single words.

```python
# If max_index < 256 (fits in 8 bits), pack 4 indices per 32-bit word
# Original: 4 × 4 bytes = 16 bytes
# Packed:   1 × 4 bytes = 4 bytes (4× compression)

def pack_indices_8bit(indices):
    packed = []
    for i in range(0, len(indices), 4):
        chunk = indices[i:i+4]
        word = 0
        for j, idx in enumerate(chunk):
            word |= (idx & 0xFF) << (8 * j)
        packed.append(word)
    return packed
```

**Compression Ratio**: Up to 8× when indices fit in narrower data types.

**GPU Considerations**: 
- Unpacking creates dependency chains (slow on GPU)
- Works best for CPU-side index storage
- GPU prefers word-aligned accesses

---

## 4. Performance Implications Across Hardware

### 4.1 Memory Access Patterns

#### CSR vs. COO Row Access
```
Matrix (4×4):
1 0 2 0
0 3 0 0
4 0 5 6
0 0 0 7

CSR: row_ptr=[0,2,3,6,7], col_idx=[0,2,1,0,2,3,3], data=[1,2,3,4,5,6,7]
  Access Row 1: Follow row_ptr[1:3] → sequential col_idx + data access
  Cache behavior: 2 cache misses (data array)

COO: row=[0,0,1,2,2,2,3], col=[0,2,1,0,2,3,3], data=[1,2,3,4,5,6,7]
  Access Row 1: Scan entire row array for matches → 7 comparisons
  Cache behavior: 1-2 cache misses (row array), but less predictable
```

**Result**: CSR 2-5× faster for row-wise operations due to cache locality.

#### Dense Mask vs. Random Access
```
Dense Mask (1 bit per element):
  - Outlier at (0, 5): 1 CPU cycle to check bit
  - Outlier at (2, 8): 1 CPU cycle to check bit
  - Sequential in-memory layout → great cache behavior
  - Total latency: ~4 cycles per outlier check

COO with Binary Search:
  - Look up (row, col) in sorted list → log(nnz) comparisons
  - ~20 CPU cycles for typical nnz (10K-1M)
  - Unpredictable access patterns → L1 cache misses
  - Total latency: ~30-50 cycles per outlier
```

**Result**: Dense mask 5-10× faster for random access patterns.

### 4.2 GPU Kernel Performance

#### GEMM with Sparse Outliers (Dense Mask)

```
Baseline: A × B = C (all float32)
  Peak throughput: 100-300 TFLOPS (A100 GPU)

With sparse outliers (dense mask):
  1. Dequantize A_quantized → ~0.5% compute overhead
  2. Check outlier mask in parallel → ~1-2% overhead
  3. Fetch full-precision outliers → ~0.5-1% bandwidth overhead
  Total overhead: 2-4% (90-98% of baseline throughput)
```

**GPU Optimizations**:
- Separate memory streams: prefetch mask while computing quantized path
- Warp-parallel mask checking: 32 threads check 32 bits simultaneously
- Tensor-core utilization: Keep cores fed from quantized pipeline

#### Memory Bandwidth Analysis
```
Dense int8 GEMM:
  Input bandwidth: 8 GB/s typical
  Peak compute: Need 2× loads per FLOP
  Saturation: ~16 TFLOPS (bandwidth limited on most GPUs)

With full-precision outliers (1% rate):
  Outlier bandwidth: +1% overhead (same bandwidth)
  Index mask bandwidth: Negligible (1 bit per element = 1/8 byte)
  Total bandwidth: 8.125 GB/s (imperceptible increase)
  Throughput: Still ~16 TFLOPS (limited by bandwidth, not indices)
```

---

### 4.3 CPU Performance with SIMD

#### Dense Mask with AVX2 (256-bit registers)

```c
// Check 64 mask bits in parallel (64 elements at once)
__m256i mask_v1 = _mm256_loadu_si256((__m256i*) mask_ptr);
__m256i mask_v2 = _mm256_loadu_si256((__m256i*) mask_ptr + 1);

// Blend quantized and full-precision results
__m256 quant_v1 = _mm256_loadu_ps(quant_ptr);
__m256 full_v1 = _mm256_loadu_ps(full_ptr);
__m256 result_v1 = _mm256_blendv_ps(quant_v1, full_v1, 
                                    _mm256_cvtepi32_ps(mask_v1));

// Throughput: 1 blend per cycle = 32 elements/cycle
// Latency: ~4 cycles to compute result
```

**Performance**: 1-5% overhead for dense mask vs. pure quantized SIMD.

---

### 4.4 Hardware-Specific Features

| Hardware | Feature | Benefit |
|----------|---------|---------|
| **Intel AVX-512** | Gather/Scatter | Hardware-accelerated sparse loads (1 cycle latency) |
| **AMD RDNA3+** | Sparse Matrix Engine | Native sparse matrix ops (2-4× speedup) |
| **NVIDIA H100** | Structured Sparsity | 2:4 sparsity support in Tensor Cores (2× speedup) |
| **ARM SVE** | Gather/Scatter | Scalable sparse operations |

---

## 5. Index Map Overhead Analysis

### 5.1 Overhead Comparison Table

| Sparsity | Format | Index Overhead | Total Storage | vs. Float32 |
|----------|--------|---|---|---|
| 0.01% | Dense mask | 0.125 bytes | 1.125 bytes | 3.6× |
| 0.01% | COO | 0.0004 bytes | 1.0004 bytes | 4.0× |
| 0.1% | Dense mask | 0.125 bytes | 1.125 bytes | 3.6× |
| 0.1% | COO | 0.004 bytes | 1.004 bytes | 4.0× |
| 1% | Dense mask | 0.125 bytes | 1.125 bytes | 3.6× |
| 1% | COO | 0.04 bytes | 1.04 bytes | 3.85× |
| 5% | Dense mask | 0.125 bytes | 1.125 bytes | 3.6× |
| 5% | COO | 0.2 bytes | 1.2 bytes | 3.3× |

**Break-even Point**: ~1% sparsity (dense mask and COO converge in total storage)

**Winner**:
- **<0.1% sparsity**: COO (100× lower index overhead)
- **0.1-1% sparsity**: Depends on access pattern (random = dense mask, sequential = COO)
- **>1% sparsity**: Dense mask (lower index overhead)

---

## 6. Real-World Outlier Rates

### 6.1 Activation Outliers

In LLM.int8() and similar methods:
- **Typical rate**: 0.01-0.1% of activations
- **Threshold**: top percentile (e.g., 99th percentile = 0.01%)
- **Distribution**: Heavy-tailed (few values much larger than rest)
- **Format choice**: COO with hash table or binary search

**Example (LLM.int8())**:
```
Layer: attention_output [batch=32, seq_len=1024, hidden=4096]
Total elements: 32 × 1024 × 4096 ≈ 134M
Outliers (0.1%): 134K values
COO index size: 134K × 8 bytes = 1 MB
Quantized base: 134M × 1 byte = 134 MB
Outlier values: 134K × 4 bytes = 536 KB
Total: 136 MB (vs. 512 MB for float32 = 3.76× compression)
```

### 6.2 Weight Outliers

In GPTQ, AWQ, and similar methods:
- **Typical rate**: 1-5% after per-channel quantization
- **Reason**: Channel-wise scaling reduces outliers significantly
- **Distribution**: Cluster around scale factor (fewer extreme values)
- **Format choice**: Dense mask (1-5% rate is sweet spot)

**Example (GPTQ)**:
```
Layer: weight matrix [hidden=4096, out_features=14336]
Total elements: 4096 × 14336 ≈ 59M
Outliers (2%): 1.18M values
Dense mask size: 59M bits ≈ 7.4 MB
Quantized base (3.5-bit): 59M × 0.44 bytes = 26 MB
Outlier values: 1.18M × 2 bytes (fp16) = 2.36 MB
Total: 35.8 MB (vs. 236 MB for float32 = 6.6× compression vs. 3.5× for just quantization)
```

### 6.3 KV Cache Sparsity

In attention pruning (vLLM, etc.):
- **Typical rate**: 30-50% pruning (50-70% kept)
- **Reason**: Many attention heads focus on recent tokens
- **Distribution**: Structured (prune entire token columns)
- **Format choice**: CSR with block sparsity

**Example**:
```
KV cache [seq_len=4096, num_heads=32, head_dim=128]
Full size: 4096 × 32 × 128 × 2 (K,V) ≈ 67 MB per token
With 40% sparsity: 67 MB × 0.6 ≈ 40 MB (40% savings)
Index overhead (CSR): seq_len × 4 bytes = 16 KB (negligible)
```

---

## 7. Production Implementations

### 7.1 LLM.int8() (bitsandbytes)

**GitHub**: github.com/TimDettmers/bitsandbytes

**Approach**:
- 8-bit quantization of activations
- Full-precision outliers (top 0.1%-1% by magnitude)
- Dense mask for indexing

**Key Implementation**:
```python
# Pseudocode from bitsandbytes
def quantize_llm_int8(A, threshold_percent=0.1):
    # Identify outliers
    outlier_mask = torch.abs(A) > torch.quantile(torch.abs(A), 
                                                   1.0 - threshold_percent/100)
    
    # Quantize non-outliers
    A_quantized = torch.round(A / scale).clamp(-128, 127).to(torch.int8)
    
    # Store separately
    return {
        'quantized': A_quantized,
        'mask': outlier_mask,  # Dense mask (1 bit per element)
        'outliers': A[outlier_mask]  # Full precision
    }
```

**Performance**: ~2× memory reduction, 1-2% latency overhead

---

### 7.2 GPTQ (IST-DM/GPTQ)

**GitHub**: github.com/IST-DM/GPTQ

**Approach**:
- 3-4 bit quantization via learned quantization grids
- Outlier preservation via per-layer masks
- Block-wise quantization (amortizes index overhead)

**Key Insight**: Per-channel/per-token scaling dramatically reduces outlier rate.

```python
# Simplified GPTQ approach
def quantize_gptq(W, bits=4, group_size=128):
    # Group quantization
    for group in range(0, W.shape[1], group_size):
        group_data = W[:, group:group+group_size]
        
        # Find optimal scale
        scale = find_optimal_scale(group_data, bits)
        
        # Quantize with outlier preservation
        quantized = torch.round(group_data / scale)
        
        # Identify outliers (values that lose >5% precision)
        reconstruction_error = torch.abs(group_data - quantized * scale)
        outliers = reconstruction_error > 0.05 * torch.abs(group_data)
```

**Performance**: 3-4× compression, 2-5% accuracy loss vs. 8-bit

---

### 7.3 vLLM Sparse KV Cache

**GitHub**: github.com/vllm-project/vllm

**Approach**:
- Attention pruning identifies important tokens
- Store KV values for pruned tokens only
- CSR format for efficient GPU access

**Implementation**:
```python
# Pseudocode from vLLM
def sparse_kv_cache_attention(Q, K_sparse, V_sparse, attention_mask):
    # K_sparse, V_sparse store only important tokens
    # Attention mask indicates which tokens are kept
    
    # Compute attention with sparse K
    scores = torch.matmul(Q, K_sparse.transpose())  # Uses CSR format
    scores = scores + attention_mask
    attn_weights = torch.softmax(scores, dim=-1)
    
    # Aggregate with sparse V
    output = torch.matmul(attn_weights, V_sparse)  # CSR matrix mult
    return output
```

**Benefit**: 30-50% KV cache memory reduction for long sequences

---

### 7.4 TensorRT INT8 Quantization

**Implementation**: NVIDIA proprietary, integrated in TensorRT 8.0+

**Approach**:
- Per-tensor or per-channel dynamic range calibration
- Optional per-layer full-precision fallback (implicit outlier handling)
- Hardware-fused kernels in CUDA

**Performance**: 2-4× throughput improvement (vs. float32)

---

### 7.5 TVM Sparse Tensor Support

**GitHub**: github.com/apache/tvm

**Current Status**: 
- CSR, CSC, COO format support
- Sparse GEMM optimization for CPUs and GPUs
- Block-sparse decomposition for structured sparsity

**Research Direction**: Automatic format selection based on sparsity pattern and target hardware.

---

## 8. Best Practices & Recommendations

### 8.1 Format Selection Flowchart

```
Sparsity Rate?
├─ <0.1% (activation outliers)
│  └─ Use: COO with binary search or hash table
│     Index overhead: ~0.04 bytes/element
│     Access: Random (acceptable latency 20-40 cycles)
│
├─ 0.1-1% (mixed scenarios)
│  ├─ Access pattern: Random?
│  │  └─ Yes → Dense mask (1-2% index overhead acceptable)
│  │  └─ No → CSR with structured access
│  └─ Latency sensitive?
│     └─ Yes → Dense mask
│     └─ No → COO if very sparse region
│
└─ >1% (weight outliers after per-channel quant)
   └─ Use: Dense mask (1 bit per element)
      Index overhead: 0.125 bytes/element (fixed)
      Access: Sequential or random (both reasonable)
```

### 8.2 Optimization Techniques

| Problem | Solution |
|---------|----------|
| Index overhead too high | Use per-channel/per-token quantization to reduce outlier rate |
| Slow outlier access (CPU) | Dense mask + SIMD blend operations |
| Slow outlier access (GPU) | CSR format + warp-parallel gather |
| Limited GPU memory | Use COO for <0.1% outliers, delta encoding for indices |
| Inference latency | Separate streams: quantized path + index path in parallel |
| Random access performance | Tile-based processing (8×8 or 16×16 blocks) |

### 8.3 Implementation Checklist

- [ ] Profile outlier distribution in your model (histogram by layer)
- [ ] Benchmark format performance on target hardware (A100 GPU, CPU, etc.)
- [ ] Consider access patterns (row-wise, column-wise, random)
- [ ] Implement index compression (delta encoding, varint)
- [ ] Add format conversion optimizations (sparse → dense in GEMM)
- [ ] Monitor memory footprint (include index overhead in budget)
- [ ] Validate numerical precision (reconstruct ~1000 values spot-check)

---

## 9. References

**Key Papers**:
1. Dettmers et al. (2022) - LLM.int8(): 8-bit Matrix Multiplication for Transformers at Scale
   - Publication: NeurIPS 2022
   - Key insight: Dense mask for activation outliers

2. Frantar et al. (2023) - GPTQ: Accurate Post-Training Quantization for Generative Pre-trained Transformers
   - Publication: ICLR 2023
   - Key insight: Per-channel quantization reduces weight outliers to 1-5%

3. Ashkboos et al. (2023) - Efficient Second-Order Optimizers on GPUs via Preprocessed Blockwise Quantization
   - Key insight: Block-wise outlier handling

**Production Frameworks**:
- bitsandbytes: github.com/TimDettmers/bitsandbytes
- AutoGPTQ: github.com/PanQiWei/AutoGPTQ
- vLLM: github.com/vllm-project/vllm
- TVM: github.com/apache/tvm
- TensorRT: developer.nvidia.com/tensorrt

---

## 10. Summary Table: Format Comparison

| Aspect | COO | CSR/CSC | Dense Mask | Hash Table |
|--------|-----|---------|------------|-----------|
| **Memory (0.1% sparsity)** | 0.04 B/elem | 0.4 B/elem | 0.125 B/elem | 0.6 B/elem |
| **Memory (1% sparsity)** | 0.04 B/elem | 0.04 B/elem | 0.125 B/elem | 0.06 B/elem |
| **Lookup Latency** | 20 cycles | 10 cycles | 3 cycles | 40 cycles |
| **Random Access** | Slow | Medium | Fast | Very Slow |
| **Sequential Access** | Slow | Fast | Medium | Very Slow |
| **SIMD Friendly** | No | Yes (reorder) | Yes | No |
| **GPU Friendly** | Limited | Excellent | Good | Poor |
| **Index Compression** | Delta, Varint | Good candidate | N/A | Poor candidate |
| **Incremental Updates** | Slow | Very Slow | Medium | Fast |
| **Best Use** | Activation outliers | GPU GEMM | Weight outliers | Construction |

---

**Document Version**: 1.0 (2026)
**Last Updated**: Research synthesis of production implementations and performance characterization
