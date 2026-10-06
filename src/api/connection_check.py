"""Bounded, non-recording connection checks with objective frame-quality evidence."""
import json
import os
import socket
import subprocess
import sys

from src.api.camera_survey import QUALITY_THRESHOLDS, endpoint_parts
from src.api.ssh_tunnel import CameraTunnel

PROBE = r'''
import cv2, json, os
import numpy as np

source = os.environ['SENTINEL_PROBE_SOURCE']
cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG,
    [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000])
try:
    if not cap.isOpened():
        print(json.dumps({'ok': False, 'blocker': 'camera_open_failed'}))
    else:
        ok, frame = cap.read()
        if not ok or frame is None or frame.size == 0:
            print(json.dumps({'ok': False, 'blocker': 'no_video_frame'}))
        else:
            height, width = frame.shape[:2]
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            brightness = float(gray.mean())
            contrast = float(gray.std())
            notes = []
            min_width = int(os.environ['QUALITY_MIN_WIDTH'])
            min_height = int(os.environ['QUALITY_MIN_HEIGHT'])
            dark_mean = float(os.environ['DARK_MEAN'])
            bright_mean = float(os.environ['BRIGHT_MEAN'])
            min_sharpness = float(os.environ['MIN_SHARPNESS'])
            min_contrast = float(os.environ['MIN_CONTRAST'])
            if width < min_width or height < min_height:
                notes.append('resolution below PoC screening minimum')
            if brightness < dark_mean:
                notes.append('image is very dark')
            if brightness > bright_mean:
                notes.append('image is over-bright')
            if sharpness < min_sharpness:
                notes.append('image may be blurred')
            if contrast < min_contrast:
                notes.append('image contrast is low')
            quality = {
                'status': 'review' if notes else 'acceptable',
                'brightness_mean': round(brightness, 2),
                'contrast_std': round(contrast, 2),
                'sharpness_laplacian': round(sharpness, 2),
                'resolution_status': ('meets_poc_minimum' if width >= min_width and height >= min_height else 'below_poc_minimum'),
                'notes': notes,
            }
            print(json.dumps({'ok': True, 'width': int(width), 'height': int(height), 'quality': quality}))
finally:
    cap.release()
'''


def _probe_env() -> dict:
    return {
        **os.environ,
        'SENTINEL_PROBE_SOURCE': '',
        'OPENCV_FFMPEG_CAPTURE_OPTIONS': 'rtsp_transport;tcp',
        'QUALITY_MIN_WIDTH': str(QUALITY_THRESHOLDS['min_width']),
        'QUALITY_MIN_HEIGHT': str(QUALITY_THRESHOLDS['min_height']),
        'MIN_SHARPNESS': str(QUALITY_THRESHOLDS['min_sharpness_laplacian']),
        'DARK_MEAN': str(QUALITY_THRESHOLDS['dark_mean_luma']),
        'BRIGHT_MEAN': str(QUALITY_THRESHOLDS['bright_mean_luma']),
        'MIN_CONTRAST': str(QUALITY_THRESHOLDS['min_contrast_std']),
    }


def _access_evidence(draft, transport, tcp_reachable=None):
    host, port, path = endpoint_parts(draft)
    return {
        'transport': transport,
        'host': host,
        'rtsp_port': port,
        'stream_path': path,
        'access_path': getattr(draft, 'access_path', '') or transport,
        'tcp_reachable': tcp_reachable,
        'tunnel_established': None if transport != 'ssh_tunnel' else False,
    }


def _tcp_check(draft):
    host, port, _ = endpoint_parts(draft)
    if not host or not port:
        return None
    try:
        with socket.create_connection((host, int(port)), timeout=0.75):
            return True
    except OSError:
        return False


def _failure(stage, message, blocker, detail='', access=None):
    result = {
        'ok': False,
        'stage': stage,
        'blocker': blocker,
        'blocker_detail': detail,
        'message': message,
    }
    if access:
        result.update(access)
    return result


def _classify_error(error: str) -> tuple[str, str]:
    lowered = error.lower()
    if '401' in lowered or 'unauthorized' in lowered:
        return 'camera_authentication', 'Camera rejected RTSP authentication. Check the camera username and password.'
    if '404' in lowered or 'not found' in lowered:
        return 'stream_not_found', 'Camera RTSP stream path was not found. Check the complete stream URL.'
    if 'connection refused' in lowered:
        return 'connection_refused', 'Camera refused the RTSP connection. Check the camera address, port and streaming service.'
    if 'timed out' in lowered or 'timeout' in lowered:
        return 'connection_timeout', 'Camera connection timed out. Check camera reachability and the complete RTSP path.'
    if 'host key verification failed' in lowered:
        return 'ssh_host_key', 'SSH host-key verification failed. Configure the independently verified server host key.'
    if 'permission denied' in lowered:
        return 'ssh_authentication', 'SSH authentication failed. Check the SSH username and private key; the camera password is separate.'
    return 'camera_unreachable', 'Camera did not return a video frame. Check RTSP reachability, path, credentials, and network policy.'


def check_connection(draft):
    tunnel = None
    transport = 'ssh_tunnel' if draft.ssh_tunnel else 'direct'
    stage = 'ssh' if draft.ssh_tunnel else 'camera'
    access = _access_evidence(draft, transport, None)
    try:
        source = draft.source()
        if draft.ssh_tunnel:
            tunnel = CameraTunnel(draft.ssh_tunnel, source, draft.ssh_key_file, draft.ssh_known_hosts)
            tunnel.start()
            source = tunnel.source
            access = _access_evidence(draft, transport, None)
            access['tunnel_established'] = True
        else:
            access = _access_evidence(draft, transport, _tcp_check(draft))
        stage = 'camera'
        environment = _probe_env()
        environment['SENTINEL_PROBE_SOURCE'] = source
        result = subprocess.run(
            [sys.executable, '-c', PROBE],
            env=environment,
            capture_output=True,
            text=True,
            timeout=16,
        )
        try:
            packet = json.loads(result.stdout) if result.returncode == 0 else {'ok': False}
        except (TypeError, ValueError):
            packet = {'ok': False}
        if packet.get('ok'):
            return {
                **packet,
                **access,
                'stage': 'connected',
                'blocker': None,
                'blocker_detail': '',
                'message': 'Camera connected and a video frame was received. Review the quality measurements before selecting it for the PoC.',
            }
        if packet.get('blocker') == 'camera_open_failed':
            return _failure(stage, 'Camera connection could not be opened. Check address, port, stream path and network policy.', packet['blocker'], access=access)
        if packet.get('blocker') == 'no_video_frame':
            return _failure(stage, 'Camera connected but did not return a decodable video frame.', packet['blocker'], access=access)
        blocker, message = _classify_error(result.stderr or '')
        return _failure(stage, message, blocker, access=access)
    except subprocess.TimeoutExpired:
        return _failure(stage, 'Camera connection timed out. Check camera reachability and the complete RTSP path.', 'connection_timeout', access=access)
    except ValueError as error:
        if stage == 'ssh':
            return _failure(stage, str(error), 'ssh_configuration', access=access)
        return _failure(stage, 'Camera connection check failed to decode a response.', 'decode_failed', access=access)
    except OSError:
        return _failure(stage, 'Connection check could not start. Check the SSH client and key-file access on the application host.', 'probe_start_failed', access=access)
    finally:
        if tunnel:
            tunnel.close()
