#!/usr/bin/env python3
"""
Burst Capture Module for ScreenCortex.

Enables capturing N screenshots evenly spaced over a specified duration.
Useful for reviewing game bot behavior that cannot be observed in real-time.

Usage:
    from vision.burst_capture import capture_burst

    # Capture 10 screenshots over 10 seconds
    result = capture_burst(count=10, duration_seconds=10.0)
    print(result.frames)  # List of FrameInfo with paths and timing
"""
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Callable, Dict, Any

from .screen_capture import ScreenCortex


@dataclass
class FrameInfo:
    """Information about a single captured frame."""
    index: int
    path: str
    timestamp: float          # Unix timestamp when captured
    offset_ms: float          # Milliseconds since burst start
    capture_time_ms: float    # Time taken to capture this frame
    width: int
    height: int


@dataclass
class BurstResult:
    """Result of a burst capture operation."""
    success: bool
    frames: List[FrameInfo] = field(default_factory=list)
    total_duration_ms: float = 0.0
    target_interval_ms: float = 0.0
    actual_interval_avg_ms: float = 0.0
    output_dir: str = ""
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'success': self.success,
            'frames': [
                {
                    'index': f.index,
                    'path': f.path,
                    'timestamp': f.timestamp,
                    'offset_ms': f.offset_ms,
                    'capture_time_ms': f.capture_time_ms,
                    'width': f.width,
                    'height': f.height
                }
                for f in self.frames
            ],
            'total_duration_ms': self.total_duration_ms,
            'target_interval_ms': self.target_interval_ms,
            'actual_interval_avg_ms': self.actual_interval_avg_ms,
            'output_dir': self.output_dir,
            'error': self.error
        }


def capture_burst(
    count: int = 3,
    duration_seconds: float = 6.0,
    display: str = ':99',
    output_dir: str = '/tmp/burst',
    format: str = 'png',
    downscale: int = 1,
    prefix: str = 'burst',
    jpeg_quality: int = 85,
    progress_callback: Optional[Callable[[int, int, str], None]] = None
) -> BurstResult:
    """
    Capture N screenshots evenly spaced over a duration.

    Args:
        count: Number of screenshots to capture (min: 1)
        duration_seconds: Total duration to spread captures over (min: 0)
        display: X11 display to capture (default: ':99')
        output_dir: Directory to save screenshots
        format: Image format ('png' or 'jpeg')
        downscale: Downscale factor (1 = full resolution)
        prefix: Filename prefix
        jpeg_quality: JPEG quality if format='jpeg' (1-100)
        progress_callback: Called after each capture with (current, total, path)

    Returns:
        BurstResult with frame info and timing statistics

    Example:
        # 10 screenshots over 10 seconds = 1 per second
        result = capture_burst(count=10, duration_seconds=10.0)

        # 3 screenshots over 6 seconds = 1 every 2 seconds
        result = capture_burst(count=3, duration_seconds=6.0)

        # Single instant capture
        result = capture_burst(count=1, duration_seconds=0)
    """
    # Validate inputs
    count = max(1, count)
    duration_seconds = max(0.0, duration_seconds)

    # Calculate interval
    if count == 1:
        interval_seconds = 0.0
    else:
        interval_seconds = duration_seconds / (count - 1)

    target_interval_ms = interval_seconds * 1000

    # Create output directory
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Generate timestamp for this burst
    burst_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    result = BurstResult(
        success=False,
        output_dir=str(output_path),
        target_interval_ms=target_interval_ms
    )

    try:
        # Initialize ScreenCortex once for entire burst
        with ScreenCortex(display_name=display, downscale=downscale) as cortex:
            start_time = time.perf_counter()

            for i in range(count):
                # Calculate target time for this frame
                if i > 0:
                    target_time = start_time + (i * interval_seconds)
                    now = time.perf_counter()

                    # Sleep until target time if we're early
                    if now < target_time:
                        time.sleep(target_time - now)

                # Capture timestamp
                capture_start = time.perf_counter()
                frame_timestamp = time.time()
                offset_ms = (capture_start - start_time) * 1000

                # Generate filename
                filename = f"{prefix}_{burst_timestamp}_{i+1:03d}_{int(offset_ms)}ms.{format}"
                filepath = output_path / filename

                # Capture and save
                cortex.save_screenshot(str(filepath))

                capture_time_ms = (time.perf_counter() - capture_start) * 1000

                # Create frame info
                frame_info = FrameInfo(
                    index=i,
                    path=str(filepath),
                    timestamp=frame_timestamp,
                    offset_ms=offset_ms,
                    capture_time_ms=capture_time_ms,
                    width=cortex.output_width,
                    height=cortex.output_height
                )
                result.frames.append(frame_info)

                # Call progress callback if provided
                if progress_callback:
                    progress_callback(i + 1, count, str(filepath))

            # Calculate final statistics
            end_time = time.perf_counter()
            result.total_duration_ms = (end_time - start_time) * 1000

            if len(result.frames) > 1:
                intervals = []
                for j in range(1, len(result.frames)):
                    interval = result.frames[j].offset_ms - result.frames[j-1].offset_ms
                    intervals.append(interval)
                result.actual_interval_avg_ms = sum(intervals) / len(intervals)
            else:
                result.actual_interval_avg_ms = 0.0

            result.success = True

    except Exception as e:
        result.error = str(e)
        result.success = False

    return result


def see_burst(
    count: int = 3,
    duration_seconds: float = 6.0,
    display: str = ':99',
    downscale: int = 1,
    format: str = 'png'
) -> Dict[str, Any]:
    """
    High-level burst capture for Claude integration.

    Saves screenshots to /tmp/burst_<timestamp>/ and returns metadata.

    Args:
        count: Number of screenshots
        duration_seconds: Total duration
        display: X11 display
        downscale: Downscale factor
        format: 'png' or 'jpeg'

    Returns:
        Dictionary with capture results suitable for Claude
    """
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_dir = f'/tmp/burst_{timestamp}'

    result = capture_burst(
        count=count,
        duration_seconds=duration_seconds,
        display=display,
        output_dir=output_dir,
        format=format,
        downscale=downscale
    )

    return result.to_dict()


if __name__ == '__main__':
    # Quick test
    import argparse
    import json

    parser = argparse.ArgumentParser(description='Burst capture test')
    parser.add_argument('--count', '-n', type=int, default=3, help='Number of shots')
    parser.add_argument('--duration', '-d', type=float, default=6.0, help='Duration in seconds')
    parser.add_argument('--display', default=':99', help='X11 display')
    parser.add_argument('--output', '-o', default='/tmp/burst_test', help='Output directory')

    args = parser.parse_args()

    def progress(current, total, path):
        print(f"  [{current}/{total}] {path}")

    print(f"Capturing {args.count} screenshots over {args.duration}s...")
    result = capture_burst(
        count=args.count,
        duration_seconds=args.duration,
        display=args.display,
        output_dir=args.output,
        progress_callback=progress
    )

    print(f"\nResult: {'SUCCESS' if result.success else 'FAILED'}")
    if result.success:
        print(f"Total duration: {result.total_duration_ms:.0f}ms")
        print(f"Target interval: {result.target_interval_ms:.0f}ms")
        print(f"Actual avg interval: {result.actual_interval_avg_ms:.0f}ms")
        print(f"Output directory: {result.output_dir}")
    else:
        print(f"Error: {result.error}")
