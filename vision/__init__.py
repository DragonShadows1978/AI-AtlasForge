"""
Vision module for Claude desktop awareness.
Provides X11 screen capture and base64 PNG output for Claude to analyze.

Quick Start:
    from vision import see, see_compact, describe_screen

    # Capture screen for Claude analysis
    result = see()  # Returns {'success': True, 'image_base64': '...', ...}

    # Compact version (smaller, faster)
    result = see_compact()

    # Full analysis with metadata
    result = describe_screen()

Burst Capture and Review:
    from vision import capture_burst, BurstReview, review_burst

    # Capture 10 screenshots over 10 seconds
    result = capture_burst(count=10, duration_seconds=10.0)

    # Review the burst with thumbnail grid and change detection
    review = BurstReview.from_directory(result.output_dir)
    grid_path = review.generate_thumbnail_grid()

    # Quick review function
    summary = review_burst('/tmp/burst_20231213')
"""

from .screen_capture import ScreenCortex, capture_screenshot
from .desktop_vision import capture_display, capture_to_base64
from .claude_vision import see, see_compact, describe_screen, save_screenshot
from .burst_capture import capture_burst, see_burst, BurstResult, FrameInfo
from .burst_review import BurstReview, FrameAnnotation, FrameDiff, review_burst

__all__ = [
    # Low-level
    'ScreenCortex',
    'capture_screenshot',
    'capture_display',
    'capture_to_base64',
    # High-level Claude integration
    'see',
    'see_compact',
    'describe_screen',
    'save_screenshot',
    # Burst capture
    'capture_burst',
    'see_burst',
    'BurstResult',
    'FrameInfo',
    # Burst review
    'BurstReview',
    'FrameAnnotation',
    'FrameDiff',
    'review_burst',
]
