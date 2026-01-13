#!/usr/bin/env python3
"""
Generate ENVIRONMENT.md from detected hardware.

This script detects the system's hardware profile and generates
an ENVIRONMENT.md file that documents available resources for
the autonomous agent to consider during planning.
"""

import os
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def run_command(cmd: list, default: str = "Unknown") -> str:
    """Run a command and return output, or default if it fails."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.stdout.strip() if result.returncode == 0 else default
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return default


def get_gpu_info() -> dict:
    """Detect GPU information."""
    info = {
        "available": False,
        "name": "None detected",
        "memory_total": "N/A",
        "memory_free": "N/A",
        "cuda_available": False,
        "cuda_version": "N/A"
    }

    # Try nvidia-smi
    nvidia_output = run_command([
        "nvidia-smi",
        "--query-gpu=name,memory.total,memory.free",
        "--format=csv,noheader,nounits"
    ])

    if nvidia_output and nvidia_output != "Unknown":
        parts = nvidia_output.split(",")
        if len(parts) >= 3:
            info["available"] = True
            info["name"] = parts[0].strip()
            info["memory_total"] = f"{parts[1].strip()} MiB"
            info["memory_free"] = f"{parts[2].strip()} MiB"

    # Check CUDA availability via Python
    try:
        import torch
        info["cuda_available"] = torch.cuda.is_available()
        if info["cuda_available"]:
            info["cuda_version"] = torch.version.cuda or "N/A"
    except ImportError:
        pass

    return info


def get_cpu_info() -> dict:
    """Detect CPU information."""
    info = {
        "model": "Unknown",
        "cores": 0,
        "threads": 0,
        "architecture": platform.machine()
    }

    # Try lscpu
    lscpu_output = run_command(["lscpu"])
    if lscpu_output != "Unknown":
        for line in lscpu_output.split("\n"):
            if "Model name:" in line:
                info["model"] = line.split(":", 1)[1].strip()
            elif line.startswith("CPU(s):"):
                try:
                    info["threads"] = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif "Core(s) per socket:" in line:
                try:
                    info["cores"] = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass

    # Fallback to /proc/cpuinfo
    if info["model"] == "Unknown":
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "model name" in line:
                        info["model"] = line.split(":", 1)[1].strip()
                        break
        except Exception:
            pass

    # Fallback for thread count
    if info["threads"] == 0:
        info["threads"] = os.cpu_count() or 1

    return info


def get_memory_info() -> dict:
    """Detect memory information."""
    info = {
        "total": "Unknown",
        "available": "Unknown",
        "swap_total": "Unknown"
    }

    # Try free command
    free_output = run_command(["free", "-h"])
    if free_output != "Unknown":
        lines = free_output.split("\n")
        for line in lines:
            if line.startswith("Mem:"):
                parts = line.split()
                if len(parts) >= 2:
                    info["total"] = parts[1]
                if len(parts) >= 7:
                    info["available"] = parts[6]
            elif line.startswith("Swap:"):
                parts = line.split()
                if len(parts) >= 2:
                    info["swap_total"] = parts[1]

    return info


def get_disk_info() -> dict:
    """Detect disk information for relevant paths."""
    info = {
        "root_total": "Unknown",
        "root_available": "Unknown"
    }

    # Try df command
    df_output = run_command(["df", "-h", "/"])
    if df_output != "Unknown":
        lines = df_output.split("\n")
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 4:
                info["root_total"] = parts[1]
                info["root_available"] = parts[3]

    return info


def check_services() -> dict:
    """Check for running services relevant to AI-AtlasForge."""
    services = {
        "ollama": False,
        "docker": False
    }

    # Check ollama
    ps_output = run_command(["pgrep", "-x", "ollama"])
    services["ollama"] = bool(ps_output and ps_output != "Unknown")

    # Check docker
    docker_output = run_command(["docker", "info"])
    services["docker"] = docker_output != "Unknown" and "Server Version" in docker_output

    return services


def generate_environment_md(output_path: Path) -> None:
    """Generate ENVIRONMENT.md with detected hardware profile."""

    gpu = get_gpu_info()
    cpu = get_cpu_info()
    memory = get_memory_info()
    disk = get_disk_info()
    services = check_services()

    content = f"""# Environment Profile

Auto-generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

This file documents the hardware profile of this installation.
The autonomous agent uses this information for resource-aware planning.

## System Overview

| Component | Details |
|-----------|---------|
| OS | {platform.system()} {platform.release()} |
| Architecture | {cpu['architecture']} |
| Python | {platform.python_version()} |

## CPU

| Property | Value |
|----------|-------|
| Model | {cpu['model']} |
| Cores | {cpu['cores']} |
| Threads | {cpu['threads']} |

## Memory

| Property | Value |
|----------|-------|
| Total RAM | {memory['total']} |
| Available | {memory['available']} |
| Swap | {memory['swap_total']} |

## GPU

| Property | Value |
|----------|-------|
| Available | {'Yes' if gpu['available'] else 'No'} |
| Model | {gpu['name']} |
| VRAM Total | {gpu['memory_total']} |
| VRAM Free | {gpu['memory_free']} |
| CUDA Available | {'Yes' if gpu['cuda_available'] else 'No'} |
| CUDA Version | {gpu['cuda_version']} |

## Storage

| Path | Total | Available |
|------|-------|-----------|
| / | {disk['root_total']} | {disk['root_available']} |

## Services

| Service | Status |
|---------|--------|
| Ollama | {'Running' if services['ollama'] else 'Not running'} |
| Docker | {'Available' if services['docker'] else 'Not available'} |

## Resource Recommendations

Based on detected hardware:

"""

    # Add recommendations based on detected resources
    recommendations = []

    if gpu['available']:
        recommendations.append(f"- **GPU Available**: Use CUDA for ML workloads. {gpu['name']} detected with {gpu['memory_total']} VRAM.")
    else:
        recommendations.append("- **No GPU**: Fall back to CPU for ML workloads. Consider smaller models.")

    if cpu['threads'] >= 8:
        recommendations.append(f"- **Multi-core CPU**: Use multiprocessing for parallel tasks ({cpu['threads']} threads available).")

    # Parse memory to give recommendations
    try:
        mem_str = memory['total'].replace('Gi', '').replace('G', '').strip()
        mem_gb = float(mem_str)
        if mem_gb >= 32:
            recommendations.append(f"- **High RAM**: Can cache large datasets in memory ({memory['total']} total).")
        elif mem_gb >= 16:
            recommendations.append(f"- **Moderate RAM**: Use memory-efficient data structures ({memory['total']} total).")
    except (ValueError, AttributeError):
        pass

    if services['ollama']:
        recommendations.append("- **Ollama Running**: Local LLM available for testing and data generation.")

    if services['docker']:
        recommendations.append("- **Docker Available**: Can use containerized environments if needed.")

    content += "\n".join(recommendations) if recommendations else "- No specific recommendations."

    content += """

## Notes

- This file is auto-generated by `scripts/generate_environment.py`
- Regenerate with: `python3 scripts/generate_environment.py`
- Resource availability may change at runtime (especially GPU VRAM)
"""

    output_path.write_text(content)
    print(f"Generated: {output_path}")


def main():
    # Determine output path
    script_dir = Path(__file__).resolve().parent
    atlasforge_root = script_dir.parent
    output_path = atlasforge_root / "ENVIRONMENT.md"

    print("Detecting hardware profile...")
    generate_environment_md(output_path)
    print("Done!")


if __name__ == "__main__":
    main()
