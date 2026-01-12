# RDE Enhancements Quick Start Guide

Get started with the RDE enhancement suite in 5 minutes.

## Installation

```bash
pip install numpy sentence-transformers torch
```

## Basic Usage

### 1. Initialize the Enhancer

```python
from rde_enhancements import RDEEnhancer
from pathlib import Path

# Create an enhancer for your mission
enhancer = RDEEnhancer(
    mission_id="build_api_v1",
    storage_base=Path("./my_rde_data")
)
```

### 2. Set Mission Baseline

Set the original mission to track continuity:

```python
mission = """
Build a REST API for user management with:
- JWT authentication
- Role-based access control
- Rate limiting
Security is critical.
"""

enhancer.set_mission_baseline(mission)
```

### 3. Record Explorations

As you explore the codebase, record what you learn:

```python
# Record file explorations
enhancer.record_file_exploration(
    path="/src/api/auth.py",
    summary="JWT authentication endpoints using PyJWT",
    tags=["api", "security", "auth"]
)

# Record discovered concepts
enhancer.record_concept(
    name="Token Bucket Algorithm",
    summary="Rate limiting using token bucket with 100 req/min capacity"
)

# Record patterns you find
enhancer.record_pattern(
    name="Decorator Auth",
    summary="Using @requires_auth decorator for endpoint protection",
    code_example="@requires_auth(roles=['admin'])\ndef protected_route():"
)

# Record insights/gotchas
enhancer.record_insight(
    insight_type="gotcha",
    title="Session Expiry",
    description="JWT tokens expire after 1 hour, refresh tokens after 7 days",
    confidence=0.9
)
```

### 4. Query What You Know

Ask the exploration memory about topics:

```python
# Get everything we know about a topic
knowledge = enhancer.what_do_we_know("authentication")

print(f"Summary: {knowledge['summary']}")
print(f"Files: {len(knowledge['files'])}")
print(f"Insights: {len(knowledge['insights'])}")

# Check if you should explore a file
should, reason = enhancer.should_explore("/src/db/models.py")
print(f"Should explore: {should}")
print(f"Reason: {reason}")
```

### 5. Check for Mission Drift

After working on the mission, check if you've drifted:

```python
current_output = """
I've been exploring the frontend framework options.
Looking at React vs Vue for the admin dashboard.
Also considering mobile app architecture.
"""

report = enhancer.check_continuity(current_output)

print(f"Similarity to mission: {report.overall_similarity:.1%}")
print(f"Drift severity: {report.drift_severity}")

if report.healing_recommended:
    print("\nHealing prompt:")
    print(report.healing_prompt)
```

### 6. Process Cycle End

At the end of each work cycle, process everything:

```python
cycle_output = """
Implemented JWT authentication in /src/api/auth.py.
Added rate limiting middleware.
Created user model and CRUD endpoints.
"""

report = enhancer.process_cycle_end(
    cycle_number=1,
    cycle_output=cycle_output,
    files_created=["/src/api/auth.py", "/src/middleware/rate_limit.py"],
    files_modified=["/src/models/user.py"],
    cycle_summary="Core authentication and rate limiting implemented"
)

print(f"Cycle {report['cycle']} complete")
print(f"Drift: {report['drift']['severity']}")
print(f"Added: {report['exploration']['added']}")
```

### 7. Apply Prompt Scaffolding

Reduce cognitive biases in continuation prompts:

```python
previous_response = """
This approach is definitely the best solution.
There's absolutely no reason to consider alternatives.
"""

scaffolded, analysis = enhancer.scaffold_prompt(
    prompt="Continue implementing the feature",
    previous_response=previous_response
)

# The scaffolded prompt will include bias-reducing additions
print(scaffolded)
```

## Advanced Features

### Semantic Search

Search exploration memory semantically:

```python
# Search nodes
results = enhancer.exploration_graph.semantic_search(
    "security patterns",
    top_k=5
)
for node, score in results:
    print(f"{node.name}: {score:.2f}")

# Search insights
insights = enhancer.exploration_graph.semantic_search_insights(
    "authentication best practices"
)
```

### Knowledge Transfer

Leverage knowledge from prior missions:

```python
enhancer.enable_knowledge_transfer(
    missions_base=Path("/home/vader/mini-mind-v2/missions"),
    current_mission_context="Building a REST API with authentication"
)

# Query prior knowledge
prior = enhancer.query_prior_knowledge("JWT implementation", top_k=5)
for item in prior:
    print(f"From {item['source_mission']}: {item['title']}")

# Get starting point suggestions
suggestions = enhancer.get_starting_point_suggestions()
```

### Graph Visualization

Export graph for visualization:

```python
viz_data = enhancer.export_graph_for_visualization(
    width=800,
    height=600
)

# viz_data contains nodes with positions and edges
for node in viz_data['nodes'][:5]:
    print(f"{node['name']} at {node['position']}")
```

### Memory Management

Monitor and manage graph memory:

```python
# Get memory stats
stats = enhancer.exploration_graph.get_memory_usage()
print(f"Nodes: {stats['node_count']}")
print(f"Memory: {stats['estimated_embedding_memory_mb']:.1f} MB")

# Prune if needed
if stats['needs_pruning']:
    pruned = enhancer.exploration_graph.prune_old_nodes()
    print(f"Pruned {pruned} old nodes")
```

## Common Patterns

### Pattern 1: Exploration-First Development

```python
enhancer = RDEEnhancer("feature_x")
enhancer.set_mission_baseline(mission_text)

# Phase 1: Explore
for file in files_to_explore:
    # Read and understand the file
    enhancer.record_file_exploration(file, summary)

# Phase 2: Check knowledge
knowledge = enhancer.what_do_we_know("relevant_topic")

# Phase 3: Build informed by exploration
```

### Pattern 2: Multi-Cycle Mission

```python
enhancer = RDEEnhancer("multi_cycle_mission")
enhancer.set_mission_baseline(mission_text)

for cycle in range(1, max_cycles + 1):
    # Do work...

    # Check continuity
    report = enhancer.check_continuity(cycle_output)
    if report.drift_severity in ['moderate', 'severe']:
        # Apply healing
        continuation = enhancer.heal_continuation(base_prompt, cycle_output)

    # Process cycle end
    enhancer.process_cycle_end(cycle, cycle_output, created, modified, summary)
```

### Pattern 3: Cross-Mission Learning

```python
# New mission starting
enhancer = RDEEnhancer("new_mission")
enhancer.enable_knowledge_transfer()

# Check what we learned before
prior = enhancer.query_prior_knowledge("similar_problem")
if prior:
    print(f"Found {len(prior)} relevant prior insights")

# Import useful insights
enhancer.import_prior_insights(max_imports=10)
```

## Troubleshooting

### Embedding Model Not Loading

If semantic search falls back to keyword search:

```python
from rde_enhancements.exploration_graph import EmbeddingModel

# Check availability
print(f"Available: {EmbeddingModel.is_available()}")
print(f"Device: {EmbeddingModel.get_device()}")

# Reset to retry
EmbeddingModel.reset()
```

### Memory Usage High

If graph is consuming too much memory:

```python
# Lower the max nodes threshold
enhancer.exploration_graph.set_max_nodes(5000)

# Force pruning
enhancer.exploration_graph.prune_old_nodes(target_count=4000)

# Save to persist
enhancer.exploration_graph.save()
```

### Thread Safety Issues

All operations are thread-safe, but for best performance:

```python
# Use a single enhancer instance
enhancer = RDEEnhancer("shared_mission")

# Safe from multiple threads
with ThreadPoolExecutor(max_workers=5) as executor:
    futures = [
        executor.submit(enhancer.record_file_exploration, path, summary)
        for path, summary in explorations
    ]
```

## Next Steps

- Read the full [README.md](README.md) for complete API reference
- Check the test suite for more examples: `workspace/tests/test_rde_integration.py`
- Explore the source code in `rde_enhancements/*.py`
