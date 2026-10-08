from typing import Literal
from urllib.parse import urlsplit
import re

import numpy as np
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from shapely.geometry import Polygon


class Calibration(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    K: list[list[float]]
    dist: list[float]
    H: list[list[float]]
    pixel_references: list[tuple[float, float]] = Field(min_length=4)
    ground_references: list[tuple[float, float]] = Field(min_length=4)
    coordinate_frame: str = Field(min_length=1, max_length=80)
    max_rmse_m: float = Field(default=0.15, gt=0, le=0.15)

    @model_validator(mode='after')
    def validate_matrices(self):
        from src.edge.homography import HomographyProjector
        k, h = np.asarray(self.K), np.asarray(self.H)
        if k.shape != (3, 3) or h.shape != (3, 3) or not np.isfinite(k).all() or not np.isfinite(h).all():
            raise ValueError('K and H must be finite 3x3 matrices')
        if abs(np.linalg.det(h)) < 1e-10 or abs(np.linalg.det(k)) < 1e-10:
            raise ValueError('Calibration matrices must be invertible')
        if len(self.dist) not in (4, 5, 8, 12, 14) or not np.isfinite(self.dist).all():
            raise ValueError('Invalid distortion coefficients')
        p, g = np.asarray(self.pixel_references), np.asarray(self.ground_references)
        if p.shape != g.shape or not np.isfinite(p).all() or not np.isfinite(g).all():
            raise ValueError('Survey references must be finite matching points')
        if np.linalg.matrix_rank(g - g.mean(axis=0)) < 2:
            raise ValueError('Survey references must span the ground plane')
        projector = HomographyProjector(k, np.asarray(self.dist), h)
        if not projector.evaluate_calibration_drift(p, g, self.max_rmse_m)[0]:
            raise ValueError('Survey calibration exceeds permitted RMSE')
        return self


class Zone(BaseModel):
    zone_id: str = Field(min_length=1, max_length=80)
    polygon: list[tuple[float, float]] = Field(min_length=3, max_length=100)
    coordinate_frame: str = Field(min_length=1, max_length=80)
    ttc_multiplier: float = Field(default=1, ge=1, le=3)

    @model_validator(mode='after')
    def valid_polygon(self):
        poly = Polygon(self.polygon)
        if not np.isfinite(self.polygon).all() or not poly.is_valid or poly.area <= 0:
            raise ValueError('Zone must be a finite, nonempty valid polygon')
        return self


class Camera(BaseModel):
    camera_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    site_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=80)
    rtsp_url: str = Field(max_length=1024)
    credential_secret: str | None = None
    enabled: bool = True
    inference_fps: float = Field(default=2, ge=0.1, le=10)
    preview_fps: float = Field(default=5, ge=1, le=10)
    max_width: int = Field(default=960, ge=320, le=1920)
    calibration: Calibration | None = None
    zones: list[Zone] = Field(default_factory=list, max_length=100)

    @field_validator('rtsp_url')
    @classmethod
    def source(cls, value):
        url = urlsplit(value)
        if url.scheme not in ('rtsp', 'rtsps') or not url.hostname or url.username or url.password:
            raise ValueError('Use RTSP without embedded credentials; select a credential secret')
        return value

    @field_validator('credential_secret')
    @classmethod
    def secret_name(cls, value):
        if value and not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value):
            raise ValueError('Invalid secret name')
        return value

    @model_validator(mode='after')
    def frames_match(self):
        if self.zones and (not self.calibration or any(z.coordinate_frame != self.calibration.coordinate_frame for z in self.zones)):
            raise ValueError('Zones require calibration in the same coordinate frame')
        return self


class CameraConnection(Camera):
    """Write-only RTSP credentials, separate from the public camera configuration."""
    rtsp_username: SecretStr | None = Field(default=None, min_length=1, max_length=512)
    rtsp_password: SecretStr | None = Field(default=None, max_length=512)
    clear_credentials: bool = False

    @model_validator(mode='after')
    def credential_pair(self):
        provided = self.rtsp_username is not None or self.rtsp_password is not None
        if provided and (self.rtsp_username is None or self.rtsp_password is None):
            raise ValueError('Supply both camera username and password to replace credentials')
        if provided and self.clear_credentials:
            raise ValueError('Choose replacement credentials or removal, not both')
        return self


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class Review(BaseModel):
    verdict: Literal['TRUE_POSITIVE', 'FALSE_POSITIVE', 'CONTROLLED_WORK', 'UNCERTAIN']
    notes: str = Field(default='', max_length=4000)


class ManifestDraft(BaseModel):
    camera_id: str
    zones: list[Zone] = Field(max_length=100)
    permit_text: str = Field(default='', max_length=20000)


class IncidentPublish(BaseModel):
    event_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    camera_id: str | None = Field(default=None, max_length=128)
    severity: str = Field(default="EXTERNAL", max_length=80)
    source: str = Field(default="external", max_length=80)
    frame_index: int | None = Field(default=None, ge=0)
    telemetry: dict = Field(default_factory=dict)


class SupervisorRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    site_id: str | None = Field(default=None, max_length=80)
    camera_id: str | None = Field(default=None, max_length=64)
    event_id: str | None = Field(default=None, max_length=128)
    zones: list[Zone] | None = Field(default=None, max_length=100)
    permit_text: str = Field(default='', max_length=20000)
