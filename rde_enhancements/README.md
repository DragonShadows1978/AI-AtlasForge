# RDE Framework Enhancements - Production Hardened

A comprehensive enhancement suite for the RDE (Research & Development Engine) framework, providing mission continuity tracking, exploration memory, and self-calibrating prompt scaffolding.

**Version:** 1.0.0 (Production Hardened - Cycle 5)

## Quick Start

```python
from rde_enhancements import RDEEnhancer

# Initialize
enhancer = RDEEnhancer(mission_id="my_mission")

# Track mission continuity
enhancer.set_mission_baseline("Build a REST API with authentication")
report = enhancer.check_continuity(current_output)

# Record exploration
enhancer.record_file_exploration("/src/api.py", "REST handlers", tags=["api"])

# Query knowledge
knowledge = enhancer.what_do_we_know("authentication")

# Apply scaffolding
scaffolded, analysis = enhancer.scaffold_prompt(prompt, previous_response)

# Process cycle end
report = enhancer.process_cycle_end(cycle_number=1, cycle_output=output, ...)
```

## Features Overview

| Feature | Purpose | Key Components |
|---------|---------|----------------|
| **Mission Continuity** | Detect and heal drift from original mission | fingerprint_extractor, context_healing |
| **Exploration Memory** | Graph-based knowledge with semantic search | exploration_graph, insight_extractor |
| **Prompt Scaffolding** | Self-calibrating bias reduction | bias_detector, scaffold_calibrator |
| **Knowledge Transfer** | Cross-mission knowledge reuse | knowledge_transfer |

### Production Hardening (Cycle 5)

- Thread-safe operations using RLock
- Comprehensive error handling with graceful degradation
- Retry logic for embedding model initialization (3 attempts with backoff)
- Memory usage monitoring and automatic pruning (>10k nodes)
- Input validation and sanitization on all public methods

## Installation

```bash
# Dependencies
pip install numpy sentence-transformers torch

# Optional for FAISS indexing
pip install faiss-cpu  # or faiss-gpu for CUDA
```

## Core Components

### 1. Mission Continuity Tracker

Tracks "cognitive fingerprints" - ratio-based concept frequencies that persist across context windows.

```python
from rde_enhancements import MissionContinuityTracker

tracker = MissionContinuityTracker("my_mission")
tracker.set_baseline(mission_statement)

# Check alignment
report = tracker.check_continuity(current_output)
print(f"Similarity: {report.overall_similarity:.1%}")
print(f"Drift: {report.drift_severity}")

if report.healing_recommended:
    print(report.healing_prompt)
```

### 2. Exploration Memory Graph

Graph-based memory with semantic search using sentence-transformers.

```python
from rde_enhancements import ExplorationGraph, ExplorationAdvisor

graph = ExplorationGraph("./exploration_data")

# Add nodes
graph.add_file_node("/src/api.py", "REST API handlers", "mission_1")
graph.add_concept_node("JWT Auth", "Token-based authentication", "mission_1")
graph.add_insight("gotcha", "Sessions expire after 1 hour", "...", "mission_1")

# Semantic search
results = graph.semantic_search("authentication", top_k=10)
insights = graph.semantic_search_insights("security patterns")

# Query advisor
advisor = ExplorationAdvisor(graph)
knowledge = advisor.what_do_we_know("authentication")
should, reason = advisor.should_explore("/src/db.py")
```

### 3. Self-Calibrating Scaffolding

Detects cognitive biases and applies targeted scaffolds, then calibrates based on outcomes.

```python
from rde_enhancements import ScaffoldCalibrator

calibrator = ScaffoldCalibrator()

# Apply scaffolds based on detected biases
scaffolded, analysis = calibrator.apply_scaffolds_to_prompt(
    prompt,
    previous_response  # Analyzed for biases
)

# Record outcome for calibration
outcome = calibrator.record_outcome(analysis['application_id'], response)
```

### 4. Knowledge Transfer

Transfer knowledge from prior missions to new ones.

```python
enhancer = RDEEnhancer("new_mission")
enhancer.enable_knowledge_transfer(
    missions_base=Path("/home/vader/mini-mind-v2/missions"),
    current_mission_context="Building a REST API"
)

# Query prior knowledge
prior = enhancer.query_prior_knowledge("authentication", top_k=10)

# Get starting suggestions
suggestions = enhancer.get_starting_point_suggestions()

# Import relevant insights
imported = enhancer.import_prior_insights(max_imports=20)
```

## API Reference

### RDEEnhancer

Main unified interface. See [QUICKSTART.md](QUICKSTART.md) for examples.

#### Mission Continuity
- `set_mission_baseline(text, source)` - Set baseline fingerprint
- `check_continuity(output, source)` - Check alignment
- `heal_continuation(prompt, output)` - Add healing
- `checkpoint_cycle(...)` - Create checkpoint
- `get_continuity_evolution()` - Get evolution summary

#### Exploration Memory
- `record_file_exploration(path, summary, tags)` - Record file
- `record_concept(name, summary, tags)` - Record concept
- `record_pattern(name, summary, code)` - Record pattern
- `record_insight(type, title, desc, confidence)` - Record insight
- `process_exploration_output(text)` - Auto-extract from text
- `should_explore(path)` - Check if should explore
- `what_do_we_know(topic)` - Query knowledge
- `get_exploration_stats()` - Get statistics

#### Scaffolding
- `scaffold_prompt(prompt, prev, ctx)` - Apply scaffolds
- `record_scaffold_outcome(response, id)` - Record outcome
- `analyze_for_bias(text)` - Analyze biases
- `get_scaffold_effectiveness()` - Get report

#### Knowledge Transfer
- `enable_knowledge_transfer(base, context)` - Enable
- `query_prior_knowledge(query, top_k, min)` - Query
- `get_starting_point_suggestions()` - Get suggestions
- `import_prior_insights(missions, max)` - Import

#### Combined
- `process_cycle_end(...)` - Full cycle processing
- `generate_enhanced_continuation(...)` - Enhanced prompt
- `get_comprehensive_status()` - Full status

### ExplorationGraph

Low-level graph API. Thread-safe with RLock.

```python
# Node types: 'file', 'concept', 'pattern'
# Edge relationships: 'imports', 'calls', 'uses', 'related_to'
# Insight types: 'pattern', 'gotcha', 'best_practice', 'observation'
```

### Memory Management

```python
# Get memory stats
stats = graph.get_memory_usage()
# {'node_count': 8500, 'estimated_embedding_memory_mb': 12.5, ...}

# Manual pruning
pruned = graph.prune_old_nodes(target_count=4000)

# Configure max (default 10k)
graph.set_max_nodes(5000)
```

### Visualization

```python
# Export for canvas rendering
viz = graph.export_for_visualization(width=800, height=600)
# Returns {'nodes': [...], 'edges': [...]} with positions
```

## Data Structures

### ExplorationNode
```python
id: str                    # Unique ID
node_type: str             # 'file', 'concept', 'pattern'
name: str                  # Display name
path: Optional[str]        # File path
summary: str               # Description
tags: List[str]
embedding: Optional[List[float]]  # 384-dim vector
```

### ExplorationInsight
```python
id: str
insight_type: str          # 'pattern', 'gotcha', 'best_practice'
title: str
description: str
confidence: float          # 0.0 to 1.0
```

### ContinuityReport
```python
overall_similarity: float   # 0.0 to 1.0
drift_severity: str         # 'none', 'minor', 'moderate', 'severe'
healing_recommended: bool
healing_prompt: Optional[str]
```

## Thread Safety

All public methods are thread-safe:

```python
from concurrent.futures import ThreadPoolExecutor

with ThreadPoolExecutor(max_workers=5) as executor:
    futures = [
        executor.submit(enhancer.record_file_exploration, f"/file_{i}.py", "desc")
        for i in range(100)
    ]
```

## Error Handling

Graceful degradation - features continue working when components fail:

```python
# Embedding model unavailable -> falls back to keyword search
results = graph.semantic_search("query")

# Invalid input -> validated and clamped
graph.add_insight("type", "title", "desc", "mission", confidence=1.5)  # Clamped to 1.0

# Component failure -> other features still work
node = enhancer.record_file_exploration("/test.py", "summary")  # Works even if continuity fails
```

## Testing

```bash
cd /home/vader/mini-mind-v2
python3 -m pytest workspace/tests/test_rde_integration.py -v
```

29 tests covering:
- Input validation
- Thread safety (concurrent access)
- Error handling (graceful degradation)
- Memory management (auto-pruning)
- Embedding model (retry logic)
- End-to-end integration
- Performance benchmarks

## File Structure

```
rde_enhancements/
├── __init__.py                    # Package exports
├── README.md                      # This file
├── QUICKSTART.md                  # Quick start guide
├── rde_enhancer.py                # Unified interface
├── exploration_graph.py           # Graph with semantic search
├── fingerprint_extractor.py       # Concept extraction
├── mission_continuity_tracker.py  # Cycle tracking
├── context_healing.py             # Drift healing
├── insight_extractor.py           # Text processing
├── bias_detector.py               # Bias detection
├── scaffold_calibrator.py         # Self-calibration
└── knowledge_transfer.py          # Cross-mission transfer
```

## Integration Points

### rd_engine.py

```python
enhancer = RDEEnhancer(mission_id)
report = enhancer.process_cycle_end(cycle, output, files_created, files_modified, summary)
enhanced = enhancer.generate_enhanced_continuation(base_prompt, cycle_output)
```

### dashboard_v2.py

```python
viz = enhancer.export_graph_for_visualization(800, 600)
# Render nodes with viz['nodes'][i]['position']
```

### exploration_hooks.py

```python
enhancer.record_file_exploration(path, summary)
should, reason = enhancer.should_explore(path)
```

## Design Principles

1. **Ratio-based tracking** - Patterns persist as proportions, not absolute counts
2. **Self-calibration** - Systems learn from their effectiveness
3. **Minimal intrusion** - Scaffolds intervene only when needed
4. **Persistent memory** - Knowledge accumulates across missions
5. **Graceful degradation** - Continue working when components fail
6. **Thread safety** - Safe for concurrent access

## License

Part of the mini-mind-v2 project.
