# Memory Allocation Algorithms: Detailed Analysis and Implementation

## Table of Contents
1. Bonwick Slab Allocator Algorithm
2. Segregated Free List Allocation
3. Buddy System Allocation
4. Arena Allocation
5. Magazine Layer Implementation
6. Modern Variations (jemalloc, TCMalloc, Mimalloc)

---

## 1. Bonwick Slab Allocator Algorithm

### Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│             kmem_cache_t (Cache)                    │
├─────────────────────────────────────────────────────┤
│  Magazine Layer (Per-CPU Caching)                   │
│  ┌─────────────────────────────────────────────┐   │
│  │ cpu_0: Magazine(N loaded, M unloaded)       │   │
│  │ cpu_1: Magazine(N loaded, M unloaded)       │   │
│  │ cpu_n: Magazine(N loaded, M unloaded)       │   │
│  └─────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────┤
│  Depot Layer (Global Magazine Storage)              │
│  ┌─────────────────────────────────────────────┐   │
│  │ Loaded Magazines (full, ready to use)       │   │
│  │ Unloaded Magazines (empty, for refilling)   │   │
│  └─────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────┤
│  Slab Layer (Core Memory Management)                │
│  ┌─────────────────────────────────────────────┐   │
│  │ Free Slabs (all objects unused)             │   │
│  │ Partial Slabs (some objects used)           │   │
│  │ Full Slabs (all objects allocated)          │   │
│  └─────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

### Data Structures

#### Cache Structure
```c
typedef struct kmem_cache {
    // Object properties
    size_t object_size;        // Size of each object
    size_t align;              // Alignment requirement
    const char *name;          // Debug name
    
    // Slab properties
    size_t slab_size;          // Size of slab (usually 1-8 pages)
    size_t objects_per_slab;   // How many objects fit
    size_t colour;             // Number of color offsets
    size_t colour_off;         // Color offset stride
    
    // Constructor/destructor
    void (*ctor)(void *);      // Constructor callback
    void (*dtor)(void *);      // Destructor callback
    
    // Magazine layer (per-CPU)
    struct array_cache *array[NR_CPUS];  // Per-CPU magazines
    
    // Depot layer
    struct kmem_list3 {
        struct array_cache *shared;      // Shared full magazines
        struct array_cache *free;        // Empty magazines
        unsigned long free_objects;      // Count
    } lists;
    
    // Slab layer
    struct list_head slabs_full;
    struct list_head slabs_partial;
    struct list_head slabs_free;
    
    // Statistics
    unsigned long active_objs;
    unsigned long num_objs;
} kmem_cache_t;
```

#### Slab Structure
```c
typedef struct slab {
    struct list_head list;              // Link to cache slab lists
    void *s_mem;                        // Virtual address of slab
    
    // Object tracking
    unsigned int inuse;                 // Objects currently allocated
    unsigned int free;                  // Objects available for allocation
    
    // Free list of available objects
    kmem_bufctl_t *freelist;            // Index array for free objects
    
    // Color offset for this slab
    unsigned int colour_off;
    
    // Pointer to parent cache
    struct kmem_cache *cachep;
    
    // Statistics
    unsigned int nodeid;                // NUMA node
} slab_t;

// Buffer control (tracks individual objects in slab)
typedef unsigned int kmem_bufctl_t;     // Index into objects array
```

#### Magazine Structure
```c
typedef struct array_cache {
    unsigned int avail;                 // Objects available in magazine
    unsigned int limit;                 // Magazine capacity
    unsigned int batchcount;            // Refill batch size
    unsigned int touched;               // Last access time
    
    // Pointer array to objects
    void *entry[MAGAZINE_LIMIT];        // [0..avail) = loaded objects
                                        // [avail..limit) = empty slots
} array_cache_t;
```

### Allocation Algorithm

#### Pseudocode: kmem_cache_alloc()
```
function kmem_cache_alloc(cache, flags):
    // Step 1: Try per-CPU magazine (fast path)
    cpu_id = get_cpu_id()
    magazine = cache.array[cpu_id]
    
    if magazine.avail > 0:
        object = magazine.entry[--magazine.avail]
        return object
    end if
    
    // Step 2: Per-CPU magazine empty, try to refill from depot
    if try_refill_magazine(cache, magazine):
        object = magazine.entry[--magazine.avail]
        return object
    end if
    
    // Step 3: Depot empty, allocate from slab layer
    slab = get_slab_from_partial_or_free(cache)
    
    if slab == NULL:
        // No partial slab, allocate new slab
        slab = allocate_slab(cache, flags)
        if slab == NULL:
            return NULL
        end if
        // Add to partial list
        add_slab_to_list(cache, slab, PARTIAL)
    end if
    
    // Step 4: Get object from slab free list
    object_index = slab.freelist[0]
    slab.freelist[0] = slab.freelist[object_index]
    
    slab.inuse++
    slab.free--
    
    if slab.free == 0:
        remove_from_partial_list(cache, slab)
        add_slab_to_full_list(cache, slab)
    end if
    
    return object
end function
```

#### Refill Mechanism
```
function try_refill_magazine(cache, magazine):
    // Try to get loaded magazine from depot
    if cache.lists.shared is not NULL and cache.lists.shared.avail > 0:
        old_magazine = magazine
        magazine = cache.lists.shared
        cache.lists.shared = old_magazine
        return TRUE
    end if
    
    // Try to get empty magazine and refill from slab layer
    if cache.lists.free is not NULL:
        empty_magazine = get_magazine_from_depot(cache.lists.free)
        empty_magazine.avail = 0
        
        // Refill magazine from slab layer
        for i = 0 to cache.batchcount:
            object = allocate_from_slab(cache)
            if object == NULL:
                break
            end if
            empty_magazine.entry[empty_magazine.avail++] = object
        end for
        
        if empty_magazine.avail > 0:
            magazine = empty_magazine
            return TRUE
        else
            return_magazine_to_depot(cache.lists.free, empty_magazine)
            return FALSE
        end if
    end if
    
    return FALSE
end function
```

### Deallocation Algorithm

#### Pseudocode: kmem_cache_free()
```
function kmem_cache_free(cache, object):
    // Verify object belongs to this cache (debug)
    slab = get_slab_from_object(cache, object)
    
    if slab == NULL:
        PANIC("Object not in cache")
    end if
    
    // Step 1: Try to return to per-CPU magazine
    cpu_id = get_cpu_id()
    magazine = cache.array[cpu_id]
    
    if magazine.avail < magazine.limit:
        magazine.entry[magazine.avail++] = object
        return
    end if
    
    // Step 2: Magazine full, try to return full magazine to depot
    if try_return_full_magazine_to_depot(cache):
        magazine.entry[magazine.avail++] = object
        return
    end if
    
    // Step 3: Return object directly to slab layer
    slab.freelist[slab.free] = get_object_index(slab, object)
    
    if slab.inuse == slab.objects_per_slab:
        // Moving from full to partial
        remove_from_full_list(cache, slab)
        add_slab_to_partial_list(cache, slab)
    end if
    
    slab.inuse--
    slab.free++
end function
```

### Cache Coloring Implementation

#### Coloring Algorithm
```
function setup_cache_coloring(cache, slab_size):
    // Calculate cache line conflicts
    cache_line_size = get_cache_line_size()  // Typically 64 bytes
    
    // Color range to avoid conflicts
    cache_ways = get_cache_associativity()   // L2/L3 ways
    
    color_range = (cache_line_size * cache_ways) / cache.object_size
    
    // Stride for each color
    cache.colour = color_range
    cache.colour_off = cache_line_size
    
    // Verify coloring doesn't exceed slab
    if color_range * cache_line_size > slab_size:
        cache.colour = slab_size / cache_line_size
    end if
end function

function allocate_slab_with_coloring(cache):
    slab = allocate_slab(cache)
    
    // Calculate color offset for this slab
    static next_color = 0
    color = (next_color++) % cache.colour
    slab.colour_off = color * cache.colour_off
    
    // Adjust slab memory start
    slab.s_mem = slab.s_mem + slab.colour_off
    
    return slab
end function
```

---

## 2. Segregated Free List Allocation

### Algorithm Overview

```
Size Classes: [32, 64, 128, 256, 512, 1024, 2048, 4096]

Free Lists:
┌─────────┬──────────┬────────────┬─────────────┬────────────┐
│ Size 32 │ Size 64  │ Size 128   │ Size 256    │ Size 512   │
├────┬────┼─────┬────┼────┬───┬───┼────┬────┬───┼────┬───┬───┤
│obj │obj │ obj │obj │obj │obj│obj│obj │obj │obj│obj │ --│ --│
└────┴────┴─────┴────┴────┴───┴───┴────┴────┴───┴────┴───┴───┘
```

### Data Structures

```c
typedef struct size_class {
    size_t size;                // Exact object size for this class
    struct free_list_head *free_list;  // Head of free list
    unsigned int count;         // Number of free objects
} size_class_t;

typedef struct free_list_node {
    struct free_list_node *next;  // Next free object
    // Actual object data follows
} free_list_node_t;

typedef struct {
    size_class_t *size_classes;
    unsigned int num_classes;
    
    // Statistics
    unsigned long total_allocated;
    unsigned long total_freed;
} allocator_t;
```

### Allocation Algorithm

#### Pseudocode: segregated_alloc()
```
function segregated_alloc(allocator, size):
    // Step 1: Find appropriate size class
    size_class = find_size_class(allocator, size)
    
    if size_class == NULL:
        // Size too large, use special allocation
        return system_allocate(size)
    end if
    
    // Step 2: Check free list
    if size_class.free_list != NULL:
        object = (void *)size_class.free_list
        size_class.free_list = size_class.free_list.next
        size_class.count--
        return object
    end if
    
    // Step 3: Free list empty, allocate new chunk
    new_object = allocate_from_system(size_class.size)
    return new_object
end function

function find_size_class(allocator, size):
    for each class in allocator.size_classes:
        if class.size >= size:
            return class
        end if
    end for
    return NULL  // Size too large
end function
```

#### Deallocation
```
function segregated_free(allocator, object, size):
    size_class = find_size_class(allocator, size)
    
    if size_class == NULL:
        system_free(object)
        return
    end if
    
    // Insert at head of free list (O(1))
    node = (free_list_node_t *)object
    node.next = size_class.free_list
    size_class.free_list = node
    size_class.count++
end function
```

### Optimal Size Class Selection

#### Analysis
For object size `s` and size class `c`, internal fragmentation:
```
waste = (c - s) / c
```

**Example Calculations**:
- Request 48 bytes, class 64: waste = 16/64 = 25%
- Request 100 bytes, class 128: waste = 28/128 = 22%
- Request 512 bytes, class 512: waste = 0/512 = 0%

#### Fragmentation vs. Granularity
```
Granularity | Max Waste | Avg Waste | Classes | Metadata
─────────────────────────────────────────────────────────
Coarse (2x)  | 50%       | 25%       | 10      | Low
Fine (10%)   | 10%       | 5%        | 30      | Medium
Very Fine (5%)| 5%       | 2.5%      | 60      | High
```

---

## 3. Buddy System Allocation

### Algorithm Overview

```
              [0 KB────────────────8 MB───────────────16 MB]
                        Full Block

              [0 KB────────────────8 MB]    [8 MB──────────16 MB]
                    Split to Order 8         Order 8

              [0 KB────4 MB]    [4 MB────8 MB]    [8 MB────────16 MB]
                Order 7           Order 7           Order 8

Continuing until requested size is found or reached order 0 (4 KB)
```

### Data Structures

```c
typedef struct buddy_block {
    unsigned int order;           // 2^order = block size
    int is_free;                  // 1 if free, 0 if allocated
    struct buddy_block *buddy;    // Pointer to buddy block
    struct buddy_block *parent;   // Parent block (for coalescing)
    struct buddy_block *left;     // Left child (for tree)
    struct buddy_block *right;    // Right child
} buddy_block_t;

typedef struct {
    buddy_block_t *root;
    unsigned int max_order;       // Max block size = 2^max_order
    struct list_head free_lists[MAX_ORDER];  // Free lists per order
} buddy_allocator_t;
```

### Allocation Algorithm

#### Pseudocode: buddy_alloc()
```
function buddy_alloc(allocator, size):
    // Step 1: Determine order needed
    order = get_order(size)
    
    // Step 2: Search free lists from order to max_order
    for current_order = order to allocator.max_order:
        if free_lists[current_order] is not empty:
            block = get_free_block(free_lists[current_order])
            break
        end if
    end for
    
    if block == NULL:
        return NULL  // Memory exhausted
    end if
    
    // Step 3: Split block down to needed order
    while block.order > order:
        new_block = split_block(block)
        block.order--
        new_block.order--
        add_to_free_list(allocator.free_lists[new_block.order], new_block)
    end while
    
    block.is_free = 0
    return block.address
end function

function get_order(size):
    // Find smallest order where 2^order >= size
    order = 0
    block_size = MIN_BLOCK_SIZE
    
    while block_size < size:
        block_size *= 2
        order++
    end while
    
    return order
end function
```

### Deallocation and Coalescing

#### Pseudocode: buddy_free()
```
function buddy_free(allocator, address):
    block = find_block_by_address(allocator.root, address)
    
    if block == NULL or block.is_free:
        PANIC("Invalid free")
    end if
    
    block.is_free = 1
    
    // Coalesce with buddy if also free
    while block.order < allocator.max_order:
        buddy = find_buddy(block)
        
        if buddy == NULL or not buddy.is_free:
            break  // Can't coalesce
        end if
        
        // Merge buddies
        remove_from_free_list(allocator.free_lists[buddy.order], buddy)
        
        if get_buddy_index(block) == 0:
            parent = coalesce(block, buddy)
        else:
            parent = coalesce(buddy, block)
        end if
        
        block = parent
    end while
    
    add_to_free_list(allocator.free_lists[block.order], block)
end function
```

### Complexity Analysis
- **Allocation**: O(log N) where N = total memory
- **Deallocation**: O(log N) coalescing
- **Fragmentation**: Internal = ~25% worst case, External = minimal

---

## 4. Arena Allocation

### Algorithm Overview

```
Arena: [Header][Obj1][Obj2][Obj3][Obj4][Free]
       ↑                                    ↑
    allocation ptr                  allocation limit
```

### Data Structures

```c
typedef struct arena {
    void *base;                   // Start of arena memory
    size_t size;                  // Total arena size
    void *current;                // Current allocation point
    struct list_head chunks;      // Allocated chunks (for tracking)
} arena_t;

typedef struct arena_chunk {
    arena_t *arena;               // Parent arena
    void *ptr;                    // Allocated pointer
    size_t size;                  // Allocation size
    struct list_node node;        // Link in arena.chunks
} arena_chunk_t;
```

### Allocation Algorithm

#### Pseudocode: arena_alloc()
```
function arena_alloc(arena, size):
    // Align allocation
    aligned_size = ALIGN_UP(size, ALIGNMENT)
    
    // Check if space available
    if arena.current + aligned_size > arena.base + arena.size:
        return NULL  // Arena exhausted
    end if
    
    ptr = arena.current
    arena.current += aligned_size
    
    // Optional: track allocation for statistics
    chunk = allocate_chunk_metadata()
    chunk.ptr = ptr
    chunk.size = aligned_size
    add_to_list(arena.chunks, chunk)
    
    return ptr
end function
```

### Deallocation

#### Single Deallocation (Not Supported)
```
// Individual free typically not supported in arena allocators
// This is by design - arena frees objects in bulk
function arena_free(arena, ptr):
    // Could track and mark as free, but no coalescing
    // Alternative: keep used list
    chunk = find_chunk(arena.chunks, ptr)
    chunk.is_free = TRUE
end function
```

#### Bulk Deallocation (Destruction)
```
function arena_destroy(arena):
    // Free all memory at once
    for each chunk in arena.chunks:
        free_list_node(chunk)
    end for
    
    free_system_memory(arena.base, arena.size)
    free(arena)
end function
```

### Performance Characteristics
- **Allocation**: O(1) - just pointer bump
- **Individual Deallocation**: O(1) mark free, or not supported
- **Bulk Deallocation**: O(1) - free arena wholesale
- **Fragmentation**: Minimal internal (only alignment), no external (no coalescing needed)
- **Best For**: Scoped allocations, temporary objects

---

## 5. Magazine Layer Implementation

### Core Magazine Mechanism

#### Loaded Magazine (Full)
```
Magazine: [obj1][obj2][obj3][obj4][obj5]──→ NULL
           ↑
        next_free (ready to allocate)
```

#### Unloaded Magazine (Empty)
```
Magazine: [──][──][──][──][──]
           ↑
        next_free (all empty slots)
```

### Magazine Exchange Algorithm

```
function exchange_full_magazine(cache, magazine):
    // Current per-CPU magazine is empty
    // Try to get loaded magazine from depot
    
    empty_magazine = magazine
    loaded_magazine = depot.get_loaded()
    
    if loaded_magazine != NULL:
        // Swap magazines
        cache.per_cpu_magazine = loaded_magazine
        depot.add_unloaded(empty_magazine)  // Return empty for refilling
        return TRUE
    end if
    
    // No loaded magazines in depot, return empty
    return FALSE
end function

function refill_magazine(cache, magazine):
    // Magazine empty, try to refill from slab layer
    
    for i = 0 to cache.batchcount:
        object = allocate_from_slab(cache)
        if object == NULL:
            break
        end if
        magazine.objects[i] = object
        magazine.avail = i + 1
    end for
    
    if magazine.avail > 0:
        magazine.available = TRUE
        return TRUE
    else
        return FALSE
    end if
end function
```

---

## 6. Modern Allocator Variations

### jemalloc: Logarithmic Binning

#### Size Class Scheme
```
Small sizes:  8, 16, 24, 32, 48, 64, 80, 96, ...
             (linear steps up to 128)

Medium sizes: 256, 320, 384, 448, 512, 640, 768, ...
             (log-linear steps)

Large sizes:  Extent-based allocation
```

#### Run Concept (jemalloc)
```
Run: [metadata][obj1][obj2]...[objN]
     ↑
  Per-thread run queue
```

**Key Features**:
- Multiple runs per size class
- Active defragmentation moves objects
- Decay-based background reclamation

### TCMalloc: Thread-Local Caching

#### Architecture
```
┌──────────────────────────────────────────┐
│         Thread-Local Cache (TLS)         │
│  ┌────────────────────────────────────┐  │
│  │ Size 8: [obj, obj, obj]            │  │
│  │ Size 16: [obj, obj]                │  │
│  │ Size 32: [obj, obj, obj, obj]      │  │
│  └────────────────────────────────────┘  │
└──────────────────────────────────────────┘
              ↓
┌──────────────────────────────────────────┐
│      Central Heap (Global)               │
│  ┌────────────────────────────────────┐  │
│  │ Size 8: [obj, obj, obj, obj, ...] │  │
│  │ Size 16: [obj, obj, obj, obj, ...]│  │
│  │ etc.                               │  │
│  └────────────────────────────────────┘  │
└──────────────────────────────────────────┘
```

**Refill Strategy**:
```
function refill_thread_cache(size_class):
    if thread_cache[size_class].empty:
        // Get batch from central heap
        objects = central_heap.allocate_batch(size_class, batch_size)
        thread_cache[size_class].insert_batch(objects)
    end if
end function
```

### Mimalloc: Segment-Based

#### Segment Structure
```
Segment: [Header][Run1: free][Run2: used][Run3: free]...
         ↑
      Per-thread ownership
```

**Key Innovation**:
- Segment allocation instead of page allocation
- Free-list sharding to reduce contention
- Overcommit for security (detect-use-after-free)

---

## Comparison Matrix

| Aspect | Slab | Segregated Free List | Buddy | Arena |
|--------|------|---------------------|-------|-------|
| **Allocation** | O(1) avg | O(1) | O(log N) | O(1) |
| **Deallocation** | O(1) avg | O(1) | O(log N) | O(N) or O(1) bulk |
| **Int. Fragment** | Very Low | Medium (5-25%) | Medium (25%) | Very Low |
| **Ext. Fragment** | None | Low (segregated) | Low (coalescing) | None |
| **Cache Coloring** | Yes | No | No | Optional |
| **Lock Contention** | Low (magazines) | Low (per-class) | Medium | Low |
| **Best For** | Kernel objects | General purpose | Page allocation | Scoped allocs |

---

## Performance Tuning Parameters

### Magazine Size Trade-offs
```
Magazine Size | Cache Miss Rate | Lock Contention | Memory Waste
──────────────────────────────────────────────────────────────
4 objects     | Higher (refills) | Lower           | Very Low
16 objects    | Medium           | Low             | Low
64 objects    | Lower            | Medium          | Medium
256+ objects  | Very Low         | Higher          | High
```

### Batch Size in Refilling
```
Batch Size | Refill Frequency | Lock Hold Time | Contention
──────────────────────────────────────────────────────────
1 object   | Very High        | Very Short     | Very High
4 objects  | High             | Short          | High
16 objects | Medium           | Medium         | Medium
64 objects | Low              | Long           | Low
```

---

## Fragmentation Calculation Examples

### Example 1: Segregated Free List
```
Size classes: [32, 64, 128, 256]
Request: 48 bytes

Allocated from class 64 (next larger)
Internal waste: 64 - 48 = 16 bytes (25%)
```

### Example 2: Buddy System
```
Total memory: 16 MB
Request: 10 KB

Allocate 2^14 = 16 KB
Internal waste: 16 KB - 10 KB = 6 KB (37.5%)
```

### Example 3: Slab with Coloring
```
Object size: 128 bytes
Cache line: 64 bytes
Color stride: 64 bytes
Color overhead per slab: ~64 bytes (0.4-1.2% depending on slab size)
```

---

## References in Code

### Linux Kernel Implementation Files
- `mm/slab.c` (2.2-2.6.22) - Bonwick implementation
- `mm/slub.c` (2.6.23+) - SLUB variant
- `include/linux/slab.h` - Public API

### jemalloc Source
- `src/arena.c` - Arena implementation
- `src/bin.c` - Binning (size classes)
- `src/ckh.c` - Core hash tables

### TCMalloc Source
- `src/thread_cache.cc` - Thread-local caching
- `src/central_freelist.cc` - Central heap
- `src/page_heap.cc` - Page allocation

---

**Algorithms Document**: Comprehensive implementation guide  
**Code Quality**: Production-grade pseudocode with complexity analysis  
**Last Updated**: 2026-07-07
