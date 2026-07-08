#!/usr/bin/env python3
"""Invariant 2: Monotonicity of MSE vs refine_percentile."""
import sys
sys.path.insert(0, '/mnt/ForgeRealm/Project-Tensor')

import cupy as cp
from tensor_gpu_v2 import Tensor
from tensor_gpu_v2._core import apa_quant_attention, scaled_dot_product_attention

print("="*70)
print("INVARIANT 2: Monotonicity of MSE")
print("="*70)

B, H, L, D = 2, 4, 64, 64
percentiles = [0.0, 0.05, 0.10, 0.15, 0.25, 0.50, 0.75, 1.0]

# Fixed seed for reproducibility
cp.random.seed(42)
query_data = cp.random.randn(B, H, L, D).astype(cp.float32)
key_data = cp.random.randn(B, H, L, D).astype(cp.float32)
value_data = cp.random.randn(B, H, L, D).astype(cp.float32)

# Reference SDPA
query = Tensor(query_data)
key = Tensor(key_data)
value = Tensor(value_data)
sdpa_out = scaled_dot_product_attention(query, key, value)
sdpa_data = sdpa_out.data

print("\nTesting refine_percentile sweep...")
mses = []

for pct in percentiles:
    query = Tensor(query_data.copy())
    key = Tensor(key_data.copy())
    value = Tensor(value_data.copy())

    apa_out = apa_quant_attention(
        query, key, value,
        bulk_bits=2,
        refine_percentile=pct,
        is_causal=False,
        dropout_p=0.0
    )

    apa_data = apa_out.data
    diff = apa_data - sdpa_data
    mse = float(cp.mean(diff ** 2))
    mses.append(mse)
    print(f"  refine_percentile={pct:4.2f}: MSE={mse:.6e}")

# Check monotonicity
is_monotonic = True
violations = []
for i in range(len(mses)-1):
    if mses[i] < mses[i+1]:
        is_monotonic = False
        violations.append((i, percentiles[i], mses[i], percentiles[i+1], mses[i+1]))

print(f"\nMonotonicity check: {'PASS' if is_monotonic else 'FAIL'}")
if violations:
    print("Violations:")
    for i, p1, m1, p2, m2 in violations:
        print(f"  pct {p1:.2f} ({m1:.6e}) -> {p2:.2f} ({m2:.6e})")
