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
        result = self.app.element_result.get(ele_id)

        if result is None:
            raise ValueError(f"Element {ele_id} is not found in element_results."
                             "Check that the element ID exists and that a load has been applied.")
        
        return float(result['combined_strain'])
    
    def _read_all_gauges(self):
        """
        Reads all gauges in the current solved state and makes an array with the readings
        """
        return np.ndarray([self._read_gauge(j) for j in self.gauge_definitions])
    
    def _run_damaged(self, scenario):
        """
        Runs the model for a damage scenario, returns the array of the strain measurements of all gauges.
        """
        self.app.damage_overrides = {ele_id: scenario['alpha'] for ele_id in scenario['element_ids']}
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
        """
        n_gauges    = len(self.gauge_definitions)
        n_scenarios = len(self.damage_scenarios)

        S = np.zeros((n_gauges, n_scenarios))
        Error = np.zeros((n_gauges, n_scenarios))
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
            Error[:,j] = (
                (strain_damaged - measured_strain)
                / (np.abs(measured_strain) + 1e-12)) #Gives percentage difference with measured prototype
            norm_product = np.linalg.norm(measured_strain) * np.linalg.norm(strain_damaged)
            MAC[1,j] = (float(np.abs(np.dot(strain_damaged, measured_strain))^2/norm_product) if norm_product > 1e-12 else 0.0)
        
        return S, Error, MAC
            
            


    

        
    


