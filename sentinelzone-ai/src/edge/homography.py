import numpy as np
from typing import Tuple, Optional
import logging

logger = logging.getLogger("SentinelHomography")

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    cv2 = None
    HAS_CV2 = False


class HomographyProjector:
    """
    Handles bidirectional planar projection between camera pixel coordinates
    and ground-plane metric coordinates (meters). Monitors calibration drift.
    """
    def __init__(self, camera_matrix: np.ndarray, dist_coeffs: np.ndarray, homography_matrix: np.ndarray):
        assert camera_matrix.shape == (3, 3), "Camera matrix K must be 3x3"
        assert homography_matrix.shape == (3, 3), "Homography matrix H must be 3x3"
        self.K = camera_matrix.astype(np.float64)
        self.dist = dist_coeffs.astype(np.float64)
        self.H = homography_matrix.astype(np.float64)
        self.H_inv = np.linalg.inv(self.H)
        self.is_stable = True

    def pixel_to_metric(self, pixel_points: np.ndarray) -> np.ndarray:
        """
        Transforms pixel coordinates [N, 2] to ground-plane coordinates [N, 2] in meters.
        """
        if len(pixel_points) == 0:
            return np.empty((0, 2), dtype=np.float64)

        if HAS_CV2 and cv2 is not None:
            pts = pixel_points.reshape(-1, 1, 2).astype(np.float32)
            undistorted = cv2.undistortPoints(pts, self.K, self.dist, P=self.K).reshape(-1, 2)
        else:
            undistorted = pixel_points.astype(np.float64).reshape(-1, 2)

        n = len(undistorted)
        homog = np.hstack([undistorted, np.ones((n, 1), dtype=np.float64)])

        world_homog = (self.H_inv @ homog.T).T
        w = world_homog[:, 2:3]
        w = np.where(np.abs(w) < 1e-8, 1e-8, w)
        return world_homog[:, :2] / w

    def metric_to_pixel(self, metric_points: np.ndarray) -> np.ndarray:
        """
        Transforms metric coordinates [N, 2] back to image pixel coordinates [N, 2].
        """
        if len(metric_points) == 0:
            return np.empty((0, 2), dtype=np.float64)

        n = len(metric_points)
        homog = np.hstack([metric_points, np.ones((n, 1), dtype=np.float64)])
        img_homog = (self.H @ homog.T).T
        w = img_homog[:, 2:3]
        w = np.where(np.abs(w) < 1e-8, 1e-8, w)
        return img_homog[:, :2] / w

    def extract_bottom_center_anchors(self, bboxes: np.ndarray) -> np.ndarray:
        """
        Extracts bottom-center coordinate for each bounding box [x1, y1, x2, y2].
        """
        if len(bboxes) == 0:
            return np.empty((0, 2), dtype=np.float64)
        u_mid = (bboxes[:, 0] + bboxes[:, 2]) / 2.0
        v_bottom = bboxes[:, 3]
        return np.column_stack([u_mid, v_bottom])

    def metric_polygon_to_pixels(
        self,
        polygon: np.ndarray,
        width: int,
        height: int,
        step_meters: float = 1.0,
        margin: int = 8,
    ) -> Optional[np.ndarray]:
        """
        Project a metric ground polygon back into camera pixels so the zone can
        be drawn on the image itself (project card SEC-04.3, SEC-08.2).

        Edges are densified before projection and the result is clipped to the
        frame, because ground that lies outside the camera field of view maps
        to points at or beyond the horizon. Returns None when nothing of the
        zone is visible, so a caller never draws a zone it cannot see.
        """
        points = np.asarray(polygon, dtype=np.float64)
        if points.ndim != 2 or points.shape[0] < 3 or points.shape[1] != 2:
            return None
        if points.shape[0] > 2 and np.allclose(points[0], points[-1]):
            points = points[:-1]
        # Drop repeated vertices: a polygon with fewer than three distinct
        # points encloses no area and must not be drawn.
        distinct = [points[0]]
        for candidate in points[1:]:
            if not np.allclose(candidate, distinct[-1]):
                distinct.append(candidate)
        if len(distinct) > 1 and np.allclose(distinct[0], distinct[-1]):
            distinct.pop()
        if len(distinct) < 3:
            return None
        points = np.asarray(distinct, dtype=np.float64)

        densified = []
        for index in range(points.shape[0]):
            start = points[index]
            end = points[(index + 1) % points.shape[0]]
            length = float(np.hypot(*(end - start)))
            divisions = max(1, int(np.ceil(length / max(step_meters, 0.1))))
            for step in range(divisions):
                densified.append(start + (end - start) * (step / divisions))
        boundary = np.asarray(densified, dtype=np.float64)
        if boundary.shape[0] < 3:
            return None

        pixels = self.metric_to_pixel(boundary)
        if pixels.shape[0] != boundary.shape[0]:
            return None
        if not np.all(np.isfinite(pixels)):
            return None

        inside = (
            (pixels[:, 0] >= -margin)
            & (pixels[:, 0] <= width + margin)
            & (pixels[:, 1] >= -margin)
            & (pixels[:, 1] <= height + margin)
        )
        if int(inside.sum()) < 3:
            return None

        # Keep the visible run; a zone that leaves the frame re-enters later and
        # drawing one continuous outline would connect the wrong vertices.
        segments, current = [], []
        for index, keep in enumerate(inside):
            if keep:
                current.append(index)
            elif current:
                segments.append(current)
                current = []
        if current:
            segments.append(current)
        segments = [seg for seg in segments if len(seg) >= 3]
        if not segments:
            return None
        best = max(segments, key=len)
        return np.clip(pixels[best], [0, 0], [width - 1, height - 1])

    def evaluate_calibration_drift(
        self, 
        observed_pixel_anchors: np.ndarray, 
        reference_metric_anchors: np.ndarray, 
        threshold_rmse_meters: float = 0.15
    ) -> Tuple[bool, float]:
        """
        Monitors invariant ground survey targets (e.g. ArUco markers, static bollards).
        """
        projected_metric = self.pixel_to_metric(observed_pixel_anchors)
        errors = np.linalg.norm(projected_metric - reference_metric_anchors, axis=1)
        rmse = float(np.sqrt(np.mean(errors ** 2)))
        self.is_stable = (rmse <= threshold_rmse_meters)
        if not self.is_stable:
            logger.error(f"CRITICAL: Calibration drift RMSE = {rmse:.4f}m exceeds {threshold_rmse_meters}m")
        return self.is_stable, rmse
