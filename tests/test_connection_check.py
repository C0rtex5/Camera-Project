import subprocess
import unittest
from unittest.mock import patch, MagicMock
from src.api.camera_setup import SetupDraft
from src.api.connection_check import check_connection


class ConnectionCheckTests(unittest.TestCase):
    def test_one_frame_without_calibration_or_models(self):
        draft=SetupDraft(rtsp_url='rtsp://camera/stream',username='operator',password='secret')
        with patch('src.api.connection_check.subprocess.run',return_value=MagicMock(returncode=0,stdout='{"ok":true,"width":640,"height":480}')) as run:
            result=check_connection(draft)
            self.assertTrue(result['ok'])
            self.assertNotIn('secret',str(run.call_args.args))
            self.assertNotIn('secret',str(result))
            self.assertEqual(run.call_args.kwargs['timeout'],16)

    def test_authentication_error_is_specific_and_redacted(self):
        with patch('src.api.connection_check.subprocess.run',return_value=MagicMock(returncode=0,stdout='{"ok":false}',stderr='rtsp://user:secret@camera: 401 Unauthorized')):
            result=check_connection(SetupDraft(rtsp_url='rtsp://camera/'))
            self.assertIn('authentication',result['message'])
            self.assertNotIn('secret',str(result))

    def test_timeout_closes_tunnel(self):
        draft=SetupDraft(ssh_tunnel='ssh user@server',rtsp_url='rtsp://camera/')
        with patch('src.api.connection_check.CameraTunnel') as tunnel,patch('src.api.connection_check.subprocess.run',side_effect=subprocess.TimeoutExpired('probe',16)):
            result=check_connection(draft)
            self.assertFalse(result['ok'])
            tunnel.return_value.close.assert_called_once()
