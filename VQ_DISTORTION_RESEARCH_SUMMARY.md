# Vector Quantization Clustering & Distortion Minimization Research

## Overview
This research compiles mathematical theory, algorithms, and implementations for Vector Quantization (VQ) clustering with focus on distortion minimization through K-means and related algorithms.

## Key Mathematical Frameworks

### 1. Basic Distortion Definition
**Standard MSE Distortion:**
```
D = E[||X - Q^(-1)(Q(X))||_2²]
  = Σ_i ||x_i - μ_i||²  (sum of squared distances to nearest centroids)
```

Where:
- X: input vector
- Q: quantization function
- Q^(-1): dequantization/reconstruction function
- μ_i: centroid of cluster i
- d: number of dimensions

### 2. Advanced Distortion Metrics

**Inner Product Distortion** (TurboQuant, 2025):
```
D_prod(Q) = E[|⟨y, x⟩ - ⟨y, Q^(-1)(Q(x))⟩|²]
         ≤ (√3·π²·||y||_2²/d)·(1/4^b)
```

**Entropy-Constrained Distortion** (RDVQ, 2026):
```
minimize D + λ·R
where D = expected squared error
      R = entropy (bitrate constraint)
      λ = rate-distortion tradeoff parameter
```

### 3. Lloyd's Optimality Conditions
Any optimal quantizer must satisfy:

1. **Nearest-Neighbor Condition:** Each input vector assigned to nearest centroid
   ```
   For x in partition S_i: d(x, μ_i) ≤ d(x, μ_j) for all j ≠ i
   ```

2. **Centroid Condition:** Each centroid is the mean of its partition
   ```
   μ_i = E[X | X ∈ S_i]  (conditional expectation)
   ```

These conditions form the basis for iterative Lloyd/K-means algorithms.

## Clustering Algorithms

### K-means / Lloyd Algorithm
**Iterative Process:**
1. Initialize k centroids
2. Assign each vector to nearest centroid (nearest-neighbor condition)
3. Recalculate centroids as arithmetic mean of assigned vectors (centroid condition)
4. Repeat steps 2-3 until convergence (distortion change < threshold)

**Convergence:** Monotonic decrease in distortion with guaranteed termination at local minimum

### Linde-Buzo-Gray (LBG) Algorithm
**Extension of Lloyd for codebook design:**
1. Start with single centroid = mean of all data
2. Use "splitting method": double codebook by adding perturbations
3. Run Lloyd algorithm on enlarged codebook
4. Repeat until desired codebook size reached

**Advantages:**
- Hierarchical codebook growth avoids poor initialization
- Reduces dependence on initial centroid choice
- Commonly used for VQ in speech/image compression

### Lloyd-Max Algorithm
**Specialized for scalar quantization:**
- Optimizes decision thresholds and representation levels iteratively
- Minimizes MSE for univariate distributions
- Extended version: entropy-constrained Lloyd-Max for rate control

## Modern Approaches

### TurboQuant (2025)
**Key Innovation:** Data-oblivious online quantization with near-optimal distortion rates

**Mathematical Foundation:**
- Random rotation induces Beta distribution on coordinates
- In high dimensions, coordinates become nearly independent
- Apply optimal 1D scalar quantizers per coordinate

**Distortion Bounds:**
```
MSE: D_mse ≤ (√3·π/2)·(1/4^b)           [upper bound]
     D_mse ≥ (1/4^b)                    [information-theoretic lower bound]
     Gap factor: 2.7x at high bitwidths, 1.45x at b=1
```

**Algorithm:**
1. Random rotation of input vectors
2. Apply optimal Lloyd-Max per coordinate (scalar quantization)
3. For inner product: apply QJL transform on residual for unbiasedness

### RDVQ (2026)
**Rate-Distortion Vector Quantization:**
- Joint optimization of representation learning + entropy modeling
- Differentiable relaxation of codebook distribution
- Entropy-constrained VQ formulation
- Enables end-to-end training with rate control

## Theoretical Foundations

### Shannon's Source Coding Theory
Vector quantization rooted in Shannon's distortion-rate theory:
```
D(R) = minimum achievable distortion at rate R bits
R(D) = minimum bitrate for distortion D
```

### High-Resolution Theory
At high bitrates (many quantization levels), Bennett's formula approximates distortion:
```
D ≈ (12·σ²)/(2^(2R))  (for scalar quantizers)
```

### Voronoi Partitioning
VQ partitions space into Voronoi cells:
- Each cell S_i = {x : d(x,μ_i) ≤ d(x,μ_j) for all j}
- Partition determined by nearest-neighbor rule
- Optimal partition minimizes expected distortion

## Key Distance Metrics

### Euclidean Distance (L2 norm)
```
d(x, μ) = ||x - μ||² = Σ(x_i - μ_i)²
```
- Results in spherical clusters
- Optimal center = arithmetic mean
- Most common in K-means

### Weighted Euclidean
```
d(x, μ) = (x - μ)^T · W · (x - μ)
```
- Allows dimension-weighted importance
- Useful for non-uniform feature scaling

### Normalized Dot Product
```
d(x, μ) = x^T·y / (||x||·||y||)
```
- Measures cosine similarity
- Suitable for normalized embeddings

## Applications

### Data Compression
- **Image**: VQ for color palette reduction (e.g., 24-bit to 8-bit)
- **Audio**: Speech coding, audio compression (CELP, G.729)
- **Video**: Earlier codecs (Cinepak, Sorenson SVQ) before motion compensation became standard

### Machine Learning Quantization
- **KV Cache Compression**: LLM inference with reduced memory/latency
- **Model Compression**: Weight/activation quantization preserving inner products
- **Nearest Neighbor Search**: Product quantization in vector databases

### Pattern Recognition
- **Speaker Recognition**: Multiple codebooks per user class
- **Speech Recognition**: Acoustic modeling
- **Biometric Authentication**: Efficient nearest neighbor classification

## Convergence Properties

### K-means Convergence Guarantees
1. **Monotonic Convergence:** Distortion decreases with each iteration
2. **Local Optimality:** Converges to local minimum (not guaranteed global)
3. **Termination:** Guaranteed to terminate in finite iterations
4. **Rate:** Linear convergence in theory, often faster in practice

### Limitations
- Sensitive to initialization (addressed by LBG hierarchical approach)
- Non-convex optimization (multiple local minima exist)
- No guarantee of global optimum
- Initialization strategies: random, k-means++, LBG splitting

## Summary of Sources

| Source | Year | Focus | Key Contribution |
|--------|------|-------|-----------------|
| Wikipedia (Gray, 1980s) | 2024 | VQ basics | Foundational concepts, Voronoi partitioning |
| SciPy v1.18 | 2024 | K-means implementation | Practical algorithms, convergence |
| TurboQuant (arXiv) | 2025 | Online VQ | Near-optimal distortion rates, bit-width bounds |
| RDVQ (arXiv) | 2026 | Rate-distortion VQ | Entropy-constrained optimization |
| Gray's IEEE IT paper | 1998 | Quantization theory | Lloyd's optimality conditions, high-resolution theory |
| MIT OpenCourseWare | 2003 | VQ tutorial | Distance metrics, practical applications |

## Mathematical Notation Reference

- **x, X**: input vector(s)
- **μ_i, c_i**: centroid of cluster i
- **Q**: quantization map
- **Q^(-1)**: dequantization map
- **d(·,·)**: distance metric (usually Euclidean)
- **D**: distortion (expected error)
- **R**: rate/bitrate in bits
- **b**: bits per coordinate
- **S_i**: partition/cluster i (Voronoi cell)
- **E[·]**: expectation operator
- **||·||**: norm (L2 Euclidean unless specified)
- **⟨·,·⟩**: inner product

## References for Further Study

1. **Shannon, 1959** - Rate-distortion theory foundations
2. **Lloyd, 1957** - Original quantization algorithm (Method I)
3. **Max, 1960** - Scalar quantization (Method II/Lloyd-Max)
4. **Linde, Buzo, Gray, 1980** - LBG algorithm for VQ
5. **Gray, 1984** - Comprehensive VQ survey (IEEE ASSP Magazine)
6. **Zador, 1963** - High-resolution quantization theory
7. **Gersho, 1979** - Vector quantization foundations, lattice VQ
8. **Cover & Thomas, 2006** - Information theory textbook with rate-distortion chapter
