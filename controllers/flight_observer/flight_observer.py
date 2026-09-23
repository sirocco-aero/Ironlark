"""Record evaluation truth and render the player's map; never control flight."""

import json
import os
from pathlib import Path

from controller import Supervisor


robot = Supervisor()
drone = robot.getFromDef("LOITER_DRONE")
destination = os.environ.get("LOITER_RUN_DIR")
if destination is None:
    raise SystemExit("Start this world through ./loiter so its evaluation output has a destination.")

scene_path = os.environ.get("LOITER_SCENE")
world_view = None
if scene_path:
    from minimap import WorldView
    world_view = WorldView(robot, scene_path, destination)

# Controllers start once the world has loaded; the launcher waits for this.
(Path(destination) / "world-ready").touch()

with (Path(destination) / "ground_truth.jsonl").open("w", buffering=1) as stream:
    while robot.step(100) != -1:
        position = drone.getPosition()
        stream.write(json.dumps({"time": robot.getTime(), "position": position,
                                 "velocity": drone.getVelocity()}) + "\n")
        if world_view:
            world_view.update(position, drone.getOrientation())
