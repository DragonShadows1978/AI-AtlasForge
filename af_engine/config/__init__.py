"""
af_engine.config - Configuration Loading and Validation

This module provides configuration loading from YAML files for stages
and integrations. It also provides validation for configuration schemas.

Configuration Files:
    - stage_definitions.yaml: Stage handler configuration
    - integration_config.yaml: Integration handler configuration
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Configuration directory
CONFIG_DIR = Path(__file__).parent

# Default configuration file paths
STAGE_DEFINITIONS_PATH = CONFIG_DIR / "stage_definitions.yaml"
INTEGRATION_CONFIG_PATH = CONFIG_DIR / "integration_config.yaml"


def load_yaml(path: Path) -> Dict[str, Any]:
    """
    Load a YAML configuration file.

    Args:
        path: Path to the YAML file

    Returns:
        Parsed YAML content as a dictionary

    Raises:
        FileNotFoundError: If the file doesn't exist
        yaml.YAMLError: If the file contains invalid YAML
    """
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not installed, using fallback config")
        return {}

    if not path.exists():
        logger.warning(f"Configuration file not found: {path}")
        return {}

    with open(path, 'r') as f:
        return yaml.safe_load(f) or {}


def load_stage_definitions() -> Dict[str, Any]:
    """Load stage definitions from YAML."""
    return load_yaml(STAGE_DEFINITIONS_PATH)


def load_integration_config() -> Dict[str, Any]:
    """Load integration configuration from YAML."""
    return load_yaml(INTEGRATION_CONFIG_PATH)


def get_stage_config(stage_name: str) -> Optional[Dict[str, Any]]:
    """
    Get configuration for a specific stage.

    Args:
        stage_name: Name of the stage (e.g., 'PLANNING')

    Returns:
        Stage configuration dictionary or None if not found
    """
    config = load_stage_definitions()
    return config.get('stages', {}).get(stage_name)


def get_integration_config(integration_name: str) -> Optional[Dict[str, Any]]:
    """
    Get configuration for a specific integration.

    Args:
        integration_name: Name of the integration (e.g., 'analytics')

    Returns:
        Integration configuration dictionary or None if not found
    """
    config = load_integration_config()
    return config.get('integrations', {}).get(integration_name)


__all__ = [
    'CONFIG_DIR',
    'STAGE_DEFINITIONS_PATH',
    'INTEGRATION_CONFIG_PATH',
    'load_yaml',
    'load_stage_definitions',
    'load_integration_config',
    'get_stage_config',
    'get_integration_config',
]
