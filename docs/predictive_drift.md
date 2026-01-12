# Predictive Drift Prevention System

A machine learning-based system for predicting and preventing mission drift in the R&D Engine before it occurs.

## Overview

The Predictive Drift Prevention System uses ML models to predict when an agent is about to drift from its mission objectives, enabling proactive intervention through "nudges" rather than reactive correction after drift has already occurred.

### Key Features

- **Real-time Drift Prediction**: ML model predicts drift risk based on exploration patterns
- **Proactive Nudges**: Warns agents before drift occurs, not after
- **Risk Heatmap**: Visualizes which file categories are drift-prone
- **Path Recommendations**: Suggests optimal exploration paths
- **A/B Testing Framework**: Compares predictive vs reactive intervention effectiveness
- **Dashboard Widget**: Real-time visualization in the R&D Dashboard

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Predictive Drift Prevention                      │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────┐  │
│  │ Exploration  │───▶│   Feature    │───▶│   Drift Predictor    │  │
│  │    Hooks     │    │   Engine     │    │   (Random Forest)    │  │
│  └──────────────┘    └──────────────┘    └──────────────────────┘  │
│         │                   │                      │                │
│         ▼                   ▼                      ▼                │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────┐  │
│  │   Decision   │    │  Similarity  │    │   Nudge Generator    │  │
│  │    Graph     │    │   Tracker    │    │   (4 severity lvls)  │  │
│  └──────────────┘    └──────────────┘    └──────────────────────┘  │
│                                                    │                │
│                                                    ▼                │
│                                          ┌──────────────────────┐  │
│                                          │   WebSocket Events   │──┼──▶ Dashboard
│                                          └──────────────────────┘  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Modules

### Core Modules (`/home/vader/mini-mind-v2/predictive_drift/`)

| Module | Purpose |
|--------|---------|
| `__init__.py` | Package initialization, singleton predictor access |
| `drift_predictor.py` | ML model for drift prediction (Random Forest) |
| `feature_engine.py` | Feature extraction from exploration patterns |
| `ab_testing.py` | A/B testing framework for intervention comparison |
| `nudge_generator.py` | Generates proactive warnings at 4 severity levels |
| `heatmap_generator.py` | Generates drift risk heatmaps by file category |
| `path_recommender.py` | Suggests optimal exploration paths |
| `data_collector.py` | Collects training data from historical missions |
| `model_trainer.py` | Trains and validates ML models |

### Dashboard Integration

| Component | Path |
|-----------|------|
| API Blueprint | `/home/vader/mini-mind-v2/dashboard_modules/drift_prevention.py` |
| Widget Module | `/home/vader/mini-mind-v2/dashboard_static/src/modules/drift-prevention.js` |

## API Reference

### Status Endpoint

```http
GET /api/drift-prevention/status
```

Returns current drift prevention status.

**Response:**
```json
{
    "active": true,
    "mission_id": "mission_xyz",
    "model_ready": true,
    "predictive_enabled": true,
    "experiment": {
        "experiment_id": "exp_...",
        "variant": "B",
        "config": {...}
    },
    "nudge_statistics": {
        "total": 5,
        "by_level": {"gentle": 3, "warning": 2}
    }
}
```

### Prediction Endpoint

```http
POST /api/drift-prevention/predict
Content-Type: application/json

{
    "current_file": "/path/to/current/file.py",
    "explored_files": ["/path/to/file1.py", "/path/to/file2.py"],
    "mission_text": "Mission description",
    "similarity_history": [0.95, 0.92, 0.88],
    "velocity_history": [-0.03, -0.04, -0.03]
}
```

**Response:**
```json
{
    "prediction": {
        "risk_score": 0.35,
        "risk_level": "medium",
        "confidence": 0.85,
        "model_version": "1.0.0",
        "contributing_features": {
            "similarity_trend": 0.40,
            "similarity_rolling_mean": 0.18,
            "drift_prone_file_count": 0.15
        }
    }
}
```

### Model Status

```http
GET /api/drift-prevention/model/status
```

Returns ML model information.

**Response:**
```json
{
    "model_loaded": true,
    "model_version": "1.0.0",
    "feature_count": 26,
    "metrics": {
        "auc_roc": 1.0,
        "n_samples": 104,
        "top_features": [...]
    }
}
```

### Train Model

```http
POST /api/drift-prevention/model/train
Content-Type: application/json

{"force": false}
```

Triggers model training.

### Nudges

```http
GET /api/drift-prevention/nudges?count=10
```

Returns recent nudges for the current mission.

### Heatmap

```http
GET /api/drift-prevention/heatmap?rebuild=false
```

Returns drift risk heatmap.

### Recommendations

```http
POST /api/drift-prevention/recommendations
Content-Type: application/json

{
    "current_file": "/path/to/current.py",
    "explored_files": [...]
}
```

Returns path recommendations.

## Feature Engineering

The ML model uses 26 features extracted from exploration patterns:

### Similarity Features
- `current_similarity` - Similarity of current exploration to mission
- `similarity_min_last_5` - Minimum similarity in last 5 files
- `similarity_rolling_mean` - Rolling average similarity
- `similarity_trend` - Direction of similarity (positive = improving)
- `similarity_velocity` - Rate of similarity change
- `similarity_std` - Variance in similarity scores

### File Type Features
- `core_file_ratio` - Proportion of core source files explored
- `test_file_ratio` - Proportion of test files
- `doc_file_ratio` - Proportion of documentation files
- `config_file_ratio` - Proportion of config files
- `drift_prone_file_count` - Count of historically drift-prone files

### Exploration Pattern Features
- `files_explored_count` - Total files explored
- `exploration_depth` - How deep in directory tree
- `path_diversity` - Diversity of exploration paths
- `path_coherence` - Coherence of consecutive file choices

### Mission Context Features
- `mission_length` - Length of mission description
- `failure_count` - Number of failures so far
- `current_cycle` - Current cycle number
- `time_in_cycle` - Time spent in current cycle

## Nudge Levels

| Level | Threshold | Description |
|-------|-----------|-------------|
| `gentle` | 0.3-0.5 | Light suggestion to refocus |
| `warning` | 0.5-0.7 | Clear warning about potential drift |
| `urgent` | 0.7-0.85 | Strong intervention needed |
| `critical` | 0.85+ | Immediate action required |

## A/B Testing

The system includes an A/B testing framework to compare intervention strategies:

### Variants

- **Variant A (Control)**: Reactive intervention - traditional drift detection
- **Variant B (Treatment)**: Predictive intervention - proactive nudges

### Metrics Tracked

- Drift occurrence rate
- Mission completion rate
- Time to first drift
- Nudge response rate
- Recovery success rate

### Usage

```python
from predictive_drift.ab_testing import ABTestingFramework

framework = ABTestingFramework()

# Create experiment
framework.create_experiment(
    name="Predictive vs Reactive",
    variant_a={"intervention_type": "reactive"},
    variant_b={"intervention_type": "predictive"},
)

# Assign mission
assignment = framework.assign_mission("mission_123")

# Record results
framework.record_result("mission_123", {
    "drift_occurred": 0.0,
    "completed": 1.0,
    "nudges_generated": 5,
})

# Get summary
summary = framework.get_experiment_summary(experiment_id)
```

## Dashboard Widget

The widget provides real-time visualization of:

1. **Risk Gauge** - Current drift risk level with visual indicator
2. **Contributing Factors** - Top features contributing to risk score
3. **Proactive Nudges** - Recent nudge notifications
4. **Path Recommendations** - Suggested files to explore next
5. **Risk Heatmap** - File categories by drift risk
6. **A/B Experiment Status** - Current experiment assignment
7. **Model Status** - Model health and training controls

### WebSocket Events

The widget receives live updates via WebSocket:

- `predictive_drift_update` - New prediction available
- `drift_nudge` - Nudge generated
- `path_recommendation` - New recommendations

## Configuration

### Environment Variables

```bash
# Model storage
RDE_DATA_DIR=/home/vader/mini-mind-v2/rde_data

# Feature engineering
DRIFT_NUDGE_THRESHOLD=0.5
DRIFT_ENABLE_HEATMAP=true
DRIFT_ENABLE_RECOMMENDATIONS=true
```

### Model Files

- Model: `/home/vader/mini-mind-v2/rde_data/drift_models/drift_predictor_model.pkl`
- Scaler: `/home/vader/mini-mind-v2/rde_data/drift_models/drift_predictor_scaler.pkl`
- Features: `/home/vader/mini-mind-v2/rde_data/drift_models/drift_predictor_features.json`

## Training Data Collection

Training data is collected from historical missions:

```python
from predictive_drift.data_collector import DriftDataCollector

collector = DriftDataCollector()
samples = collector.collect_from_mission_logs()
```

Data sources:
- Mission logs (`/home/vader/mini-mind-v2/missions/mission_logs/`)
- Decision graphs (`/home/vader/mini-mind-v2/rde_data/decision_graph.db`)
- Exploration history

## Integration with Exploration Hooks

The system integrates with `exploration_hooks.py` to:

1. Track file reads in real-time
2. Extract features from exploration patterns
3. Trigger predictions on significant exploration events
4. Generate nudges when risk exceeds threshold
5. Emit WebSocket events for dashboard updates

## Troubleshooting

### Flask Tensor Issue

If you see "Cannot copy out of meta tensor" errors:

The sentence-transformer model may have threading issues in Flask context. The fix loads the model on CPU first, then moves to GPU:

```python
# In feature_engine.py _get_embedder()
self._embedder = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
if cuda_available:
    self._embedder = self._embedder.to('cuda')
```

### Model Not Training

Check:
1. Sufficient training data (>50 samples recommended)
2. Data directory permissions
3. Model file path exists

### Widget Not Loading

Verify:
1. Dashboard is running (`http://localhost:5000`)
2. API endpoints responding
3. WebSocket connection established
4. Browser console for JavaScript errors

## Future Improvements

1. **Multi-model ensemble** - Combine multiple prediction models
2. **Online learning** - Update model with new data continuously
3. **Personalized thresholds** - Adjust nudge thresholds per agent
4. **Advanced features** - Add code complexity and semantic analysis
5. **Automated hyperparameter tuning** - Optimize model parameters

## References

- Exploration Hooks: `/home/vader/mini-mind-v2/exploration_hooks.py`
- Dashboard Blueprint: `/home/vader/mini-mind-v2/dashboard_modules/drift_prevention.py`
- Knowledge Base: `/home/vader/mini-mind-v2/mission_knowledge_base.py`
