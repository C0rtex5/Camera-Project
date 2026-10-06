import unittest
import cv2
import numpy as np

from src.edge.calibration import fit_ground_plane


class CameraCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.K=np.array([[700.,0,640],[0,700,360],[0,0,1]])
        self.H=np.array([[30.,2.,350.],[1.,25.,200.],[.003,.002,1.]])
        self.camera={'width':1280,'height':720,'homography':{'K':self.K.tolist(),'dist':[.01,-.002,0,0,0]}}
        self.fit=self.records([[0,0],[10,0],[10,10],[0,10],[5,2]])
        self.check=self.records([[2,2],[8,2],[8,8],[2,8]])

    def records(self,ground):
        world=np.asarray(ground,dtype=np.float64)
        ideal=cv2.perspectiveTransform(world.reshape(-1,1,2),self.H).reshape(-1,2)
        rays=np.column_stack(((ideal[:,0]-640)/700,(ideal[:,1]-360)/700,np.ones(len(ground))))
        pixel,_=cv2.projectPoints(rays,np.zeros(3),np.zeros(3),self.K,np.array(self.camera['homography']['dist']))
        return [{'pixel':p.tolist(),'ground':g.tolist()} for p,g in zip(pixel.reshape(-1,2),world)]

    def test_measured_distorted_points_recover_ground_transform(self):
        H,report=fit_ground_plane(self.camera,self.fit,self.check)
        np.testing.assert_allclose(H,self.H,rtol=1e-5,atol=1e-4)
        self.assertLess(report['check_rmse_m'],1e-4)

    def test_incorrect_independent_points_fail_validation(self):
        self.check[0]['ground'][0]+=2
        with self.assertRaisesRegex(ValueError,'RMSE'):
            fit_ground_plane(self.camera,self.fit,self.check)

    def test_fit_points_cannot_be_reused_as_validation(self):
        with self.assertRaisesRegex(ValueError,'different surveyed'):
            fit_ground_plane(self.camera,self.fit,self.fit[:4])

    def test_collinear_survey_rejected(self):
        points=self.records([[0,0],[1,1],[2,2],[3,3]])
        with self.assertRaisesRegex(ValueError,'collinear'):
            fit_ground_plane(self.camera,points,self.check)

    def test_pixel_outside_resolution_rejected(self):
        self.fit[0]['pixel'][0]=1280
        with self.assertRaisesRegex(ValueError,'resolution'):
            fit_ground_plane(self.camera,self.fit,self.check)

    def test_cli_outputs_profile_and_matching_fingerprint(self):
        import json
        import os
        from pathlib import Path
        import subprocess
        import sys
        import tempfile
        from src.edge.checkpoint import camera_fingerprint
        from src.api.live_service import CameraConfig
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            camera={**self.camera,'camera_id':'gate','site_id':'SITE-01','url_env':'CAMERA_GATE'}
            survey=root/'survey.json'
            survey.write_text(json.dumps({'camera':camera,'fit_points':self.fit,'check_points':self.check}))
            script=Path(__file__).resolve().parents[1]/'scripts/calibrate_camera.py'
            command=[sys.executable,str(script),'--survey',str(survey),'--output',str(root/'camera.json'),
                     '--report',str(root/'report.json')]
            run=subprocess.run(command,capture_output=True,text=True,timeout=20)
            self.assertEqual(run.returncode,0,run.stderr)
            profile=CameraConfig.model_validate(json.loads((root/'camera.json').read_text())[0]).model_dump()
            report=json.loads((root/'report.json').read_text())
            self.assertEqual(report['camera_fingerprint'],camera_fingerprint(profile))
            before=(root/'camera.json').read_bytes()
            repeated=subprocess.run(command,capture_output=True,text=True,timeout=20)
            self.assertNotEqual(repeated.returncode,0)
            self.assertEqual((root/'camera.json').read_bytes(),before)
