#!/usr/bin/env python3
"""
Experiment Framework for Claude Autonomous

This module provides infrastructure for running controlled experiments
with fresh Claude instances, collecting results, and comparing outcomes.

Key capabilities:
1. Spawn fresh Claude instances with controlled prompts
2. Run experiments with multiple conditions
3. Collect and store results in structured format
4. Compare results across experiments and models
"""

import json
import subprocess
import time
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, asdict
from enum import Enum
import hashlib


# ============================================================================
# CONFIGURATION - use centralized configuration
# ============================================================================

from atlasforge_config import BASE_DIR
EXPERIMENTS_DIR = BASE_DIR / "experiments"
RESULTS_DIR = EXPERIMENTS_DIR / "results"

# Ensure directories exist
EXPERIMENTS_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)


# ============================================================================
# DATA STRUCTURES
# ============================================================================

class ModelType(Enum):
    """Available model types for experiments."""
    CLAUDE_SONNET = "sonnet"
    CLAUDE_OPUS = "opus"
    CLAUDE_HAIKU = "haiku"
    MINI_MIND = "llama3.1:8b"  # Local Ollama model


@dataclass
class TrialResult:
    """Result from a single experimental trial."""
    trial_id: str
    condition: str
    prompt: str
    system_prompt: Optional[str]
    response: str
    response_time_ms: float
    model: str
    timestamp: str
    metadata: Dict[str, Any]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExperimentConfig:
    """Configuration for an experiment."""
    name: str
    description: str
    conditions: List[str]
    model: ModelType
    timeout_seconds: int = 120
    trials_per_condition: int = 1
    system_prompt: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d['model'] = self.model.value
        return d


@dataclass
class ExperimentResults:
    """Complete results from an experiment."""
    config: ExperimentConfig
    trials: List[TrialResult]
    summary: Dict[str, Any]
    started_at: str
    completed_at: str

    def save(self, filepath: Optional[Path] = None) -> Path:
        """Save results to JSON file."""
        if filepath is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{self.config.name}_{timestamp}.json"
            filepath = RESULTS_DIR / filename

        data = {
            "config": self.config.to_dict(),
            "trials": [t.to_dict() for t in self.trials],
            "summary": self.summary,
            "started_at": self.started_at,
            "completed_at": self.completed_at
        }

        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)

        return filepath


# ============================================================================
# INSTANCE SPAWNING
# ============================================================================

def invoke_fresh_claude(
    prompt: str,
    model: ModelType = ModelType.CLAUDE_SONNET,
    system_prompt: Optional[str] = None,
    timeout: int = 120,
    cwd: Optional[Path] = None
) -> tuple[str, float]:
    """
    Invoke a fresh Claude instance with no prior context.

    Args:
        prompt: The prompt to send
        model: Which model to use
        system_prompt: Optional system prompt
        timeout: Timeout in seconds
        cwd: Working directory for the command

    Returns:
        Tuple of (response_text, response_time_ms)
    """
    if cwd is None:
        cwd = BASE_DIR

    start_time = time.time()

    if model == ModelType.MINI_MIND:
        # Use ollama for local model
        response = _invoke_ollama(prompt, model.value, timeout)
    else:
        # Use claude CLI for Claude models
        response = _invoke_claude_cli(prompt, model.value, system_prompt, timeout, cwd)

    elapsed_ms = (time.time() - start_time) * 1000
    return response, elapsed_ms


def _invoke_claude_cli(
    prompt: str,
    model: str,
    system_prompt: Optional[str],
    timeout: int,
    cwd: Path
) -> str:
    """Invoke Claude via CLI."""
    cmd = ["claude", "-p", "--dangerously-skip-permissions"]

    if model:
        cmd.extend(["--model", model])

    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])

    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd),
            start_new_session=True  # Prevent FD inheritance blocking from background processes
        )

        if result.returncode == 0:
            return result.stdout.strip()
        else:
            return f"ERROR: {result.stderr}"
    except subprocess.TimeoutExpired:
        return "ERROR: Timeout"
    except Exception as e:
        return f"ERROR: {str(e)}"


def _invoke_ollama(
    prompt: str,
    model: str,
    timeout: int
) -> str:
    """Invoke local model via Ollama."""
    try:
        result = subprocess.run(
            ["ollama", "run", model, prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
            start_new_session=True  # Prevent FD inheritance blocking from background processes
        )

        if result.returncode == 0:
            return result.stdout.strip()
        else:
            return f"ERROR: {result.stderr}"
    except subprocess.TimeoutExpired:
        return "ERROR: Timeout"
    except Exception as e:
        return f"ERROR: {str(e)}"


# ============================================================================
# EXPERIMENT EXECUTION
# ============================================================================

class Experiment:
    """
    Manages execution of a controlled experiment.

    Usage:
        exp = Experiment(config)
        exp.add_condition("baseline", baseline_prompt_fn)
        exp.add_condition("with_scaffold", scaffold_prompt_fn)
        results = exp.run()
        results.save()
    """

    def __init__(self, config: ExperimentConfig):
        self.config = config
        self.condition_prompts: Dict[str, Callable[[], str]] = {}
        self.trials: List[TrialResult] = []
        self.started_at: Optional[str] = None
        self.completed_at: Optional[str] = None

    def add_condition(self, name: str, prompt_generator: Callable[[], str]):
        """Add an experimental condition with its prompt generator."""
        if name not in self.config.conditions:
            raise ValueError(f"Condition '{name}' not in config.conditions")
        self.condition_prompts[name] = prompt_generator

    def run(self, progress_callback: Optional[Callable[[str], None]] = None) -> ExperimentResults:
        """
        Execute the experiment.

        Args:
            progress_callback: Optional function to call with progress updates

        Returns:
            ExperimentResults containing all trial data and summary
        """
        self.started_at = datetime.now().isoformat()
        self.trials = []

        total_trials = len(self.config.conditions) * self.config.trials_per_condition
        trial_num = 0

        for condition in self.config.conditions:
            if condition not in self.condition_prompts:
                raise ValueError(f"No prompt generator for condition '{condition}'")

            prompt_gen = self.condition_prompts[condition]

            for i in range(self.config.trials_per_condition):
                trial_num += 1

                if progress_callback:
                    progress_callback(f"Running trial {trial_num}/{total_trials}: {condition}")

                prompt = prompt_gen()
                response, response_time = invoke_fresh_claude(
                    prompt=prompt,
                    model=self.config.model,
                    system_prompt=self.config.system_prompt,
                    timeout=self.config.timeout_seconds
                )

                trial_id = f"{condition}_{i}_{hashlib.md5(prompt.encode()).hexdigest()[:8]}"

                trial = TrialResult(
                    trial_id=trial_id,
                    condition=condition,
                    prompt=prompt,
                    system_prompt=self.config.system_prompt,
                    response=response,
                    response_time_ms=response_time,
                    model=self.config.model.value,
                    timestamp=datetime.now().isoformat(),
                    metadata={}
                )

                self.trials.append(trial)

        self.completed_at = datetime.now().isoformat()

        summary = self._compute_summary()

        return ExperimentResults(
            config=self.config,
            trials=self.trials,
            summary=summary,
            started_at=self.started_at,
            completed_at=self.completed_at
        )

    def _compute_summary(self) -> Dict[str, Any]:
        """Compute summary statistics for the experiment."""
        summary = {
            "total_trials": len(self.trials),
            "by_condition": {}
        }

        for condition in self.config.conditions:
            condition_trials = [t for t in self.trials if t.condition == condition]

            if condition_trials:
                response_times = [t.response_time_ms for t in condition_trials]
                errors = [t for t in condition_trials if t.response.startswith("ERROR")]

                summary["by_condition"][condition] = {
                    "trials": len(condition_trials),
                    "errors": len(errors),
                    "avg_response_time_ms": sum(response_times) / len(response_times),
                    "min_response_time_ms": min(response_times),
                    "max_response_time_ms": max(response_times)
                }

        return summary


# ============================================================================
# RESULT ANALYSIS
# ============================================================================

def load_experiment_results(filepath: Path) -> ExperimentResults:
    """Load experiment results from JSON file."""
    with open(filepath) as f:
        data = json.load(f)

    config = ExperimentConfig(
        name=data["config"]["name"],
        description=data["config"]["description"],
        conditions=data["config"]["conditions"],
        model=ModelType(data["config"]["model"]),
        timeout_seconds=data["config"].get("timeout_seconds", 120),
        trials_per_condition=data["config"].get("trials_per_condition", 1),
        system_prompt=data["config"].get("system_prompt"),
        metadata=data["config"].get("metadata")
    )

    trials = [
        TrialResult(**t) for t in data["trials"]
    ]

    return ExperimentResults(
        config=config,
        trials=trials,
        summary=data["summary"],
        started_at=data["started_at"],
        completed_at=data["completed_at"]
    )


def compare_experiments(results_list: List[ExperimentResults]) -> Dict[str, Any]:
    """
    Compare results across multiple experiments.

    Args:
        results_list: List of ExperimentResults to compare

    Returns:
        Dictionary with comparison data
    """
    comparison = {
        "experiments": [],
        "comparison_timestamp": datetime.now().isoformat()
    }

    for results in results_list:
        exp_summary = {
            "name": results.config.name,
            "model": results.config.model.value,
            "total_trials": len(results.trials),
            "conditions": results.summary.get("by_condition", {})
        }
        comparison["experiments"].append(exp_summary)

    return comparison


# ============================================================================
# BUILT-IN EXPERIMENT TEMPLATES
# ============================================================================

def create_accuracy_test_experiment(
    name: str,
    test_cases: List[Dict[str, Any]],
    model: ModelType = ModelType.CLAUDE_SONNET,
    description: str = ""
) -> Experiment:
    """
    Create an accuracy test experiment with predefined test cases.

    Args:
        name: Experiment name
        test_cases: List of dicts with 'prompt' and 'expected' keys
        model: Which model to test
        description: Optional description

    Returns:
        Configured Experiment ready to run
    """
    conditions = [f"test_{i}" for i in range(len(test_cases))]

    config = ExperimentConfig(
        name=name,
        description=description or f"Accuracy test with {len(test_cases)} cases",
        conditions=conditions,
        model=model,
        trials_per_condition=1
    )

    exp = Experiment(config)

    for i, test_case in enumerate(test_cases):
        condition_name = f"test_{i}"
        exp.add_condition(condition_name, lambda tc=test_case: tc["prompt"])

    return exp


def create_comparison_experiment(
    name: str,
    prompt: str,
    models: List[ModelType],
    trials_per_model: int = 3,
    description: str = ""
) -> List[Experiment]:
    """
    Create experiments to compare the same prompt across different models.

    Args:
        name: Base experiment name
        prompt: The prompt to test
        models: List of models to compare
        trials_per_model: How many trials per model
        description: Optional description

    Returns:
        List of configured Experiments
    """
    experiments = []

    for model in models:
        config = ExperimentConfig(
            name=f"{name}_{model.value}",
            description=description or f"Model comparison: {model.value}",
            conditions=["test"],
            model=model,
            trials_per_condition=trials_per_model
        )

        exp = Experiment(config)
        exp.add_condition("test", lambda: prompt)
        experiments.append(exp)

    return experiments


# ============================================================================
# SCORING UTILITIES
# ============================================================================

def score_response(
    response: str,
    expected: str,
    scoring_type: str = "contains"
) -> tuple[bool, float]:
    """
    Score a response against expected answer.

    Args:
        response: The model's response
        expected: The expected answer
        scoring_type: One of 'exact', 'contains', 'keyword'

    Returns:
        Tuple of (is_correct, confidence_score)
    """
    response_lower = response.lower()
    expected_lower = expected.lower()

    if scoring_type == "exact":
        is_correct = response_lower.strip() == expected_lower.strip()
        confidence = 1.0 if is_correct else 0.0

    elif scoring_type == "contains":
        is_correct = expected_lower in response_lower
        confidence = 1.0 if is_correct else 0.0

    elif scoring_type == "keyword":
        # Check if key terms from expected are in response
        expected_words = set(expected_lower.split())
        response_words = set(response_lower.split())
        overlap = len(expected_words & response_words)
        confidence = overlap / len(expected_words) if expected_words else 0.0
        is_correct = confidence >= 0.5

    else:
        raise ValueError(f"Unknown scoring_type: {scoring_type}")

    return is_correct, confidence


# ============================================================================
# CLI ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    # Example usage / self-test
    print("Experiment Framework - Self Test")
    print("=" * 50)

    # Create a simple test experiment
    config = ExperimentConfig(
        name="framework_self_test",
        description="Test that the experiment framework works",
        conditions=["simple_test"],
        model=ModelType.CLAUDE_HAIKU,  # Use haiku for fast test
        timeout_seconds=30,
        trials_per_condition=1
    )

    exp = Experiment(config)
    exp.add_condition("simple_test", lambda: "What is 2+2? Reply with just the number.")

    print("Running self-test...")
    results = exp.run(progress_callback=lambda msg: print(f"  {msg}"))

    # Check result
    if results.trials:
        trial = results.trials[0]
        print(f"\nResponse: {trial.response}")
        print(f"Time: {trial.response_time_ms:.0f}ms")

        is_correct, _ = score_response(trial.response, "4", "contains")
        print(f"Correct: {is_correct}")

    # Save results
    filepath = results.save()
    print(f"\nResults saved to: {filepath}")

    print("\nFramework self-test complete!")
