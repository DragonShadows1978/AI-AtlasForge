#!/usr/bin/env python3
"""Invariants 3-6: Full precision equivalence, causal mask, sum-to-one, determinism."""
import sys
sys.path.insert(0, '/mnt/ForgeRealm/Project-Tensor')

import cupy as cp
from tensor_gpu_v2 import Tensor
from tensor_gpu_v2._core import apa_quant_attention, scaled_dot_product_attention

# ==================== INVARIANT 3 ====================
print("="*70)
print("INVARIANT 3: Full-Precision Equivalence (refine_pct=1.0)")
print("="*70)

for B, H, L, D in [(2, 4, 64, 64), (2, 4, 256, 64)]:
    print(f"\nShape {(B, H, L, D)}:")

    cp.random.seed(42)
    query_data = cp.random.randn(B, H, L, D).astype(cp.float32)
    key_data = cp.random.randn(B, H, L, D).astype(cp.float32)
    value_data = cp.random.randn(B, H, L, D).astype(cp.float32)

    # SDPA reference
    query = Tensor(query_data)
    key = Tensor(key_data)
    value = Tensor(value_data)
    sdpa_out = scaled_dot_product_attention(query, key, value)
    sdpa_data = sdpa_out.data
    sdpa_norm = float(cp.sqrt(cp.mean(sdpa_data ** 2)))

    # APA with full refinement
    query = Tensor(query_data.copy())
    key = Tensor(key_data.copy())
    value = Tensor(value_data.copy())

    apa_out = apa_quant_attention(
        query, key, value,
        bulk_bits=2,
        refine_percentile=1.0,
        is_causal=False,
        dropout_p=0.0
    )

    apa_data = apa_out.data
    diff = apa_data - sdpa_data
    mse = float(cp.mean(diff ** 2))
    relative_mse = mse / (sdpa_norm ** 2 + 1e-10)

    print(f"  MSE: {mse:.6e}")
    print(f"  Relative MSE: {relative_mse:.6e}")
    print(f"  Pass (<1e-4): {relative_mse <= 1e-4}")

# ==================== INVARIANT 4 ====================
print("\n" + "="*70)
print("INVARIANT 4: Causal Mask Independence")
print("="*70)

B, H, L, D = 1, 1, 8, 8
print(f"\nShape {(B, H, L, D)} with is_causal=True:")

cp.random.seed(42)
query_data = cp.random.randn(B, H, L, D).astype(cp.float32)
key_data = cp.random.randn(B, H, L, D).astype(cp.float32)
value_data_base = cp.random.randn(B, H, L, D).astype(cp.float32)

# Original output
query = Tensor(query_data.copy())
key = Tensor(key_data.copy())
value = Tensor(value_data_base.copy())

out_original = apa_quant_attention(
    query, key, value,
    bulk_bits=2,
    refine_percentile=0.15,
    is_causal=True,
    dropout_p=0.0
)
out_original_data = out_original.data

# Modify future values
value_data_modified = value_data_base.copy()
for i in range(L):
    for k in range(i+1, L):
        value_data_modified[:, :, k, :] = cp.random.randn(D).astype(cp.float32)

# Modified output
query = Tensor(query_data.copy())
key = Tensor(key_data.copy())
value = Tensor(value_data_modified)

out_modified = apa_quant_attention(
    query, key, value,
    bulk_bits=2,
    refine_percentile=0.15,
    is_causal=True,
    dropout_p=0.0
)
out_modified_data = out_modified.data

diff = cp.abs(out_original_data - out_modified_data)
max_diff = float(cp.max(diff))

print(f"  Max difference (original vs modified V): {max_diff:.6e}")
print(f"  Pass (<1e-5): {max_diff < 1e-5}")

# ==================== INVARIANT 5 ====================
print("\n" + "="*70)
print("INVARIANT 5: Softmax Sum-to-One (V=ones check)")
print("="*70)

B, H, L, D = 2, 4, 64, 64
print(f"\nShape {(B, H, L, D)} with V=ones:")

cp.random.seed(42)
query_data = cp.random.randn(B, H, L, D).astype(cp.float32)
key_data = cp.random.randn(B, H, L, D).astype(cp.float32)
value_data = cp.ones((B, H, L, D), dtype=cp.float32)

query = Tensor(query_data)
key = Tensor(key_data)
value = Tensor(value_data)

output = apa_quant_attention(
    query, key, value,
    bulk_bits=2,
    refine_percentile=0.15,
    is_causal=False,
    dropout_p=0.0
)

output_data = output.data
expected = cp.ones_like(output_data)
diff = cp.abs(output_data - expected)
max_diff = float(cp.max(diff))
mean_diff = float(cp.mean(diff))

print(f"  Max deviation from ones: {max_diff:.6e}")
print(f"  Mean deviation from ones: {mean_diff:.6e}")
print(f"  Pass (<1e-3): {max_diff < 1e-3}")

# ==================== INVARIANT 6 ====================
print("\n" + "="*70)
print("INVARIANT 6: Determinism (10 repeated calls)")
print("="*70)

B, H, L, D = 2, 4, 64, 64
print(f"\nShape {(B, H, L, D)} with dropout_p=0:")

cp.random.seed(42)
query_data = cp.random.randn(B, H, L, D).astype(cp.float32)
key_data = cp.random.randn(B, H, L, D).astype(cp.float32)
value_data = cp.random.randn(B, H, L, D).astype(cp.float32)

outputs = []
for i in range(10):
    query = Tensor(query_data.copy())
    key = Tensor(key_data.copy())
    value = Tensor(value_data.copy())

    output = apa_quant_attention(
        query, key, value,
        bulk_bits=2,
        refine_percentile=0.15,
        is_causal=False,
        dropout_p=0.0
    )
    outputs.append(output.data)

max_variance = 0.0
for i in range(1, len(outputs)):
    diff = cp.abs(outputs[0] - outputs[i])
    max_diff = float(cp.max(diff))
    max_variance = max(max_variance, max_diff)

print(f"  Max variance across 10 calls: {max_variance:.6e}")
print(f"  Pass (==0 or <1e-5): {max_variance == 0.0 or max_variance < 1e-5}")
