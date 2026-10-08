from tests.hub_support import HubAPITest, calibration


class TestAgentAPI(HubAPITest):
    def test_supervisor_reports_rules_and_actual_camera_state(self):
        data = self.client.post('/api/v1/agent/supervisor/chat', json={'message': 'Telemetry'}).json()
        self.assertEqual(data['reasoning_mode'], 'rules')
        self.assertEqual(data['visual_review'], 'human_required')
        self.assertEqual(data['cameras'], [])
        self.assertEqual(data['incident_count'], 0)

    def test_manifest_requires_survey_then_human_approval(self):
        self.save_camera(calibration=calibration())
        zones = [{'zone_id': 'loading', 'polygon': [[1, 1], [5, 1], [5, 5], [1, 5]],
                  'coordinate_frame': 'surveyed-floor', 'ttc_multiplier': 1.5}]
        self.login('operator')
        response = self.client.post('/api/v1/agent/pipeline1/compile_manifest', json={'camera_id': 'cam1', 'permit_text': 'Loading zone', 'zones': zones})
        self.assertEqual(response.status_code, 200, response.text)
        draft = response.json()
        self.assertEqual(draft['status'], 'DRAFT')
        self.assertEqual(self.store.cameras()[0]['zones'], [])
        self.assertEqual(self.client.post('/api/v1/manifests/' + draft['manifest_id'] + '/approve', json={}).status_code, 403)
        self.login('admin')
        response = self.client.post('/api/v1/manifests/' + draft['manifest_id'] + '/approve', json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.cameras()[0]['zones'][0]['ttc_multiplier'], 1.5)

    def test_unknown_geometry_cannot_be_approved(self):
        self.save_camera()
        zones = [{'zone_id': 'z', 'polygon': [[0, 0], [1, 0], [1, 1]], 'coordinate_frame': 'unknown'}]
        self.assertEqual(self.client.post('/api/v1/manifests', json={'camera_id': 'cam1', 'zones': zones}).status_code, 422)

    def test_triage_does_not_claim_visual_verdict(self):
        self.client.post('/api/v1/incidents/publish', json={'event_id': 'incident'})
        data = self.client.post('/api/v1/agent/pipeline2/adjudicate', json={'event_id': 'incident'}).json()
        self.assertFalse(data['visual_analysis_performed'])
        self.assertEqual(data['review_status'], 'HUMAN_REVIEW_REQUIRED')
        self.assertNotIn('verdict', data)

    def test_offline_briefing_and_learning_queue(self):
        data = self.client.get('/api/v1/agent/toolbox_talk/factory').json()
        self.assertTrue(data['requires_human_review'])
        self.assertEqual(data['reasoning_mode'], 'rules')
        self.assertEqual(self.client.get('/api/v1/agent/active_learning/queue').json(), [])

    def test_supervisor_stages_zone_draft_and_viewer_cannot_mutate(self):
        self.save_camera(calibration=calibration())
        zones = [{'zone_id': 'z', 'coordinate_frame': 'surveyed-floor', 'polygon': [[0,0],[1,0],[1,1]]}]
        response = self.client.post('/api/v1/agent/supervisor/chat', json={'message': 'Change exclusion zone', 'camera_id': 'cam1', 'zones': zones})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.store.manifests()), 1)
        self.assertEqual(self.store.cameras()[0]['zones'], [])
        self.login('viewer')
        self.assertEqual(self.client.post('/api/v1/agent/supervisor/chat', json={'message': 'Change exclusion zone', 'camera_id': 'cam1', 'zones': zones}).status_code, 403)
