"""Private, persistent camera setup drafts; incomplete drafts never start inference."""
import json
import os
import tempfile
import threading
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, quote

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from src.api.live_service import CameraConfig


class SetupDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    camera_id: str = Field(default='gate', pattern=r'^[A-Za-z0-9_-]{1,64}$')
    site_id: str = Field(default='SITE-01', pattern=r'^[A-Za-z0-9_-]{1,64}$')
    location: str = Field(default='', max_length=160)
    host: str = Field(default='', max_length=255)
    rtsp_port: int | None = Field(default=None, gt=0, le=65535)
    stream_path: str = Field(default='', max_length=512)
    access_path: str = Field(default='', max_length=64)
    ssh_tunnel: str = Field(default='', max_length=512)
    ssh_key_file: str = Field(default='', max_length=2048)
    ssh_known_hosts: str = Field(default='', max_length=2048)
    rtsp_url: str = Field(default='', max_length=2048)
    username: str = Field(default='', max_length=256)
    password: SecretStr = SecretStr('')
    width: int | None = Field(default=None, gt=0, le=16384)
    height: int | None = Field(default=None, gt=0, le=16384)
    inference_fps: float = Field(default=5, gt=0, le=30)
    stale_after_seconds: float = Field(default=1, gt=0, le=30)
    homography: dict | None = None

    @field_validator('camera_id', 'site_id', mode='before')
    @classmethod
    def normalize_name(cls, value, info):
        if isinstance(value, str):
            return re.sub(r'\s+', '-', value.strip()) or ('gate' if info.field_name == 'camera_id' else 'SITE-01')
        return value

    @field_validator('host')
    @classmethod
    def validate_host(cls, value):
        value = value.strip()
        if value and (any(char in value for char in '/?#@') or any(char.isspace() for char in value) or '://' in value):
            raise ValueError('Camera host must be an IP address or hostname without scheme, path, or credentials')
        return value

    @field_validator('stream_path')
    @classmethod
    def validate_stream_path(cls, value):
        value = value.strip()
        if value and (any(char in value for char in '?#') or any(char in value for char in '\r\n')):
            raise ValueError('Stream path cannot contain query strings, fragments, or line breaks')
        return ('/' + value.lstrip('/')) if value else ''

    @field_validator('access_path')
    @classmethod
    def normalize_access_path(cls, value):
        return value.strip().lower()

    @field_validator('ssh_tunnel')
    @classmethod
    def validate_ssh(cls, value):
        if value:
            from src.api.ssh_tunnel import parse_connection
            parse_connection(value)
        return value

    @field_validator('rtsp_url')
    @classmethod
    def validate_rtsp(cls, value):
        if value:
            url = urlsplit(value)
            if url.scheme not in ('rtsp', 'rtsps') or not url.hostname or url.username is not None or url.fragment:
                raise ValueError('Use an RTSP address with credentials in separate fields')
            _ = url.port
        return value

    @model_validator(mode='after')
    def valid_url(self):
        if self.host and (self.rtsp_port is None or not self.stream_path):
            raise ValueError('Structured camera endpoint requires RTSP port and stream path')
        if not self.host and (self.rtsp_port is not None or self.stream_path):
            raise ValueError('Structured camera endpoint requires a host')
        if self.ssh_tunnel:
            from src.api.ssh_tunnel import parse_connection
            parse_connection(self.ssh_tunnel)
            if self.resolved_rtsp_url().startswith('rtsps:'):
                raise ValueError('SSH forwarding requires an RTSP/TCP stream')
        if self.rtsp_url:
            url = urlsplit(self.rtsp_url)
            if url.scheme not in ('rtsp', 'rtsps') or not url.hostname or url.username is not None or url.fragment:
                raise ValueError('Use an RTSP address with credentials in separate fields')
            _ = url.port
            if self.host and self.resolved_rtsp_url() != self.rtsp_url:
                raise ValueError('Structured camera endpoint and RTSP address disagree')
        return self

    def resolved_rtsp_url(self):
        if self.host:
            host = f'[{self.host}]' if ':' in self.host and not self.host.startswith('[') else self.host
            return urlunsplit(('rtsp', f'{host}:{self.rtsp_port}', self.stream_path or '/', '', ''))
        return self.rtsp_url

    def camera(self):
        return CameraConfig.model_validate({key: getattr(self,key) for key in
            ('camera_id','site_id','width','height','inference_fps','stale_after_seconds','homography')} | {'url_env':'SENTINEL_CAMERA_URL'})

    def source(self):
        url=urlsplit(self.resolved_rtsp_url())
        auth=(quote(self.username,safe='')+':'+quote(self.password.get_secret_value(),safe='')+'@') if self.username else ''
        return urlunsplit((url.scheme,auth+url.netloc,url.path,url.query,''))

    def connection_missing(self):
        missing = []
        if self.ssh_tunnel:
            from src.api.ssh_tunnel import requirements
            missing.extend(requirements(self.ssh_key_file, self.ssh_known_hosts))
        if not self.resolved_rtsp_url(): missing.append('Camera stream address or host, port, and path')
        if self.password.get_secret_value() and not self.username: missing.append('Camera username')
        return missing


class SetupStore:
    lock = threading.RLock()
    def __init__(self):
        self.path=Path(os.getenv('SENTINEL_SETUP_FILE','data/setup/camera.json'))

    def load(self):
        with self.lock:
            return SetupDraft.model_validate_json(self.path.read_text()) if self.path.exists() else SetupDraft()

    def save(self, payload):
        with self.lock:
            previous=self.load()
            # An omitted password preserves it; an explicit empty string clears it.
            if 'password' not in payload:
                payload={**payload,'password':previous.password.get_secret_value()}
            draft=SetupDraft.model_validate(payload)
            data=draft.model_dump(mode='json')
            data['password']=draft.password.get_secret_value()
            self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            fd,name=tempfile.mkstemp(dir=self.path.parent,prefix='.camera-')
            try:
                with os.fdopen(fd,'w') as stream:
                    json.dump(data,stream);stream.flush();os.fsync(stream.fileno())
                os.replace(name,self.path)
            finally:
                if os.path.exists(name):os.unlink(name)
            return draft

    def public(self):
        draft=self.load()
        data=draft.model_dump(mode='json',exclude={'password'})
        data['password_saved']=bool(draft.password.get_secret_value())
        missing=draft.connection_missing()
        data['connection_missing']=list(missing)
        try:draft.camera()
        except Exception:missing.append('Stream dimensions and valid measured calibration')
        if draft.password.get_secret_value() and not draft.username:missing.append('Camera username')
        for env,label in [('SENTINEL_DETECTOR_WEIGHTS','Detector model'),('SENTINEL_GNN_CHECKPOINT','Validated forecasting checkpoint')]:
            if not Path(os.getenv(env,'/missing')).is_file():missing.append(label)
        data['missing']=missing
        return data
