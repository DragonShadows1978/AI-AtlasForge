#!/usr/bin/env python3
"""
Remote Configuration Manager
=============================
Handles Git remote configuration and credential management for the AtlasForge system.

Features:
- Configure remote URLs for repositories
- Test remote connectivity
- SSH key detection and generation
- Integration with gh CLI for GitHub auth
- Update repo_routing.yaml configuration

Usage:
    from remote_config_manager import RemoteConfigManager

    manager = RemoteConfigManager()

    # Configure remote for a repo
    success, message = manager.set_repo_remote("brotato_bot", "git@github.com:user/repo.git")

    # Test connection
    can_connect, details = manager.test_remote_connection("brotato_bot", "git@github.com:user/repo.git")
"""

import os
import re
import subprocess
import logging
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
from dataclasses import dataclass
from datetime import datetime

from atlasforge_config import BASE_DIR

# YAML parsing
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

logger = logging.getLogger("remote_config_manager")


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class RemoteInfo:
    """Remote configuration for a repository."""
    remote_name: str  # e.g., "origin"
    remote_url: str   # e.g., "git@github.com:user/repo.git"
    auth_method: str  # "ssh", "https_gh", "https_token"
    is_configured: bool
    host: Optional[str] = None  # e.g., "github.com"
    user: Optional[str] = None  # e.g., "user"
    repo: Optional[str] = None  # e.g., "repo"


@dataclass
class AuthStatus:
    """Authentication status."""
    is_authenticated: bool
    method: str  # "ssh", "gh_cli", "token", "none"
    details: str
    username: Optional[str] = None


# =============================================================================
# URL Utilities
# =============================================================================

def parse_remote_url(url: str) -> Dict[str, Any]:
    """
    Parse a Git remote URL into components.

    Supports:
        - SSH: git@github.com:user/repo.git
        - HTTPS: https://github.com/user/repo.git
        - HTTPS with token: https://token@github.com/user/repo.git

    Returns:
        Dict with: protocol, host, user, repo, is_valid, error
    """
    result = {
        "protocol": None,
        "host": None,
        "user": None,
        "repo": None,
        "is_valid": False,
        "error": None
    }

    if not url:
        result["error"] = "URL is empty"
        return result

    url = url.strip()

    # SSH format: git@host:user/repo.git
    ssh_match = re.match(r'^git@([^:]+):([^/]+)/(.+?)(?:\.git)?$', url)
    if ssh_match:
        result["protocol"] = "ssh"
        result["host"] = ssh_match.group(1)
        result["user"] = ssh_match.group(2)
        result["repo"] = ssh_match.group(3)
        result["is_valid"] = True
        return result

    # HTTPS format: https://[token@]host/user/repo.git
    https_match = re.match(
        r'^https://(?:[^@]+@)?([^/]+)/([^/]+)/(.+?)(?:\.git)?$',
        url
    )
    if https_match:
        result["protocol"] = "https"
        result["host"] = https_match.group(1)
        result["user"] = https_match.group(2)
        result["repo"] = https_match.group(3)
        result["is_valid"] = True
        return result

    result["error"] = f"Invalid URL format: {url}"
    return result


def validate_remote_url(url: str) -> Tuple[bool, str]:
    """
    Validate a Git remote URL.

    Returns:
        Tuple of (is_valid, error_message_or_empty)
    """
    parsed = parse_remote_url(url)

    if not parsed["is_valid"]:
        return False, parsed["error"]

    # Additional validation
    host = parsed["host"].lower()

    # Known hosts
    known_hosts = ["github.com", "gitlab.com", "bitbucket.org"]
    if not any(h in host for h in known_hosts):
        # Not a known host, but might still be valid (self-hosted)
        logger.info(f"Remote URL uses unknown host: {host}")

    return True, ""


# =============================================================================
# Credential Manager
# =============================================================================

class CredentialManager:
    """
    Manages authentication credentials for Git remotes.

    Supports:
        - SSH key detection and testing
        - gh CLI authentication status
        - SSH key generation (optional)
    """

    DEFAULT_SSH_KEY_PATHS = [
        "~/.ssh/id_ed25519",
        "~/.ssh/id_rsa",
        "~/.ssh/atlasforge_deploy_key"
    ]

    def __init__(self):
        self.ssh_key_path: Optional[str] = None
        self._detect_ssh_key()

    def _detect_ssh_key(self):
        """Detect available SSH key."""
        for key_path in self.DEFAULT_SSH_KEY_PATHS:
            expanded = os.path.expanduser(key_path)
            if os.path.exists(expanded):
                self.ssh_key_path = expanded
                break

    def get_ssh_key_path(self) -> Optional[str]:
        """Get path to detected SSH key."""
        return self.ssh_key_path

    def has_ssh_key(self) -> bool:
        """Check if SSH key is available."""
        return self.ssh_key_path is not None

    def test_ssh_auth(self, host: str = "github.com") -> Tuple[bool, str]:
        """
        Test SSH authentication to a host.

        Args:
            host: Host to test (default: github.com)

        Returns:
            Tuple of (success, message)
        """
        try:
            result = subprocess.run(
                ["ssh", "-T", "-o", "StrictHostKeyChecking=accept-new",
                 "-o", "BatchMode=yes", f"git@{host}"],
                capture_output=True,
                text=True,
                timeout=15
            )

            # GitHub returns exit code 1 but says "successfully authenticated"
            output = result.stdout + result.stderr

            if "successfully authenticated" in output.lower():
                # Extract username if present
                match = re.search(r'Hi ([^!]+)!', output)
                username = match.group(1) if match else "unknown"
                return True, f"SSH authenticated as {username}"

            if "permission denied" in output.lower():
                return False, "SSH permission denied - key not authorized"

            if "could not resolve" in output.lower():
                return False, f"Could not resolve host: {host}"

            # Check if at least connection worked
            if result.returncode in [0, 1] and "authenticated" not in output.lower():
                return False, f"SSH connection issue: {output.strip()[:100]}"

            return False, f"SSH test failed: {output.strip()[:100]}"

        except subprocess.TimeoutExpired:
            return False, "SSH connection timed out"
        except FileNotFoundError:
            return False, "SSH client not installed"
        except Exception as e:
            return False, f"SSH test error: {e}"

    def get_gh_auth_status(self) -> Tuple[bool, str, Optional[str]]:
        """
        Check GitHub CLI authentication status.

        Returns:
            Tuple of (is_authenticated, status_message, username_or_none)
        """
        try:
            result = subprocess.run(
                ["gh", "auth", "status"],
                capture_output=True,
                text=True,
                timeout=10
            )

            output = result.stdout + result.stderr

            if result.returncode == 0:
                # Extract username
                match = re.search(r'Logged in to github.com account (\S+)', output)
                username = match.group(1) if match else None
                return True, "Authenticated via gh CLI", username

            return False, "Not authenticated with gh CLI", None

        except FileNotFoundError:
            return False, "gh CLI not installed", None
        except subprocess.TimeoutExpired:
            return False, "gh CLI check timed out", None
        except Exception as e:
            return False, f"gh CLI error: {e}", None

    def get_auth_method(self, url: str) -> str:
        """
        Determine the authentication method for a URL.

        Returns:
            "ssh", "https_gh", "https_token", or "unknown"
        """
        parsed = parse_remote_url(url)

        if not parsed["is_valid"]:
            return "unknown"

        if parsed["protocol"] == "ssh":
            return "ssh"

        if parsed["protocol"] == "https":
            # Check if gh CLI is available for GitHub
            if parsed["host"] == "github.com":
                is_auth, _, _ = self.get_gh_auth_status()
                if is_auth:
                    return "https_gh"

            return "https_token"

        return "unknown"

    def get_auth_status(self, url: str) -> AuthStatus:
        """
        Get comprehensive authentication status for a URL.

        Returns:
            AuthStatus object
        """
        parsed = parse_remote_url(url)

        if not parsed["is_valid"]:
            return AuthStatus(
                is_authenticated=False,
                method="none",
                details="Invalid URL format"
            )

        host = parsed["host"]

        # SSH URL
        if parsed["protocol"] == "ssh":
            success, message = self.test_ssh_auth(host)
            return AuthStatus(
                is_authenticated=success,
                method="ssh",
                details=message,
                username=None  # Could extract from message
            )

        # HTTPS URL
        if parsed["protocol"] == "https":
            if host == "github.com":
                is_auth, message, username = self.get_gh_auth_status()
                return AuthStatus(
                    is_authenticated=is_auth,
                    method="gh_cli" if is_auth else "none",
                    details=message,
                    username=username
                )

            # Other hosts - assume need token
            return AuthStatus(
                is_authenticated=False,
                method="token",
                details="HTTPS requires personal access token"
            )

        return AuthStatus(
            is_authenticated=False,
            method="none",
            details="Unknown protocol"
        )

    def generate_ssh_key(
        self,
        key_name: str = "atlasforge_deploy_key",
        email: str = "atlasforge-bot@localhost"
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Generate a new SSH key for deployments.

        Args:
            key_name: Name for the key file
            email: Email for the key comment

        Returns:
            Tuple of (success, message, public_key_path_or_none)
        """
        ssh_dir = os.path.expanduser("~/.ssh")
        key_path = os.path.join(ssh_dir, key_name)
        pub_path = f"{key_path}.pub"

        # Don't overwrite existing keys
        if os.path.exists(key_path):
            return False, f"Key already exists: {key_path}", pub_path if os.path.exists(pub_path) else None

        # Ensure .ssh directory exists
        os.makedirs(ssh_dir, mode=0o700, exist_ok=True)

        try:
            result = subprocess.run(
                [
                    "ssh-keygen",
                    "-t", "ed25519",
                    "-C", email,
                    "-f", key_path,
                    "-N", ""  # No passphrase for automation
                ],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0 and os.path.exists(pub_path):
                # Read public key
                with open(pub_path, 'r') as f:
                    pub_key = f.read().strip()

                logger.info(f"Generated SSH key: {key_path}")
                self.ssh_key_path = key_path

                return True, f"Generated key: {key_path}", pub_path

            return False, f"Key generation failed: {result.stderr}", None

        except Exception as e:
            return False, f"Key generation error: {e}", None

    def check_auth_health(self) -> Dict[str, Any]:
        """
        Perform comprehensive auth health check.

        Checks:
        - SSH key presence and validity
        - SSH connectivity to GitHub
        - gh CLI authentication status
        - Token expiration status

        Returns:
            Dict with:
                healthy: bool - Overall health status
                issues: List[str] - List of issues found
                checks: Dict - Individual check results
                recommendations: List[str] - Suggested fixes
        """
        result = {
            "healthy": True,
            "issues": [],
            "checks": {
                "ssh_key_exists": False,
                "ssh_key_valid": False,
                "ssh_github_auth": False,
                "gh_cli_installed": False,
                "gh_cli_authenticated": False,
                "gh_cli_token_valid": False
            },
            "recommendations": [],
            "details": {}
        }

        # Check 1: SSH key exists
        if self.ssh_key_path and os.path.exists(self.ssh_key_path):
            result["checks"]["ssh_key_exists"] = True
            result["details"]["ssh_key_path"] = self.ssh_key_path

            # Check SSH key validity (not corrupted)
            try:
                key_check = subprocess.run(
                    ["ssh-keygen", "-l", "-f", self.ssh_key_path],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if key_check.returncode == 0:
                    result["checks"]["ssh_key_valid"] = True
                    # Extract key fingerprint
                    result["details"]["ssh_key_fingerprint"] = key_check.stdout.strip().split()[1] if key_check.stdout else "unknown"
                else:
                    result["issues"].append("SSH key appears corrupted or invalid")
                    result["recommendations"].append("Regenerate SSH key using the dashboard")
            except Exception as e:
                result["issues"].append(f"Could not verify SSH key: {e}")
        else:
            result["issues"].append("No SSH key found")
            result["recommendations"].append("Generate an SSH key using the dashboard SSH Key Management")

        # Check 2: SSH connectivity to GitHub
        try:
            ssh_test = subprocess.run(
                ["ssh", "-T", "-o", "StrictHostKeyChecking=accept-new",
                 "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "git@github.com"],
                capture_output=True,
                text=True,
                timeout=10
            )
            output = ssh_test.stdout + ssh_test.stderr

            if "successfully authenticated" in output.lower():
                result["checks"]["ssh_github_auth"] = True
                # Extract username
                match = re.search(r'Hi ([^!]+)!', output)
                if match:
                    result["details"]["ssh_github_user"] = match.group(1)
            elif "permission denied" in output.lower():
                result["issues"].append("SSH key not authorized on GitHub")
                result["recommendations"].append("Add your SSH public key to GitHub Settings > SSH Keys")
            elif "could not resolve" in output.lower():
                result["issues"].append("Cannot resolve github.com - network issue")
                result["recommendations"].append("Check network connectivity")
        except subprocess.TimeoutExpired:
            result["issues"].append("SSH connection to GitHub timed out")
            result["recommendations"].append("Check firewall/network settings for SSH (port 22)")
        except FileNotFoundError:
            result["issues"].append("SSH client not installed")
            result["recommendations"].append("Install OpenSSH client")
        except Exception as e:
            result["issues"].append(f"SSH test error: {e}")

        # Check 3: gh CLI status
        try:
            # Check if gh is installed
            gh_version = subprocess.run(
                ["gh", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if gh_version.returncode == 0:
                result["checks"]["gh_cli_installed"] = True
                result["details"]["gh_version"] = gh_version.stdout.split('\n')[0]

                # Check auth status
                gh_auth = subprocess.run(
                    ["gh", "auth", "status"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                auth_output = gh_auth.stdout + gh_auth.stderr

                if gh_auth.returncode == 0:
                    result["checks"]["gh_cli_authenticated"] = True

                    # Extract username
                    match = re.search(r'Logged in to github.com account (\S+)', auth_output)
                    if match:
                        result["details"]["gh_cli_user"] = match.group(1)

                    # Check token validity
                    if "Token:" in auth_output:
                        result["checks"]["gh_cli_token_valid"] = True
                    elif "expired" in auth_output.lower():
                        result["issues"].append("gh CLI token has expired")
                        result["recommendations"].append("Run 'gh auth refresh' to renew token")
                    else:
                        result["checks"]["gh_cli_token_valid"] = True  # Assume valid if logged in
                else:
                    result["issues"].append("gh CLI not authenticated")
                    result["recommendations"].append("Run 'gh auth login' to authenticate")
        except FileNotFoundError:
            result["issues"].append("gh CLI not installed")
            result["recommendations"].append("Install GitHub CLI: https://cli.github.com/")
        except subprocess.TimeoutExpired:
            result["issues"].append("gh CLI check timed out")
        except Exception as e:
            result["issues"].append(f"gh CLI check error: {e}")

        # Determine overall health
        # Healthy if at least one auth method works
        has_working_ssh = result["checks"]["ssh_github_auth"]
        has_working_gh = result["checks"]["gh_cli_authenticated"] and result["checks"]["gh_cli_token_valid"]

        if not has_working_ssh and not has_working_gh:
            result["healthy"] = False
            if not result["recommendations"]:
                result["recommendations"].append("Set up at least one authentication method (SSH or gh CLI)")

        return result


# =============================================================================
# Remote Configuration Manager
# =============================================================================

class RemoteConfigManager:
    """
    Manages Git remote configuration for repositories.

    Handles:
        - Reading/writing remote config to repo_routing.yaml
        - Adding git remotes to repositories
        - Testing remote connectivity
    """

    def __init__(self, atlasforge_root: Optional[str] = None):
        """
        Initialize the remote configuration manager.

        Args:
            atlasforge_root: Path to AtlasForge root (default: from env or BASE_DIR from atlasforge_config)
        """
        self.atlasforge_root = atlasforge_root or os.environ.get(
            "ATLASFORGE_REPO_PATH",
            str(BASE_DIR)
        )
        self.config_path = os.path.join(self.atlasforge_root, "repo_routing.yaml")
        self.credentials = CredentialManager()

    def _load_yaml_config(self) -> Dict:
        """Load the repo_routing.yaml configuration."""
        if not HAS_YAML:
            raise ImportError("PyYAML is required for remote configuration")

        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        with open(self.config_path, 'r') as f:
            return yaml.safe_load(f) or {}

    def _save_yaml_config(self, config: Dict) -> None:
        """Save the repo_routing.yaml configuration."""
        if not HAS_YAML:
            raise ImportError("PyYAML is required for remote configuration")

        # Backup before write
        backup_path = f"{self.config_path}.bak"
        if os.path.exists(self.config_path):
            import shutil
            shutil.copy2(self.config_path, backup_path)

        # Write with proper formatting
        with open(self.config_path, 'w') as f:
            yaml.dump(
                config,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False
            )

    def get_repo_remote(self, repo_id: str) -> RemoteInfo:
        """
        Get remote configuration for a repository.

        Args:
            repo_id: Repository ID

        Returns:
            RemoteInfo object
        """
        try:
            config = self._load_yaml_config()
            repo_config = config.get("repos", {}).get(repo_id, {})

            remote_name = repo_config.get("remote") or "origin"
            remote_url = repo_config.get("remote_url") or ""

            is_configured = bool(remote_url)

            # Parse URL for details
            parsed = parse_remote_url(remote_url) if remote_url else {}

            # Determine auth method
            auth_method = "none"
            if is_configured:
                auth_method = self.credentials.get_auth_method(remote_url)

            return RemoteInfo(
                remote_name=remote_name,
                remote_url=remote_url,
                auth_method=auth_method,
                is_configured=is_configured,
                host=parsed.get("host"),
                user=parsed.get("user"),
                repo=parsed.get("repo")
            )

        except Exception as e:
            logger.error(f"Error getting remote for {repo_id}: {e}")
            return RemoteInfo(
                remote_name="origin",
                remote_url="",
                auth_method="none",
                is_configured=False
            )

    def set_repo_remote(
        self,
        repo_id: str,
        remote_url: str,
        remote_name: str = "origin"
    ) -> Tuple[bool, str]:
        """
        Set remote URL for a repository.

        Updates both repo_routing.yaml and the actual git remote.

        Args:
            repo_id: Repository ID
            remote_url: Remote URL (SSH or HTTPS)
            remote_name: Remote name (default: origin)

        Returns:
            Tuple of (success, message)
        """
        # Validate URL
        is_valid, error = validate_remote_url(remote_url)
        if not is_valid:
            return False, f"Invalid URL: {error}"

        try:
            # Load config
            config = self._load_yaml_config()

            # Check repo exists
            if repo_id not in config.get("repos", {}):
                return False, f"Unknown repository: {repo_id}"

            repo_config = config["repos"][repo_id]
            repo_path = repo_config.get("path", "")

            if not os.path.isdir(repo_path):
                return False, f"Repository path does not exist: {repo_path}"

            git_dir = os.path.join(repo_path, ".git")
            if not os.path.isdir(git_dir):
                return False, f"Not a git repository: {repo_path}"

            # Update git remote
            remote_set = self._set_git_remote(repo_path, remote_name, remote_url)
            if not remote_set[0]:
                return remote_set

            # Update YAML config
            config["repos"][repo_id]["remote"] = remote_name
            config["repos"][repo_id]["remote_url"] = remote_url

            self._save_yaml_config(config)

            logger.info(f"Configured remote for {repo_id}: {remote_name} -> {remote_url}")
            return True, f"Remote configured: {remote_name} -> {remote_url}"

        except Exception as e:
            logger.error(f"Error setting remote for {repo_id}: {e}")
            return False, f"Error: {e}"

    def _set_git_remote(
        self,
        repo_path: str,
        remote_name: str,
        remote_url: str
    ) -> Tuple[bool, str]:
        """
        Set git remote in the repository.

        Adds or updates the remote.
        """
        try:
            # Check if remote exists
            result = subprocess.run(
                ["git", "-C", repo_path, "remote", "get-url", remote_name],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                # Remote exists, update it
                result = subprocess.run(
                    ["git", "-C", repo_path, "remote", "set-url", remote_name, remote_url],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
            else:
                # Remote doesn't exist, add it
                result = subprocess.run(
                    ["git", "-C", repo_path, "remote", "add", remote_name, remote_url],
                    capture_output=True,
                    text=True,
                    timeout=10
                )

            if result.returncode != 0:
                return False, f"Git remote command failed: {result.stderr}"

            return True, "Git remote configured"

        except subprocess.TimeoutExpired:
            return False, "Git command timed out"
        except Exception as e:
            return False, f"Git remote error: {e}"

    def test_remote_connection(
        self,
        repo_id: str,
        remote_url: Optional[str] = None
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Test connectivity to a remote.

        Args:
            repo_id: Repository ID (to get repo path)
            remote_url: URL to test (if None, uses configured URL)

        Returns:
            Tuple of (success, details_dict)
        """
        details = {
            "url": remote_url or "",
            "auth_status": None,
            "connection_test": None,
            "error": None
        }

        try:
            # Get repo info
            config = self._load_yaml_config()
            repo_config = config.get("repos", {}).get(repo_id, {})

            if not repo_config:
                details["error"] = f"Unknown repository: {repo_id}"
                return False, details

            repo_path = repo_config.get("path", "")

            if not remote_url:
                remote_url = repo_config.get("remote_url", "")

            if not remote_url:
                details["error"] = "No remote URL to test"
                return False, details

            details["url"] = remote_url

            # Test authentication
            auth_status = self.credentials.get_auth_status(remote_url)
            details["auth_status"] = {
                "authenticated": auth_status.is_authenticated,
                "method": auth_status.method,
                "message": auth_status.details,
                "username": auth_status.username
            }

            if not auth_status.is_authenticated:
                details["error"] = f"Authentication failed: {auth_status.details}"
                return False, details

            # Test actual connection with git ls-remote
            result = subprocess.run(
                ["git", "ls-remote", "--exit-code", remote_url, "HEAD"],
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            )

            if result.returncode == 0:
                details["connection_test"] = "success"
                return True, details
            elif result.returncode == 2:
                # Repository exists but is empty
                details["connection_test"] = "empty_repo"
                return True, details
            else:
                details["connection_test"] = "failed"
                details["error"] = f"Cannot access remote: {result.stderr.strip()}"
                return False, details

        except subprocess.TimeoutExpired:
            details["error"] = "Connection test timed out"
            return False, details
        except Exception as e:
            details["error"] = f"Connection test error: {e}"
            return False, details

    def remove_repo_remote(self, repo_id: str) -> Tuple[bool, str]:
        """
        Remove remote configuration for a repository.

        Args:
            repo_id: Repository ID

        Returns:
            Tuple of (success, message)
        """
        try:
            config = self._load_yaml_config()

            if repo_id not in config.get("repos", {}):
                return False, f"Unknown repository: {repo_id}"

            repo_config = config["repos"][repo_id]
            repo_path = repo_config.get("path", "")
            remote_name = repo_config.get("remote", "origin")

            # Remove git remote if exists
            if os.path.isdir(os.path.join(repo_path, ".git")):
                subprocess.run(
                    ["git", "-C", repo_path, "remote", "remove", remote_name],
                    capture_output=True,
                    timeout=10
                )

            # Update YAML config
            config["repos"][repo_id]["remote"] = None
            config["repos"][repo_id]["remote_url"] = None

            self._save_yaml_config(config)

            return True, "Remote removed"

        except Exception as e:
            return False, f"Error: {e}"

    def get_all_remotes(self) -> Dict[str, RemoteInfo]:
        """
        Get remote configuration for all repositories.

        Returns:
            Dict mapping repo_id to RemoteInfo
        """
        try:
            config = self._load_yaml_config()
            repos = config.get("repos", {})

            result = {}
            for repo_id in repos:
                result[repo_id] = self.get_repo_remote(repo_id)

            return result

        except Exception as e:
            logger.error(f"Error getting all remotes: {e}")
            return {}


# =============================================================================
# Singleton Access
# =============================================================================

_manager: Optional[RemoteConfigManager] = None


def get_remote_config_manager(force_reload: bool = False) -> RemoteConfigManager:
    """Get the singleton RemoteConfigManager instance."""
    global _manager

    if _manager is None or force_reload:
        _manager = RemoteConfigManager()

    return _manager


# =============================================================================
# Self-Test
# =============================================================================

if __name__ == "__main__":
    print("Remote Configuration Manager - Self Test")
    print("=" * 60)

    try:
        manager = RemoteConfigManager()

        # Test URL parsing
        print("\nURL Parsing Tests:")
        print("-" * 40)

        test_urls = [
            "git@github.com:user/repo.git",
            "https://github.com/user/repo.git",
            "https://github.com/user/repo",
            "git@gitlab.com:org/project.git",
            "invalid-url"
        ]

        for url in test_urls:
            parsed = parse_remote_url(url)
            status = "VALID" if parsed["is_valid"] else "INVALID"
            print(f"  [{status}] {url}")
            if parsed["is_valid"]:
                print(f"          Protocol: {parsed['protocol']}, Host: {parsed['host']}")

        # Test credential manager
        print("\nCredential Manager:")
        print("-" * 40)

        creds = manager.credentials
        print(f"  SSH Key: {creds.get_ssh_key_path() or 'Not found'}")

        is_auth, msg, user = creds.get_gh_auth_status()
        print(f"  gh CLI: {'Authenticated' if is_auth else 'Not authenticated'} - {msg}")

        # Test remote info
        print("\nRepository Remotes:")
        print("-" * 40)

        all_remotes = manager.get_all_remotes()
        for repo_id, remote in all_remotes.items():
            status = "Configured" if remote.is_configured else "Not configured"
            print(f"  {repo_id}: {status}")
            if remote.is_configured:
                print(f"    URL: {remote.remote_url}")
                print(f"    Auth: {remote.auth_method}")

        print("\n" + "=" * 60)
        print("Self-test PASSED")

    except Exception as e:
        print(f"Self-test FAILED: {e}")
        import traceback
        traceback.print_exc()
