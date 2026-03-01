#!/usr/bin/env python3
"""
Burst Review Interface for Claude.

Enables efficient review of burst screenshot sequences by:
1. Generating thumbnail grids showing all frames at a glance
2. Detecting visual differences between consecutive frames
3. Supporting annotations with frame-level notes
4. Integrating with Claude's Read tool for full-resolution viewing

Usage:
    from vision.burst_review import BurstReview

    # Load a burst capture
    review = BurstReview.from_directory('/tmp/burst_20231213_143022')

    # Generate thumbnail grid
    grid_path = review.generate_thumbnail_grid()

    # Detect differences between frames
    changes = review.detect_changes()

    # Add annotations
    review.annotate(2, "Shop opened here")
    review.annotate(5, "Enemy spawned", importance="high")

    # Save annotations
    review.save_annotations()
"""
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
import numpy as np

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    raise ImportError("Pillow is required: pip install Pillow")

try:
    from skimage.metrics import structural_similarity as ssim
    SSIM_AVAILABLE = True
except ImportError:
    SSIM_AVAILABLE = False

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    import imageio
    IMAGEIO_AVAILABLE = True
except ImportError:
    IMAGEIO_AVAILABLE = False


@dataclass
class FrameAnnotation:
    """Annotation attached to a specific frame."""
    frame_index: int
    note: str
    timestamp: float
    importance: str = "normal"  # normal, high, critical
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'frame_index': self.frame_index,
            'note': self.note,
            'timestamp': self.timestamp,
            'importance': self.importance,
            'tags': self.tags
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'FrameAnnotation':
        return cls(
            frame_index=data['frame_index'],
            note=data['note'],
            timestamp=data['timestamp'],
            importance=data.get('importance', 'normal'),
            tags=data.get('tags', [])
        )


@dataclass
class FrameDiff:
    """Visual difference between two frames."""
    frame_a: int
    frame_b: int
    diff_score: float  # 0-1, higher = more different
    diff_regions: List[Tuple[int, int, int, int]] = field(default_factory=list)
    significant: bool = False
    ssim_score: float = None  # SSIM score (1.0 = identical, 0.0 = completely different)
    ssim_map: np.ndarray = None  # Per-pixel SSIM values for heatmap generation


@dataclass
class BurstMetadata:
    """Metadata about a burst capture."""
    burst_id: str
    output_dir: str
    frame_count: int
    frame_paths: List[str]
    timestamps: List[float]
    offsets_ms: List[float]
    width: int
    height: int
    created_at: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            'burst_id': self.burst_id,
            'output_dir': self.output_dir,
            'frame_count': self.frame_count,
            'frame_paths': self.frame_paths,
            'timestamps': self.timestamps,
            'offsets_ms': self.offsets_ms,
            'width': self.width,
            'height': self.height,
            'created_at': self.created_at
        }


class BurstReview:
    """
    Review interface for burst screenshot sequences.

    Enables Claude to efficiently analyze burst captures by:
    - Viewing thumbnail grids for quick overview
    - Detecting changes between frames
    - Adding annotations for later reference
    - Accessing individual frames at full resolution
    """

    def __init__(self, metadata: BurstMetadata):
        self.metadata = metadata
        self.annotations: List[FrameAnnotation] = []
        self.diffs: List[FrameDiff] = []
        self._annotations_path = Path(metadata.output_dir) / 'annotations.json'

    @classmethod
    def from_directory(cls, directory: str) -> 'BurstReview':
        """
        Load a burst capture from a directory.

        Args:
            directory: Path to burst capture directory

        Returns:
            BurstReview instance
        """
        dir_path = Path(directory)
        if not dir_path.exists():
            raise FileNotFoundError(f"Directory not found: {directory}")

        # Find all image files
        image_extensions = {'.png', '.jpg', '.jpeg'}
        frames = []
        for ext in image_extensions:
            frames.extend(sorted(dir_path.glob(f'*{ext}')))
            frames.extend(sorted(dir_path.glob(f'*{ext.upper()}')))

        # Sort by name to maintain order
        frames = sorted(set(frames), key=lambda p: p.name)

        if not frames:
            raise ValueError(f"No image files found in {directory}")

        # Load first frame to get dimensions
        first_img = Image.open(frames[0])
        width, height = first_img.size
        first_img.close()

        # Parse timestamps from filenames if possible
        timestamps = []
        offsets_ms = []
        for frame_path in frames:
            # Try to extract offset from filename like "burst_20231213_143022_001_0ms.png"
            name = frame_path.stem
            parts = name.split('_')
            offset = 0.0
            for part in parts:
                if part.endswith('ms'):
                    try:
                        offset = float(part[:-2])
                        break
                    except ValueError:
                        pass
            offsets_ms.append(offset)
            timestamps.append(frame_path.stat().st_mtime)

        # Generate burst ID from directory name
        burst_id = dir_path.name

        metadata = BurstMetadata(
            burst_id=burst_id,
            output_dir=str(dir_path),
            frame_count=len(frames),
            frame_paths=[str(f) for f in frames],
            timestamps=timestamps,
            offsets_ms=offsets_ms,
            width=width,
            height=height,
            created_at=min(timestamps) if timestamps else 0.0
        )

        review = cls(metadata)
        review._load_annotations()
        return review

    @classmethod
    def from_burst_result(cls, burst_result: Dict[str, Any]) -> 'BurstReview':
        """
        Create BurstReview from a capture_burst result.

        Args:
            burst_result: Result dict from capture_burst() or see_burst()

        Returns:
            BurstReview instance
        """
        if not burst_result.get('success'):
            raise ValueError(f"Burst capture failed: {burst_result.get('error')}")

        frames = burst_result.get('frames', [])
        if not frames:
            raise ValueError("No frames in burst result")

        # Get dimensions from first frame dict or load from file
        frame_details = burst_result.get('frame_details', [])
        if frame_details and 'width' in frame_details[0]:
            width = frame_details[0]['width']
            height = frame_details[0]['height']
        else:
            first_frame = frames[0] if isinstance(frames[0], str) else frames[0]['path']
            img = Image.open(first_frame)
            width, height = img.size
            img.close()

        # Build metadata
        timestamps = []
        offsets_ms = []
        frame_paths = []

        for i, frame in enumerate(frames):
            if isinstance(frame, str):
                frame_paths.append(frame)
                offsets_ms.append(0.0)
                timestamps.append(0.0)
            else:
                frame_paths.append(frame.get('path', frame.get('filepath', '')))
                offsets_ms.append(frame.get('offset_ms', 0.0))
                timestamps.append(frame.get('timestamp', 0.0))

        # Use frame details if available
        if frame_details:
            for i, fd in enumerate(frame_details):
                if i < len(timestamps):
                    timestamps[i] = fd.get('timestamp', timestamps[i])
                    offsets_ms[i] = fd.get('offset_ms', offsets_ms[i])

        output_dir = burst_result.get('output_dir', str(Path(frame_paths[0]).parent))
        burst_id = Path(output_dir).name

        metadata = BurstMetadata(
            burst_id=burst_id,
            output_dir=output_dir,
            frame_count=len(frame_paths),
            frame_paths=frame_paths,
            timestamps=timestamps,
            offsets_ms=offsets_ms,
            width=width,
            height=height,
            created_at=min(timestamps) if timestamps else 0.0
        )

        review = cls(metadata)
        review._load_annotations()
        return review

    def generate_thumbnail_grid(
        self,
        output_path: str = None,
        thumb_width: int = 320,
        columns: int = None,
        include_timestamps: bool = True,
        include_annotations: bool = True,
        highlight_changes: bool = True,
        change_threshold: float = 0.05
    ) -> str:
        """
        Generate a thumbnail grid showing all burst frames.

        Args:
            output_path: Path to save grid image (default: <burst_dir>/grid.png)
            thumb_width: Width of each thumbnail in pixels
            columns: Number of columns (auto-calculated if None)
            include_timestamps: Add timestamp labels to thumbnails
            include_annotations: Highlight annotated frames
            highlight_changes: Highlight frames with significant changes
            change_threshold: Threshold for "significant" change (0-1)

        Returns:
            Path to generated grid image
        """
        if output_path is None:
            output_path = str(Path(self.metadata.output_dir) / 'grid.png')

        # Calculate thumbnail dimensions
        aspect_ratio = self.metadata.height / self.metadata.width
        thumb_height = int(thumb_width * aspect_ratio)

        # Calculate grid layout
        n_frames = self.metadata.frame_count
        if columns is None:
            columns = min(5, n_frames)
        rows = (n_frames + columns - 1) // columns

        # Label height for timestamps
        label_height = 24 if include_timestamps else 0
        cell_width = thumb_width
        cell_height = thumb_height + label_height

        # Create grid canvas
        grid_width = columns * cell_width
        grid_height = rows * cell_height
        grid = Image.new('RGB', (grid_width, grid_height), color=(40, 40, 40))
        draw = ImageDraw.Draw(grid)

        # Try to load a font, fallback to default
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 14)
        except:
            font = ImageFont.load_default()

        # Detect changes if needed
        if highlight_changes and not self.diffs:
            self.detect_changes(threshold=change_threshold)

        # Get set of annotated frame indices
        annotated_frames = {a.frame_index for a in self.annotations}

        # Get set of frames with significant changes
        change_frames = {d.frame_b for d in self.diffs if d.significant}

        # Generate grid
        for i, frame_path in enumerate(self.metadata.frame_paths):
            row = i // columns
            col = i % columns
            x = col * cell_width
            y = row * cell_height

            # Load and resize frame
            try:
                img = Image.open(frame_path)
                img_thumb = img.resize((thumb_width, thumb_height), Image.Resampling.LANCZOS)
                img.close()

                # Paste thumbnail
                grid.paste(img_thumb, (x, y))

                # Draw border highlighting
                border_color = None
                border_width = 2

                if include_annotations and i in annotated_frames:
                    # Get annotation importance
                    frame_annots = [a for a in self.annotations if a.frame_index == i]
                    max_importance = max((a.importance for a in frame_annots), default='normal')
                    if max_importance == 'critical':
                        border_color = (255, 0, 0)
                        border_width = 4
                    elif max_importance == 'high':
                        border_color = (255, 165, 0)
                        border_width = 3
                    else:
                        border_color = (0, 200, 255)
                        border_width = 2

                elif highlight_changes and i in change_frames:
                    border_color = (0, 255, 0)
                    border_width = 2

                if border_color:
                    for w in range(border_width):
                        draw.rectangle(
                            [x + w, y + w, x + thumb_width - 1 - w, y + thumb_height - 1 - w],
                            outline=border_color
                        )

                # Add timestamp label
                if include_timestamps:
                    offset_ms = self.metadata.offsets_ms[i] if i < len(self.metadata.offsets_ms) else 0
                    label = f"#{i+1} +{offset_ms:.0f}ms"

                    # Draw label background
                    label_y = y + thumb_height
                    draw.rectangle([x, label_y, x + thumb_width, label_y + label_height], fill=(30, 30, 30))

                    # Draw label text
                    text_color = (200, 200, 200)
                    if i in annotated_frames:
                        text_color = (0, 200, 255)
                    draw.text((x + 4, label_y + 4), label, fill=text_color, font=font)

            except Exception as e:
                # Draw error placeholder
                draw.rectangle([x, y, x + thumb_width, y + thumb_height], fill=(60, 30, 30))
                draw.text((x + 4, y + 4), f"Error: {str(e)[:20]}", fill=(255, 100, 100), font=font)

        # Save grid
        grid.save(output_path)
        return output_path

    def detect_changes(
        self,
        threshold: float = 0.05,
        min_region_size: int = 20
    ) -> List[FrameDiff]:
        """
        Detect visual differences between consecutive frames.

        Args:
            threshold: Minimum diff score to mark as significant (0-1)
            min_region_size: Minimum pixel region size to count as a change

        Returns:
            List of FrameDiff objects describing changes
        """
        self.diffs = []

        if self.metadata.frame_count < 2:
            return self.diffs

        prev_img = None
        prev_array = None

        for i, frame_path in enumerate(self.metadata.frame_paths):
            try:
                img = Image.open(frame_path).convert('RGB')
                curr_array = np.array(img)

                if prev_array is not None:
                    # Calculate difference
                    diff = np.abs(curr_array.astype(np.float32) - prev_array.astype(np.float32))
                    diff_gray = np.mean(diff, axis=2)  # Average across RGB channels

                    # Normalize to 0-1
                    max_diff = 255.0
                    diff_normalized = diff_gray / max_diff

                    # Overall diff score
                    diff_score = float(np.mean(diff_normalized))

                    # Find regions with significant change
                    diff_mask = diff_normalized > 0.1
                    diff_regions = self._find_diff_regions(diff_mask, min_region_size)

                    frame_diff = FrameDiff(
                        frame_a=i - 1,
                        frame_b=i,
                        diff_score=diff_score,
                        diff_regions=diff_regions,
                        significant=diff_score >= threshold
                    )
                    self.diffs.append(frame_diff)

                prev_img = img
                prev_array = curr_array

            except Exception as e:
                # Skip frames that can't be loaded
                if prev_img:
                    frame_diff = FrameDiff(
                        frame_a=i - 1,
                        frame_b=i,
                        diff_score=-1.0,
                        significant=False
                    )
                    self.diffs.append(frame_diff)

        return self.diffs

    def _find_diff_regions(
        self,
        diff_mask: np.ndarray,
        min_size: int
    ) -> List[Tuple[int, int, int, int]]:
        """Find bounding boxes of changed regions."""
        regions = []

        # Simple connected component approximation using row/column projections
        if not np.any(diff_mask):
            return regions

        # Find rows and columns with changes
        row_sums = np.sum(diff_mask, axis=1)
        col_sums = np.sum(diff_mask, axis=0)

        # Get bounds
        rows_with_change = np.where(row_sums > 0)[0]
        cols_with_change = np.where(col_sums > 0)[0]

        if len(rows_with_change) == 0 or len(cols_with_change) == 0:
            return regions

        y1, y2 = int(rows_with_change[0]), int(rows_with_change[-1])
        x1, x2 = int(cols_with_change[0]), int(cols_with_change[-1])

        if (y2 - y1) >= min_size or (x2 - x1) >= min_size:
            regions.append((x1, y1, x2, y2))

        return regions

    def detect_changes_ssim(
        self,
        threshold: float = 0.85,
        gaussian_weights: bool = True,
        min_region_size: int = 20
    ) -> List[FrameDiff]:
        """
        Detect visual differences using Structural Similarity Index (SSIM).

        SSIM is superior to pixel-diff for game state detection because it
        accounts for structural similarity, not just raw pixel values. This
        better distinguishes meaningful state changes (shop opened, enemy
        spawned) from visual noise (particle effects, minor animations).

        Args:
            threshold: SSIM threshold below which change is significant (0-1)
                      Default 0.85 means 15% structural difference triggers detection.
            gaussian_weights: Use Gaussian weighting for SSIM (more accurate)
            min_region_size: Minimum pixel region size to count as a change

        Returns:
            List of FrameDiff objects with SSIM scores and maps
        """
        if not SSIM_AVAILABLE:
            raise ImportError("scikit-image is required for SSIM: pip install scikit-image")

        self.diffs = []
        self._ssim_maps = {}  # Store SSIM maps for heatmap generation

        if self.metadata.frame_count < 2:
            return self.diffs

        prev_gray = None

        for i, frame_path in enumerate(self.metadata.frame_paths):
            try:
                img = Image.open(frame_path).convert('L')  # Convert to grayscale
                curr_gray = np.array(img)

                if prev_gray is not None:
                    # Compute SSIM with full map
                    ssim_score, ssim_map = ssim(
                        prev_gray,
                        curr_gray,
                        data_range=255,
                        full=True,
                        gaussian_weights=gaussian_weights
                    )

                    # Convert SSIM score to diff_score (higher = more different)
                    diff_score = 1.0 - ssim_score

                    # Find regions with significant SSIM drop
                    change_mask = ssim_map < threshold
                    diff_regions = self._find_diff_regions(change_mask, min_region_size)

                    frame_diff = FrameDiff(
                        frame_a=i - 1,
                        frame_b=i,
                        diff_score=diff_score,
                        diff_regions=diff_regions,
                        significant=ssim_score < threshold,
                        ssim_score=float(ssim_score),
                        ssim_map=ssim_map.astype(np.float32)
                    )
                    self.diffs.append(frame_diff)

                    # Store SSIM map for heatmap generation
                    self._ssim_maps[i] = ssim_map.astype(np.float32)

                prev_gray = curr_gray

            except Exception as e:
                if prev_gray is not None:
                    frame_diff = FrameDiff(
                        frame_a=i - 1,
                        frame_b=i,
                        diff_score=-1.0,
                        significant=False,
                        ssim_score=None
                    )
                    self.diffs.append(frame_diff)

        return self.diffs

    def generate_heatmap_overlay(
        self,
        frame_index: int,
        colormap: int = None,
        alpha: float = 0.6,
        output_path: str = None
    ) -> str:
        """
        Generate a heatmap overlay showing where changes occurred in a frame.

        The heatmap highlights regions that changed compared to the previous frame,
        useful for identifying specific screen regions (enemy spawn zones, shop UI, etc.).

        Args:
            frame_index: Index of frame to generate heatmap for (1 to frame_count-1)
            colormap: OpenCV colormap constant (default: cv2.COLORMAP_JET)
            alpha: Blend factor for overlay (0-1, higher = more visible heatmap)
            output_path: Path to save heatmap image (default: <burst_dir>/heatmaps/frame_N_heatmap.png)

        Returns:
            Path to generated heatmap image
        """
        if not CV2_AVAILABLE:
            raise ImportError("OpenCV is required for heatmaps: pip install opencv-python-headless")

        if colormap is None:
            colormap = cv2.COLORMAP_JET

        # Ensure SSIM detection has been run
        if not hasattr(self, '_ssim_maps') or not self._ssim_maps:
            self.detect_changes_ssim()

        if frame_index not in self._ssim_maps:
            raise ValueError(f"No SSIM map available for frame {frame_index}. Must be >= 1.")

        ssim_map = self._ssim_maps[frame_index]

        # Create heatmaps directory
        heatmap_dir = Path(self.metadata.output_dir) / 'heatmaps'
        heatmap_dir.mkdir(exist_ok=True)

        if output_path is None:
            output_path = str(heatmap_dir / f'frame_{frame_index:03d}_heatmap.png')

        # Load original frame
        img = Image.open(self.metadata.frame_paths[frame_index]).convert('RGB')
        img_array = np.array(img)

        # Scale SSIM map to image size if needed (SSIM map may be grayscale size)
        if ssim_map.shape != img_array.shape[:2]:
            # Resize SSIM map to match frame dimensions
            ssim_map_resized = cv2.resize(ssim_map, (img_array.shape[1], img_array.shape[0]))
        else:
            ssim_map_resized = ssim_map

        # Convert SSIM to change intensity (1.0 - ssim = change amount)
        change_intensity = 1.0 - ssim_map_resized
        change_intensity = np.clip(change_intensity, 0, 1)

        # Scale to uint8 and amplify for visibility
        change_uint8 = (change_intensity * 255 * 2).astype(np.uint8)  # Amplify x2
        change_uint8 = np.clip(change_uint8, 0, 255)

        # Apply colormap
        heatmap = cv2.applyColorMap(change_uint8, colormap)
        heatmap = cv2.cvtColor(heatmap, cv2.COLOR_BGR2RGB)  # OpenCV uses BGR

        # Blend with original
        img_bgr = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
        overlay = cv2.addWeighted(img_array, 1 - alpha, heatmap, alpha, 0)

        # Save result
        result_img = Image.fromarray(overlay)
        result_img.save(output_path)

        return output_path

    def generate_all_heatmaps(
        self,
        colormap: int = None,
        alpha: float = 0.6
    ) -> List[str]:
        """
        Generate heatmap overlays for all frames with changes.

        Args:
            colormap: OpenCV colormap constant (default: cv2.COLORMAP_JET)
            alpha: Blend factor for overlay

        Returns:
            List of paths to generated heatmap images
        """
        if not hasattr(self, '_ssim_maps') or not self._ssim_maps:
            self.detect_changes_ssim()

        heatmap_paths = []
        for frame_index in self._ssim_maps.keys():
            path = self.generate_heatmap_overlay(frame_index, colormap, alpha)
            heatmap_paths.append(path)

        return heatmap_paths

    def extract_keyframes(
        self,
        min_ssim_gap: float = 0.15,
        max_keyframes: int = 10
    ) -> List[int]:
        """
        Extract keyframes representing distinct visual states.

        Uses temporal clustering to identify frames that represent significant
        state changes, reducing the number of frames Claude needs to review.

        Algorithm:
        1. Compute consecutive SSIM scores
        2. Find "scene boundaries" where SSIM drops significantly
        3. For each scene, select representative keyframe (most central)
        4. Return keyframe indices

        Args:
            min_ssim_gap: SSIM drop threshold for scene boundary detection
                         Default 0.15 means 15% structural change = new scene
            max_keyframes: Maximum number of keyframes to return

        Returns:
            List of frame indices representing keyframes
        """
        # Ensure SSIM detection has been run
        if not self.diffs or self.diffs[0].ssim_score is None:
            self.detect_changes_ssim()

        if not self.diffs:
            return [0] if self.metadata.frame_count > 0 else []

        # Get SSIM scores between consecutive frames
        ssim_scores = []
        for diff in self.diffs:
            if diff.ssim_score is not None:
                ssim_scores.append(diff.ssim_score)
            else:
                ssim_scores.append(1.0)  # Assume no change if SSIM unavailable

        # Find scene boundaries (where SSIM drops significantly)
        boundaries = [0]  # First frame is always a boundary
        for i, score in enumerate(ssim_scores):
            if score < (1.0 - min_ssim_gap):
                # Significant change detected at frame i+1
                boundaries.append(i + 1)

        # Add final frame as boundary
        boundaries.append(self.metadata.frame_count - 1)
        boundaries = sorted(set(boundaries))

        # Extract keyframes from each scene segment
        keyframes = []
        for i in range(len(boundaries) - 1):
            start = boundaries[i]
            end = boundaries[i + 1]

            if end <= start:
                continue

            # Select middle frame of the segment as keyframe
            mid = (start + end) // 2
            keyframes.append(mid)

        # Include first and last frames if not already
        if 0 not in keyframes:
            keyframes.insert(0, 0)
        if self.metadata.frame_count - 1 not in keyframes:
            keyframes.append(self.metadata.frame_count - 1)

        # Sort and limit
        keyframes = sorted(set(keyframes))
        if len(keyframes) > max_keyframes:
            # Evenly sample to max_keyframes
            step = len(keyframes) / max_keyframes
            keyframes = [keyframes[int(i * step)] for i in range(max_keyframes)]

        return keyframes

    def generate_keyframe_grid(
        self,
        output_path: str = None,
        thumb_width: int = 400
    ) -> str:
        """
        Generate a thumbnail grid showing only keyframes.

        This is a condensed view showing distinct visual states.

        Args:
            output_path: Path to save grid image
            thumb_width: Width of each thumbnail

        Returns:
            Path to generated grid image
        """
        keyframes = self.extract_keyframes()

        if output_path is None:
            output_path = str(Path(self.metadata.output_dir) / 'keyframe_grid.png')

        # Calculate layout
        aspect_ratio = self.metadata.height / self.metadata.width
        thumb_height = int(thumb_width * aspect_ratio)

        n_frames = len(keyframes)
        columns = min(4, n_frames)
        rows = (n_frames + columns - 1) // columns

        label_height = 30
        cell_width = thumb_width
        cell_height = thumb_height + label_height

        grid_width = columns * cell_width
        grid_height = rows * cell_height
        grid = Image.new('RGB', (grid_width, grid_height), color=(30, 30, 30))
        draw = ImageDraw.Draw(grid)

        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 14)
        except:
            font = ImageFont.load_default()

        for idx, frame_index in enumerate(keyframes):
            row = idx // columns
            col = idx % columns
            x = col * cell_width
            y = row * cell_height

            try:
                img = Image.open(self.metadata.frame_paths[frame_index])
                img_thumb = img.resize((thumb_width, thumb_height), Image.Resampling.LANCZOS)
                img.close()

                grid.paste(img_thumb, (x, y))

                # Draw keyframe indicator border
                for w in range(3):
                    draw.rectangle(
                        [x + w, y + w, x + thumb_width - 1 - w, y + thumb_height - 1 - w],
                        outline=(255, 215, 0)  # Gold color for keyframes
                    )

                # Label
                offset_ms = self.metadata.offsets_ms[frame_index] if frame_index < len(self.metadata.offsets_ms) else 0
                label = f"KF #{idx+1} | Frame {frame_index+1} | +{offset_ms:.0f}ms"
                label_y = y + thumb_height + 5
                draw.rectangle([x, label_y - 5, x + thumb_width, label_y + label_height], fill=(40, 40, 40))
                draw.text((x + 5, label_y), label, fill=(255, 215, 0), font=font)

            except Exception as e:
                draw.rectangle([x, y, x + thumb_width, y + thumb_height], fill=(60, 30, 30))
                draw.text((x + 5, y + 5), f"Error: {str(e)[:20]}", fill=(255, 100, 100), font=font)

        grid.save(output_path)
        return output_path

    def export_video(
        self,
        output_path: str = None,
        format: str = 'gif',
        fps: float = 2.0,
        include_annotations: bool = True,
        include_timestamps: bool = True,
        include_heatmaps: bool = False,
        resize_width: int = None
    ) -> str:
        """
        Export burst capture as animated video (GIF or MP4).

        Args:
            output_path: Path to save video (default: <burst_dir>/burst.<format>)
            format: 'gif' or 'mp4'
            fps: Frames per second for playback
            include_annotations: Draw annotation text on frames
            include_timestamps: Draw timestamp labels on frames
            include_heatmaps: Include heatmap overlays (side-by-side or interleaved)
            resize_width: Resize frames to this width (maintains aspect ratio)

        Returns:
            Path to generated video file
        """
        if not IMAGEIO_AVAILABLE:
            raise ImportError("imageio is required for video export: pip install imageio")

        if format not in ('gif', 'mp4'):
            raise ValueError(f"Format must be 'gif' or 'mp4', got: {format}")

        if output_path is None:
            output_path = str(Path(self.metadata.output_dir) / f'burst.{format}')

        # Prepare frames
        frames = []
        annotated_set = {a.frame_index for a in self.annotations}

        # Get heatmap maps if needed
        if include_heatmaps:
            if not hasattr(self, '_ssim_maps') or not self._ssim_maps:
                self.detect_changes_ssim()

        # Calculate resize dimensions
        if resize_width:
            aspect_ratio = self.metadata.height / self.metadata.width
            resize_height = int(resize_width * aspect_ratio)
            resize_size = (resize_width, resize_height)
        else:
            resize_size = None

        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 16)
        except:
            font = ImageFont.load_default()

        for i, frame_path in enumerate(self.metadata.frame_paths):
            try:
                img = Image.open(frame_path).convert('RGB')

                if resize_size:
                    img = img.resize(resize_size, Image.Resampling.LANCZOS)

                # Draw overlay text
                if include_timestamps or include_annotations:
                    draw = ImageDraw.Draw(img)

                    # Timestamp
                    if include_timestamps:
                        offset_ms = self.metadata.offsets_ms[i] if i < len(self.metadata.offsets_ms) else 0
                        ts_text = f"#{i+1} +{offset_ms:.0f}ms"
                        # Draw with shadow for readability
                        draw.text((11, 11), ts_text, fill=(0, 0, 0), font=font)
                        draw.text((10, 10), ts_text, fill=(255, 255, 255), font=font)

                    # Annotation
                    if include_annotations and i in annotated_set:
                        annots = [a for a in self.annotations if a.frame_index == i]
                        y_pos = 35
                        for annot in annots:
                            color = (255, 255, 0) if annot.importance == 'normal' else \
                                    (255, 165, 0) if annot.importance == 'high' else (255, 0, 0)
                            draw.text((11, y_pos + 1), annot.note[:50], fill=(0, 0, 0), font=font)
                            draw.text((10, y_pos), annot.note[:50], fill=color, font=font)
                            y_pos += 25

                frames.append(np.array(img))
                img.close()

            except Exception as e:
                # Create error placeholder frame
                if resize_size:
                    error_frame = np.zeros((resize_size[1], resize_size[0], 3), dtype=np.uint8)
                else:
                    error_frame = np.zeros((self.metadata.height, self.metadata.width, 3), dtype=np.uint8)
                error_frame[..., 0] = 60  # Dark red
                frames.append(error_frame)

        # Export video
        if format == 'gif':
            imageio.mimsave(output_path, frames, format='GIF', duration=1.0/fps, loop=0)
        else:  # mp4
            try:
                imageio.mimsave(output_path, frames, format='FFMPEG', fps=fps)
            except Exception as e:
                # Fallback: try writing with default codec
                writer = imageio.get_writer(output_path, fps=fps)
                for frame in frames:
                    writer.append_data(frame)
                writer.close()

        return output_path

    def generate_diff_visualization(
        self,
        output_path: str = None,
        highlight_color: Tuple[int, int, int] = (255, 0, 0)
    ) -> str:
        """
        Generate visualization showing differences between frames.

        Creates a strip showing frame pairs with differences highlighted.

        Args:
            output_path: Path to save visualization
            highlight_color: Color for difference overlay

        Returns:
            Path to generated visualization
        """
        if output_path is None:
            output_path = str(Path(self.metadata.output_dir) / 'differences.png')

        if not self.diffs:
            self.detect_changes()

        # Filter to significant diffs only
        sig_diffs = [d for d in self.diffs if d.significant and d.diff_score > 0]

        if not sig_diffs:
            # No significant changes - create placeholder
            placeholder = Image.new('RGB', (400, 100), color=(40, 40, 40))
            draw = ImageDraw.Draw(placeholder)
            draw.text((10, 40), "No significant changes detected", fill=(200, 200, 200))
            placeholder.save(output_path)
            return output_path

        # Create diff strip
        thumb_width = 320
        aspect_ratio = self.metadata.height / self.metadata.width
        thumb_height = int(thumb_width * aspect_ratio)

        # Each diff shows: frame_a | diff_overlay | frame_b
        strip_width = thumb_width * 3
        strip_height = thumb_height + 40  # Extra space for labels
        total_height = strip_height * len(sig_diffs)

        canvas = Image.new('RGB', (strip_width, total_height), color=(30, 30, 30))
        draw = ImageDraw.Draw(canvas)

        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 12)
        except:
            font = ImageFont.load_default()

        for idx, diff in enumerate(sig_diffs):
            y_offset = idx * strip_height

            # Load frames
            try:
                img_a = Image.open(self.metadata.frame_paths[diff.frame_a]).convert('RGB')
                img_b = Image.open(self.metadata.frame_paths[diff.frame_b]).convert('RGB')

                img_a_thumb = img_a.resize((thumb_width, thumb_height), Image.Resampling.LANCZOS)
                img_b_thumb = img_b.resize((thumb_width, thumb_height), Image.Resampling.LANCZOS)

                # Create diff overlay
                arr_a = np.array(img_a_thumb).astype(np.float32)
                arr_b = np.array(img_b_thumb).astype(np.float32)
                diff_arr = np.abs(arr_b - arr_a)
                diff_gray = np.mean(diff_arr, axis=2)

                # Amplify differences for visibility
                diff_normalized = np.clip(diff_gray * 5, 0, 255).astype(np.uint8)

                # Create colored overlay
                overlay = np.zeros_like(arr_a, dtype=np.uint8)
                overlay[:, :, 0] = np.clip(diff_normalized, 0, 255)  # Red channel

                # Blend with frame B
                blend = img_b_thumb.copy()
                blend_arr = np.array(blend)
                mask = diff_normalized > 30
                blend_arr[mask] = (blend_arr[mask] * 0.5 + overlay[mask] * 0.5).astype(np.uint8)
                diff_img = Image.fromarray(blend_arr)

                # Paste frames
                canvas.paste(img_a_thumb, (0, y_offset))
                canvas.paste(diff_img, (thumb_width, y_offset))
                canvas.paste(img_b_thumb, (thumb_width * 2, y_offset))

                img_a.close()
                img_b.close()

            except Exception as e:
                draw.text((10, y_offset + 10), f"Error loading frames: {str(e)[:40]}", fill=(255, 100, 100))

            # Labels
            label_y = y_offset + thumb_height + 5
            draw.text((10, label_y), f"Frame {diff.frame_a + 1}", fill=(180, 180, 180), font=font)
            draw.text((thumb_width + 10, label_y), f"Diff: {diff.diff_score:.2%}", fill=(255, 150, 150), font=font)
            draw.text((thumb_width * 2 + 10, label_y), f"Frame {diff.frame_b + 1}", fill=(180, 180, 180), font=font)

        canvas.save(output_path)
        return output_path

    def annotate(
        self,
        frame_index: int,
        note: str,
        importance: str = "normal",
        tags: List[str] = None
    ) -> FrameAnnotation:
        """
        Add an annotation to a specific frame.

        Args:
            frame_index: 0-based index of the frame
            note: Text annotation
            importance: "normal", "high", or "critical"
            tags: Optional list of tags for categorization

        Returns:
            Created FrameAnnotation
        """
        if frame_index < 0 or frame_index >= self.metadata.frame_count:
            raise ValueError(f"Frame index {frame_index} out of range (0-{self.metadata.frame_count - 1})")

        if importance not in ('normal', 'high', 'critical'):
            raise ValueError(f"Invalid importance: {importance}")

        annotation = FrameAnnotation(
            frame_index=frame_index,
            note=note,
            timestamp=datetime.now().timestamp(),
            importance=importance,
            tags=tags or []
        )
        self.annotations.append(annotation)
        return annotation

    def get_annotations(self, frame_index: int = None) -> List[FrameAnnotation]:
        """
        Get annotations for a specific frame or all frames.

        Args:
            frame_index: Optional frame index to filter by

        Returns:
            List of annotations
        """
        if frame_index is None:
            return self.annotations.copy()
        return [a for a in self.annotations if a.frame_index == frame_index]

    def remove_annotation(self, frame_index: int, note_substring: str = None):
        """
        Remove annotations from a frame.

        Args:
            frame_index: Frame to remove annotations from
            note_substring: If provided, only remove annotations containing this text
        """
        if note_substring:
            self.annotations = [
                a for a in self.annotations
                if not (a.frame_index == frame_index and note_substring in a.note)
            ]
        else:
            self.annotations = [a for a in self.annotations if a.frame_index != frame_index]

    def save_annotations(self, path: str = None):
        """
        Save annotations to JSON file.

        Args:
            path: Optional custom path (default: <burst_dir>/annotations.json)
        """
        if path is None:
            path = str(self._annotations_path)

        data = {
            'burst_id': self.metadata.burst_id,
            'frame_count': self.metadata.frame_count,
            'annotations': [a.to_dict() for a in self.annotations]
        }

        with open(path, 'w') as f:
            json.dump(data, f, indent=2)

    def _load_annotations(self):
        """Load annotations from file if it exists."""
        if self._annotations_path.exists():
            try:
                with open(self._annotations_path) as f:
                    data = json.load(f)
                self.annotations = [
                    FrameAnnotation.from_dict(a)
                    for a in data.get('annotations', [])
                ]
            except Exception:
                self.annotations = []

    def get_frame_path(self, frame_index: int) -> str:
        """
        Get the full path to a specific frame for Claude's Read tool.

        Args:
            frame_index: 0-based frame index

        Returns:
            Absolute path to frame image

        Note:
            Use this to get paths for Claude's Read tool:
            `Read(file_path=review.get_frame_path(5))`
        """
        if frame_index < 0 or frame_index >= self.metadata.frame_count:
            raise ValueError(f"Frame index {frame_index} out of range")
        return self.metadata.frame_paths[frame_index]

    def get_summary(self, use_ssim: bool = False) -> Dict[str, Any]:
        """
        Get a summary of the burst capture for Claude.

        Args:
            use_ssim: If True, use SSIM-based change detection instead of pixel diff

        Returns:
            Summary dict with metadata, annotations, and change info
        """
        # Ensure changes are detected
        if not self.diffs and self.metadata.frame_count > 1:
            if use_ssim and SSIM_AVAILABLE:
                self.detect_changes_ssim()
            else:
                self.detect_changes()

        significant_changes = [d for d in self.diffs if d.significant]

        # Check if SSIM was used
        ssim_used = bool(self.diffs and self.diffs[0].ssim_score is not None)

        # Build base summary
        summary = {
            'burst_id': self.metadata.burst_id,
            'output_dir': self.metadata.output_dir,
            'frame_count': self.metadata.frame_count,
            'resolution': f"{self.metadata.width}x{self.metadata.height}",
            'duration_ms': max(self.metadata.offsets_ms) if self.metadata.offsets_ms else 0,
            'annotation_count': len(self.annotations),
            'significant_changes': len(significant_changes),
            'change_frames': [d.frame_b for d in significant_changes],
            'annotated_frames': list({a.frame_index for a in self.annotations}),
            'frame_paths': self.metadata.frame_paths,
            'grid_path': str(Path(self.metadata.output_dir) / 'grid.png'),
            'diff_path': str(Path(self.metadata.output_dir) / 'differences.png'),
            'ssim_available': SSIM_AVAILABLE,
            'ssim_used': ssim_used
        }

        # Add SSIM-specific info if available
        if ssim_used:
            ssim_scores = [d.ssim_score for d in self.diffs if d.ssim_score is not None]
            summary['ssim_scores'] = ssim_scores
            summary['ssim_min'] = min(ssim_scores) if ssim_scores else None
            summary['ssim_avg'] = sum(ssim_scores) / len(ssim_scores) if ssim_scores else None
            summary['keyframe_grid_path'] = str(Path(self.metadata.output_dir) / 'keyframe_grid.png')
            summary['heatmaps_dir'] = str(Path(self.metadata.output_dir) / 'heatmaps')

        return summary

    def __repr__(self) -> str:
        return f"BurstReview(burst_id={self.metadata.burst_id!r}, frames={self.metadata.frame_count})"


def review_burst(burst_dir: str) -> Dict[str, Any]:
    """
    Quick review function for Claude integration.

    Loads burst, generates grid, detects changes, returns summary.

    Args:
        burst_dir: Path to burst capture directory

    Returns:
        Summary dict with grid_path, change info, etc.
    """
    review = BurstReview.from_directory(burst_dir)
    grid_path = review.generate_thumbnail_grid()
    diff_path = review.generate_diff_visualization()
    summary = review.get_summary()
    summary['grid_generated'] = True
    summary['diff_visualization_generated'] = True
    return summary


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Burst Review Tool')
    parser.add_argument('directory', help='Burst capture directory')
    parser.add_argument('--grid', '-g', action='store_true', help='Generate thumbnail grid')
    parser.add_argument('--diff', '-d', action='store_true', help='Generate diff visualization')
    parser.add_argument('--summary', '-s', action='store_true', help='Print summary')
    parser.add_argument('--annotate', '-a', nargs=2, metavar=('FRAME', 'NOTE'),
                        help='Add annotation (frame_index note)')

    args = parser.parse_args()

    review = BurstReview.from_directory(args.directory)

    if args.grid:
        path = review.generate_thumbnail_grid()
        print(f"Grid saved to: {path}")

    if args.diff:
        path = review.generate_diff_visualization()
        print(f"Diff visualization saved to: {path}")

    if args.annotate:
        frame_idx = int(args.annotate[0])
        note = args.annotate[1]
        review.annotate(frame_idx, note)
        review.save_annotations()
        print(f"Added annotation to frame {frame_idx}: {note}")

    if args.summary or not (args.grid or args.diff or args.annotate):
        summary = review.get_summary()
        print(json.dumps(summary, indent=2))
