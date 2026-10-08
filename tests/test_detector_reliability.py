import unittest
from unittest.mock import Mock
import numpy as np
from src.perception.detector import ConstructionSafetyDetector, InferenceUnavailable


class TestDetectorReliability(unittest.TestCase):
    def detector(self, names):
        detector = ConstructionSafetyDetector('/missing/weights.pt', device='cpu')
        detector.model = Mock(names=names)
        return detector

    def test_empty_predictions_stay_empty(self):
        detector = self.detector({0: 'person'})
        result = Mock(); result.boxes = []
        detector.model.predict.return_value = [result]
        detections, ppe = detector.detect(np.zeros((64, 64, 3), np.uint8))
        self.assertEqual(detections.shape, (0, 6))
        self.assertEqual(ppe, [])

    def test_missing_model_and_model_failure_are_explicit(self):
        detector = ConstructionSafetyDetector('/missing/weights.pt', device='cpu')
        with self.assertRaises(InferenceUnavailable):
            detector.detect(np.zeros((64, 64, 3), np.uint8))
        detector.model = Mock(); detector.model.predict.side_effect = RuntimeError('GPU broke')
        with self.assertRaises(InferenceUnavailable):
            detector.detect(np.zeros((64, 64, 3), np.uint8))

    def test_coco_person_and_bus_do_not_collide_with_ppe_class_ids(self):
        detector = self.detector({0: 'person', 5: 'bus', 7: 'truck'})
        detections, ppe = detector._process_detections(np.asarray([[0, 0, 100, 100]] * 3), np.asarray([.9] * 3), np.asarray([0, 5, 7]))
        self.assertEqual(detections[:, 5].tolist(), [0, 2, 2])
        self.assertEqual(ppe[0]['state'], 'unknown')

    def test_construction_model_reports_positive_negative_and_unknown_ppe(self):
        detector = self.detector({0: 'Hardhat', 2: 'NO-Hardhat', 5: 'Person', 7: 'Safety Vest', 8: 'machinery'})
        boxes = np.asarray([[0, 0, 100, 100], [20, 0, 40, 20], [20, 30, 60, 80]])
        _, ppe = detector._process_detections(boxes, np.asarray([.9] * 3), np.asarray([5, 0, 7]))
        self.assertEqual(ppe[0]['state'], 'compliant')
        _, ppe = detector._process_detections(boxes[:2], np.asarray([.9] * 2), np.asarray([5, 2]))
        self.assertEqual(ppe[0]['state'], 'noncompliant')
        self.assertEqual(ppe[0]['vest'], 'unknown')

    def test_bim_missing_geometry_is_not_fabricated(self):
        from src.spatial.bim_resolver import BIMSpatialResolver
        with self.assertRaisesRegex(ValueError, 'geometry unavailable'):
            BIMSpatialResolver('/missing/site.ifc').resolve_element_polygon('Trench')
