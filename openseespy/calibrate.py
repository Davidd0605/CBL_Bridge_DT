#!/usr/bin/env python3
"""One-shot bridge model calibration.

Run this script once to fit the OpenSeesPy model parameters (E and cross-section
area scale factors) to the real physical bridge.  The result is saved to
``calibration.json`` in the same directory and is **automatically loaded** by
``BridgeModel`` on every subsequent startup — no further action needed.

Workflow
--------
1. Make sure the physical bridge sensors are publishing to ``cbl/bridge/real/state``.
2. Run this script.
3. For each load position prompted, place the calibration weight on the bridge.
4. Press Enter when the readings on the sensors are stable.
5. The script optimises and saves ``calibration.json``.
6. (Re)start ``bridge_model.py`` — it picks up the file automatically.

Examples
--------
Apply 100 N at three midspan positions (nodes 30, 32, 34)::

    python calibrate.py --loads 30:100 32:100 34:100

Use a global search with differential evolution::

    python calibrate.py --loads 30:100 32:100 --method differential_evolution

Override the output file location::

    python calibrate.py --loads 30:100 --output /path/to/my_calibration.json
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bridge_model import BridgeModel  # noqa: E402 — path manipulation above
from calibration import BridgeCalibrator  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_load_specs(specs: list[str]) -> list[tuple[int, float]]:
    """Parse ``["30:100", "32:100"]`` into ``[(30, 100.0), (32, 100.0)]``."""
    result = []
    for spec in specs:
        parts = spec.split(":")
        if len(parts) != 2:
            raise argparse.ArgumentTypeError(
                f"Load spec must be 'node:load_n', got: {spec!r}"
            )
        try:
            node = int(parts[0])
            load_n = float(parts[1])
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"Invalid load spec {spec!r}: {exc}"
            ) from exc
        if load_n <= 0.0:
            raise argparse.ArgumentTypeError(
                f"Load must be positive, got {load_n} in spec {spec!r}"
            )
        result.append((node, load_n))
    return result


def _wait_for_real_state(model: BridgeModel, timeout: float) -> dict | None:
    """Block until ``model.latest_real_state`` is populated or *timeout* seconds pass."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if model.latest_real_state:
            return model.latest_real_state
        time.sleep(0.25)
    return None


# ---------------------------------------------------------------------------
# Main calibration routine
# ---------------------------------------------------------------------------

def run_calibration(args: argparse.Namespace) -> int:
    load_specs = _parse_load_specs(args.loads)

    print("=" * 60)
    print("  Bridge model one-shot calibration")
    print("=" * 60)
    print(f"  Load positions : {', '.join(f'node {n} @ {l} N' for n, l in load_specs)}")
    print(f"  Method         : {args.method}")
    print(f"  Max iterations : {args.max_iter}")
    print(f"  Output file    : {Path(args.output).resolve()}")
    print("=" * 60)
    print()

    print("Starting bridge model (MQTT enabled) …")
    model = BridgeModel()
    calibrator = BridgeCalibrator(model)

    if model.model_warnings:
        for warning in model.model_warnings:
            print(f"  [warning] {warning}")

    print(f"  Model ready.  Gauge count: {len(calibrator._get_gauge_definitions())}")
    print()

    if not calibrator._get_gauge_definitions():
        print("ERROR: No strain gauges found in bridge JSON.")
        print("       Expected a 'strain_gauges' list with 'gauge_id'/'ele_id' entries.")
        model.close()
        return 1

    if not model.mqtt._connected:
        print("WARNING: MQTT not connected — cannot receive real/state readings.")
        print("         Set MQTT_BROKER_HOST (and MQTT_BROKER_PORT) environment variables.")
        print()

    # ------------------------------------------------------------------
    # Collect one measurement per load position
    # ------------------------------------------------------------------
    for idx, (node, load_n) in enumerate(load_specs, start=1):
        if node not in model.node_coords:
            print(f"  [skip] Node {node} does not exist in the model.")
            continue

        print(f"[{idx}/{len(load_specs)}]  Apply {load_n:.1f} N at bridge node {node}.")
        input("         Press Enter when load is applied and readings are stable … ")

        # Clear any stale reading so we get a fresh one after the load
        model.latest_real_state = None
        state = _wait_for_real_state(model, timeout=args.timeout)

        if state is None:
            print(
                f"  [skip] No real/state received within {args.timeout:.0f} s. "
                "Is the sensor publisher running?"
            )
            continue

        try:
            calibrator.add_measurement({node: load_n}, state)
        except ValueError as exc:
            print(f"  [skip] Could not parse readings: {exc}")
            continue

        print(f"  Measurement {idx} recorded.\n")

    if not calibrator.measurements:
        print("ERROR: No measurements were collected. Aborting.")
        model.close()
        return 1

    # ------------------------------------------------------------------
    # Run optimiser
    # ------------------------------------------------------------------
    n = len(calibrator.measurements)
    print(f"Running optimiser over {n} measurement(s) …  (this may take a moment)")

    try:
        result = calibrator.run(method=args.method, max_iter=args.max_iter)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Calibration failed: {exc}")
        model.close()
        return 1

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------
    print()
    print("Calibration result")
    print("-" * 40)
    print(f"  E_scale       = {result.E_scale:.6f}")
    print(f"  angle_A_scale = {result.angle_A_scale:.6f}")
    print(f"  flat_A_scale  = {result.flat_A_scale:.6f}")
    print(f"  NRMSE         = {result.nrmse:.6f}")
    print(f"  Iterations    = {result.iterations}")
    print(f"  Converged     = {result.success}")
    print("-" * 40)

    if result.nrmse > 0.20:
        print(
            "WARNING: NRMSE is high (> 20 %). "
            "Consider more load positions, checking sensor wiring, or using "
            "--method differential_evolution for a global search."
        )

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------
    out = Path(args.output)
    # Default: save alongside the bridge JSON (auto-loaded by BridgeModel)
    if args.output == "calibration.json":
        out = model._calibration_path

    calibrator.save(out, result)
    print(f"\nSaved to: {out.resolve()}")
    print("Restart bridge_model.py — it will load this calibration automatically.")

    model.close()
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "One-shot bridge model calibration. "
            "Run once; results are auto-loaded by bridge_model.py on startup."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--loads",
        nargs="+",
        default=["30:100", "32:100", "34:100"],
        metavar="NODE:LOAD_N",
        help=(
            "Load positions as 'node:load_n' pairs (N). "
            "Use at least 2 positions for reliable calibration. "
            "Default: %(default)s"
        ),
    )
    parser.add_argument(
        "--output",
        default="calibration.json",
        help=(
            "Output path. Default 'calibration.json' saves alongside the bridge JSON "
            "and is auto-loaded by BridgeModel on startup."
        ),
    )
    parser.add_argument(
        "--method",
        default="L-BFGS-B",
        choices=["L-BFGS-B", "differential_evolution", "Nelder-Mead"],
        help=(
            "Optimisation method. L-BFGS-B is fast; "
            "differential_evolution is slower but finds global minima. "
            "Default: %(default)s"
        ),
    )
    parser.add_argument(
        "--max-iter",
        type=int,
        default=300,
        dest="max_iter",
        help="Maximum optimiser iterations. Default: %(default)s",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for an MQTT real/state reading per step. Default: %(default)s",
    )

    args = parser.parse_args()
    sys.exit(run_calibration(args))


if __name__ == "__main__":
    main()
