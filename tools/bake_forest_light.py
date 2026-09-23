"""Bake static canopy light through the actual exported alpha foliage.

Webots' stencil shadow volumes do not follow texture alpha. A ray-traced
static light map preserves the twig silhouettes without rectangular shadows.
The moving drone and opaque props can still cast runtime shadows.
"""
import argparse,json,math,sys
from pathlib import Path
import bpy
import numpy as np
from mathutils import Matrix,Vector

p=argparse.ArgumentParser();p.add_argument('--world',required=True);p.add_argument('--out',required=True)
a=p.parse_args(sys.argv[sys.argv.index('--')+1:]);world=Path(a.world).resolve();out=Path(a.out).resolve()
bpy.ops.wm.read_factory_settings(use_empty=True)
scene=bpy.context.scene;scene.render.engine='CYCLES';scene.cycles.samples=8;scene.cycles.device='CPU'
scene.cycles.transparent_max_bounces=16;scene.cycles.diffuse_bounces=0;scene.cycles.use_denoising=False
scene.world=bpy.data.worlds.new('ambient');scene.world.use_nodes=True;scene.world.node_tree.nodes['Background'].inputs[1].default_value=0
sun=bpy.data.objects.new('source sun',bpy.data.lights.new('source sun','SUN'));scene.collection.objects.link(sun);sun.data.energy=3.5;sun.data.angle=.035
sun.rotation_euler=Vector((.272788,-.814025,-.512786)).to_track_quat('-Z','Y').to_euler()
materials={};meshes={}

def material(texture):
 if texture in materials:return materials[texture]
 mat=bpy.data.materials.new(texture);mat.use_nodes=True;ns,ls=mat.node_tree.nodes,mat.node_tree.links;ns.clear()
 output=ns.new('ShaderNodeOutputMaterial');diffuse=ns.new('ShaderNodeBsdfDiffuse');diffuse.inputs['Color'].default_value=(1,1,1,1)
 alpha=False
 if texture:
  with (world/texture).open('rb') as f:header=f.read(26)
  alpha=header[25]==6
 if alpha:
  tex=ns.new('ShaderNodeTexImage');tex.image=bpy.data.images.load(str(world/texture),check_existing=True)
  mix=ns.new('ShaderNodeMixShader');transparent=ns.new('ShaderNodeBsdfTransparent')
  ls.new(tex.outputs['Alpha'],mix.inputs[0]);ls.new(transparent.outputs[0],mix.inputs[1]);ls.new(diffuse.outputs[0],mix.inputs[2]);ls.new(mix.outputs[0],output.inputs[0])
 else:ls.new(diffuse.outputs[0],output.inputs[0])
 materials[texture]=mat;return mat

def mesh(url,texture):
 key=(url,texture)
 if key in meshes:return meshes[key]
 bpy.ops.wm.obj_import(filepath=str(world/url),forward_axis='Y',up_axis='Z')
 obj=bpy.context.selected_objects[0];data=obj.data;data.transform(obj.matrix_world);data.materials.clear();data.materials.append(material(texture))
 for face in data.polygons:face.material_index=0
 bpy.data.objects.remove(obj,do_unlink=True);meshes[key]=data
 return data

records=json.loads((world/'visual_instances.json').read_text())
for i,r in enumerate(records):
 ob=bpy.data.objects.new('instance',mesh(r['mesh'],r['texture']));scene.collection.objects.link(ob)
 ob.location=r['translation'];ob.rotation_mode='AXIS_ANGLE';axis=r['rotation'];ob.rotation_axis_angle=(axis[3],*axis[:3]);ob.scale=r['scale']
 if i%3000==0:print('LIGHT_INSTANCES',i,flush=True)
terrain=[]
for x in range(2):
 for y in range(2):
  ob=bpy.data.objects.new(f'terrain_{x}_{y}',mesh(f'meshes/terrain_{x}_{y}.obj',''));scene.collection.objects.link(ob);terrain.append((x,y,ob))
# Allocate the target once and reuse it. Every other mesh is linked/shared.
img=bpy.data.images.new('canopy visibility',width=1024,height=1024,float_buffer=True);img.colorspace_settings.name='Non-Color'
mat=materials[''];target=mat.node_tree.nodes.new('ShaderNodeTexImage');target.image=img;mat.node_tree.nodes.active=target
scene.render.bake.margin=8;scene.render.bake.use_clear=True
for x,y,ob in terrain:
 for selected in bpy.context.selected_objects:selected.select_set(False)
 ob.select_set(True);bpy.context.view_layer.objects.active=ob
 bpy.ops.object.bake(type='DIFFUSE',pass_filter={'DIRECT'})
 pixels=np.empty(1024*1024*4,dtype=np.float32);img.pixels.foreach_get(pixels)
 pixels=pixels.reshape(1024,1024,4)[::-1,:,:3].copy()
 np.save(out/f'terrain_{x}_{y}_light.npy',pixels)
 print('LIGHT_TILE',x,y,'max',pixels.max(),'mean',pixels.mean(),flush=True)
