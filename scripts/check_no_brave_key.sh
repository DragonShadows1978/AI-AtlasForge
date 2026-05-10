#!/usr/bin/env bash
# check_no_brave_key.sh — refuse to push Brave API keys.
#
# Scans either the staged diff (default) or the whole tracked tree
# (--all) for anything that looks like a Brave Search API key. Brave
# keys are ~32-char URL-safe ASCII strings starting with "BSA".
#
# In staged mode the scanner reads the STAGED BLOB (git show ":<path>"),
# not the working-tree copy — so stashing a cleaner working copy after
# `git add` does not bypass the check.
#
# Exits 0 on clean, 1 on detection, 2 on usage error. Meant to be wired
# into .git/hooks/pre-commit and invoked from `make check-secrets`.

set -euo pipefail

case "${1:-}" in
    ""|"staged") mode="staged" ;;
    "--all")     mode="all" ;;
    *) echo "Usage: check_no_brave_key.sh [--all]" >&2; exit 2 ;;
esac

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$repo_root"

# Resolve the scanner's own real path so a renamed/moved copy still self-skips.
script_real="$(readlink -f "$0" 2>/dev/null || echo "$0")"

key_min_suffix="${BRAVE_KEY_MIN_SUFFIX:-20}"
if ! [[ "$key_min_suffix" =~ ^[0-9]+$ ]] || [[ "$key_min_suffix" -lt 8 ]]; then
    echo "BRAVE_KEY_MIN_SUFFIX must be an integer >= 8" >&2
    exit 2
fi

# Pattern 1: literal "BSA"-prefixed token. -i applied at grep time so
# bsa... / Bsa... / etc. do not slip past.
token_re="BSA[A-Za-z0-9_-]{${key_min_suffix},}"

# Pattern 2: assignment to any Brave key env-var name we actually use
# in the tree (ATLASFORGE_BRAVE_API_KEY, BRAVE_API_KEY,
# BRAVE_SEARCH_API_KEY) plus close variants (BRAVE_KEY, BRAVE_TOKEN).
# Uppercase anchored because env-var names are conventionally upper;
# the placeholder allowlist below handles legitimate examples.
assign_re='(ATLASFORGE_)?BRAVE(_SEARCH)?_(API_)?(KEY|TOKEN)[[:space:]]*[:=][[:space:]]*["'"'"']?[A-Za-z0-9_-]{20,}'

# Collect files null-delimited so newlines/tabs/quotes in filenames are safe.
if [[ "$mode" == "staged" ]]; then
    mapfile -d '' files < <(git diff --cached -z --name-only --diff-filter=ACMR 2>/dev/null || true)
else
    mapfile -d '' files < <(git ls-files -z --recurse-submodules 2>/dev/null || git ls-files -z)
fi

if [[ ${#files[@]} -eq 0 ]]; then
    echo "check_no_brave_key: no files to scan ($mode)."
    exit 0
fi

# Allowlist applied to CONTENT ONLY (not the "path:line:" prefix), so a
# filename like `example/leak.py` can't drop real matches. -i so
# `change_me` / `Change_Me` behave like `CHANGE_ME`.
placeholder_re='CHANGE_ME|your[-_]key[-_]here|<[^>]+>|example|placeholder|xxxx+|\*\*\*|dummy|fake|test[-_]?key'

scan_encoded_content() {
    local min_suffix="$1"
    local tmp
    tmp="$(mktemp)"
    cat > "$tmp"
    python3 - "$min_suffix" "$tmp" <<'PY'
import base64
import binascii
import codecs
import re
import sys

min_suffix = int(sys.argv[1])
with open(sys.argv[2], "r", encoding="utf-8", errors="ignore") as fh:
    content = fh.read()
token_re = re.compile(rf"BSA[A-Za-z0-9_-]{{{min_suffix},}}", re.IGNORECASE)


def has_key(value: str) -> bool:
    return bool(token_re.search(value or ""))


def emit(kind: str) -> None:
    print(f"{kind}: encoded or split Brave API key candidate")


lines = content.splitlines()
assignment_re = re.compile(
    r"(?:ATLASFORGE_)?BRAVE(?:_SEARCH)?_(?:API_)?(?:KEY|TOKEN)\s*[:=]",
    re.IGNORECASE,
)
for idx, line in enumerate(lines):
    if not assignment_re.search(line):
        continue
    block = [line]
    for follow in lines[idx + 1:idx + 12]:
        if not follow.strip():
            continue
        if follow[:1].isspace() or follow.lstrip().startswith(("|", ">")):
            block.append(follow)
            continue
        break
    if has_key(re.sub(r"\s+", "", "\n".join(block))):
        emit("multiline")
        break

rot13 = codecs.decode(content, "rot_13")
if has_key(rot13):
    emit("rot13")

for match in re.finditer(r"\b[0-9A-Fa-f]{46,}\b", content):
    token = match.group(0)
    if len(token) % 2:
        continue
    try:
        decoded = bytes.fromhex(token).decode("utf-8", "ignore")
    except ValueError:
        continue
    if has_key(decoded):
        emit("hex")

for match in re.finditer(r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{24,}={0,2}(?![A-Za-z0-9+/_=-])", content):
    token = match.group(0)
    if token.upper().startswith("BSA"):
        continue
    padded = token + ("=" * ((4 - len(token) % 4) % 4))
    for urlsafe in (False, True):
        try:
            if urlsafe:
                decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
            else:
                decoded = base64.b64decode(padded.encode("ascii"), validate=True)
        except (binascii.Error, ValueError, TypeError):
            continue
        if has_key(decoded.decode("utf-8", "ignore")):
            emit("base64")
            break
PY
    rm -f "$tmp"
}

violation=0
for f in "${files[@]}"; do
    [[ -n "$f" ]] || continue

    # Self-skip by real path — survives `git mv` of the scanner.
    file_real="$(readlink -f "$f" 2>/dev/null || echo "")"
    [[ -n "$file_real" && "$file_real" == "$script_real" ]] && continue

    # Skip binaries — Brave keys don't live in them and the null-byte
    # noise confuses bash command substitution.
    if [[ "$mode" == "all" ]]; then
        [[ -f "$f" ]] || continue
        if LC_ALL=C grep -qI '' -- "$f" 2>/dev/null; then
            :  # text file, proceed
        else
            continue  # binary
        fi
    fi

    # Read the right version of the content:
    # - staged mode: staged blob (git show ":<path>")
    # - all mode:    working tree
    if [[ "$mode" == "staged" ]]; then
        content="$(git show ":$f" 2>/dev/null | tr -d '\0' || true)"
    else
        content="$(tr -d '\0' < "$f" 2>/dev/null || true)"
    fi
    [[ -n "$content" ]] || continue

    # --- Token check (case-insensitive on the BSA prefix) ---
    if token_hits="$(printf '%s\n' "$content" | grep -En -i "$token_re" || true)"; then
        if [[ -n "$token_hits" ]]; then
            # Filter placeholders on the CONTENT side only. grep -n prefixes
            # "LINENO:content"; strip that for allowlist, then report with prefix.
            filtered="$(printf '%s\n' "$token_hits" \
                | awk -F: 'BEGIN{OFS=":"} { ln=$1; $1=""; sub(/^:/, ""); print ln":"$0 }' \
                | while IFS=: read -r ln rest; do
                    if ! printf '%s' "$rest" | grep -Eqi "$placeholder_re"; then
                        printf '%s:%s:%s\n' "$f" "$ln" "$rest"
                    fi
                  done)"
            if [[ -n "$filtered" ]]; then
                printf '%s\n' "$filtered"
                violation=1
            fi
        fi
    fi

    # --- Encoded / split-token checks ---
    if encoded_hits="$(printf '%s' "$content" | scan_encoded_content "$key_min_suffix" || true)"; then
        if [[ -n "$encoded_hits" ]]; then
            filtered="$(printf '%s\n' "$encoded_hits" \
                | while IFS= read -r rest; do
                    if ! printf '%s' "$rest" | grep -Eqi "$placeholder_re"; then
                        printf '%s:%s\n' "$f" "$rest"
                    fi
                  done)"
            if [[ -n "$filtered" ]]; then
                printf '%s\n' "$filtered"
                violation=1
            fi
        fi
    fi

    # --- Assignment check (env-var names are uppercase by convention) ---
    if assign_hits="$(printf '%s\n' "$content" | grep -En "$assign_re" || true)"; then
        if [[ -n "$assign_hits" ]]; then
            filtered="$(printf '%s\n' "$assign_hits" \
                | awk -F: 'BEGIN{OFS=":"} { ln=$1; $1=""; sub(/^:/, ""); print ln":"$0 }' \
                | while IFS=: read -r ln rest; do
                    if ! printf '%s' "$rest" | grep -Eqi "$placeholder_re"; then
                        printf '%s:%s:%s\n' "$f" "$ln" "$rest"
                    fi
                  done)"
            if [[ -n "$filtered" ]]; then
                printf '%s\n' "$filtered"
                violation=1
            fi
        fi
    fi
done

if [[ "$violation" -ne 0 ]]; then
    echo ""
    echo "ERROR: possible Brave API key found in the lines above."
    echo "Move the value to .env (which is gitignored) and re-stage."
    exit 1
fi

echo "check_no_brave_key: clean ($mode)."
exit 0
