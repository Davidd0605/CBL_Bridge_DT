import numpy as np

class SensitivityMatrix:
    """

    """

    def __init__(self, app):
        self.app = app 
        self.gauge_definitions = []
        self.damage_scenarios = []
        self.S = None
        self.Error = None
        self.MAC = None
        self.OrthoError = None

    def define_gauges(self, gauge_definitions):
        """
        gauge_definitions : list of dicts:
            'gauge_id'  : str   : which sensor it is
            'ele_id'    : int   : on which element the gauge resides
        
        example:
        [
            {'gauge_id': 'S1', 'ele_id': 3}, (one entry of the list of dicts)
        ]
        """
        self.gauge_definitions = gauge_definitions

    def define_damage_scenarios(self, damage_scenarios):
        """
        damage_scenarios : list of dicts:
            'element_ids'   : list[int] : elements whose E and G are reduced
            'alpha'         : float     : stiffness multiplier (0.8 means a 20% reduction)
            'gauge_index'   : int       : index into gauge_definitions of closest gauge to the damage
        """
        self.damage_scenarios = damage_scenarios

    def _read_gauge(self, gauge):
        """
        Reads the combined strain from one specified gauge in the model using _collect_element_results()
        """
        ele_id = gauge['ele_id']
        result = self.app.element_results.get(ele_id)

        if result is None:
            raise ValueError(f"Element {ele_id} is not found in element_results."
                             "Check that the element ID exists and that a load has been applied.")
        
        return float(result['combined_strain'])
    
    def _read_all_gauges(self):
        """
        Reads all gauges in the current solved state and makes an array with the readings
        """
        return np.array([self._read_gauge(j) for j in self.gauge_definitions])
    
    def _run_damaged(self, scenario):
        """
        Runs the model for a damage scenario, returns the array of the strain measurements of all gauges.
        """
        self.app.set_damage(scenario['element_ids'], alpha=scenario['alpha'])
        ok = self.app._solve_current_loads()
        if ok != 0:
            self.app.damage_overrides = {}
            self.app._solve_current_loads() #Restores the bridge to the healthy state in case the model does not finish running.
            raise RuntimeError(f"Analysis failed for scenario '{scenario['scenario_id']}'.")
        
        strain_damaged = self._read_all_gauges()
        self.app.damage_overrides = {}
        return strain_damaged
    
    def _build_sensitivity(self, measured_strain, verbose=True):
        """
        Builds the sensitivity matrix S, and computes the error, MAC, and orthogonality metrics for each damage scenario compared to the measured strain from the prototype.
        """
        n_gauges    = len(self.gauge_definitions)
        n_scenarios = len(self.damage_scenarios)

        S = np.zeros((n_gauges, n_scenarios))
        Error = np.zeros((n_gauges, n_scenarios))
        OrthoError = np.zeros((1, n_scenarios))
        MAC = np.zeros((1, n_scenarios))

        for j, scenario in enumerate(self.damage_scenarios):
            if verbose:
                print(
                    f"\nScenario {j+1}/{n_scenarios}: "
                    f"'{scenario['scenario_id']}'  "
                    f"(elements {scenario['element_ids']}, alpha={scenario['alpha']})"
                )
            strain_damaged = self._run_damaged(scenario)

            S[:,j] = strain_damaged
            Error[:,j] = (strain_damaged - measured_strain)
                
            denom = (np.dot(measured_strain, measured_strain) * np.dot(strain_damaged, strain_damaged) + 1e-12)
            MAC[0,j] = (float(np.abs(np.dot(strain_damaged, measured_strain))**2/denom) if denom > 1e-12 else 0.0)

            s_dot_m = float(np.dot(strain_damaged, measured_strain))
            s_parallel = (s_dot_m / (np.dot(measured_strain, measured_strain) + 1e-24)) * measured_strain
            r = strain_damaged - s_parallel
            OrthoError[0, j] = float(np.linalg.norm(r)) / (float(np.linalg.norm(measured_strain)) + 1e-24)
        
        self.measured_strain = measured_strain
        self.S = S
        self.Error = Error
        self.MAC = MAC
        self.OrthoError = OrthoError
        return S, Error, MAC, OrthoError
    
    def detect(self, mac_weight= 10.0):
        """
        mac_weight : float
            A weighting factor to balance the importance of MAC vs orthogonality or rmse in their combined scores.
        Uses two different combined scoring approaches to rank the damage scenarios, one based on MAC and orthogonality, and another based on MAC and RMSE.
        returns a dict with the best scenarios according to both combined scores, and whether they agree on the same scenario.
        """
        if self.Error is None:
            raise RuntimeError("Call _build_sensitivity() before detect().")
 
        n_scenarios = len(self.damage_scenarios)
        nrmse_errors = [
            float(np.sqrt(np.mean(self.Error[:, j] ** 2)))/float(np.mean(np.abs(self.measured_strain))+1e-24)
            for j in range(n_scenarios)
        ]
        mac_scores = [float(self.MAC[0, j]) for j in range(n_scenarios)]
        ortho_scores = [float(self.OrthoError[0, j]) for j in range(n_scenarios)]

        combined_ortho_scores = [
            mac_scores[j] / (1.0 + mac_weight * ortho_scores[j])
            for j in range(n_scenarios)
        ]


        #Rank by ascending combined ortho score (higher score = more likely damage location).
        ranked_ortho_indices = sorted(range(n_scenarios), key=lambda j: combined_ortho_scores[j], reverse=True)
        best_ortho_idx       = ranked_ortho_indices[0]
        best_ortho_scenario  = self.damage_scenarios[best_ortho_idx]
        #ranking_ortho = [self.damage_scenarios[j] for j in ranked_ortho_indices]
        best_ortho_mac = mac_scores[best_ortho_idx]
        best_ortho_ortho = ortho_scores[best_ortho_idx]


        #Rank by descending combined NRMSE (lower score = more likely damage location).
        ranked_nrmse_indices = sorted(range(n_scenarios), key=lambda j: nrmse_errors[j])
        best_nrmse_idx       = ranked_nrmse_indices[0]
        best_nrmse_scenario  = self.damage_scenarios[best_nrmse_idx]
        #ranking_nrmse = [self.damage_scenarios[j] for j in ranked_nrmse_indices]
        best_nrmse_mac = mac_scores[best_nrmse_idx]
        best_nrmse_nrmse = nrmse_errors[best_nrmse_idx]

        agreement = (best_ortho_idx == best_nrmse_idx)

        return{

            'best_ortho': {
                'scenario': best_ortho_scenario,
                'MAC': best_ortho_mac,
                'OrthoError': best_ortho_ortho
            },
            'best_nrmse': {
                'scenario': best_nrmse_scenario,
                'MAC': best_nrmse_mac,
                'NRMSE_Error': best_nrmse_nrmse
            },
            'agreement': agreement
        }

        