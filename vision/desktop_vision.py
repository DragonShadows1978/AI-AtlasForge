#!/usr/bin/env python3
"""
Desktop Vision Tool for Claude.

This module provides Claude with the ability to capture and "see" the desktop.
It captures screenshots from X11 displays and outputs them in formats Claude can process.

Usage:
    # As a CLI tool
    python -m vision.desktop_vision --display :99 --output base64

    # As a Python module
    from vision.desktop_vision import capture_display, capture_to_base64
    screenshot_b64 = capture_to_base64(display=':99')
"""
import argparse
import json
import sys
import os
import time
from typing import Optional, Dict, Any


def capture_display(display: str = ':99',
                    downscale: int = 1,
                    output_format: str = 'base64_png',
                    save_path: Optional[str] = None,
                    jpeg_quality: int = 85,
                    png_compression: int = 6) -> Dict[str, Any]:
    """
    Capture the display and return screenshot data.

    Args:
        display: X11 display name (e.g., ':99')
        downscale: Downscale factor (1 = full resolution, 2 = half, etc.)
        output_format: Output format ('base64_png', 'base64_jpeg', 'file', 'stats_only')
        save_path: File path to save screenshot (required if output_format is 'file')
        jpeg_quality: JPEG quality (1-100, only for JPEG output)
        png_compression: PNG compression level (0-9, only for PNG output)

    Returns:
        Dictionary with capture results:
        {
            'success': bool,
            'display': str,
            'width': int,
            'height': int,
            'capture_time_ms': float,
            'format': str,
            'data': str (base64) or None,
            'file_path': str or None,
            'file_size_bytes': int or None,
            'error': str or None
        }
    """
    result = {
        'success': False,
        'display': display,
        'width': 0,
        'height': 0,
        'capture_time_ms': 0.0,
        'format': output_format,
        'data': None,
        'file_path': None,
        'file_size_bytes': None,
        'error': None
    }

    try:
        from .screen_capture import ScreenCortex
    except ImportError:
        # Allow running as standalone script
        from screen_capture import ScreenCortex

    start_time = time.perf_counter()

    try:
        with ScreenCortex(display_name=display, downscale=downscale) as cortex:
            result['width'] = cortex.output_width
            result['height'] = cortex.output_height

            if output_format == 'base64_png':
                result['data'] = cortex.capture_to_base64(quality=png_compression)
                result['format'] = 'base64_png'

            elif output_format == 'base64_jpeg':
                result['data'] = cortex.capture_to_base64_jpeg(quality=jpeg_quality)
                result['format'] = 'base64_jpeg'

            elif output_format == 'file':
                if not save_path:
                    save_path = f'/tmp/screenshot_{int(time.time())}.png'
                cortex.save_screenshot(save_path)
                result['file_path'] = save_path
                result['file_size_bytes'] = os.path.getsize(save_path)

            elif output_format == 'stats_only':
                # Just capture to get stats, don't encode
                cortex.capture()
                stats = cortex.get_stats()
                result['capture_stats'] = stats

            else:
                raise ValueError(f"Unknown output format: {output_format}")

            result['capture_time_ms'] = (time.perf_counter() - start_time) * 1000
            result['success'] = True

    except Exception as e:
        result['error'] = str(e)
        result['capture_time_ms'] = (time.perf_counter() - start_time) * 1000

    return result


def capture_to_base64(display: str = ':99',
                      downscale: int = 1,
                      format: str = 'png') -> str:
    """
    Simple function to capture display and return base64 string.

    Args:
        display: X11 display name
        downscale: Downscale factor
        format: 'png' or 'jpeg'

    Returns:
        Base64-encoded image string

    Raises:
        RuntimeError: If capture fails
    """
    output_format = f'base64_{format}'
    result = capture_display(display=display, downscale=downscale, output_format=output_format)

    if not result['success']:
        raise RuntimeError(f"Failed to capture display: {result['error']}")

    return result['data']


def capture_and_save(display: str = ':99',
                     filepath: str = '/tmp/screenshot.png',
                     downscale: int = 1) -> str:
    """
    Capture display and save to file.

    Args:
        display: X11 display name
        filepath: Output file path
        downscale: Downscale factor

    Returns:
        Path to saved file

    Raises:
        RuntimeError: If capture fails
    """
    result = capture_display(display=display, downscale=downscale,
                            output_format='file', save_path=filepath)

    if not result['success']:
        raise RuntimeError(f"Failed to capture display: {result['error']}")

    return result['file_path']


def main():
    """CLI entry point for desktop vision tool."""
    parser = argparse.ArgumentParser(
        description='Desktop Vision Tool - Capture X11 display for Claude',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Capture display :99 as base64 PNG (for Claude)
  python -m vision.desktop_vision --display :99 --output base64_png

  # Save screenshot to file
  python -m vision.desktop_vision --display :99 --output file --save /tmp/screenshot.png

  # Capture at half resolution
  python -m vision.desktop_vision --display :99 --downscale 2 --output base64_jpeg

  # Just get capture stats
  python -m vision.desktop_vision --display :99 --output stats_only
        """
    )

    parser.add_argument('--display', '-d', default=':99',
                        help='X11 display to capture (default: :99)')
    parser.add_argument('--output', '-o', default='base64_png',
                        choices=['base64_png', 'base64_jpeg', 'file', 'stats_only'],
                        help='Output format (default: base64_png)')
    parser.add_argument('--save', '-s', default=None,
                        help='File path for saving (required for --output file)')
    parser.add_argument('--downscale', type=int, default=1,
                        help='Downscale factor (default: 1)')
    parser.add_argument('--jpeg-quality', type=int, default=85,
                        help='JPEG quality 1-100 (default: 85)')
    parser.add_argument('--png-compression', type=int, default=6,
                        help='PNG compression 0-9 (default: 6)')
    parser.add_argument('--json', action='store_true',
                        help='Output as JSON (default: just the data)')
    parser.add_argument('--quiet', '-q', action='store_true',
                        help='Suppress status messages')

    args = parser.parse_args()

    # Validate arguments
    if args.output == 'file' and not args.save:
        args.save = f'/tmp/screenshot_{int(time.time())}.png'

    # Capture the display
    result = capture_display(
        display=args.display,
        downscale=args.downscale,
        output_format=args.output,
        save_path=args.save,
        jpeg_quality=args.jpeg_quality,
        png_compression=args.png_compression
    )

    # Output results
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if not result['success']:
            print(f"ERROR: {result['error']}", file=sys.stderr)
            sys.exit(1)

        if args.output in ['base64_png', 'base64_jpeg']:
            # Output just the base64 data (for Claude to use)
            print(result['data'])
            if not args.quiet:
                print(f"# Captured {result['width']}x{result['height']} in {result['capture_time_ms']:.1f}ms",
                      file=sys.stderr)

        elif args.output == 'file':
            print(result['file_path'])
            if not args.quiet:
                print(f"# Saved {result['width']}x{result['height']} ({result['file_size_bytes']} bytes) "
                      f"in {result['capture_time_ms']:.1f}ms", file=sys.stderr)

        elif args.output == 'stats_only':
            print(f"Display: {result['display']}")
            print(f"Size: {result['width']}x{result['height']}")
            print(f"Capture time: {result['capture_time_ms']:.1f}ms")
            if 'capture_stats' in result:
                print(f"Stats: {result['capture_stats']}")


if __name__ == '__main__':
    main()
