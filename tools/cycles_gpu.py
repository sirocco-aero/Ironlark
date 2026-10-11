"""Cycles on a GPU when Blender finds one (OptiX, then CUDA or HIP), for the forest's bakes; else the CPU, said so.

The same estimator either way: results differ by sampling noise (the canopy light: means equal to 4 decimals), or by
float rounding where one sample is taken (trunk materials: within 1/255, 2/255 on 0.03% of normal texels).
"""
import bpy


def use_gpu(scene, label):
    """Point scene's Cycles at the first GPU backend Blender finds, and print which device renders."""
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for backend in ("OPTIX", "CUDA", "HIP"):
        try:
            prefs.compute_device_type = backend
        except TypeError:  # a backend this Blender was built without
            continue
        prefs.get_devices()
        if any(device.type == backend for device in prefs.devices):
            for device in prefs.devices:
                device.use = device.type == backend
            scene.cycles.device = "GPU"
            break
    else:
        scene.cycles.device = "CPU"
    print(label, "device", scene.cycles.device, flush=True)
