#!/usr/bin/env python3
"""
Vision tool for Claude Autonomous - enables screen capture capability.
"""
import sys
import base64
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "workspace"))

from vision.screen_capture import ScreenCortex


def capture_screen(display: str = ':99', save_path: str = None) -> dict:
    """
    Capture the screen and return as base64 PNG.

    Args:
        display: X11 display to capture (default :99)
        save_path: Optional path to save PNG file

    Returns:
        dict with 'base64_png' and 'capture_ms' keys
    """
    try:
        cortex = ScreenCortex(display_name=display)

        if save_path:
            cortex.save_screenshot(save_path)

        base64_png = cortex.capture_to_base64()
        capture_ms = cortex.last_frame_ms

        cortex.close()

        return {
            'success': True,
            'base64_png': base64_png,
            'capture_ms': capture_ms,
            'display': display
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


def get_screen_description(display: str = ':99') -> str:
    """
    Capture screen and return a description placeholder.
    This is meant to be used when we can't send actual images.

    For now, just saves to /tmp and returns path.
    """
    result = capture_screen(display, save_path='/tmp/claude_vision.png')
    if result['success']:
        return f"Screenshot captured to /tmp/claude_vision.png ({result['capture_ms']:.1f}ms)"
    else:
        return f"Screenshot failed: {result['error']}"


def capture_burst_screenshots(
    count: int = 3,
    duration: float = 6.0,
    display: str = ':99',
    output_dir: str = None,
    downscale: int = 1,
    format: str = 'png'
) -> dict:
    """
    Capture multiple screenshots evenly spaced over time.

    Args:
        count: Number of screenshots to capture
        duration: Duration in seconds to spread captures over
        display: X11 display to capture (default :99)
        output_dir: Directory to save screenshots (default: /tmp/burst_<timestamp>)
        downscale: Downscale factor (1 = full resolution)
        format: 'png' or 'jpeg'

    Returns:
        dict with 'success', 'frames' (list of paths), 'total_duration_ms'

    Example:
        # 10 screenshots over 10 seconds
        result = capture_burst_screenshots(count=10, duration=10)

        # 3 screenshots over 6 seconds
        result = capture_burst_screenshots(count=3, duration=6)
    """
    from datetime import datetime

    from vision.burst_capture import capture_burst

    if output_dir is None:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_dir = str(Path(__file__).resolve().parent / "screenshots" / f"burst_{timestamp}")

    try:
        result = capture_burst(
            count=count,
            duration_seconds=duration,
            display=display,
            output_dir=output_dir,
            downscale=downscale,
            format=format
        )

        return {
            'success': result.success,
            'frames': [f.path for f in result.frames],
            'frame_details': result.to_dict()['frames'],
            'total_duration_ms': result.total_duration_ms,
            'target_interval_ms': result.target_interval_ms,
            'actual_interval_avg_ms': result.actual_interval_avg_ms,
            'output_dir': result.output_dir,
            'count': len(result.frames),
            'error': result.error
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'frames': [],
            'count': 0
        }


def review_burst_screenshots(
    burst_dir: str,
    generate_grid: bool = True,
    generate_diff: bool = True,
    use_ssim: bool = True,
    generate_heatmaps: bool = False,
    extract_keyframes: bool = False,
    generate_keyframe_grid: bool = False
) -> dict:
    """
    Review a burst capture with thumbnail grid and change detection.

    Args:
        burst_dir: Directory containing burst screenshots
        generate_grid: Generate thumbnail grid image
        generate_diff: Generate difference visualization
        use_ssim: Use SSIM-based change detection (more accurate for games)
        generate_heatmaps: Generate heatmap overlays showing WHERE changes occurred
        extract_keyframes: Extract keyframes representing distinct visual states
        generate_keyframe_grid: Generate a grid showing only keyframes

    Returns:
        dict with summary, grid_path, diff_path, keyframes, heatmaps, etc.

    Example:
        result = review_burst_screenshots('/tmp/burst_20231213_143022', use_ssim=True)
        print(result['grid_path'])  # Path to thumbnail grid
        print(result['ssim_avg'])   # Average SSIM score between frames
        for frame in result['change_frames']:
            print(f"Significant change at frame {frame}")

        # With heatmaps
        result = review_burst_screenshots('/tmp/burst', generate_heatmaps=True)
        print(result['heatmap_paths'])  # Paths to heatmap overlays

        # With keyframes
        result = review_burst_screenshots('/tmp/burst', extract_keyframes=True)
        print(result['keyframes'])  # List of keyframe indices
    """
    from vision.burst_review import BurstReview

    try:
        review = BurstReview.from_directory(burst_dir)

        grid_path = None
        diff_path = None
        keyframe_grid_path = None
        heatmap_paths = []
        keyframes = []

        # Use SSIM-based detection if requested
        if use_ssim:
            review.detect_changes_ssim()
        else:
            review.detect_changes()

        if generate_grid:
            grid_path = review.generate_thumbnail_grid()

        if generate_diff:
            diff_path = review.generate_diff_visualization()

        if generate_heatmaps:
            heatmap_paths = review.generate_all_heatmaps()

        if extract_keyframes or generate_keyframe_grid:
            keyframes = review.extract_keyframes()

        if generate_keyframe_grid:
            keyframe_grid_path = review.generate_keyframe_grid()

        summary = review.get_summary(use_ssim=use_ssim)
        summary['success'] = True
        summary['grid_path'] = grid_path
        summary['diff_path'] = diff_path
        summary['heatmap_paths'] = heatmap_paths
        summary['keyframes'] = keyframes
        summary['keyframe_grid_path'] = keyframe_grid_path

        return summary

    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'burst_dir': burst_dir
        }


def annotate_burst_frame(
    burst_dir: str,
    frame_index: int,
    note: str,
    importance: str = "normal",
    tags: list = None
) -> dict:
    """
    Add an annotation to a specific frame in a burst capture.

    Args:
        burst_dir: Directory containing burst screenshots
        frame_index: 0-based index of the frame to annotate
        note: Annotation text (e.g., "enemy spawned here", "shop opened")
        importance: "normal", "high", or "critical"
        tags: Optional list of tags for categorization

    Returns:
        dict with success status and annotation details

    Example:
        annotate_burst_frame('/tmp/burst_20231213', 5, "Shop opened here", importance="high")
        annotate_burst_frame('/tmp/burst_20231213', 8, "Enemy spawn", tags=["combat", "wave_start"])
    """
    from vision.burst_review import BurstReview

    try:
        review = BurstReview.from_directory(burst_dir)
        annotation = review.annotate(
            frame_index=frame_index,
            note=note,
            importance=importance,
            tags=tags or []
        )
        review.save_annotations()

        return {
            'success': True,
            'frame_index': annotation.frame_index,
            'note': annotation.note,
            'importance': annotation.importance,
            'tags': annotation.tags,
            'total_annotations': len(review.annotations)
        }

    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'burst_dir': burst_dir,
            'frame_index': frame_index
        }


def get_burst_frame_path(burst_dir: str, frame_index: int) -> dict:
    """
    Get the full path to a specific frame for viewing with Claude's Read tool.

    Args:
        burst_dir: Directory containing burst screenshots
        frame_index: 0-based index of the frame

    Returns:
        dict with success status and frame_path

    Example:
        result = get_burst_frame_path('/tmp/burst_20231213', 5)
        if result['success']:
            # Use Claude's Read tool with result['frame_path']
            pass
    """
    from vision.burst_review import BurstReview

    try:
        review = BurstReview.from_directory(burst_dir)
        frame_path = review.get_frame_path(frame_index)

        return {
            'success': True,
            'frame_path': frame_path,
            'frame_index': frame_index,
            'frame_count': review.metadata.frame_count
        }

    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'burst_dir': burst_dir,
            'frame_index': frame_index
        }


def export_burst_video(
    burst_dir: str,
    format: str = 'gif',
    fps: float = 2.0,
    include_annotations: bool = True,
    include_timestamps: bool = True,
    resize_width: int = None
) -> dict:
    """
    Export a burst capture as an animated video (GIF or MP4).

    Args:
        burst_dir: Directory containing burst screenshots
        format: 'gif' or 'mp4'
        fps: Frames per second for playback
        include_annotations: Draw annotation text on frames
        include_timestamps: Draw timestamp labels on frames
        resize_width: Resize frames to this width (maintains aspect ratio)

    Returns:
        dict with success status and video_path

    Example:
        result = export_burst_video('/tmp/burst_20231213', format='gif', fps=2.0)
        print(result['video_path'])  # Path to generated GIF
    """
    from vision.burst_review import BurstReview

    try:
        review = BurstReview.from_directory(burst_dir)

        video_path = review.export_video(
            format=format,
            fps=fps,
            include_annotations=include_annotations,
            include_timestamps=include_timestamps,
            resize_width=resize_width
        )

        return {
            'success': True,
            'video_path': video_path,
            'format': format,
            'fps': fps,
            'frame_count': review.metadata.frame_count
        }

    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'burst_dir': burst_dir
        }


if __name__ == '__main__':
    # Test
    result = capture_screen(':99', '/tmp/vision_test.png')
    print(f"Capture result: {result['success']}")
    if result['success']:
        print(f"Capture time: {result['capture_ms']:.1f}ms")
        print(f"Base64 length: {len(result['base64_png'])} chars")
