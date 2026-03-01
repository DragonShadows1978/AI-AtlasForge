#!/usr/bin/env python3
"""
Claude Vision Integration.

Provides a simple interface for Claude to "see" the desktop.
Returns screenshot data in a format optimized for Claude's vision capabilities.

Usage:
    # Simple one-liner for Claude to see the screen
    python -m vision.claude_vision

    # Or in Python:
    from vision.claude_vision import see, describe_screen

    # Get base64 PNG for Claude to analyze
    image_data = see()

    # Get a complete screen analysis request
    analysis = describe_screen()
"""
import json
import sys
import time
from typing import Dict, Any, Optional


def see(display: str = ':99',
        downscale: int = 1,
        format: str = 'png') -> Dict[str, Any]:
    """
    Capture the screen and return data for Claude to analyze.

    This function is designed to be called by Claude when it needs
    visual information about the desktop state.

    Args:
        display: X11 display to capture (default: ':99' virtual display)
        downscale: Downscale factor (1=full, 2=half resolution, etc.)
        format: Image format ('png' for best quality, 'jpeg' for smaller size)

    Returns:
        Dictionary containing:
        - success: bool
        - image_base64: str (base64-encoded image data)
        - width: int
        - height: int
        - capture_time_ms: float
        - media_type: str ('image/png' or 'image/jpeg')
        - error: str or None
    """
    from .desktop_vision import capture_display

    output_format = f'base64_{format}'
    result = capture_display(
        display=display,
        downscale=downscale,
        output_format=output_format
    )

    media_type = 'image/png' if format == 'png' else 'image/jpeg'

    return {
        'success': result['success'],
        'image_base64': result['data'],
        'width': result['width'],
        'height': result['height'],
        'capture_time_ms': result['capture_time_ms'],
        'media_type': media_type,
        'error': result['error']
    }


def see_compact(display: str = ':99', quality: int = 60) -> Dict[str, Any]:
    """
    Capture the screen at reduced size for faster processing.

    Uses JPEG compression and 2x downscaling for minimal data transfer.
    Useful when bandwidth or processing time is a concern.

    Args:
        display: X11 display to capture
        quality: JPEG quality (1-100, lower = smaller file)

    Returns:
        Same format as see() but with compressed data
    """
    from .desktop_vision import capture_display

    result = capture_display(
        display=display,
        downscale=2,
        output_format='base64_jpeg',
        jpeg_quality=quality
    )

    return {
        'success': result['success'],
        'image_base64': result['data'],
        'width': result['width'],
        'height': result['height'],
        'capture_time_ms': result['capture_time_ms'],
        'media_type': 'image/jpeg',
        'error': result['error']
    }


def describe_screen(display: str = ':99',
                    downscale: int = 1) -> Dict[str, Any]:
    """
    Get a complete screen capture with metadata for analysis.

    This function captures the screen and returns it in a format
    suitable for sending to Claude's vision API.

    Args:
        display: X11 display to capture
        downscale: Downscale factor

    Returns:
        Dictionary with:
        - image: dict with 'type', 'media_type', 'data' for Claude API
        - metadata: dict with capture details
    """
    vision_data = see(display=display, downscale=downscale, format='png')

    if not vision_data['success']:
        return {
            'success': False,
            'error': vision_data['error'],
            'image': None,
            'metadata': None
        }

    return {
        'success': True,
        'error': None,
        'image': {
            'type': 'base64',
            'media_type': vision_data['media_type'],
            'data': vision_data['image_base64']
        },
        'metadata': {
            'display': display,
            'width': vision_data['width'],
            'height': vision_data['height'],
            'capture_time_ms': vision_data['capture_time_ms'],
            'timestamp': time.time()
        }
    }


def save_screenshot(filepath: str = '/tmp/claude_screenshot.png',
                    display: str = ':99') -> Dict[str, Any]:
    """
    Save a screenshot to disk.

    Args:
        filepath: Where to save the screenshot
        display: X11 display to capture

    Returns:
        Dictionary with success status and file info
    """
    from .desktop_vision import capture_display
    import os

    result = capture_display(
        display=display,
        output_format='file',
        save_path=filepath
    )

    return {
        'success': result['success'],
        'file_path': result['file_path'],
        'file_size_bytes': result['file_size_bytes'],
        'width': result['width'],
        'height': result['height'],
        'error': result['error']
    }


def main():
    """
    CLI entry point for Claude vision.

    When run directly, outputs a JSON object containing the screenshot
    data that Claude can use for visual analysis.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description='Capture screen for Claude vision analysis'
    )
    parser.add_argument('--display', '-d', default=':99',
                        help='X11 display (default: :99)')
    parser.add_argument('--downscale', type=int, default=1,
                        help='Downscale factor (default: 1)')
    parser.add_argument('--compact', '-c', action='store_true',
                        help='Use compact mode (JPEG, 2x downscale)')
    parser.add_argument('--save', '-s', default=None,
                        help='Save to file instead of outputting base64')
    parser.add_argument('--raw', action='store_true',
                        help='Output just the base64 data (no JSON)')
    # Burst capture options
    parser.add_argument('--burst', '-b', action='store_true',
                        help='Enable burst capture mode')
    parser.add_argument('--count', type=int, default=3,
                        help='Number of screenshots in burst (default: 3)')
    parser.add_argument('--duration', type=float, default=6.0,
                        help='Duration in seconds for burst (default: 6.0)')
    parser.add_argument('--output-dir', '-o', default=None,
                        help='Output directory for burst captures')
    parser.add_argument('--format', '-f', default='png', choices=['png', 'jpeg'],
                        help='Image format (default: png)')

    args = parser.parse_args()

    # Burst capture mode
    if args.burst:
        from .burst_capture import capture_burst
        from datetime import datetime

        output_dir = args.output_dir
        if output_dir is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_dir = f'/tmp/burst_{timestamp}'

        def progress(current, total, path):
            print(f"  [{current}/{total}] {path}", file=sys.stderr)

        print(f"Capturing {args.count} screenshots over {args.duration}s...", file=sys.stderr)
        result = capture_burst(
            count=args.count,
            duration_seconds=args.duration,
            display=args.display,
            output_dir=output_dir,
            format=args.format,
            downscale=args.downscale,
            progress_callback=progress
        )

        if result.success:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            print(f"ERROR: {result.error}", file=sys.stderr)
            sys.exit(1)
        return

    # Standard single-capture modes
    if args.save:
        result = save_screenshot(filepath=args.save, display=args.display)
        print(json.dumps(result, indent=2))
    elif args.compact:
        result = see_compact(display=args.display)
        if args.raw:
            if result['success']:
                print(result['image_base64'])
            else:
                print(f"ERROR: {result['error']}", file=sys.stderr)
                sys.exit(1)
        else:
            print(json.dumps(result, indent=2))
    else:
        result = see(display=args.display, downscale=args.downscale)
        if args.raw:
            if result['success']:
                print(result['image_base64'])
            else:
                print(f"ERROR: {result['error']}", file=sys.stderr)
                sys.exit(1)
        else:
            print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
