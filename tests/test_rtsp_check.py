import base64
import hashlib
import json
import socketserver
import threading
from contextlib import contextmanager
from unittest.mock import patch
from urllib.request import parse_http_list, parse_keqv_list

from src.hub.rtsp_check import check_rtsp
from tests.hub_support import HubAPITest, camera


@contextmanager
def rtsp_camera(scheme='Basic', password='test:camera@password', status=200):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            request = b''
            while b'\r\n\r\n' not in request:
                chunk=self.request.recv(4096)
                if not chunk: return
                request+=chunk
            headers = dict(line.split(': ',1) for line in request.decode().split('\r\n')[1:] if ': ' in line)
            auth=headers.get('Authorization','')
            accepted=False
            if scheme=='Basic':
                accepted=auth=='Basic '+base64.b64encode(f'camera-user:{password}'.encode()).decode()
            elif auth.startswith('Digest '):
                fields=parse_keqv_list(parse_http_list(auth[7:]))
                uri=request.decode().split(' ')[1]
                md5=lambda s: hashlib.md5(s.encode()).hexdigest()
                expected=md5(f'{md5("camera-user:camera:"+password)}:fixed-nonce:{fields.get("nc", "")}:{fields.get("cnonce", "")}:auth:{md5("DESCRIBE:"+uri)}')
                accepted=fields.get('response')==expected and fields.get('uri')==uri
            response=f'RTSP/1.0 {status if accepted else 401} Result\r\nCSeq: 1\r\n'
            if not accepted:
                response+=f'WWW-Authenticate: {scheme} realm="camera"'+(', nonce="fixed-nonce", qop="auth"' if scheme=='Digest' else '')+'\r\n'
            self.request.sendall((response+'Content-Length: 0\r\n\r\n').encode())
    with socketserver.ThreadingTCPServer(('127.0.0.1',0),Handler) as server:
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try: yield f'rtsp://127.0.0.1:{server.server_address[1]}/stream'
        finally: server.shutdown();thread.join()


def test_basic_special_characters_are_not_url_encoded_in_auth():
    password='test:camera@pass/#?% word'
    with rtsp_camera(password=password) as url:
        result=check_rtsp(url,{'username':'camera-user','password':password})
        assert result['ok']
        assert password not in json.dumps(result)


def test_digest_special_characters_and_invalid_login():
    password='p@ss:/?#% word'
    with rtsp_camera(scheme='Digest',password=password) as url:
        assert check_rtsp(url,{'username':'camera-user','password':password})['ok']
        assert check_rtsp(url,{'username':'camera-user','password':'wrong'})['code']=='authentication_rejected'
        assert check_rtsp(url)['code']=='authentication_required'


def test_authenticated_wrong_path_is_not_reported_as_bad_password():
    with rtsp_camera(status=404) as url:
        assert check_rtsp(url,{'username':'camera-user','password':'test:camera@password'})['code']=='path_not_found'


class TestRTSPAPI(HubAPITest):
    def test_check_reuses_saved_credentials_without_returning_them(self):
        password='private-camera-password'
        self.save_camera(rtsp_username='camera-user',rtsp_password=password)
        with patch('src.hub.rtsp_check.check_rtsp',return_value={'ok':True,'message':'accepted'}) as probe:
            result=self.client.post('/api/v1/cameras/check',json=camera())
        self.assertEqual(result.status_code,200)
        self.assertEqual(probe.call_args.args[1],{'username':'camera-user','password':password})
        self.assertNotIn(password,result.text)

    def test_unsaved_credentials_are_used_without_persistence(self):
        with patch('src.hub.rtsp_check.check_rtsp',return_value={'ok':True,'message':'accepted'}) as probe:
            result=self.client.post('/api/v1/cameras/check',json=camera(rtsp_username='user',rtsp_password='secret'))
        self.assertEqual(result.status_code,200)
        self.assertEqual(probe.call_args.args[1]['password'],'secret')
        self.assertIsNone(self.store.camera_credentials('cam1'))
        self.assertEqual(self.store.cameras(),[])

    def test_viewer_cannot_test_private_camera_login(self):
        self.login('viewer')
        self.assertEqual(self.client.post('/api/v1/cameras/check',json=camera()).status_code,403)

    def test_operator_retry_exits_forced_cpu_mode(self):
        self.hub.device.mode='cpu'
        self.hub.device.gpu_available=lambda:True
        response=self.client.post('/api/v1/compute/retry',json={})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['mode'],'auto')
        self.assertEqual(response.json()['device'],'cuda:0')

    def test_malformed_camera_url_does_not_send_requests_or_expose_input(self):
        for url in ('rtsp://127.0.0.1/private\r\nInjected: secret', 'rtsp://127.0.0.1:secret/stream'):
            with patch('src.hub.rtsp_check.check_rtsp') as probe:
                result=self.client.post('/api/v1/cameras/check',json=camera(rtsp_url=url))
            self.assertEqual(result.status_code,422)
            self.assertNotIn('secret',result.text)
            probe.assert_not_called()
