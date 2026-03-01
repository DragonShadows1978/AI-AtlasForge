# AtlasForge Vision Module

High-performance X11 screen capture for Claude. Provides direct desktop
awareness via shared memory (SHM) — no virtual display, no subprocess overhead.

Capture latency is typically **under 30ms** at full 1080p resolution.

---

## Files

| File | Purpose |
|---|---|
| `x11_bindings.py` | Low-level ctypes bindings for libX11 and libXext (SHM) |
| `screen_capture.py` | `ScreenCortex` — SHM-based frame capture, numpy/PNG/base64 output |
| `desktop_vision.py` | `capture_display()`, `capture_and_save()` — file/base64 API |
| `claude_vision.py` | `see()`, `see_compact()`, `describe_screen()` — Claude-ready API |
| `burst_capture.py` | `capture_burst()` — multi-frame capture over a time window |
| `burst_review.py` | `BurstReview` — thumbnail grids and change detection on burst frames |
| `__init__.py` | Re-exports all public symbols |

---

## System Requirements

### Linux packages

```bash
sudo apt install libx11-dev libxext-dev
```

These provide `libX11.so` and `libXext.so` which the module loads at import
time via `ctypes`. If they're missing, the import will raise `ImportError` with
a clear message.

### Python packages

```bash
pip install numpy Pillow
```

- **numpy** — frame data is returned as RGB numpy arrays
- **Pillow** — PNG/JPEG encoding (only needed when saving files or encoding)

---

## Quick Start

```python
# Capture and save to file
from vision.desktop_vision import capture_and_save
path = capture_and_save(display=':0', filepath='/tmp/screenshot.png')

# Capture as base64 (for passing directly to Claude API)
from vision.claude_vision import see
result = see(display=':0')
# result = {'success': True, 'image_base64': '...', 'width': 1920, 'height': 1080, ...}

# Compact version (downscaled 2x, faster/smaller)
from vision.claude_vision import see_compact
result = see_compact(display=':0')

# Burst capture — 10 frames over 5 seconds
from vision.burst_capture import capture_burst
burst = capture_burst(count=10, duration_seconds=5.0, display=':0')
# burst.output_dir contains the saved frames

# Review a burst with thumbnail grid
from vision.burst_review import BurstReview
review = BurstReview.from_directory(burst.output_dir)
grid_path = review.generate_thumbnail_grid()
```

---

## Claude Code Screenshot Skill Setup

The `screenshot` Claude Code skill uses this module. To set it up on a new
machine:

### 1. Install system dependencies

```bash
sudo apt install libx11-dev libxext-dev
pip install numpy Pillow
```

### 2. Verify the capture works

```bash
cd /home/vader/AI-AtlasForge
python3 -c "from vision.desktop_vision import capture_and_save; print(capture_and_save(display=':0', filepath='/tmp/test.png'))"
```

You should see `/tmp/test.png` printed. Open it to confirm the capture.

### 3. Create the skill file

The skill lives at `~/.claude/skills/screenshot/SKILL.md`. Create it:

```markdown
---
name: screenshot
description: Take a screenshot of the desktop. Use when user says screenshot, take a screenshot, capture screen, show me the screen, what's on screen, or wants to see the display.
---

# Screenshot

When the user wants a screenshot:

## Capture the Screen

Use the vision module for fast X11 capture:

\```bash
cd /home/vader/AI-AtlasForge && python3 -c "from vision.desktop_vision import capture_and_save; print(capture_and_save(display=':0', filepath='/tmp/screenshot.png'))"
\```

## Display the Screenshot

After capturing, use the Read tool to view `/tmp/screenshot.png`. The Read tool can display images.

## Notes

- Uses direct X11 capture via ScreenCortex (much faster than gnome-screenshot)
- Default display is `:0` (the main desktop)
- Always show the screenshot to the user after capturing
```

### 4. Confirm display name

On most systems the main display is `:0`. If capture fails with "Failed to open
X display", check with:

```bash
echo $DISPLAY
```

Update the display string in the skill file to match.

---

## Display Names

| Display | Typical use |
|---|---|
| `:0` | Main physical desktop (most common) |
| `:1`, `:2` | Secondary displays or additional X sessions |
| `:99` | Virtual/headless display (Xvfb) |

---

## Performance

`ScreenCortex` uses the X11 Shared Memory extension (MIT-SHM) for zero-copy
capture. The shared memory segment is allocated once at initialization and
reused across captures. Typical performance on a 1080p display:

- First capture (including SHM setup): ~15–40ms
- Subsequent captures: ~5–15ms

For burst capture use `capture_burst()` rather than calling `capture_and_save()`
in a loop — it reuses the same `ScreenCortex` instance.
