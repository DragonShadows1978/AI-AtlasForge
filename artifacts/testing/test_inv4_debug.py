#!/usr/bin/env python3
"""Debug Invariant 4: Causal Mask Independence - detailed inspection."""
import sys
sys.path.insert(0, '/mnt/ForgeRealm/Project-Tensor')

import cupy as cp
from tensor_gpu_v2 import Tensor
from tensor_gpu_v2._core import apa_quant_attention

print("="*70)
print("INVARIANT 4 DEBUG: Causal Mask Implementation Check")
print("="*70)

B, H, L, D = 1, 1, 8, 8
print(f"\nDetailed test with shape {(B, H, L, D)} and is_causal=True")
print("Strategy: Set V[k] = [k] (scalar replicated) for k > i")
print("         Output[i] should only depend on V[0..i]")

cp.random.seed(42)
query_data = cp.random.randn(B, H, L, D).astype(cp.float32)
key_data = cp.random.randn(B, H, L, D).astype(cp.float32)

# Create value where each token is its index repeated
value_data = cp.zeros((B, H, L, D), dtype=cp.float32)
for k in range(L):
    value_data[:, :, k, :] = k  # V[k] = [k, k, k, ..., k]

print(f"\nV[k] = [k, k, ..., k] for k in range({L})")
print(f"Query shape: {query_data.shape}")
print(f"Key shape: {key_data.shape}")

# Run with causal mask
query = Tensor(query_data.copy())
key = Tensor(key_data.copy())
value = Tensor(value_data.copy())

output = apa_quant_attention(
    query, key, value,
    bulk_bits=2,
    refine_percentile=0.15,
    is_causal=True,
    dropout_p=0.0
)

output_data = output.data.get()  # Convert to numpy for inspection

print("\nOutput analysis:")
print("If causal mask works, output[b,h,i,:] should equal:")
print("  weighted_sum(V[0:i+1]) where weights are from softmax(Q[i]·K[0:i+1].T)")
print("\nActual output values (should be between 0 and i-1):")

for i in range(L):
    out_i = output_data[0, 0, i, 0]  # First dimension of output at position i
    print(f"  Position {i}: output[i,0] = {out_i:.4f} (expected in [0, {i}])")

    # Check if output is reasonable for causal attention
    if out_i > i:
        print(f"    WARNING: Output {out_i:.4f} > {i} (future info leak?)")

# More detailed test: Set future values very differently
print("\n" + "="*70)
print("INVARIANT 4 DEBUG: Extreme Value Test")
print("="*70)

cp.random.seed(42)
query_data = cp.random.randn(B, H, L, D).astype(cp.float32)
key_data = cp.random.randn(B, H, L, D).astype(cp.float32)

# Create two different value tensors
value_data_A = cp.ones((B, H, L, D), dtype=cp.float32) * 0.1  # Small values
value_data_B = cp.ones((B, H, L, D), dtype=cp.float32) * 100.0  # Large values

# Set future values to be different in B
for k in range(L//2, L):
    value_data_B[:, :, k, :] = 100.0

print(f"\nValue tensor A: all 0.1")
print(f"Value tensor B: [0.1 for k<{L//2}], [100.0 for k>={L//2}]")

# Run with both value tensors
query = Tensor(query_data.copy())
key = Tensor(key_data.copy())
value = Tensor(value_data_A.copy())

output_A = apa_quant_attention(
    query, key, value,
    bulk_bits=2,
    refine_percentile=0.15,
    is_causal=True,
    dropout_p=0.0
)

query = Tensor(query_data.copy())
key = Tensor(key_data.copy())
value = Tensor(value_data_B.copy())

output_B = apa_quant_attention(
    query, key, value,
    bulk_bits=2,
    refine_percentile=0.15,
    is_causal=True,
    dropout_p=0.0
)

output_A_data = output_A.data
output_B_data = output_B.data

print(f"\nComparing outputs (should be identical for i < {L//2}):")
for i in range(L//2):
    diff = float(cp.max(cp.abs(output_A_data[:, :, i, :] - output_B_data[:, :, i, :])))
    print(f"  Position {i}: max diff = {diff:.6e} (should be ~0)")
    if diff > 1e-5:
        print(f"    ERROR: Positions 0..{L//2} should be identical!")

print(f"\nComparing outputs (can differ for i >= {L//2}):")
for i in range(L//2, L):
    diff = float(cp.max(cp.abs(output_A_data[:, :, i, :] - output_B_data[:, :, i, :])))
    print(f"  Position {i}: max diff = {diff:.6e}")
