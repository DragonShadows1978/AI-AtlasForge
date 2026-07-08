# Mimalloc: Algorithmic Deep Dive

## 1. Fast-Path Allocation Algorithm

### 1.1 Small Object Allocation (< 8 KiB)

```c
// From alloc.c: mi_heap_malloc_small_zero
static inline void* mi_heap_malloc_small_zero(
  mi_heap_t* heap, 
  size_t size, 
  bool zero, 
  size_t* usable) 
{
  // Step 1: Validation (in debug only)
  mi_assert(heap != NULL);
  mi_assert(size <= MI_SMALL_SIZE_MAX);
  
  // Step 2: Get page in O(1) constant time
  mi_page_t* page = _mi_heap_get_free_small_page(heap, size + MI_PADDING_SIZE);
  
  // Step 3: Allocate from page (7 instructions)
  void* const p = _mi_page_malloc_zero(heap, page, size + MI_PADDING_SIZE, zero, usable);
  
  return p;
}

// Fast path: _mi_page_malloc_zero
extern inline void* _mi_page_malloc_zero(
  mi_heap_t* heap, 
  mi_page_t* page, 
  size_t size, 
  bool zero, 
  size_t* usable)
{
  // STEP 1: Get block from free list
  mi_block_t* const block = page->free;
  
  // STEP 2: Test if free list empty (single branch, predicted unlikely)
  if mi_unlikely(block == NULL) {
    return _mi_malloc_generic(heap, size, zero, 0, usable);  // SLOW PATH
  }
  
  // STEP 3: Pop from free list (decode next pointer)
  page->free = mi_block_next(page, block);
  
  // STEP 4: Increment used counter
  page->used++;
  
  // STEP 5: Set usable size if requested
  if (usable != NULL) { *usable = mi_page_usable_block_size(page); }
  
  // STEP 6: Optional zeroing
  if mi_unlikely(zero) {
    // Zero the block
    _mi_memzero_aligned(block, page->block_size - MI_PADDING_SIZE);
  }
  
  // STEP 7: Return block
  return block;
}
```

**Performance Characteristics:**
- **Best case**: 7 machine instructions
  1. Load `page->free`
  2. Test for NULL
  3. Load `block->next` (decode if encoded)
  4. Store `page->free`
  5. Load `page->used`
  6. Increment and store `page->used`
  7. Return register
- **Single branch prediction**: `if mi_unlikely(block == NULL)`
- **Cache effects**: Two memory locations (page structure + block)

### 1.2 Bin Selection Algorithm

```c
// Binning: size class to bin mapping
// Uses exponential spacing in 12.5% increments

// Example bin progression:
// bin=0:   16 bytes
// bin=1:   32 bytes
// bin=2:   48 bytes
// ...
// bin=20:  512 bytes
// ...
// bin=50:  32 KiB
// ...
// bin=72:  1 MiB + (max large object size)

static inline size_t _mi_bin(size_t size) {
  // Convert size to bin index using bit operations
  // Exponential sizing: each bin ~12.5% larger than previous
}
```

**Why 73 bins?**
- Exponential spacing minimizes fragmentation
- 12.5% waste limit (1/8): `(size_next - size_current) / size_current ≈ 0.125`
- Covers range: 16 bytes to 1+ MiB
- Small enough to iterate, large enough to differentiate

### 1.3 Page Selection: O(1) Heap Lookup

```c
// From types.h: heap structure
struct mi_heap_s {
  // Direct page pointers for common small sizes (optimization)
  mi_page_t* pages_free_direct[MI_PAGES_DIRECT];
  
  // Queue of pages for each bin
  mi_page_queue_t pages[MI_BIN_FULL + 1];
};

// Page retrieval (constant time)
static inline mi_page_t* _mi_heap_get_free_small_page(
  mi_heap_t* heap, 
  size_t size)
{
  // Compute bin from size
  size_t bin = _mi_bin(size);
  
  // Get queue of pages for this bin
  mi_page_queue_t* queue = &heap->pages[bin];
  
  // Get first page with free blocks (likely cached)
  mi_page_t* page = queue->first;
  
  if (page == NULL) {
    // Allocate new page from segment
    page = _mi_page_fresh(heap, bin);
  }
  
  return page;
}
```

**Key insight**: Page lookup is O(1) because:
1. Bin computation is O(1) (bit operations)
2. Queue lookup is O(1) (cache first pointer)
3. No tree traversal needed

---

## 2. Free List Management Algorithms

### 2.1 Deallocation (mi_free) Flow

```c
// Three scenarios for free operation:

// SCENARIO 1: Free from owning thread (fast path)
void mi_free_small(void* p) {
  // Step 1: Validate pointer and get page
  mi_page_t* page = _mi_ptr_page(p);
  mi_segment_t* segment = _mi_page_segment(page);
  
  // Step 2: Check if this is the owning thread
  if (mi_thread_id() == segment->thread_id) {
    // FAST PATH: Same thread
    // Step 3: Add to local_free list
    mi_block_t* block = (mi_block_t*)p;
    block->next = page->local_free;
    page->local_free = block;
    
    // Optionally migrate local_free to free if free list empty
    if (page->free == NULL && page->local_free != NULL) {
      page->free = page->local_free;
      page->local_free = NULL;
    }
    
  } else {
    // CROSS-THREAD PATH: Different thread
    // Step 4: Atomic add to xthread_free
    mi_thread_free_t tf = (mi_thread_free_t)block | flags;
    mi_thread_free_t old;
    do {
      old = mi_atomic_load_acquire(&page->xthread_free);
      block->next = (mi_block_t*)old;
      // CAS: only one thread succeeds, adds page to owning heap's delayed list
    } while (!mi_atomic_compare_exchange(&page->xthread_free, old, tf));
    
    // First arrival adds page to owning thread's delayed free list
    if ((old & MI_NO_DELAYED_FREE) == MI_USE_DELAYED_FREE) {
      _mi_heap_delayed_free(page->heap, page);
    }
  }
}
```

### 2.2 Local Free List Migration

```c
// Called when free list is exhausted
static inline void _mi_migrate_local_free_to_free(
  mi_page_t* page)
{
  // Move all local_free blocks back to free list
  if (page->local_free != NULL) {
    // Find tail of local_free
    mi_block_t* tail = page->local_free;
    while (tail->next != NULL) {
      tail = tail->next;
    }
    
    // Append free list to tail of local_free
    tail->next = page->free;
    page->free = page->local_free;
    page->local_free = NULL;
  }
}
```

### 2.3 Cross-Thread Free List Migration

```c
// Called periodically (on heartbeat or allocation)
static inline void _mi_migrate_xthread_free_to_free(
  mi_heap_t* heap,
  mi_page_t* page)
{
  // Safely drain xthread_free using CAS loop
  mi_thread_free_t tf;
  while ((tf = mi_atomic_load_acquire(&page->xthread_free)) != NULL) {
    // Try to grab entire xthread_free list atomically
    if (mi_atomic_compare_exchange_strong(&page->xthread_free, tf, NULL)) {
      // Success: append xthread_free to free list
      mi_block_t* block = (mi_block_t*)tf;
      
      // Find tail (blocks were added in LIFO, reverse for cache)
      mi_block_t* tail = block;
      while (tail->next != NULL) {
        tail = tail->next;
      }
      
      // Append free list to tail
      tail->next = page->free;
      page->free = block;
      break;
    }
    // If CAS fails, retry (another thread added items)
  }
}
```

### 2.4 Free List Encoding (Security)

```c
#if MI_ENCODE_FREELIST

// Encode a pointer using page keys
static inline mi_encoded_t mi_ptr_encode(
  mi_page_t* page, 
  void* p)
{
  // XOR with two random keys from page
  uintptr_t x = (uintptr_t)p;
  x ^= page->keys[0];
  x ^= page->keys[1];
  return (mi_encoded_t)x;
}

// Decode a pointer
static inline void* mi_encoded_decode(
  mi_page_t* page, 
  mi_encoded_t encoded)
{
  uintptr_t x = (uintptr_t)encoded;
  x ^= page->keys[0];
  x ^= page->keys[1];
  return (void*)x;
}

// Detect corruption: wrong page or double-free
static inline mi_block_t* mi_block_next(
  mi_page_t* page,
  mi_block_t* block)
{
  mi_encoded_t e = block->next;
  mi_block_t* next = (mi_block_t*)mi_encoded_decode(page, e);
  
  // Verify next block belongs to this page
  if (next != NULL && _mi_ptr_page(next) != page) {
    mi_abort("heap corruption detected");
  }
  
  return next;
}

#endif
```

---

## 3. Segment Lifecycle Management

### 3.1 Segment Allocation

```c
// Allocate new segment from OS
static mi_segment_t* _mi_segment_os_alloc(
  size_t required,
  mi_page_kind_t page_kind,
  mi_memid_t* memid,
  mi_arena_id_t arena_id)
{
  // Step 1: Calculate total size needed
  size_t segment_size = MI_SEGMENT_SIZE;  // 4 MiB
  
  // Step 2: Allocate aligned memory from OS
  mi_segment_t* segment = (mi_segment_t*)
    _mi_os_alloc_aligned(segment_size, MI_SEGMENT_ALIGN, memid);
  
  if (segment == NULL) {
    return NULL;  // OS allocation failed
  }
  
  // Step 3: Initialize segment metadata
  segment->memid = *memid;
  segment->allow_decommit = true;
  segment->allow_purge = true;
  segment->segment_size = segment_size;
  segment->used = 0;
  segment->capacity = (page_kind == MI_PAGE_SMALL) 
    ? MI_SMALL_PAGES_PER_SEGMENT 
    : (page_kind == MI_PAGE_MEDIUM)
    ? MI_MEDIUM_PAGES_PER_SEGMENT
    : 1;  // Large/huge
  
  // Step 4: Initialize pages array (pages[0] contains segment meta)
  for (size_t i = 0; i < segment->capacity; i++) {
    _mi_page_init(segment, i);
  }
  
  return segment;
}
```

### 3.2 Page Allocation Within Segment

```c
// Allocate fresh page from segment
static mi_page_t* _mi_page_fresh(
  mi_heap_t* heap,
  size_t bin)
{
  // Step 1: Find segment with free pages
  mi_segment_queue_t* queue;
  if (bin_to_page_kind(bin) == MI_PAGE_SMALL) {
    queue = &heap->tld->segments.small_free;
  } else {
    queue = &heap->tld->segments.medium_free;
  }
  
  mi_segment_t* segment = queue->first;
  
  if (segment == NULL || segment->used >= segment->capacity) {
    // Allocate new segment
    segment = _mi_segment_os_alloc(...);
  }
  
  // Step 2: Get free page from segment
  size_t page_idx = segment->used;
  mi_page_t* page = &segment->pages[page_idx];
  
  // Step 3: Initialize page for this bin
  size_t block_size = _mi_bin_size(bin);
  page->block_size = block_size;
  page->capacity = (MI_PAGE_SIZE) / block_size;
  page->reserved = page->capacity;
  page->used = 0;
  page->free = NULL;
  page->local_free = NULL;
  
  // Step 4: Commit virtual memory if needed
  if (!page->is_committed) {
    _mi_os_commit(page->page_start, page_size);
    page->is_committed = true;
  }
  
  // Step 5: Populate free list for all blocks
  mi_block_t* blocks = (mi_block_t*)page->page_start;
  for (size_t i = 0; i < page->capacity; i++) {
    blocks[i].next = (i + 1 < page->capacity) 
      ? mi_ptr_encode(&blocks[i+1])
      : NULL;
  }
  page->free = blocks;
  
  segment->used++;
  return page;
}
```

### 3.3 Page Purging (Eager Decommit)

```c
// Called when page becomes completely free
static void _mi_page_purge(
  mi_heap_t* heap,
  mi_page_t* page,
  bool force)
{
  // Step 1: Verify page is actually empty
  if (page->used > 0 || page->local_free != NULL) {
    return;  // Not empty yet
  }
  
  // Step 2: Check if cross-thread frees pending
  mi_thread_free_t tf = mi_atomic_load(&page->xthread_free);
  if (tf != NULL && !force) {
    return;  // Wait for cross-thread cleanup
  }
  
  // Step 3: Mark page as retired (no longer allocates)
  page->retire_expire++;
  
  // Step 4: If truly empty, reset memory to OS
  if (page->is_committed && 
      heap->tld->segments.allow_decommit) {
    
    // MADVISE_FREE on Linux, VirtualAlloc(MEM_RESET) on Windows
    _mi_os_reset(page->page_start, MI_PAGE_SIZE);
    page->is_committed = false;
  }
}
```

### 3.4 Abandoned Page Reclamation

```c
// When thread terminates, pages may be abandoned
static void _mi_abandoned_page_reclaim(
  mi_segment_t* segment)
{
  // Check if this segment has abandoned pages
  if (segment->abandoned == 0) {
    return;  // No abandoned pages
  }
  
  // Limit reclamation attempts
  segment->abandoned_visits++;
  if (segment->abandoned_visits > MI_RECLAIM_MAX_VISITS) {
    return;
  }
  
  // For each abandoned page
  for (size_t i = 0; i < segment->used; i++) {
    mi_page_t* page = &segment->pages[i];
    
    if (!page->segment_in_use) {
      continue;  // Not abandoned
    }
    
    // Try to steal this page for current thread
    if (mi_atomic_compare_exchange(
      &page->thread_id,
      old_tid,
      current_thread_id)) {
      
      // Success: page now belongs to this thread
      segment->abandoned--;
      
      // Reclaim: migrate xthread_free and mark for purge
      _mi_migrate_xthread_free_to_free(heap, page);
    }
  }
}
```

---

## 4. Monotonic Heartbeat and Temporal Cadence

### 4.1 Heartbeat Mechanism

```c
// Thread-local data
typedef struct mi_tld_s {
  unsigned long long heartbeat;  // Monotonically increasing counter
  // ...
} mi_tld_t;

// Called at regular intervals (on malloc, free, etc.)
static inline void _mi_heartbeat_increment(mi_tld_t* tld) {
  tld->heartbeat++;
  
  // Every N heartbeats, perform maintenance
  if ((tld->heartbeat % MI_HEARTBEAT_INTERVAL) == 0) {
    _mi_heap_maintenance(tld->heap);
  }
}

// Maintenance tasks
static void _mi_heap_maintenance(mi_heap_t* heap) {
  // 1. Migrate local_free → free in all pages
  for (size_t bin = 0; bin < MI_BIN_FULL; bin++) {
    mi_page_queue_t* queue = &heap->pages[bin];
    for (mi_page_t* page = queue->first; page != NULL; page = page->next) {
      if (page->local_free != NULL) {
        _mi_migrate_local_free_to_free(page);
      }
    }
  }
  
  // 2. Drain cross-thread free lists
  // (This happens more frequently for active pages)
  
  // 3. Purge empty pages
  // (Reclaim memory from fully free pages)
  
  // 4. Reclaim abandoned pages from dead threads
  // (Integrate orphaned pages back into allocation stream)
}
```

### 4.2 Deferred Free Batch Processing

```c
// Delayed free list at heap level
// Accumulates frees from other threads

struct mi_heap_s {
  _Atomic(mi_block_t*) thread_delayed_free;  // Accumulates xthread_free blocks
};

// Process delayed frees in batch
static void _mi_heap_delayed_free_batch(mi_heap_t* heap) {
  // Atomically grab entire delayed free list
  mi_block_t* batch = mi_atomic_exchange(&heap->thread_delayed_free, NULL);
  
  if (batch == NULL) return;
  
  // Process entire batch at once
  mi_block_t* block = batch;
  while (block != NULL) {
    mi_block_t* next = block->next;
    
    // Validate and free
    mi_page_t* page = _mi_ptr_page(block);
    _mi_page_free_block(page, block);
    
    block = next;
  }
}
```

---

## 5. Contention Analysis

### 5.1 Contention Points in Mimalloc

```
Total contention points ≈ # of pages × # of size classes

For typical workload:
- Threads: 64
- Segments per thread: 2 (small + medium)
- Pages per segment: 64 + 8 = 72
- Bins: 73

Total pages: 64 threads × 2 segments × 72 pages = 9,216 pages
Total potential contention points: 9,216 × 73 = 672,768 different (page, bin) pairs

Probability of any two threads contending on SAME (page, bin):
  P(contention) = 1 / 672,768 ≈ 0.0015% (extremely low)
```

### 5.2 Contention Points in Jemalloc

```
For typical jemalloc setup:
- Threads: 64
- Arenas: 8 (CPU cores)
- Run classes per arena: ~30

Total contention points: 8 arenas

Probability of any two threads contending on SAME arena:
  Assume random arena assignment: P(contention) ≈ 1/8 = 12.5% (moderate)

This is why jemalloc uses multiple arenas - to reduce contention.
Mimalloc has naturally low contention through massive distribution.
```

### 5.3 Contention Under Pathological Workloads

Even in worst case (all threads freeing to same page):

```c
// Worst case: all threads allocating from single size class
// Example: all malloc(256) calls

// Jemalloc: lock contention on that size run
//   P(lock wait) ≈ high (many threads on same bin)
//   Lock time: microseconds to milliseconds

// Mimalloc: CAS loop on xthread_free
//   P(CAS collision) ≈ low (many pages available)
//   If collision, retry immediately (no context switch)
//   CAS time: nanoseconds, no OS scheduling involved
```

---

## 6. Memory Layout Examples

### 6.1 Small Object Allocation Sequence

```
Thread A: malloc(256)
   ↓
   Bin 10 → Small page (64 KiB)
   ↓
   page->free points to block at offset 0
   ↓
   Block @ 0x7f1234000000: [data:256 bytes][padding:0 bytes]
   ↓
   page->free = block->next (points to offset 256)
   ↓
   page->used++
   ↓
   return 0x7f1234000000

Thread A: malloc(256)
   ↓
   Same bin, same page (still has space)
   ↓
   page->free points to block at offset 256
   ↓
   Block @ 0x7f1234000100: [data:256 bytes][padding:0 bytes]
   ↓
   page->free = block->next
   ↓
   return 0x7f1234000100

Thread A: free(0x7f1234000000)
   ↓
   page->local_free = block @ 0x7f1234000000
   ↓
   block->next = NULL (initially)

Thread B: malloc(512)
   ↓
   Different bin → different page
   ↓
   No contention!
```

### 6.2 Cross-Thread Free Sequence

```
Thread A: malloc(256) → Block @ 0x7f1000000000

Thread B: free(0x7f1000000000)  [Different thread!]
   ↓
   Step 1: Identify page for block
   ↓
   Step 2: Check thread_id (A ≠ B)
   ↓
   Step 3: CAS to page->xthread_free
     OLD: NULL
     NEW: block with flags
   ↓
   Step 4: Only CAS winner adds page to A's thread_delayed_free
   ↓
   Step 5: When A does next malloc
     → Maintenance triggered
     → xthread_free migrated to free
     → Block becomes available for reallocation
```

---

## 7. Allocation Size Fast Path Computation

```c
// From bin computation
// Maps allocation size to bin number efficiently

// Example: size = 384 bytes
// Which bin?

// Binary representation: 384 = 0x180
// Leading bits identify bin range
// Exponential spacing: each bin ~1.125x previous

// Typical implementation uses CLZ (count leading zeros) 
// or similar bit manipulation for O(1) computation

static inline size_t _mi_bin(size_t size) {
  // Fast path: use built-in bit operations
  // Example pseudocode:
  
  size_t bin;
  if (size <= 256) {
    bin = size >> 4;  // Divide by 16, multiple bins per power-of-2
  } else if (size <= 4096) {
    bin = 16 + ((size - 256) >> 8);
  } else {
    // Larger bins use more sparse spacing
    bin = _mi_bin_large(size);
  }
  
  return bin;
}
```

---

## Summary: Algorithmic Achievements

| Algorithm | Complexity | Key Advantage |
|-----------|-----------|---------------|
| **Page Lookup** | O(1) | Constant-time allocation start |
| **Bin Selection** | O(1) | No tree traversal |
| **Block Popping** | O(1) amortized | Free list head + next pointer |
| **Cross-thread Free** | O(1) CAS | Single atomic operation |
| **Migration** | O(n) blocks | Batched, amortized cost |
| **Page Purging** | O(1) | No page coalescing needed |
| **Contention** | O(# pages × # bins) | Massive distribution |
| **Worst-case Allocation** | O(segment_alloc) | Only on page exhaustion |

The key insight: **Distributed free lists eliminate contention bottlenecks**, making the common case incredibly fast with minimal synchronization.

