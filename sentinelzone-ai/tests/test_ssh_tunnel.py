import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from urllib.parse import urlsplit
from src.api.ssh_tunnel import CameraTunnel, parse_connection, saved_service
from src.api.camera_setup import SetupDraft, SetupStore


class TunnelTests(unittest.TestCase):
    def test_connection_is_data_not_shell(self):
        self.assertEqual(parse_connection('ssh -p 22022 abhay_m@87.103.13.53'),('abhay_m@87.103.13.53',22022))
        self.assertEqual(parse_connection('ssh user@server'),('user@server',22))
        for value in ['ssh -p 0 user@host','ssh -p 65536 user@host','ssh -o ProxyCommand=evil user@host','ssh user@host;id','ssh user@host command']:
            with self.assertRaises(ValueError):parse_connection(value)

    def test_forward_keeps_camera_password_out_of_ssh_arguments(self):
        with patch('src.api.ssh_tunnel.requirements',return_value=[]),patch.dict(os.environ,{'SENTINEL_SSH_KEY_FILE':'/key','SENTINEL_SSH_KNOWN_HOSTS':'/hosts'}):
            tunnel=CameraTunnel('ssh -p 22022 user@server','rtsp://camera:secret@192.168.1.20:8554/stream?channel=1')
        self.assertIn(f'127.0.0.1:{tunnel.port}:192.168.1.20:8554',tunnel.command)
        self.assertIn('StrictHostKeyChecking=yes',tunnel.command)
        self.assertNotIn('secret',' '.join(tunnel.command))
        url=urlsplit(tunnel.source)
        self.assertEqual((url.hostname,url.password,url.path,url.query),('127.0.0.1','secret','/stream','channel=1'))

    def test_draft_persists_tunnel_without_connecting(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'SENTINEL_SETUP_FILE':d+'/camera.json'}),patch('src.api.ssh_tunnel.requirements',return_value=['SSH key missing']):
            store=SetupStore();store.save({'ssh_tunnel':'ssh -p 22022 user@server'})
            self.assertEqual(store.load().ssh_tunnel,'ssh -p 22022 user@server')
            self.assertIn('SSH key missing',store.public()['missing'])
            store.save({'ssh_tunnel':''})
            self.assertEqual(store.load().ssh_tunnel,'')

    def test_model_initialization_failure_closes_tunnel(self):
        draft=MagicMock();draft.ssh_tunnel='ssh user@server'
        with patch('src.api.ssh_tunnel.CameraTunnel') as cls,patch('src.api.live_service.LiveService',side_effect=ValueError('missing model')):
            with self.assertRaises(ValueError):saved_service(draft)
            cls.return_value.close.assert_called_once()

    def test_shutdown_reaps_process(self):
        tunnel=CameraTunnel.__new__(CameraTunnel)
        import threading
        tunnel.stop=threading.Event();tunnel.thread=None;tunnel.process=MagicMock()
        tunnel.process.poll.return_value=None
        tunnel.close()
        tunnel.process.terminate.assert_called_once();tunnel.process.wait.assert_called_once()
