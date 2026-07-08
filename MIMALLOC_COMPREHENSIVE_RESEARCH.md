# Mimalloc Memory Allocator: Comprehensive Technical Research

## Executive Summary

Mimalloc is a modern memory allocator developed by Microsoft Research that combines several innovative design decisions to achieve superior performance, bounded worst-case latencies, and excellent scalability for multi-threaded applications. The allocator is particularly optimized for reference-counting languages like Lean and Koka but provides general-purpose performance benefits.

**Key Performance Claims:**
- 7% faster than tcmalloc on redis
- 14% faster than jemalloc on redis
- Consistently outperforms across sequential and concurrent benchmarks (as of Jan 2021)
- Usually uses less memory than competitors (up to 25% more in worst case)
- Core library: ~10k LOC with simple, consistent data structures

---

## 1. Thread-Local Bin Organization and Strategy

### 1.1 Bin Architecture

Mimalloc uses **73 size classes (bins)** spaced exponentially in 12.5% increments for object size classification:
- Bins enable efficient grouping of allocations by size
- Maximum bin: `MI_BIN_HUGE = 73U`
- Size classes prevent fragmentation through predictable categorization

### 1.2 Three-Level Free List Sharding Strategy

The critical innovation in mimalloc is **multi-level free list sharding** to enable lock-free allocation without contentious atomic operations:

```c
// From mimalloc/types.h - mi_page_t structure
typedef struct mi_page_s {
  mi_block_t*           free;        // list of available free blocks (allocatable)
  mi_block_t*           local_free;  // list of deferred free blocks by this thread (migrates to free)
  uint16_t              used;        // number of blocks in use
  _Atomic(mi_thread_free_t) xthread_free;  // list of deferred free blocks freed by OTHER threads
  // ... additional metadata
} mi_page_t;
```

#### Free List Components:

1. **`free` list**: Blocks available for allocation
   - Accessed during malloc
   - Thread-local fast path

2. **`local_free` list**: Freed blocks deferred from immediate use
   - Created during free operations within the same thread
   - Migrates to `free` list when it's exhausted
   - Enables **monotonic heartbeat** semantics

3. **`xthread_free` list**: Blocks freed by OTHER threads
   - Atomic access with CAS (Compare-And-Swap)
   - Single atomic operation per cross-thread free
   - Avoids sophisticated coordination between threads
   - Uses bottom 2 bits for `mi_delayed_t` flags to track first arrival

#### Delayed Free Flags (mi_delayed_t):
```c
typedef enum mi_delayed_e {
  MI_USE_DELAYED_FREE   = 0, // push on owning heap thread delayed list
  MI_DELAYED_FREEING    = 1, // temporary: another thread is accessing heap
  MI_NO_DELAYED_FREE    = 2, // optimize: push on page local thread free queue
  MI_NEVER_DELAYED_FREE = 3  // sticky: used for abandoned pages
} mi_delayed_t;
```

### 1.3 Thread-Local Heap Strategy

```c
typedef struct mi_tld_s {
  unsigned long long  heartbeat;     // monotonic heartbeat count
  bool                recurse;       // prevent infinite recursion
  mi_heap_t*          heap_backing;  // backing heap (cannot be deleted)
  mi_heap_t*          heaps;         // list of heaps in thread
  mi_segments_tld_t   segments;      // segment thread-local data
  mi_stats_t          stats;         // statistics
} mi_tld_t;
```

**Key Design Aspects:**
- Each thread has a default heap and can create multiple heaps
- Heaps can only allocate from the owning thread (allocation is thread-local)
- Freeing can occur from any thread (deferred)
- Per-thread, segments are shared among heaps
- Monotonic heartbeat enables predictable maintenance tasks

### 1.4 Page Queue Organization

```c
typedef struct mi_page_queue_s {
  mi_page_t* first;
  mi_page_t* last;
  size_t     block_size;
} mi_page_queue_t;

// In mi_heap_s:
mi_page_queue_t pages[MI_BIN_FULL + 1];  // queue per size class + full queue
mi_page_t*      pages_free_direct[MI_PAGES_DIRECT];  // optimization array
```

Pages for each size class (bin) are held in a doubly-linked queue for efficient management.

---

## 2. 16MB Segment/Page Allocation Strategy (Actually 4MiB)

### 2.1 Segment Architecture

Segments are large memory blocks allocated from the OS:
- **Segment size**: 4 MiB (not 16 MB as commonly misunderstood)
- **Segment shift**: `MI_SEGMENT_SHIFT = MI_LARGE_PAGE_SHIFT = 22` bits (on 64-bit)
- Multiple segments per thread for scalability

```c
typedef struct mi_segment_s {
  mi_memid_t           memid;             // memory provenance tracking
  bool                 allow_decommit;
  bool                 allow_purge;
  size_t               segment_size;      // usually 4 MiB
  
  struct mi_segment_s* next;
  struct mi_segment_s* prev;
  bool                 was_reclaimed;
  bool                 dont_free;
  
  size_t               abandoned;         // abandoned pages count
  size_t               abandoned_visits;  // reclaim attempt counter
  
  size_t               used;              // pages in use
  size_t               capacity;          // total pages available
  size_t               segment_info_size; // meta-data overhead
  
  _Atomic(mi_threadid_t) thread_id;       // owning thread
  size_t               page_shift;        // page size = 1 << page_shift
  mi_page_kind_t       page_kind;         // small/medium/large/huge
  mi_page_t            pages[1];          // flexible array of pages
} mi_segment_t;
```

### 2.2 Page Size Hierarchy

Three primary page categories within segments:

| Page Type | Size | Allocation | Object Size | Pages/Segment |
|-----------|------|-----------|-------------|---------------|
| Small | 64 KiB | 64-bit: `13+3=16 bits` | ≤ 8 KiB | 64 pages |
| Medium | 512 KiB | 64-bit: `16+3=19 bits` | ≤ 64 KiB | 8 pages |
| Large | 4 MiB | 64-bit: `22 bits` | ≤ 1 MiB | 1 page |
| Huge | Variable | Variable | > 1 MiB | Special |

**Configuration:**
```c
#define MI_SMALL_PAGE_SHIFT   (13 + MI_INTPTR_SHIFT)     // 64KiB
#define MI_MEDIUM_PAGE_SHIFT  (3 + MI_SMALL_PAGE_SHIFT)  // 512KiB
#define MI_LARGE_PAGE_SHIFT   (3 + MI_MEDIUM_PAGE_SHIFT) // 4MiB
#define MI_SEGMENT_SHIFT      MI_LARGE_PAGE_SHIFT        // 4MiB
```

### 2.3 Object Size Limits

```c
#define MI_SMALL_OBJ_SIZE_MAX   (MI_SMALL_PAGE_SIZE/8)    // 8 KiB
#define MI_MEDIUM_OBJ_SIZE_MAX  (MI_MEDIUM_PAGE_SIZE/8)   // 64 KiB
#define MI_LARGE_OBJ_SIZE_MAX   (MI_LARGE_PAGE_SIZE/4)    // 1 MiB
```

Maximum object sizes chosen to prevent internal fragmentation > 12.5%.

### 2.4 Memory Allocation Provenance Tracking

```c
typedef enum mi_memkind_e {
  MI_MEM_NONE,        // not allocated
  MI_MEM_EXTERNAL,    // externally provided
  MI_MEM_STATIC,      // static allocation
  MI_MEM_OS,          // direct OS allocation
  MI_MEM_OS_HUGE,     // huge OS pages (1 GiB, pinned)
  MI_MEM_OS_REMAP,    // remappable area (mremap)
  MI_MEM_ARENA        // arena allocated (usual case)
} mi_memkind_t;

typedef struct mi_memid_s {
  union {
    mi_memid_os_info_t    os;       // OS allocation info
    mi_memid_arena_info_t arena;    // arena allocation info
  } mem;
  bool          is_pinned;          // cannot decommit (huge pages)
  bool          initially_committed;
  bool          initially_zero;
  mi_memkind_t  memkind;
} mi_memid_t;
```

This enables sophisticated memory management strategies including arena allocation, huge page support, and deferred decommit.

---

## 3. Free List Management and Coalescing Algorithms

### 3.1 Free List Data Structure

```c
typedef struct mi_block_s {
  mi_encoded_t next;  // encoded next pointer (for security)
} mi_block_t;

typedef uintptr_t mi_encoded_t;  // encodes free list for heap integrity
```

Blocks are linked individually; coalescing is handled at the page level, not block level.

### 3.2 Free List Sharding Strategy

The fundamental principle: **Instead of one big free list per size class, have many smaller lists per page**.

**Benefits:**
1. **Reduced fragmentation**: Blocks allocated close in time go to same page
2. **Increased locality**: Working set remains in cache
3. **Lower contention**: Distributed contention over many pages
4. **Lock-free fast path**: Most frees don't require atomic operations

### 3.3 Page-Level Coalescing (Not Block-Level)

Mimalloc does NOT coalesce adjacent blocks within a page. Instead:

1. **Pages become empty and are freed**: When all blocks in a page are freed
2. **Eager page purging**: Empty pages are reset/decommitted to OS
3. **Abandoned page reclamation**: Pages abandoned by terminated threads are reclaimed

```c
// Page state tracking for emptiness
uint16_t capacity;      // committed blocks
uint16_t reserved;      // reserved blocks
uint16_t used;          // blocks in use (including xthread_free)
```

**Invariant:**
```
used - |thread_free| = actual live blocks
used - |thread_free| + |free| + |local_free| = capacity
```

### 3.4 Lazy Free List Migration

The three-level structure enables lazy migration:

1. **Fast path**: Thread frees block → added to `local_free`
2. **Deferred**: On next allocation from `free` list exhaustion → migrate `local_free` to `free`
3. **Cross-thread**: Another thread frees → CAS to `xthread_free`
4. **Eventual**: When needed → migrate `xthread_free` to `free`

**This implements "temporal cadence"**: predictable maintenance tasks at well-defined points.

### 3.5 Encoded Free Lists (Security Feature)

```c
#if (MI_SECURE>=3 || MI_DEBUG>=1)
#define MI_ENCODE_FREELIST  1
#endif

// In mi_page_t:
uintptr_t keys[2];  // random keys for encoding
```

When enabled, free list pointers are XOR-encoded with random keys to detect:
- Buffer overflows (corrupted free lists)
- Use-after-free
- Double-free

---

## 4. Comparison with Jemalloc Design Trade-offs

### 4.1 Architectural Comparison

| Aspect | Mimalloc | Jemalloc |
|--------|----------|----------|
| **Bin Strategy** | 73 exponential bins | Run-size classes |
| **Lock Strategy** | Lock-free with CAS + deferred frees | Per-arena locks |
| **Thread Binding** | Thread-local heaps + shared segments | Per-thread arena |
| **Free List** | 3-level sharded (per-page) | Per-bin free lists |
| **Coalescing** | Page-level only | Block-level |
| **Page Size** | 64 KiB small, 512 KiB medium | Typically 4 KiB OS pages |
| **Decommit Strategy** | Eager page purging | Background decommit |
| **Contention Model** | Distributed (O(thousands)) | Centralized (O(tens)) |

### 4.2 Performance Implications

**Mimalloc Advantages:**
- **Lock-free main path**: No mutex overhead in fast path
- **Lower tail latencies**: No queue for locks; predictable execution
- **Better cache locality**: Temporal allocation grouping
- **Reduced contention**: Thousands of free lists instead of tens
- **Memory efficiency**: Eager purging reduces fragmentation

**Jemalloc Advantages:**
- **Mature ecosystem**: Widely deployed, battle-tested
- **Predictable behavior**: Well-understood lock contention patterns
- **Simplicity**: Easier to reason about thread-arena mapping
- **Fragmentation control**: More sophisticated run coalescing
- **Production validated**: Long history of deployment

### 4.3 Contention Distribution

**Jemalloc**: Per-arena mutex (typical: 4-8 arenas for multi-core)
- N threads competing for M arenas (M << N)
- Higher contention per lock

**Mimalloc**: Distributed free list contention
- Thousands of pages × thousands of bins
- Very low probability of contention on single location
- Similar to randomized algorithms (skip lists) - random oracle removes need for complex coordination

### 4.4 Reference Counting Optimization

Mimalloc is specifically optimized for reference-counting languages:

1. **Deferred freeing**: Supports batched free operations
2. **Bounded worst-case**: Prevents garbage collection pauses
3. **Monotonic heartbeat**: Predictable RC update timing
4. **Temporal cadence**: Groups related deallocations

Jemalloc lacks these optimizations but provides broader general-purpose performance.

---

## 5. Performance Characteristics and Benchmarks

### 5.1 Official Benchmark Results (January 2021)

**Claims from Microsoft:**
- Always outperforms leading allocators: jemalloc, tcmalloc, Hoard
- Redis benchmarks:
  - 7% faster than tcmalloc
  - 14% faster than jemalloc
- Consistently performs well across wide range of benchmarks
- Usually uses less memory (worst case: 25% more)

### 5.2 Fast Path Characteristics

**Allocation Fast Path (7 instructions with single test in release mode):**

```c
// From alloc.c: _mi_page_malloc_zero
mi_block_t* const block = page->free;
if mi_unlikely(block == NULL) {
  return _mi_malloc_generic(...);  // slow path
}
page->free = mi_block_next(page, block);
page->used++;
// Return block with optional zeroing
```

**Allocation is:**
- Single test: `block == NULL`
- Two pointer dereferences: `page->free`, then decode next
- One increment: `page->used++`
- Minimal CPU cache effects

### 5.3 Memory Overhead

**Per-page metadata** (from mi_page_t):
- ~10 words on 64-bit systems
- Segment index, flags, capacity/reserved/used counters
- Two free lists (pointers), atomic xthread_free

**Segment metadata**:
- Minimal: tracking thread_id, page_shift, page_kind
- Unused pages tracked for reclamation

**Overall claim**: ~0.2% metadata overhead with low internal fragmentation

### 5.4 Worst-Case Latencies

Mimalloc provides **bounded worst-case allocation times (wcat)** up to OS primitives:

1. **No allocation lock contention**: CAS-based, not mutex-based
2. **Predictable page allocation**: Segment lookup in O(1)
3. **Deferred maintenance**: Maintenance happens predictably at heartbeat intervals
4. **No garbage collection**: Immediate recycling of pages

### 5.5 Concurrent Workload Performance

**Multi-threaded benchmarks** show consistent outperformance because:

1. **Reduced lock contention**: Distributed over thousands of free lists
2. **Cache-friendly**: Temporal allocation locality
3. **Thread-local fast path**: No synchronization for most operations
4. **Efficient cross-thread frees**: Single CAS operation

---

## 6. Secure Mode Implementation

Mimalloc supports multiple security levels:

```c
// From types.h
#define MI_SECURE 0     // default: no security
#define MI_SECURE 1     // guard pages around meta-data, randomize arena addresses
#define MI_SECURE 2     // randomize relative allocation addresses within pages
#define MI_SECURE 3     // encode free lists (detect overflow, UAF, double-free)
#define MI_SECURE 4     // check for double-free (more expensive)
#define MI_SECURE 5     // guard pages at end of each mimalloc page (very expensive)
```

**Security overheads**: ~10% average on secure mode

---

## 7. Versions and Evolution

### Three Maintained Versions:

**V1** (Release tags: v1.9.x, branch: dev)
- Initial design
- Recommended for PRs

**V2** (Release tags: v2.2.x, branch: dev2)
- Uses thread-local segments
- Reduces fragmentation
- Main version

**V3** (Release tags: v3.2.x, branch: dev3)
- Simplified lock-free design
- Improved memory sharing between threads
- (Much) less memory on large workloads
- True first-class heaps (allocate from any thread)
- Efficient heap-walking (for Python GC integration)

---

## 8. Key Innovations Summary

### Primary Design Innovation: Free List Multi-Sharding

Instead of traditional design:
```
Per-size-class:  free_list
    ↓
    All threads + all pages compete for one list
    ↓
    High contention, lock needed
```

Mimalloc achieves:
```
Per-page:  [free | local_free | xthread_free]
    ↓
    Thousands of lists distributed
    ↓
    Lock-free single CAS for cross-thread free
    ↓
    Natural contention distribution
```

### Supporting Innovations:

1. **Temporal cadence**: Monotonic heartbeat + deferred operations
2. **Eager page purging**: OS page reset when page empties
3. **Memory provenance tracking**: Arena/OS/static distinction
4. **Encoded free lists**: Security against heap exploitation
5. **First-class heaps**: Multiple independent heaps per thread
6. **Bounded worst-case**: No allocation time variance from contention

---

## 9. Implementation Complexity

**Simplicity Claim: ~10,000 lines of C**

Key reason: Consistent data structures throughout:
- Simple linked lists (not complex trees)
- Straightforward page management
- Clear thread-local vs. shared separation
- Minimal special cases

This simplicity enables:
- Easy integration into other projects
- Portability to many systems
- Security analysis
- Adaptation for specialized workloads

---

## 10. Real-World Deployment

Mimalloc is used in production by:
- **Koka language runtime**: Reference-counting backend
- **Lean theorem prover**: Functional language runtime
- **Large-scale distributed services**: Thousands of machines
- **Multiple OS platforms**: Windows, macOS, Linux, BSD, WASM, Haiku, MUSL

Excellent worst-case latencies for services requiring predictable response times.

---

## 11. API and Integration

### Quick Integration Example:
```bash
# Unix: use as drop-in replacement
LD_PRELOAD=/usr/bin/libmimalloc.so myprogram
```

### First-Class Heap API (V3+):
```c
// Create independent heap
mi_heap_t* heap = mi_heap_new();

// Allocate from specific heap from any thread
void* p = mi_heap_malloc(heap, size);

// Destroy entire heap at once
mi_heap_destroy(heap);
```

### Statistics and Introspection:
- `mi_heap_destroy`: Full heap destruction
- `mi_heap_stats`: Heap statistics collection
- Heap walking support for garbage collection integration
- Environment variable configuration (MI_ prefixed options)

---

## Summary Table

| Property | Value/Description |
|----------|-------------------|
| **Core Size** | ~10k LOC |
| **Primary Innovation** | 3-level free list sharding per page |
| **Page Sizes** | 64 KiB, 512 KiB, 4 MiB, Huge |
| **Segment Size** | 4 MiB |
| **Size Classes** | 73 bins (exponential 12.5% increments) |
| **Thread Model** | Per-thread heap + shared segments |
| **Locking Model** | Lock-free + CAS-based cross-thread operations |
| **Contention Points** | Thousands (pages × bins) |
| **Metadata Overhead** | ~0.2% |
| **Decommit Strategy** | Eager page purging |
| **Security Levels** | 5 levels (none to full with guard pages) |
| **Performance vs jemalloc** | 14% faster (redis), consistent wins |
| **Production Ready** | Yes (Microsoft, Koka, Lean, large services) |

---

## References

1. **GitHub Repository**: https://github.com/microsoft/mimalloc
2. **Official Documentation**: https://microsoft.github.io/mimalloc/
3. **Microsoft Research Publication**: "mimalloc: Free-List Sharding in Action"
4. **Design Focus**: Reference-counting language runtimes (Koka, Lean)
5. **Source Files Key References**:
   - `include/mimalloc/types.h`: Data structure definitions
   - `src/alloc.c`: Allocation implementation
   - `src/free.c`: Deallocation and free list management
   - `src/segment.c`: Segment management

