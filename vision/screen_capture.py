"""
ScreenCortex: High-performance screen capture using X11 SHM.
Provides efficient frame capture with output as numpy arrays or base64 PNG.
"""
import numpy as np
import time
import io
import base64
from typing import Optional, Tuple
from .x11_bindings import X11Display, XShmExtension


class ScreenCortex:
    """
    High-performance screen capture system using X11 SHM.

    Uses X11 Shared Memory extension for zero-copy screen capture,
    achieving sub-30ms latency at 1080p.

    Example:
        cortex = ScreenCortex(display_name=':99')
        frame = cortex.capture()  # Returns RGB numpy array
        cortex.close()
    """

    def __init__(self, display_name: Optional[str] = None,
                 width: Optional[int] = None,
                 height: Optional[int] = None,
                 downscale: int = 1):
        """
        Initialize the screen capture system.

        Args:
            display_name: X11 display (e.g., ':99'), None for default
            width: Capture width (None for full screen)
            height: Capture height (None for full screen)
            downscale: Downscale factor (1 = full resolution)
        """
        self.downscale = max(1, downscale)
        self.display_name = display_name

        # Open X11 display
        self.x11 = X11Display(display_name)

        # Get screen dimensions
        screen_width, screen_height = self.x11.get_screen_size()

        # Set capture dimensions
        self.capture_width = width or screen_width
        self.capture_height = height or screen_height

        # Output dimensions (after downscaling)
        self.output_width = self.capture_width // self.downscale
        self.output_height = self.capture_height // self.downscale

        # Initialize SHM extension
        self.shm = XShmExtension(self.x11)
        self.shm.create_image(self.capture_width, self.capture_height)

        # Performance tracking
        self._frame_count = 0
        self._total_time = 0.0
        self._last_frame_time = 0.0

    def capture(self, x: int = 0, y: int = 0) -> np.ndarray:
        """
        Capture a frame from the screen.

        Args:
            x, y: Capture offset from top-left

        Returns:
            RGB numpy array of shape (H, W, 3)
        """
        start_time = time.perf_counter()

        # Capture raw BGRA data
        raw_data = self.shm.capture(self.x11.root, x, y)

        # Convert to numpy array
        frame = np.frombuffer(raw_data, dtype=np.uint8)
        frame = frame.reshape((self.capture_height, self.capture_width, 4))

        # Convert BGRA to RGB
        rgb = frame[:, :, :3][:, :, ::-1]

        # Downscale if needed
        if self.downscale > 1:
            rgb = rgb[::self.downscale, ::self.downscale].copy()
        else:
            rgb = rgb.copy()

        # Update performance metrics
        self._last_frame_time = time.perf_counter() - start_time
        self._total_time += self._last_frame_time
        self._frame_count += 1

        return rgb

    def capture_to_png_bytes(self, x: int = 0, y: int = 0, quality: int = 6) -> bytes:
        """
        Capture a frame and return as PNG bytes.

        Args:
            x, y: Capture offset from top-left
            quality: PNG compression level (0-9, higher = smaller file, slower)

        Returns:
            PNG image as bytes
        """
        try:
            from PIL import Image
        except ImportError:
            raise ImportError("PIL/Pillow is required for PNG output. Install with: pip install Pillow")

        rgb = self.capture(x, y)
        img = Image.fromarray(rgb, 'RGB')

        buffer = io.BytesIO()
        img.save(buffer, format='PNG', compress_level=quality)
        return buffer.getvalue()

    def capture_to_base64(self, x: int = 0, y: int = 0, quality: int = 6) -> str:
        """
        Capture a frame and return as base64-encoded PNG.

        Args:
            x, y: Capture offset from top-left
            quality: PNG compression level (0-9)

        Returns:
            Base64-encoded PNG string
        """
        png_bytes = self.capture_to_png_bytes(x, y, quality)
        return base64.b64encode(png_bytes).decode('ascii')

    def capture_to_jpeg_bytes(self, x: int = 0, y: int = 0, quality: int = 85) -> bytes:
        """
        Capture a frame and return as JPEG bytes (smaller than PNG).

        Args:
            x, y: Capture offset from top-left
            quality: JPEG quality (1-100, higher = better quality, larger file)

        Returns:
            JPEG image as bytes
        """
        try:
            from PIL import Image
        except ImportError:
            raise ImportError("PIL/Pillow is required for JPEG output. Install with: pip install Pillow")

        rgb = self.capture(x, y)
        img = Image.fromarray(rgb, 'RGB')

        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=quality)
        return buffer.getvalue()

    def capture_to_base64_jpeg(self, x: int = 0, y: int = 0, quality: int = 85) -> str:
        """
        Capture a frame and return as base64-encoded JPEG.

        Args:
            x, y: Capture offset from top-left
            quality: JPEG quality (1-100)

        Returns:
            Base64-encoded JPEG string
        """
        jpeg_bytes = self.capture_to_jpeg_bytes(x, y, quality)
        return base64.b64encode(jpeg_bytes).decode('ascii')

    def save_screenshot(self, filepath: str, x: int = 0, y: int = 0, format: str = None) -> str:
        """
        Capture and save a screenshot to file.

        Args:
            filepath: Output file path
            x, y: Capture offset
            format: Image format (auto-detected from extension if None)

        Returns:
            The filepath where the image was saved
        """
        try:
            from PIL import Image
        except ImportError:
            raise ImportError("PIL/Pillow is required. Install with: pip install Pillow")

        rgb = self.capture(x, y)
        img = Image.fromarray(rgb, 'RGB')
        img.save(filepath, format=format)
        return filepath

    @property
    def fps(self) -> float:
        """Calculate average FPS."""
        if self._total_time == 0:
            return 0.0
        return self._frame_count / self._total_time

    @property
    def last_frame_ms(self) -> float:
        """Get the last frame capture time in milliseconds."""
        return self._last_frame_time * 1000

    @property
    def avg_frame_ms(self) -> float:
        """Get average frame capture time in milliseconds."""
        if self._frame_count == 0:
            return 0.0
        return (self._total_time / self._frame_count) * 1000

    def reset_stats(self):
        """Reset performance statistics."""
        self._frame_count = 0
        self._total_time = 0.0
        self._last_frame_time = 0.0

    def get_stats(self) -> dict:
        """Get performance statistics."""
        return {
            'frame_count': self._frame_count,
            'total_time': self._total_time,
            'fps': self.fps,
            'last_frame_ms': self.last_frame_ms,
            'avg_frame_ms': self.avg_frame_ms,
            'capture_size': (self.capture_width, self.capture_height),
            'output_size': (self.output_width, self.output_height),
            'display': self.display_name or 'default',
        }

    def close(self):
        """Clean up resources."""
        if self.shm:
            self.shm.cleanup()
            self.shm = None
        if self.x11:
            self.x11.close()
            self.x11 = None

    def __del__(self):
        self.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def capture_screenshot(display: str = ':99',
                       downscale: int = 1,
                       output_format: str = 'numpy') -> any:
    """
    Convenience function to capture a single screenshot.

    Args:
        display: X11 display name (default: ':99' for virtual display)
        downscale: Downscale factor
        output_format: 'numpy', 'png_bytes', 'base64_png', 'jpeg_bytes', 'base64_jpeg'

    Returns:
        Screenshot in requested format
    """
    with ScreenCortex(display_name=display, downscale=downscale) as cortex:
        if output_format == 'numpy':
            return cortex.capture()
        elif output_format == 'png_bytes':
            return cortex.capture_to_png_bytes()
        elif output_format == 'base64_png':
            return cortex.capture_to_base64()
        elif output_format == 'jpeg_bytes':
            return cortex.capture_to_jpeg_bytes()
        elif output_format == 'base64_jpeg':
            return cortex.capture_to_base64_jpeg()
        else:
            raise ValueError(f"Unknown output format: {output_format}")
