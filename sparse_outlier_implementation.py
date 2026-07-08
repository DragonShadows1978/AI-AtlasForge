#!/usr/bin/env python3
"""
Sparse Outlier Matrix Storage: Implementation Reference
Demonstrates different index map formats and performance characteristics.
"""

import numpy as np
import time
from typing import Tuple, Dict, Optional
from enum import Enum


class IndexFormat(Enum):
    """Index map storage formats."""
    DENSE_MASK = "dense_mask"
    COO = "coo"
    CSR = "csr"
    HASH_TABLE = "hash_table"


class SparseOutlierMatrix:
    """Base class for sparse outlier storage."""

    def __init__(self, m: int, n: int, sparsity: float, outlier_format: IndexFormat):
        self.m = m
        self.n = n
        self.sparsity = sparsity
        self.outlier_format = outlier_format
        self.nnz_outliers = int(m * n * sparsity)

    def estimate_memory_bytes(self) -> Dict[str, int]:
        raise NotImplementedError

    def get_outlier(self, i: int, j: int) -> Optional[float]:
        raise NotImplementedError

    def reconstruct(self, quantized_matrix: np.ndarray) -> np.ndarray:
        raise NotImplementedError


class DenseMaskOutliers(SparseOutlierMatrix):
    """Dense bitmap for outlier indexing - O(1) lookup."""

    def __init__(self, m: int, n: int, sparsity: float):
        super().__init__(m, n, sparsity, IndexFormat.DENSE_MASK)
        self.mask = np.zeros((m, n), dtype=bool)
        self.values = {}

        outlier_count = self.nnz_outliers
        outlier_positions = np.random.choice(m * n, size=outlier_count, replace=False)
        for pos in outlier_positions:
            i, j = divmod(pos, n)
            self.mask[i, j] = True
            self.values[(i, j)] = np.random.randn()

    def estimate_memory_bytes(self) -> Dict[str, int]:
        mask_bytes = (self.m * self.n + 7) // 8
        values_bytes = self.nnz_outliers * 4
        return {
            "mask": mask_bytes,
            "values": values_bytes,
            "total": mask_bytes + values_bytes
        }

    def get_outlier(self, i: int, j: int) -> Optional[float]:
        if self.mask[i, j]:
            return self.values[(i, j)]
        return None

    def reconstruct(self, quantized_matrix: np.ndarray) -> np.ndarray:
        result = quantized_matrix.copy()
        for (i, j), val in self.values.items():
            result[i, j] = val
        return result


class COOOutliers(SparseOutlierMatrix):
    """Coordinate format (COO) for sparse outliers."""

    def __init__(self, m: int, n: int, sparsity: float, use_hash: bool = False):
        super().__init__(m, n, sparsity, IndexFormat.COO)
        self.row_indices = np.zeros(self.nnz_outliers, dtype=np.uint32)
        self.col_indices = np.zeros(self.nnz_outliers, dtype=np.uint32)
        self.values = np.zeros(self.nnz_outliers, dtype=np.float32)

        outlier_positions = np.random.choice(m * n, size=self.nnz_outliers, replace=False)
        for idx, pos in enumerate(outlier_positions):
            i, j = divmod(pos, n)
            self.row_indices[idx] = i
            self.col_indices[idx] = j
            self.values[idx] = np.random.randn()

        sorted_indices = np.argsort(self.row_indices)
        self.row_indices = self.row_indices[sorted_indices]
        self.col_indices = self.col_indices[sorted_indices]
        self.values = self.values[sorted_indices]

        self.use_hash = use_hash
        if use_hash:
            self.hash_table = {
                (int(self.row_indices[i]), int(self.col_indices[i])): self.values[i]
                for i in range(self.nnz_outliers)
            }

    def estimate_memory_bytes(self) -> Dict[str, int]:
        indices_bytes = 2 * self.nnz_outliers * 4
        values_bytes = self.nnz_outliers * 4
        hash_bytes = self.nnz_outliers * 40 if self.use_hash else 0
        return {
            "indices": indices_bytes,
            "values": values_bytes,
            "hash_table": hash_bytes,
            "total": indices_bytes + values_bytes + hash_bytes
        }

    def get_outlier(self, i: int, j: int) -> Optional[float]:
        if self.use_hash:
            return self.hash_table.get((i, j), None)
        else:
            for idx in range(self.nnz_outliers):
                if self.row_indices[idx] == i and self.col_indices[idx] == j:
                    return float(self.values[idx])
            return None

    def reconstruct(self, quantized_matrix: np.ndarray) -> np.ndarray:
        result = quantized_matrix.copy()
        for idx in range(self.nnz_outliers):
            i, j = int(self.row_indices[idx]), int(self.col_indices[idx])
            result[i, j] = float(self.values[idx])
        return result


class CSROutliers(SparseOutlierMatrix):
    """CSR (Compressed Sparse Row) format for outliers."""

    def __init__(self, m: int, n: int, sparsity: float):
        super().__init__(m, n, sparsity, IndexFormat.CSR)
        self.row_ptr = np.zeros(m + 1, dtype=np.uint32)
        self.col_indices = np.zeros(self.nnz_outliers, dtype=np.uint32)
        self.values = np.zeros(self.nnz_outliers, dtype=np.float32)

        outlier_positions = np.random.choice(m * n, size=self.nnz_outliers, replace=False)
        outlier_dict = {}
        for pos in outlier_positions:
            i, j = divmod(pos, n)
            outlier_dict[(i, j)] = np.random.randn()

        idx = 0
        for i in range(m):
            self.row_ptr[i] = idx
            for j in range(n):
                if (i, j) in outlier_dict:
                    self.col_indices[idx] = j
                    self.values[idx] = outlier_dict[(i, j)]
                    idx += 1
        self.row_ptr[m] = idx

    def estimate_memory_bytes(self) -> Dict[str, int]:
        row_ptr_bytes = (self.m + 1) * 4
        col_idx_bytes = self.nnz_outliers * 4
        values_bytes = self.nnz_outliers * 4
        return {
            "row_ptr": row_ptr_bytes,
            "col_indices": col_idx_bytes,
            "values": values_bytes,
            "total": row_ptr_bytes + col_idx_bytes + values_bytes
        }

    def get_outlier(self, i: int, j: int) -> Optional[float]:
        for idx in range(self.row_ptr[i], self.row_ptr[i + 1]):
            if self.col_indices[idx] == j:
                return float(self.values[idx])
        return None

    def reconstruct(self, quantized_matrix: np.ndarray) -> np.ndarray:
        result = quantized_matrix.copy()
        for i in range(self.m):
            for idx in range(self.row_ptr[i], self.row_ptr[i + 1]):
                j = int(self.col_indices[idx])
                result[i, j] = float(self.values[idx])
        return result


class HashTableOutliers(SparseOutlierMatrix):
    """Hash table for outlier indexing."""

    def __init__(self, m: int, n: int, sparsity: float):
        super().__init__(m, n, sparsity, IndexFormat.HASH_TABLE)
        self.hash_table = {}

        outlier_positions = np.random.choice(m * n, size=self.nnz_outliers, replace=False)
        for pos in outlier_positions:
            i, j = divmod(pos, n)
            self.hash_table[(i, j)] = np.random.randn()

    def estimate_memory_bytes(self) -> Dict[str, int]:
        entry_bytes = self.nnz_outliers * 40
        return {
            "hash_table": entry_bytes,
            "total": entry_bytes
        }

    def get_outlier(self, i: int, j: int) -> Optional[float]:
        return self.hash_table.get((i, j), None)

    def reconstruct(self, quantized_matrix: np.ndarray) -> np.ndarray:
        result = quantized_matrix.copy()
        for (i, j), val in self.hash_table.items():
            result[i, j] = val
        return result


# ============================================================================
# Performance Benchmarking
# ============================================================================

def benchmark_format(fmt: SparseOutlierMatrix, name: str, num_lookups: int = 10000):
    """Benchmark random access performance."""
    print(f"\n{name}:")
    print(f"  Sparsity: {fmt.sparsity*100:.2f}%")
    print(f"  Outliers: {fmt.nnz_outliers}")

    mem = fmt.estimate_memory_bytes()
    print(f"  Memory (index): {mem.get('total', 0) / 1024:.2f} KB")

    start = time.perf_counter()
    for _ in range(num_lookups):
        i = np.random.randint(fmt.m)
        j = np.random.randint(fmt.n)
        _ = fmt.get_outlier(i, j)
    elapsed = time.perf_counter() - start

    print(f"  Lookup time ({num_lookups} accesses): {elapsed*1000:.2f} ms")
    print(f"  Per-lookup latency: {elapsed*1e6/num_lookups:.2f} µs")

    quantized = np.random.randint(-128, 128, size=(fmt.m, fmt.n), dtype=np.int8)
    start = time.perf_counter()
    result = fmt.reconstruct(quantized)
    elapsed = time.perf_counter() - start
    print(f"  Reconstruction time: {elapsed*1000:.2f} ms")


def main():
    """Run benchmarks."""
    print("=" * 80)
    print("SPARSE OUTLIER MATRIX STORAGE: BENCHMARK")
    print("=" * 80)

    test_cases = [
        (1000, 1000, 0.001, "Very sparse (0.1%)"),
        (1000, 1000, 0.01, "Sparse (1%)"),
        (1000, 1000, 0.05, "Moderate (5%)"),
    ]

    for m, n, sparsity, description in test_cases:
        print(f"\n{'=' * 80}")
        print(f"Test case: {m}×{n} matrix, {description}")
        print(f"{'=' * 80}")

        dm = DenseMaskOutliers(m, n, sparsity)
        benchmark_format(dm, "Dense Mask")

        coo = COOOutliers(m, n, sparsity, use_hash=False)
        benchmark_format(coo, "COO (Binary Search)")

        coo_hash = COOOutliers(m, n, sparsity, use_hash=True)
        benchmark_format(coo_hash, "COO (Hash Table)")

        csr = CSROutliers(m, n, sparsity)
        benchmark_format(csr, "CSR")

        ht = HashTableOutliers(m, n, sparsity)
        benchmark_format(ht, "Hash Table")

        print(f"\nMemory Comparison:")
        print(f"  Dense mask: {dm.estimate_memory_bytes()['total'] / 1024:.2f} KB")
        print(f"  COO (binary search): {coo.estimate_memory_bytes()['total'] / 1024:.2f} KB")
        print(f"  COO (hash): {coo_hash.estimate_memory_bytes()['total'] / 1024:.2f} KB")
        print(f"  CSR: {csr.estimate_memory_bytes()['total'] / 1024:.2f} KB")
        print(f"  Hash table: {ht.estimate_memory_bytes()['total'] / 1024:.2f} KB")

    print(f"\n{'=' * 80}")
    print("Index Compression Analysis")
    print(f"{'=' * 80}")

    print("\nDelta Encoding Example:")
    indices = np.array([100, 101, 102, 105, 200, 201, 202])
    deltas = np.concatenate([[indices[0]], np.diff(indices)])
    print(f"  Original: {indices} ({len(indices) * 4} bytes)")
    print(f"  Deltas: {deltas} (varint ~{len(deltas)} bytes)")
    print(f"  Compression: {len(indices) * 4 / len(deltas):.1f}×")

    print("\nBreak-even Analysis:")
    for sparsity in [0.001, 0.01, 0.05, 0.1]:
        m, n = 10000, 10000
        total_elements = m * n
        dm_total = (total_elements + 7) // 8 + int(total_elements * sparsity) * 4
        coo_total = int(total_elements * sparsity) * 8 + int(total_elements * sparsity) * 4
        winner = "Dense mask" if dm_total < coo_total else "COO"
        print(f"  {sparsity*100:.2f}%: DM {dm_total/1024:.1f}KB vs COO {coo_total/1024:.1f}KB → {winner}")


if __name__ == "__main__":
    main()
