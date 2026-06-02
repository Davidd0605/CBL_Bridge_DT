import numpy as np


class SensitivityMatrix:
    """Builds damage sensitivity matrices using either raw strains or tare deltas."""

    def __init__(self, app):
        self.app = app
        self.gauge_definitions = []
        self.damage_scenarios = []
        self.S = None
        self.Error = None
        self.MAC = None
        self.OrthoError = None
        self.measured_strain = None

    @property
    def baseline(self):
        return self.app.comparison

    def define_gauges(self, gauge_definitions):
        """
        gauge_definitions : list of dicts:
            'gauge_id'  : str   : which sensor it is
            'ele_id'    : int   : on which element the gauge resides

        example:
        [
            {'gauge_id': 'S1', 'ele_id': 3},
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

    def tare(self, real_state=None, strain_readings=None):
        """Set comparison baseline from physical sensors and current model state."""
        return self.baseline.tare(
            real_state=real_state,
            strain_readings=strain_readings,
        )

    def clear_tare(self):
        self.baseline.clear()

    def _read_gauge_absolute(self, gauge):
        """Read absolute combined strain from the model for one gauge element."""
        ele_id = gauge["ele_id"]
        result = self.app.element_results.get(ele_id)
        if result is None:
            raise ValueError(
                f"Element {ele_id} is not found in element_results. "
                "Check that the element ID exists and that a load has been applied."
            )
        return float(result["combined_strain"])

    def _read_all_gauges_absolute(self):
        return np.array(
            [self._read_gauge_absolute(gauge) for gauge in self.gauge_definitions]
        )

    def _read_all_gauges_delta(self):
        if self.baseline.active:
            return np.array(
                self.baseline.model_strain_deltas_for_gauges(self.gauge_definitions)
            )
        return self._read_all_gauges_absolute()

    def _physical_delta_strain(self, measured_strain):
        measured = np.asarray(measured_strain, dtype=float)
        if not self.baseline.active:
            return measured
        return np.array(
            self.baseline.physical_strain_deltas_for_gauges(
                self.gauge_definitions, measured
            )
        )

    def _run_damaged(self, scenario, mode: str):
        """Run one damage scenario and return gauge strain vectors in the requested mode."""
        self.app.set_damage(scenario["element_ids"], alpha=scenario["alpha"])
        ok = self.app._solve_current_loads()
        if ok != 0:
            self.app.reset_damage()
            self.app._solve_current_loads()
            raise RuntimeError(
                f"Analysis failed for scenario '{scenario.get('scenario_id', 'unknown')}'."
            )

        if mode == "delta":
            strain_damaged = self._read_all_gauges_delta()
        elif mode == "absolute":
            strain_damaged = self._read_all_gauges_absolute()
        else:
            raise ValueError(f"Unsupported mode: {mode!r}")
        self.app.reset_damage()
        self.app._solve_current_loads()
        return strain_damaged

    def build_sensitivity(self, measured_strain, verbose=True, mode: str | None = None):
        """Compute sensitivity matrix and metrics in either delta or absolute space.

        mode:
            - "delta": compare tare deltas (requires `self.baseline.active`)
            - "absolute": compare raw absolute strains (does not require tare)

        measured_strain must match the order of `self.gauge_definitions`.
        """
        if mode is None:
            mode = getattr(self.app, "comparison_mode", "delta")

        if mode not in ("delta", "absolute"):
            raise ValueError(f"Unsupported mode: {mode!r} (use 'delta' or 'absolute').")

        if mode == "delta":
            if not self.baseline.active:
                raise RuntimeError(
                    "Comparison tare is not active. Call tare() with physical sensor readings first."
                )
            measured_vec = self._physical_delta_strain(measured_strain)
        else:
            measured_vec = np.asarray(measured_strain, dtype=float)

        return self._build_sensitivity(measured_vec, verbose=verbose, mode=mode)

    def _build_sensitivity(self, measured_vec, verbose=True, mode: str = "delta"):
        """Internal helper: compute metrics comparing model vectors to measured vectors."""
        n_gauges = len(self.gauge_definitions)
        n_scenarios = len(self.damage_scenarios)

        S = np.zeros((n_gauges, n_scenarios))
        Error = np.zeros((n_gauges, n_scenarios))
        OrthoError = np.zeros((1, n_scenarios))
        MAC = np.zeros((1, n_scenarios))

        for j, scenario in enumerate(self.damage_scenarios):
            if verbose:
                print(
                    f"\nScenario {j + 1}/{n_scenarios}: "
                    f"'{scenario.get('scenario_id', j)}'  "
                    f"(elements {scenario['element_ids']}, alpha={scenario['alpha']})"
                )
            strain_damaged = self._run_damaged(scenario, mode=mode)

            S[:, j] = strain_damaged
            Error[:, j] = strain_damaged - measured_vec

            denom = (
                np.dot(measured_vec, measured_vec)
                * np.dot(strain_damaged, strain_damaged)
                + 1e-12
            )
            MAC[0, j] = (
                float(np.abs(np.dot(strain_damaged, measured_vec)) ** 2 / denom)
                if denom > 1e-12
                else 0.0
            )

            s_dot_m = float(np.dot(strain_damaged, measured_vec))
            s_parallel = (
                s_dot_m / (np.dot(measured_vec, measured_vec) + 1e-24)
            ) * measured_vec
            r = strain_damaged - s_parallel
            OrthoError[0, j] = float(np.linalg.norm(r)) / (
                float(np.linalg.norm(measured_vec)) + 1e-24
            )

        self.measured_strain = measured_vec
        self.S = S
        self.Error = Error
        self.MAC = MAC
        self.OrthoError = OrthoError
        return S, Error, MAC, OrthoError

    def detect(self, mac_weight=10.0):
        """
        mac_weight : float
            Weighting factor balancing MAC vs orthogonality or NRMSE in combined scores.

        Uses two combined scoring approaches (MAC+orthogonality, MAC+NRMSE) to rank
        damage scenarios. Returns the best scenario from each method and whether they agree.
        """
        if self.Error is None:
            raise RuntimeError("Call build_sensitivity() before detect().")

        n_scenarios = len(self.damage_scenarios)
        nrmse_errors = [
            float(np.sqrt(np.mean(self.Error[:, j] ** 2)))
            / float(np.mean(np.abs(self.measured_strain)) + 1e-24)
            for j in range(n_scenarios)
        ]
        mac_scores = [float(self.MAC[0, j]) for j in range(n_scenarios)]
        ortho_scores = [float(self.OrthoError[0, j]) for j in range(n_scenarios)]

        combined_ortho_scores = [
            mac_scores[j] / (1.0 + mac_weight * ortho_scores[j])
            for j in range(n_scenarios)
        ]

        combined_nrmse_scores = [
            mac_scores[j] / (1.0 + mac_weight * nrmse_errors[j])
            for j in range(n_scenarios)
        ]

        ranked_ortho_indices = sorted(
            range(n_scenarios), key=lambda j: combined_ortho_scores[j], reverse=True
        )
        best_ortho_idx = ranked_ortho_indices[0]
        best_ortho_scenario = self.damage_scenarios[best_ortho_idx]
        best_ortho_mac = mac_scores[best_ortho_idx]
        best_ortho_ortho = ortho_scores[best_ortho_idx]

        ranked_nrmse_indices = sorted(
            range(n_scenarios), key=lambda j: combined_nrmse_scores[j], reverse=True
        )
        best_nrmse_idx = ranked_nrmse_indices[0]
        best_nrmse_scenario = self.damage_scenarios[best_nrmse_idx]
        best_nrmse_mac = mac_scores[best_nrmse_idx]
        best_nrmse_nrmse = nrmse_errors[best_nrmse_idx]

        agreement = best_ortho_idx == best_nrmse_idx

        return {
            "best_ortho": {
                "scenario": best_ortho_scenario,
                "MAC": best_ortho_mac,
                "OrthoError": best_ortho_ortho,
            },
            "best_nrmse": {
                "scenario": best_nrmse_scenario,
                "MAC": best_nrmse_mac,
                "NRMSE_Error": best_nrmse_nrmse,
            },
            "agreement": agreement,
        }
