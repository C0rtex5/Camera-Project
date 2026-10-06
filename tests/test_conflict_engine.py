import unittest
import numpy as np
from src.edge.conflict_engine import DynamicConflictEngine, AlertHysteresisDebouncer


class TestConflictEngine(unittest.TestCase):
    def setUp(self):
        self.engine = DynamicConflictEngine(warning_ttc=2.5, critical_ttc=1.5, prob_threshold=0.65)
        self.debouncer = AlertHysteresisDebouncer(escalation_frames=3, deescalation_frames=5)

    def test_imminent_head_on_collision(self):
        # 3 modes, 50 steps
        num_modes = 3
        steps = 50

        # Worker moving along X: 0 to 5m
        w_x = np.tile(np.linspace(0, 5, steps), (num_modes, 1))
        w_y = np.zeros((num_modes, steps))
        # Machine moving along X: 10 to 0m (crossing worker path around t=1.0s, step 10)
        m_x = np.tile(np.linspace(6, 0, steps), (num_modes, 1))
        m_y = np.zeros((num_modes, steps))

        agent_worker = {
            "footprint_radius": 0.8,
            "class_name": "WORKER",
            "mode_probs": np.array([0.8, 0.1, 0.1]),
            "mu_x": w_x,
            "mu_y": w_y
        }
        agent_machine = {
            "footprint_radius": 2.0,
            "class_name": "HEAVY_EQUIPMENT",
            "mode_probs": np.array([0.9, 0.05, 0.05]),
            "mu_x": m_x,
            "mu_y": m_y
        }

        risk = self.engine.evaluate_pair_risk(agent_worker, agent_machine)
        # They breach r_col = 2.8m early in horizon
        self.assertIn(risk["state"], ["CRITICAL_LEVEL_3", "WARNING_LEVEL_2"])
        self.assertGreater(risk["min_ttc"], 0.0)
        self.assertGreater(risk["p_col"], 0.5)

    def test_diverging_safe_paths(self):
        steps = 50
        num_modes = 3
        # Agent A at (0, 0) moving +X
        a_x = np.tile(np.linspace(0, 5, steps), (num_modes, 1))
        a_y = np.zeros((num_modes, steps))
        # Agent B at (0, 50) moving +Y (far away)
        b_x = np.zeros((num_modes, steps))
        b_y = np.tile(np.linspace(50, 55, steps), (num_modes, 1))

        agent_a = {
            "footprint_radius": 0.8,
            "class_name": "WORKER",
            "mode_probs": np.array([1.0, 0.0, 0.0]),
            "mu_x": a_x,
            "mu_y": a_y
        }
        agent_b = {
            "footprint_radius": 2.0,
            "class_name": "HEAVY_EQUIPMENT",
            "mode_probs": np.array([1.0, 0.0, 0.0]),
            "mu_x": b_x,
            "mu_y": b_y
        }

        risk = self.engine.evaluate_pair_risk(agent_a, agent_b)
        self.assertEqual(risk["state"], "NORMAL_LEVEL_0")
        self.assertEqual(risk["min_ttc"], -1.0)
        self.assertEqual(risk["p_col"], 0.0)

    def test_debouncer_escalation_and_deescalation(self):
        # Initial: NORMAL_LEVEL_0
        self.assertEqual(self.debouncer.current_state, "NORMAL_LEVEL_0")

        # 2 frames of CRITICAL -> should not escalate yet (requires 3)
        self.debouncer.step("CRITICAL_LEVEL_3")
        self.assertEqual(self.debouncer.current_state, "NORMAL_LEVEL_0")
        self.debouncer.step("CRITICAL_LEVEL_3")
        self.assertEqual(self.debouncer.current_state, "NORMAL_LEVEL_0")

        # 3rd frame -> escalates to CRITICAL_LEVEL_3
        state = self.debouncer.step("CRITICAL_LEVEL_3")
        self.assertEqual(state, "CRITICAL_LEVEL_3")

        # 4 frames of NORMAL -> does not de-escalate yet (requires 5)
        for _ in range(4):
            self.debouncer.step("NORMAL_LEVEL_0")
            self.assertEqual(self.debouncer.current_state, "CRITICAL_LEVEL_3")

        # 5th frame -> de-escalates to NORMAL_LEVEL_0
        state = self.debouncer.step("NORMAL_LEVEL_0")
        self.assertEqual(state, "NORMAL_LEVEL_0")

class TestMixtureCollisionRisk(unittest.TestCase):
    def test_all_equally_likely_modes_collide(self):
        agent = {'footprint_radius':1, 'class_name':'WORKER', 'mode_probs':np.array([1/3]*3),
                 'mu_x':np.zeros((3,50)), 'mu_y':np.zeros((3,50))}
        risk = DynamicConflictEngine().evaluate_pair_risk(agent, {**agent,'class_name':'HEAVY_EQUIPMENT'})
        self.assertEqual(risk['state'],'CRITICAL_LEVEL_3')
        self.assertAlmostEqual(risk['p_col'],1.)
        self.assertAlmostEqual(risk['min_ttc'],.1)

    def test_late_peak_does_not_turn_tiny_early_risk_into_critical(self):
        worker = {'footprint_radius':.5, 'class_name':'WORKER','mode_probs':np.array([1.]),
                  'mu_x':np.zeros((1,50)), 'mu_y':np.zeros((1,50))}
        machine_x=np.full((2,50),100.)
        machine_x[0,0]=0. # 1% probability at 0.1 seconds
        machine_x[1,39:]=0. # 99% probability after four seconds
        machine = {'footprint_radius':.5,'class_name':'HEAVY_EQUIPMENT','mode_probs':np.array([.01,.99]),
                   'mu_x':machine_x,'mu_y':np.zeros((2,50))}
        risk = DynamicConflictEngine().evaluate_pair_risk(worker,machine)
        self.assertEqual(risk['state'],'ADVISORY_LEVEL_1')
        self.assertAlmostEqual(risk['min_ttc'],4.)
        self.assertAlmostEqual(risk['p_col'],.99)

    def test_mode_probabilities_are_not_counted_across_different_times(self):
        worker = {'footprint_radius':.5, 'class_name':'WORKER','mode_probs':np.array([1.]),
                  'mu_x':np.zeros((1,50)), 'mu_y':np.zeros((1,50))}
        paths=np.full((2,50),100.)
        paths[0,0]=0.;paths[1,1]=0.
        machine = {'footprint_radius':.5, 'class_name':'HEAVY_EQUIPMENT','mode_probs':np.array([.5,.5]),
                   'mu_x':paths,'mu_y':np.zeros((2,50))}
        risk = DynamicConflictEngine().evaluate_pair_risk(worker,machine)
        self.assertEqual(risk['state'],'WARNING_LEVEL_2')
        self.assertAlmostEqual(risk['p_col'],.5)


if __name__ == "__main__":
    unittest.main()
