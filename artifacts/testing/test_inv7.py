#!/usr/bin/env python3
"""Invariant 7: Gradient Flow."""
import sys
sys.path.insert(0, '/mnt/ForgeRealm/Project-Tensor')

import cupy as cp
from tensor_gpu_v2 import Tensor
from tensor_gpu_v2._core import apa_quant_attention

print("="*70)
print("INVARIANT 7: Gradient Flow")
print("="*70)

B, H, L, D = 2, 4, 64, 64
print(f"\nShape {(B, H, L, D)}:")

cp.random.seed(42)
query_data = cp.random.randn(B, H, L, D).astype(cp.float32)
key_data = cp.random.randn(B, H, L, D).astype(cp.float32)
value_data = cp.random.randn(B, H, L, D).astype(cp.float32)

query = Tensor(query_data)
query.grad = cp.zeros_like(query_data)
key = Tensor(key_data)
key.grad = cp.zeros_like(key_data)
value = Tensor(value_data)
value.grad = cp.zeros_like(value_data)

output = apa_quant_attention(
    query, key, value,
    bulk_bits=2,
    refine_percentile=0.15,
    is_causal=False,
    dropout_p=0.0
)

output.grad = cp.ones_like(output.data)

if hasattr(output, '_backward') and output._backward:
    output._backward()

grad_q_norm = float(cp.linalg.norm(query.grad)) if query.grad is not None else 0.0
grad_k_norm = float(cp.linalg.norm(key.grad)) if key.grad is not None else 0.0
grad_v_norm = float(cp.linalg.norm(value.grad)) if value.grad is not None else 0.0

print(f"  Query gradient norm: {grad_q_norm:.6e}")
print(f"  Key gradient norm: {grad_k_norm:.6e}")
print(f"  Value gradient norm: {grad_v_norm:.6e}")

all_finite = (
    cp.all(cp.isfinite(query.grad)) and
    cp.all(cp.isfinite(key.grad)) and
    cp.all(cp.isfinite(value.grad))
)

any_nonzero = (grad_q_norm > 0) or (grad_k_norm > 0) or (grad_v_norm > 0)

print(f"\n  All gradients finite: {all_finite}")
print(f"  Any gradient non-zero: {any_nonzero}")
print(f"  Pass: {all_finite and any_nonzero}")
