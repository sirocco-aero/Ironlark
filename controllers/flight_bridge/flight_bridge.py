"""Connect the Iris to the pinned upstream ArduPilot dynamics bridge."""

from pathlib import Path
import sys
import time

root = Path(__file__).resolve().parents[2]
upstream = root / ".cache/Webots_Python/controllers/ardupilot_vehicle_controller"
if not upstream.is_dir():
    raise SystemExit("Missing ArduPilot assets. Run ./loiter setup first.")
sys.path.insert(0, str(upstream))

from webots_vehicle import WebotsArduVehicle


class IrisBridge(WebotsArduVehicle):
    def _handle_controls(self, command):
        # SITL encodes disabled PWM outputs as -1. The upstream generic bridge
        # treats that value as reverse thrust. Iris has unidirectional motors:
        # disabled outputs must stop them, including before arming and at landing.
        super()._handle_controls(tuple(max(0.0, value) for value in command))


vehicle = IrisBridge(motor_names=["m1_motor", "m2_motor", "m3_motor", "m4_motor"])
while vehicle.webots_connected():
    time.sleep(0.1)
