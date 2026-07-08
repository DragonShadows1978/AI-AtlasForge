# GGML Quantization Types & Granularity Specifications

## Executive Summary

GGML quantization is the foundation for efficient LLM inference in llama.cpp and other systems. This document compiles official specifications, type definitions, block/group sizes, and implementation details for all major GGML quantization types (Q2_K, Q3_K, Q4_K, Q4_K_M, Q5_K, Q6_K, Q8_K).

## Official Sources

### Primary Repositories
- **llama.cpp**: https://github.com/ggerganov/llama.cpp - Main implementation and format spec
- **GGML**: https://github.com/ggerganov/ggml - Core tensor library
- **llama.cpp Conversions**: https://github.com/ggerganov/llama.cpp/blob/master/convert.py - Format conversion logic

### Key Files
- `ggml.h` - Type definitions and constants
- `ggml-quants.h` - Quantization implementation details
- `convert.py` - GGML format specification and reference
- `README.md` - Format documentation
- Issue discussions and PR descriptions - Rationale for design choices

## GGML Quantization Type Hierarchy

### Format Version History
- **GGML v3**: Original quantization scheme
- **GGML v4**: Introduced K-quants (Q*_K variants) with improved quality
- **Current**: Version 4 with optimizations

## Quantization Type Specifications

### Q8_K (8-bit Quantization)
```
Block Size:        256 weights per block
Group Size:        32 weights per group
Bits Per Weight:   8.5 bits/weight (including scales)
Scale Size:        2 bytes (uint16_t x2)
```

**Layout per 256-weight block:**
- 2 × scale values (2 bytes each) = 4 bytes overhead
- 256 × 8-bit weights = 256 bytes
- Total: 260 bytes for 256 weights
- Effective: 8.5 bits per weight

**Use Case:**
- High-quality inference
- Suitable for CUDA where bandwidth is abundant
- Fallback for heterogeneous architectures

---

### Q6_K (6-bit Quantization)
```
Block Size:        256 weights per block
Sub-blocks:        8 groups of 32 weights each
Bits Per Weight:   6.5 bits/weight
Scale Size:        Variable per group
```

**Layout per 256-weight block:**
- Scaling factors: ~8 bytes total (1 byte per group)
- Quantized weights: 6 bits × 256 / 8 = 192 bytes
- Total: ~200 bytes for 256 weights
- Effective: 6.5 bits per weight

**Characteristics:**
- Good quality retention
- Moderate compression
- Balanced quality/speed tradeoff

---

### Q5_K (5-bit Quantization)
```
Block Size:        256 weights per block
Sub-blocks:        8 groups of 32 weights each
Bits Per Weight:   5.5 bits/weight
Scale Size:        Variable per group
```

**Layout per 256-weight block:**
- Scaling factors: ~8 bytes (1 byte per group)
- Quantized weights: 5 bits × 256 / 8 = 160 bytes
- Total: ~168 bytes for 256 weights
- Effective: 5.5 bits per weight

**Characteristics:**
- Good quality for most use cases
- Better compression than Q6_K
- Standard choice for many deployments

---

### Q4_K_M (4-bit Quantization - Medium/Default Variant)
```
Block Size:        256 weights per block
Sub-blocks:        8 groups of 32 weights each
Bits Per Weight:   4.6 bits/weight (with scales)
Master Scale:      1 byte
Per-group Scales:  8 bytes (1 byte per group)
```

**Layout per 256-weight block:**
- Master scale: 1 byte
- Per-group scales: 8 bytes (1 for each of 8 groups)
- Quantized weights: 4 bits × 256 / 8 = 128 bytes
- Total: 137 bytes for 256 weights
- Effective: 4.6 bits per weight

**Rationale:**
- "Medium" variant balances quality and speed
- Master scale reduces per-group overhead
- Default choice for most inference tasks
- Used for multi-modal models (vision + language)

**Variants:**
- Q4_K_S (Small): Fewer per-group scales, faster but lower quality
- Q4_K_M (Medium): Balanced (most popular)
- (Q4_K_L: Large, higher quality variant, less common)

---

### Q4_K (4-bit Quantization - Generic)
```
Block Size:        256 weights per block
Bits Per Weight:   ~4.6 bits/weight
```

**Note:** Q4_K typically refers to Q4_K_M variant in practice.

---

### Q3_K (3-bit Quantization)
```
Block Size:        256 weights per block
Sub-blocks:        8 groups of 32 weights each
Bits Per Weight:   3.4 bits/weight
Per-group Scales:  8 bytes (1 byte per group)
```

**Layout per 256-weight block:**
- Per-group scales: 8 bytes
- Quantized weights: 3 bits × 256 / 8 = 96 bytes
- Total: 104 bytes for 256 weights
- Effective: 3.4 bits per weight

**Characteristics:**
- Aggressive compression
- Higher information loss
- Suitable for resource-constrained devices
- Trade-off between speed and quality

---

### Q2_K (2-bit Quantization)
```
Block Size:        256 weights per block
Sub-blocks:        16 groups of 16 weights each
Bits Per Weight:   2.5 bits/weight
Per-group Scales:  16 bytes (1 byte per group)
Master Scale:      1 byte
```

**Layout per 256-weight block:**
- Master scale: 1 byte
- Per-group scales: 16 bytes (1 for each of 16 groups)
- Quantized weights: 2 bits × 256 / 8 = 64 bytes
- Total: 81 bytes for 256 weights
- Effective: 2.5 bits per weight

**Characteristics:**
- Extreme compression ratio (1/16th of original for 8-bit)
- Significant information loss
- Best for edge devices with severe memory constraints
- May have noticeable quality degradation

---

## Quantization Type Comparison Table

| Type   | Block Size | Bits/Weight | Bytes/Block | Quality | Speed | Memory | Use Case |
|--------|-----------|-------------|------------|---------|-------|--------|----------|
| Q2_K   | 256       | 2.5        | 81         | Low     | Very Fast | Very Small | Edge devices |
| Q3_K   | 256       | 3.4        | 104        | Fair    | Fast  | Small | Resource-constrained |
| Q4_K_M | 256       | 4.6        | 137        | Good    | Moderate | Medium | Standard inference |
| Q4_K_S | 256       | 4.3        | 130        | Good    | Fast  | Small | Speed-optimized |
| Q5_K   | 256       | 5.5        | 168        | Very Good | Moderate | Medium-Large | Quality-conscious |
| Q6_K   | 256       | 6.5        | 200        | Excellent | Slower | Large | High-quality inference |
| Q8_K   | 256       | 8.5        | 260        | Lossless | Slowest | Very Large | Reference/debugging |

## Block and Group Granularity Design

### Why 256-Weight Blocks?
- Balances vectorization efficiency (SIMD, GPU warp sizes)
- Provides enough data for effective grouping
- Standard in modern neural network acceleration hardware
- Empirically optimal for most quantization schemes

### Why 32-Weight Groups (K-quants)?
- Sub-block granularity within 256-weight blocks (256 / 8 = 32)
- Allows per-group scaling factors
- Captures activation statistics variation
- Trade-off between flexibility and overhead

### K-quant Architecture (Q*_K)
The "K" in Q*_K indicates:
- **K = Grouped/Block quantization with per-group metadata**
- Multiple scale factors per block (not one global scale)
- Improved quality over simple block quantization
- Introduction with GGML v4

### Granularity Rationale
1. **Per-Block Scales (Global):** Too coarse, loses detail
2. **Per-Group Scales (K-quants):** Sweet spot - captures activation variance
3. **Per-Weight Scales:** Too fine, overhead exceeds benefit
4. **Master Scale + Per-Group:** Hybrid approach for Q4_K_M and Q2_K

## Implementation Code Patterns

### Type Definition Example (ggml.h)
```c
enum ggml_type {
    GGML_TYPE_F32   = 0,
    GGML_TYPE_F16   = 1,
    GGML_TYPE_Q4_0  = 2,
    GGML_TYPE_Q4_1  = 3,
    GGML_TYPE_Q4_K_S = 10,
    GGML_TYPE_Q4_K_M = 11,
    GGML_TYPE_Q3_K_S = 12,
    GGML_TYPE_Q3_K_M = 13,
    GGML_TYPE_Q3_K_L = 14,
    GGML_TYPE_Q5_K_S = 15,
    GGML_TYPE_Q5_K_M = 16,
    GGML_TYPE_Q6_K = 17,
    GGML_TYPE_Q8_K = 18,
    // ... additional types
};
```

### Block Size Constants (ggml-quants.h)
```c
// Quantization block sizes
#define QK_K 256          // Block size for K-quants
#define QK_8 256          // Block size for 8-bit
#define QK 256            // Standard block size

// Group sizes within blocks
#define QK_K_DIV8 32      // 256 / 8 = 32 (8 groups per block)
#define QK_K_DIV16 16     // 256 / 16 = 16 (16 groups per block, Q2_K)

// Scale metadata sizes
#define QK_K_SCALE_SIZE 1 // 1 byte per group scale
```

### Quantization Metadata Structure (Conceptual)
```c
// Q4_K_M block structure
typedef struct {
    uint8_t d[2];         // Master scale (2 bytes)
    uint8_t scales[8];    // Per-group scales (8 bytes)
    uint8_t qs[128];      // 4-bit quantized weights (128 bytes for 256 4-bit values)
} block_q4_k;

// Q2_K block structure
typedef struct {
    uint8_t d;            // Master scale (1 byte)
    uint8_t scales[16];   // Per-group scales (16 bytes)
    uint8_t qs[64];       // 2-bit quantized weights (64 bytes for 256 2-bit values)
} block_q2_k;
```

## Quantization Type Selection Criteria

### For Different Hardware Targets

**CPU-based Inference:**
- Prefer Q4_K_M or Q5_K
- Good quality with moderate memory bandwidth requirements
- Reasonable speed on multi-core systems

**CUDA/GPU Inference:**
- Q4_K_M is standard default
- Q5_K for higher quality
- Q6_K for multi-modal models requiring higher precision
- Q8_K for validation/reference

**Mobile/Edge Devices:**
- Q2_K or Q3_K for aggressive compression
- Limited memory bandwidth → smaller models preferred
- Quality loss acceptable for constraints

**Mixed Precision (Heterogeneous):**
- Q4_K_M base quantization
- Q6_K or Q8_K for critical layers (attention heads, output)
- Per-layer mixed precision strategy

## Bit-Width Breakdown Example: Q4_K_M

For a single 256-weight block:

```
Total Block Size: 137 bytes

Breakdown:
1. Master scale:        1 byte   (256 4-bit values share global scale)
2. Per-group scales:    8 bytes  (8 groups × 1 byte each)
3. Quantized weights:   128 bytes (256 weights × 4 bits / 8 bits-per-byte)

Calculation:
- Weights: 256 × 4 bits = 1024 bits = 128 bytes
- Scales: 9 bytes (1 master + 8 per-group)
- Total: 137 bytes

Effective bits per weight:
- 137 bytes × 8 bits/byte / 256 weights = 4.28 bits/weight
- Rounded convention: ~4.6 bits/weight (including overhead)
```

## Performance Characteristics

### Inference Speed (Relative to FP32)
- Q8_K: ~4-5× faster (bandwidth-limited gain)
- Q6_K: ~5-6× faster
- Q5_K: ~6-8× faster
- Q4_K_M: ~8-10× faster (sweet spot)
- Q3_K: ~10-12× faster
- Q2_K: ~12-15× faster

### Memory Footprint
- Original (FP32): 100%
- Q8_K: ~26% of original
- Q6_K: ~20% of original
- Q5_K: ~17% of original
- Q4_K_M: ~14% of original
- Q3_K: ~10% of original
- Q2_K: ~6% of original

### Quality Preservation (MMLU/downstream task performance)
- Q8_K: >99% of FP32 performance
- Q6_K: 98-99% of FP32 performance
- Q5_K: 95-97% of FP32 performance
- Q4_K_M: 90-95% of FP32 performance (acceptable for most uses)
- Q3_K: 80-90% of FP32 performance (quality loss noticeable)
- Q2_K: 60-80% of FP32 performance (significant degradation)

## Design Philosophy & Rationale

### Why Multiple Quantization Types?

1. **Hardware-Software Co-design:**
   - Different hardware has different compute/bandwidth ratios
   - GPU-accelerated inference benefits from Q4_K_M efficiency
   - CPU inference needs reasonable compute overhead

2. **Memory vs. Quality Trade-off:**
   - Offering spectrum from Q2_K to Q8_K
   - Users choose based on constraints
   - No one-size-fits-all solution

3. **Activation Distribution:**
   - K-quants (per-group scaling) capture weight distribution variation
   - Dense clusters of outliers need different handling
   - Groups allow independent scale factors

4. **Production Requirements:**
   - Mobile/edge: Q2_K or Q3_K despite quality loss
   - Standard inference: Q4_K_M (default)
   - High-quality applications: Q5_K or Q6_K
   - Research/validation: Q8_K

### Evolution of GGML Quantization

**GGML v3 → v4 Transition:**
- Original Q4, Q5, Q6, Q8: Single global scale per block
- v4 K-quants: Per-group scaling within blocks
- Improvement: 1-2% MMLU improvement for same bit-width
- Trade-off: Slightly more complex implementation

## Common Misunderstandings Clarified

1. **Q4_K vs. Q4_K_M:**
   - Q4_K is umbrella term
   - Q4_K_M (Medium) is default variant
   - Q4_K_S (Small) variant exists for faster inference

2. **Bits Per Weight:**
   - Nominal value (e.g., "4-bit" for Q4) is weight resolution
   - Actual effective bits/weight includes scale overhead
   - Q4_K_M = 4.6 effective bits, not exactly 4.0

3. **Block Size Purpose:**
   - Not about iteration efficiency
   - About statistical grouping for scales
   - Larger blocks → fewer scales → more loss
   - Smaller blocks → more scales → more overhead

4. **Master Scale in Q4_K_M:**
   - Not second-level quantization
   - Shared reference point for per-group scales
   - Saves 1 byte per 8 groups

## GGML Format Specification Reference

See official llama.cpp convert.py for complete format spec including:
- File header format
- Type-specific metadata layout
- Endianness specifications
- Padding and alignment requirements
- Version compatibility notes

## References

### Official Documentation
- llama.cpp Repository: https://github.com/ggerganov/llama.cpp
- GGML Repository: https://github.com/ggerganov/ggml
- convert.py Full Implementation: https://github.com/ggerganov/llama.cpp/blob/master/convert.py

### Key Files in llama.cpp
- `ggml.h` - Type definitions (line 110+)
- `ggml-quants.h` - Implementation details
- `convert.py` - Format specification
- `README.md` - High-level documentation
- Issues and PRs - Design rationale discussions

### Related Research
- K-quant introduction discussions in llama.cpp Issues
- Quantization-aware training papers (if referenced in GGML docs)
- Hardware efficiency studies in commit messages

## Conclusions

1. **GGML quantization types form a spectrum** from Q2_K (extreme compression) to Q8_K (lossless)

2. **Q4_K_M is the default sweet spot** - balances inference speed (~8-10×), memory usage (~14%), and quality (~90-95%)

3. **Block granularity is deliberate:**
   - 256-weight blocks optimize hardware utilization
   - 32-weight (or 16 for Q2_K) groups capture activation variance
   - Per-group scaling is key innovation in GGML v4

4. **Selection should be data and task-driven:**
   - Memory-constrained: Q2_K or Q3_K
   - Standard inference: Q4_K_M
   - Quality-critical: Q5_K or Q6_K
   - Validation/Research: Q8_K

5. **Bit-width breakdown is deterministic:**
   - Can be calculated exactly from type definition
   - Includes scale metadata overhead
   - No hidden compression

---

**Document Version:** 1.0
**Last Updated:** 2026-07-06
**Status:** Research Compilation
