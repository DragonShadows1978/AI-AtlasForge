import os
import subprocess
from pathlib import Path


def test_codex_interactive_launcher_injects_proxy_mcp(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_codex = fake_bin / "codex"
    fake_codex.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
    fake_codex.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["ATLASFORGE_ROOT"] = str(Path(__file__).resolve().parents[2])
    env.pop("ATLASFORGE_CODEX_WEB_SEARCH", None)

    launcher = Path(__file__).resolve().parents[1] / "scripts" / "codex_proxy_interactive.sh"
    result = subprocess.run(
        [str(launcher), "--model", "gpt-5.4"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert 'mcp_servers.atlasforge-web-proxy.command="python3"' in result.stdout
    assert "mcp_servers.atlasforge-web-proxy.args=" in result.stdout
    assert "--model" in result.stdout
    assert "gpt-5.4" in result.stdout
    assert "--search" not in result.stdout


def test_codex_interactive_launcher_rejects_native_search_by_default(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_codex = fake_bin / "codex"
    fake_codex.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
    fake_codex.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["ATLASFORGE_ROOT"] = str(Path(__file__).resolve().parents[2])
    env.pop("ATLASFORGE_CODEX_WEB_SEARCH", None)

    launcher = Path(__file__).resolve().parents[1] / "scripts" / "codex_proxy_interactive.sh"
    result = subprocess.run(
        [str(launcher), "--search"],
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 2
    assert "Refusing native Codex --search" in result.stderr
