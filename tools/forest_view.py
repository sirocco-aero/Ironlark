"""Where the forest world sits in the source scene, and its preview cameras."""

# Source (Blender) position of the world origin: the drone's home pad.
ORIGIN = (37.1, -80.0)

# Preview cameras in world (ENU) coordinates.
CAMERAS = {
    "drone": {"eye": [4, -5, 2.6], "target": [0, 0, 0.8]},
    "overview": {"eye": [-45, 100, 55], "target": [-45, 40, 0]},
    # The source's Camera1 position and heading.
    "forest": {"eye": [-13.944526, 32.4402, 0.922303], "target": [-23.765966, 33.8368, 2.18279]},
    # Facing the main sun from under the canopy, as the source's hero render does.
    "backlit": {"eye": [-13.944526, 32.4402, 1.5], "target": [-16.672, 40.58, 4.5]},
}
