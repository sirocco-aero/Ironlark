"""Bake static canopy light through the actual exported alpha foliage.

Webots' stencil shadow volumes do not follow texture alpha. A ray-traced
static light map preserves the twig silhouettes without rectangular shadows.
The moving drone and opaque props can still cast runtime shadows.
"""
import argparse,json,math,sys
from pathlib import Path
import bpy
import numpy as np
from mathutils import Matrix,Quaternion,Vector

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

def instancer():
 """One Geometry Nodes tree instancing a prototype on points: Cycles then holds
 each mesh once and each placement as an instance, not as a separate object."""
 g=bpy.data.node_groups.new('instancer','GeometryNodeTree')
 g.interface.new_socket('Geometry',in_out='INPUT',socket_type='NodeSocketGeometry')
 g.interface.new_socket('Prototype',in_out='INPUT',socket_type='NodeSocketObject')
 g.interface.new_socket('Geometry',in_out='OUTPUT',socket_type='NodeSocketGeometry')
 ns,ls=g.nodes,g.links
 gi,go=ns.new('NodeGroupInput'),ns.new('NodeGroupOutput')
 info=ns.new('GeometryNodeObjectInfo');info.transform_space='ORIGINAL';info.inputs['As Instance'].default_value=True
 place=ns.new('GeometryNodeInstanceOnPoints')
 rot=ns.new('GeometryNodeInputNamedAttribute');rot.data_type='QUATERNION';rot.inputs['Name'].default_value='rot'
 scl=ns.new('GeometryNodeInputNamedAttribute');scl.data_type='FLOAT_VECTOR';scl.inputs['Name'].default_value='scl'
 ls.new(gi.outputs['Geometry'],place.inputs['Points']);ls.new(gi.outputs['Prototype'],info.inputs['Object'])
 ls.new(info.outputs['Geometry'],place.inputs['Instance']);ls.new(rot.outputs['Attribute'],place.inputs['Rotation'])
 ls.new(scl.outputs['Attribute'],place.inputs['Scale']);ls.new(place.outputs['Instances'],go.inputs['Geometry'])
 return g

records=json.loads((world/'visual_instances.json').read_text())
groups={}
for r in records:groups.setdefault((r['mesh'],r['texture']),[]).append(r)
tree=instancer();prototype_socket=tree.interface.items_tree['Prototype'].identifier
for (url,texture),placed in groups.items():
 prototype=bpy.data.objects.new('prototype',mesh(url,texture))  # referenced, never linked to the scene
 points=bpy.data.meshes.new('placements');points.vertices.add(len(placed))
 points.vertices.foreach_set('co',[c for r in placed for c in r['translation']])
 quats=[Quaternion(r['rotation'][:3],r['rotation'][3]) for r in placed]
 points.attributes.new('rot','QUATERNION','POINT').data.foreach_set('value',[c for q in quats for c in q])
 points.attributes.new('scl','FLOAT_VECTOR','POINT').data.foreach_set('vector',[c for r in placed for c in r['scale']])
 ob=bpy.data.objects.new('instances',points);scene.collection.objects.link(ob)
 modifier=ob.modifiers.new('instancer','NODES');modifier.node_group=tree;modifier[prototype_socket]=prototype
print('LIGHT_INSTANCES',len(records),'in',len(groups),'instancers',flush=True)
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
