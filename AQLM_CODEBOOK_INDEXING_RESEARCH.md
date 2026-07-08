# AQLM Codebook Indexing Structure: Comprehensive Research Report

## Executive Summary

AQLM (Additive Quantization for Language Models) uses a multi-codebook structure to compress LLM weights to 2-4 bits per parameter. The codebook indexing scheme is based on **additive quantization** where each weight group is reconstructed as a **sum of M codewords** drawn from M independent codebooks. Indices are encoded in B-bit format (typically 4-8 bits per codebook), with codebook sizes determined by 2^B entries.

## 1. Codebook Indexing Organization

### 1.1 Index Encoding Scheme

**Bit-Width to Codebook Size Relationship:**
- Codebook size = 2^nbits_per_codebook
- Each index index encoded as nbits_per_codebook bits
- Common configurations:
  - 2 bits per index: 4 entries per codebook
  - 3 bits per index: 8 entries per codebook
  - 4 bits per index: 16 entries per codebook
  - 8 bits per index: 256 entries per codebook
  - 16 bits per index: 65,536 entries per codebook

**Source:** Hugging Face transformers documentation states "Codebooks size is 2**nbits_per_codebook"

### 1.2 Index Storage Format

Indices are stored as **integer codes** arranged in tensor form:
```
Shape: [dims, num_out_groups, num_in_groups, num_codebooks]
```

Where:
- `dims`: Number of output dimensions
- `num_out_groups`: Number of output groups (related to out_group_size)
- `num_in_groups`: Number of input groups (related to in_group_size)
- `num_codebooks`: M codebooks for additive quantization

Each position stores M integer indices (one per codebook), each requiring nbits_per_codebook bits.

**Source:** vLLM documentation specifies this exact tensor format for AQLM codes.

### 1.3 Index-to-Vector Mapping

During dequantization, indices are mapped to actual weight vectors via **lookup table operations**:

1. **Index Decompression:** Stored B-bit code → integer index (0 to 2^B - 1)
2. **Codebook Lookup:** Index used to retrieve vector from codebook
3. **Vector Retrieval:** Codebook[m][index] → codeword vector of shape (out_group_size, in_group_size)
4. **Summation:** All M codewords summed together
5. **Scaling:** Result multiplied by per-output-dimension scaling factors

**Source:** Multiple sources describe "decompressing the code vectors back into one-hot index vectors to retrieve the corresponding codewords from each codebook"

## 2. Multi-Codebook Structure

### 2.1 Number of Codebooks (M Parameter)

- **Default:** num_codebooks = 1
- **Extreme Compression (2-bit):** Often num_codebooks = 2-4
- **High Accuracy:** Can increase to num_codebooks = 8 or higher
- **Relationship to bit-width:** Total bits = num_codebooks × nbits_per_codebook

### 2.2 Codebook Organization

**Key Properties:**
- M codebooks are **independent** (not residual)
- Each codebook contains 2^B vectors
- All M codebooks share same structure but contain different learned vectors
- Codebooks organized as: [num_codebooks, codebook_size, out_group_size, in_group_size]

**Independence Model:**
Unlike Residual Vector Quantization (RVQ) which quantizes residuals iteratively, AQLM uses **Additive Quantization (AQ)** where:
- No ordering constraints on codewords from different codebooks
- Each codebook independently optimized
- Final approximation: sum of M independent vectors

**Source:** "Additive Quantization (AQ) is more general, as it does not impose constraints on the codewords from the different codebooks" (AQLM paper)

### 2.3 How Codebooks Relate

The fundamental AQLM equation:
```
weight_group ≈ sum(codebook_m[index_m]) for m in [1..M]
```

Where:
- codebook_m[index_m] = the codeword selected from codebook m by index_m
- All M selected vectors have same dimensions
- Vectors are element-wise summed before scaling

**Source:** Papers describe AQLM as "representing weights as a sum of codewords drawn from multiple learned codebooks using additive quantization"

## 3. Indexing Scheme Details

### 3.1 Index Selection Process

For each weight group (of size in_group_size × out_group_size):

1. **For each of M codebooks:**
   - Find best matching codeword (minimizing reconstruction error)
   - Store its index (requires nbits_per_codebook bits)
   
2. **Index Storage:**
   - Pack M indices together (one per codebook)
   - Total bits per group: M × nbits_per_codebook bits

### 3.2 Common Configuration Examples

**2-bit Quantization (very aggressive):**
- num_codebooks = 2, nbits_per_codebook = 1: 2 bits total
- num_codebooks = 4, nbits_per_codebook = 1: 4 bits total (not common)
- Actual setup: Usually more complex combinations

**4-bit Quantization (popular):**
- num_codebooks = 2, nbits_per_codebook = 2: 4 bits total
- num_codebooks = 4, nbits_per_codebook = 1: 4 bits total

**Naming Convention in Model Names:**
- Format: MxNxK where M=num_codebooks, N=nbits_per_codebook, K=group_size
- Examples: 1x8x8, 2x8x8, 4x8x8, 1x16x8
- M (first number): number of codebooks
- N (second number): bits per codebook index
- K (third number): group size (or often in_group_size)

**Source:** GitHub AQLM README: "AQLM quantization setups vary mainly on the number of codebooks used as well as the codebook sizes in bits"

### 3.3 Group Size and Indexing

**Weight Matrix Decomposition:**
- Full weight matrix shape: (d_out, d_in)
- Split into groups of size (out_group_size, in_group_size)
- Groups indexed by output and input group indices

**Group Organization:**
- Each row split into input groups of size g (in_group_size)
- Output dimension split into groups of size out_group_size
- Multiple output groups may share same codebooks or have independent codebooks

## 4. Codebook Vector Mapping

### 4.1 Codebook Structure Tensor

```
codebooks: shape [num_codebooks, codebook_size, out_group_size, in_group_size]
```

**Mapping:**
- codebooks[m][i] → single codeword vector from codebook m
- codebooks[m][i].shape = (out_group_size, in_group_size)
- i ranges from 0 to 2^nbits_per_codebook - 1

### 4.2 Lookup Operation

**Pseudo-code for index-to-vector mapping:**
```python
# For a single group with M codebooks:
indices = [i_0, i_1, ..., i_{M-1}]  # M index values
vectors = []
for m in range(M):
    vector = codebooks[m][indices[m]]  # Lookup vector from codebook m
    vectors.append(vector)

reconstructed = sum(vectors)  # Element-wise sum
final = reconstructed * scale  # Multiply by scale factor
```

**Source:** vLLM dequantization documentation describes this exact process.

### 4.3 Performance Considerations

**Indexing Overhead:**
- Large lookup tables require non-contiguous memory access patterns
- Multiple lookups (one per codebook) needed per group during dequantization
- On CPU: significant performance penalty (10-100× slower than scalar quantization)
- On GPU: manageable with proper kernel optimization

**Source:** "The dequantization process requires large-scale, non-contiguous codebook lookups based on indices" causing "critical performance bottlenecks"

## 5. Practical Implementation Details

### 5.1 Scaling Factors

**Where:** Applied after codebook summation
**Granularity:** One scale per output dimension (not per group)
**Shape:** [d_out]
**Application:** Each reconstructed group multiplied by corresponding scale

**Source:** "Multiply by scales (one scale per output dimension)" from AQLM paper figures

### 5.2 Dequantization Process (Forward Pass)

1. Load codes tensor: [num_out_groups, num_in_groups, num_codebooks]
2. For each position in codes:
   - Extract M index values (one per codebook)
   - Look up M vectors from respective codebooks
   - Sum the M vectors element-wise
3. Apply per-output-dimension scales
4. Return reconstructed weights

### 5.3 Inference Kernels

**Supported Configurations (varies by kernel):**
- CUDA kernels: Optimized for specific (num_codebooks, nbits_per_codebook) combinations
- Different kernels required for different configurations
- Kernel selection based on: num_codebooks, nbits_per_codebook, in_group_size, out_group_size

**Source:** "The most popular setups and supported inference kernels are shown below" (Hugging Face docs)

## 6. Key Technical Distinctions

### 6.1 AQLM vs. Residual Quantization

**Additive (AQLM):**
- M codebooks, all independent
- Final reconstruction: sum of M vectors
- No ordering constraints
- Optimization: joint codebook learning

**Residual (RVQ):**
- M codebooks applied sequentially
- Each stage quantizes residual from previous stage
- Ordered/hierarchical structure
- Optimization: stage-by-stage

### 6.2 AQLM vs. Product Quantization

**Product Quantization (PQ):**
- Slices vector into sub-vectors
- Each sub-vector quantized **independently** with own codebook
- No summation, direct concatenation

**AQLM:**
- Divides weight matrix into groups
- Multiple codebooks applied **additively** (summed)
- Result scaled by learned scaling factors

**Source:** AQLM paper distinguishes AQ from PQ

## 7. Memory Layout and Storage

### 7.1 Compressed Format on Disk

**What is Stored:**
1. Codes tensor: [dims, num_out_groups, num_in_groups, num_codebooks]
   - Data type: uint8, uint16, or uint32 depending on nbits_per_codebook
   - Size: (dims × num_out_groups × num_in_groups × num_codebooks) integers

2. Codebooks tensor: [num_codebooks, codebook_size, out_group_size, in_group_size]
   - Data type: float16 or float32 (native precision)
   - Size: (num_codebooks × 2^nbits_per_codebook × out_group_size × in_group_size) floats

3. Scales tensor: [dims]
   - Data type: float16 or float32
   - Size: dims floats

### 7.2 Index Packing

- If nbits_per_codebook ≤ 8: pack multiple indices in uint8
- If nbits_per_codebook > 8: use uint16 or uint32
- Efficient storage when B < 8 by bit-packing

## 8. Sources and References

**Primary Sources:**
1. Egiazarian et al. (2024): "Extreme Compression of Large Language Models via Additive Quantization" - arXiv:2401.06118
2. AQLM Official PyTorch Repository: github.com/Vahe1994/AQLM
3. Hugging Face Transformers Documentation: Quantization - AQLM
4. vLLM Documentation: AQLM Quantization Layer
5. Babenko & Lempitsky (2014): "Additive Quantization for Extreme Vector Compression" (foundational work)

**Secondary Sources:**
1. Medium article: "The AQLM Quantization Algorithm, Explained" by Pierre Lienhart
2. Towards Data Science: Similar AQLM explanation
3. Multi-Bitwidth Quantization extension: "Multi-Bitwidth Quantization for LLMs Using Additive Codebooks" (arXiv:2606.12876)

## Summary Table

| Aspect | Details |
|--------|---------|
| **Index Bit-Width** | Configurable: 1-16 bits per codebook index |
| **Codebook Size** | 2^(nbits_per_codebook) entries |
| **Number of Codebooks** | 1-8+ (default 1, typical 2-4 for compression) |
| **Total Bits/Group** | num_codebooks × nbits_per_codebook |
| **Index Storage Format** | Integer tensor [dims, num_out_groups, num_in_groups, num_codebooks] |
| **Vector Mapping** | One-to-many: Each index → one codeword vector |
| **Summation Model** | Additive (M vectors summed) not residual |
| **Scaling** | Per-output-dimension factors applied post-summation |
| **Group Dimensions** | (in_group_size, out_group_size) both configurable |

---

**Research Conducted:** July 6, 2026
**Search Strategy:** 15+ targeted queries via AtlasForge web proxy covering codebook structure, indexing mechanisms, multi-codebook organization, and practical implementations
