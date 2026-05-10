import base64
import codecs
import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_no_brave_key.sh"
KEY = "BSA" + ("A" * 24)


def _run(cmd, cwd, **kwargs):
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **kwargs,
    )


def _init_repo(path: Path):
    _run(["git", "init"], path, check=True)
    _run(["git", "config", "user.email", "test@example.invalid"], path, check=True)
    _run(["git", "config", "user.name", "Test User"], path, check=True)


def _scan(repo: Path, *args):
    env = {**os.environ, "BRAVE_KEY_MIN_SUFFIX": "20"}
    return _run([str(SCRIPT), *args], repo, env=env)


def test_detects_base64_encoded_brave_key(tmp_path: Path):
    _init_repo(tmp_path)
    encoded = base64.b64encode(KEY.encode()).decode()
    (tmp_path / "encoded.txt").write_text(f"secret={encoded}\n")
    _run(["git", "add", "encoded.txt"], tmp_path, check=True)

    result = _scan(tmp_path)

    assert result.returncode == 1
    assert "base64" in result.stdout


def test_detects_hex_encoded_brave_key(tmp_path: Path):
    _init_repo(tmp_path)
    encoded = KEY.encode().hex()
    (tmp_path / "encoded.txt").write_text(f"secret={encoded}\n")
    _run(["git", "add", "encoded.txt"], tmp_path, check=True)

    result = _scan(tmp_path)

    assert result.returncode == 1
    assert "hex" in result.stdout


def test_detects_rot13_encoded_brave_key(tmp_path: Path):
    _init_repo(tmp_path)
    encoded = codecs.encode(KEY, "rot_13")
    (tmp_path / "encoded.txt").write_text(f"secret={encoded}\n")
    _run(["git", "add", "encoded.txt"], tmp_path, check=True)

    result = _scan(tmp_path)

    assert result.returncode == 1
    assert "rot13" in result.stdout


def test_detects_split_yaml_brave_key(tmp_path: Path):
    _init_repo(tmp_path)
    (tmp_path / "config.yml").write_text(
        "BRAVE_API_KEY: >\n"
        f"  {KEY[:12]}\n"
        f"  {KEY[12:]}\n"
    )
    _run(["git", "add", "config.yml"], tmp_path, check=True)

    result = _scan(tmp_path)

    assert result.returncode == 1
    assert "multiline" in result.stdout


def test_all_mode_recurses_into_submodules(tmp_path: Path):
    sub = tmp_path / "subrepo"
    parent = tmp_path / "parent"
    sub.mkdir()
    parent.mkdir()
    _init_repo(sub)
    (sub / "nested.txt").write_text(f"secret={KEY}\n")
    _run(["git", "add", "nested.txt"], sub, check=True)
    _run(["git", "commit", "-m", "sub"], sub, check=True)

    _init_repo(parent)
    _run(
        ["git", "-c", "protocol.file.allow=always", "submodule", "add", str(sub), "vendor/sub"],
        parent,
        check=True,
    )
    _run(["git", "commit", "-m", "parent"], parent, check=True)

    result = _scan(parent, "--all")

    assert result.returncode == 1
    assert "vendor/sub/nested.txt" in result.stdout
