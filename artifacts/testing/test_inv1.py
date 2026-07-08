#!/usr/bin/env python3
"""Invariant 1: Refinement Fraction Calculation."""
import sys
sys.path.insert(0, '/mnt/ForgeRealm/Project-Tensor')

print("="*70)
print("INVARIANT 1: Refinement Fraction Calculation")
print("="*70)

test_configs = [(2, 4, 128, 64), (2, 4, 256, 64), (2, 4, 1024, 64)]
refine_percentile = 0.15

for B, H, L, D in test_configs:
    S = L
    refine_k = max(1, int(S * refine_percentile))
    actual_fraction = refine_k / S
    expected_fraction = refine_percentile
    within_1pct = abs(actual_fraction - expected_fraction) <= 0.01

    print(f"\nShape {(B,H,L,D)}: S={S}, refine_k={refine_k}")
    print(f"  Target: {expected_fraction*100:.2f}%, Actual: {actual_fraction*100:.2f}%")
    print(f"  Within 1% tolerance: {within_1pct}")
