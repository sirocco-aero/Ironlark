"""Bake one terrain material tile per Blender process, bounding image memory."""
import argparse,json,sys
from pathlib import Path
import bpy
import numpy as np
sys.path.insert(0,str(Path(__file__).parent))
import export_forest as base

p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--tile',type=int,nargs=2,required=True)
a=p.parse_args(sys.argv[sys.argv.index('--')+1:]);out=Path(a.out);tx,ty=a.tile
# Append only the terrain dependency tree, then drop unused scatter nodes.
# Opening the entire .blend retains every source scan even for a terrain bake.
source = out.resolve().parent/'pine-forest/source/polyhaven_pine_fir_forest.blend'
bpy.ops.wm.read_factory_settings(use_empty=True)
with bpy.data.libraries.load(str(source),link=False) as (available, loaded):
 loaded.objects=['terrain_main']
obj=loaded.objects[0];bpy.context.scene.collection.objects.link(obj)
g=bpy.data.node_groups['terrain']
g.links.new(g.nodes['landscape_subdivide'].outputs['Geometry'],g.nodes['Group Output'].inputs['Geometry'])
def prune(group, seen=None):
 seen=set() if seen is None else seen
 if group.name in seen:return
 seen.add(group.name);needed=set()
 def walk(n):
  if n in needed:return
  needed.add(n)
  for inp in n.inputs:
   for link in inp.links:walk(link.from_node)
 for n in group.nodes:
  if n.type=='GROUP_OUTPUT':walk(n)
 for n in list(group.nodes):
  if n not in needed:group.nodes.remove(n)
 for n in group.nodes:
  if n.type=='GROUP':prune(n.node_tree,seen)
prune(g)
bpy.data.orphans_purge(do_local_ids=True,do_linked_ids=True,do_recursive=True)
print('TERRAIN_ONLY',len(bpy.data.objects),'objects',len(bpy.data.meshes),'meshes',flush=True)
# Redirect image paths BEFORE decoding; image.scale() retains large decoded
# source buffers in Blender and can exceed RAM even with an isolated scene.
for img in bpy.data.images:
 small=out/'texture-source-2k'/Path(img.filepath).name
 if small.exists():
  img.buffers_free();img.filepath=str(small.resolve())
obj=bpy.data.objects['terrain_main']
# The terrain group joins millions of scatter instances to this surface.
# Request ONLY its landscape output before dependency-graph evaluation.
g=bpy.data.node_groups['terrain'];g.links.new(g.nodes['landscape_subdivide'].outputs['Geometry'],g.nodes['Group Output'].inputs['Geometry'])
scene=bpy.data.scenes.new('Ironlark terrain only');scene.collection.objects.link(obj);bpy.context.window.scene=scene
bpy.context.view_layer.update();dg=bpy.context.evaluated_depsgraph_get();ev=obj.evaluated_get(dg);m=bpy.data.meshes.new_from_object(ev,preserve_all_data_layers=True,depsgraph=dg);m.calc_loop_triangles()
world=base.mesh_world_verts(obj,m);lv=base.loop_vert_indices(m);uv=base.loop_uvs(m)
# Generated coordinates normally depend on the evaluated object's full bounds.
# Cropping it into bake tiles must not change material scale or create seams.
lo=world.min(axis=0);span=world.max(axis=0)-lo
seen=set()
def preserve_coordinates(group):
 if group.name in seen:return
 seen.add(group.name)
 for node in list(group.nodes):
  if node.type=='GROUP':preserve_coordinates(node.node_tree)
  if node.type=='TEX_COORD' and node.outputs['Generated'].links:
   position=group.nodes.new('ShaderNodeNewGeometry')
   sub=group.nodes.new('ShaderNodeVectorMath');sub.operation='SUBTRACT';sub.inputs[1].default_value=lo.tolist()
   div=group.nodes.new('ShaderNodeVectorMath');div.operation='DIVIDE';div.inputs[1].default_value=span.tolist()
   group.links.new(position.outputs['Position'],sub.inputs[0]);group.links.new(sub.outputs[0],div.inputs[0])
   for link in list(node.outputs['Generated'].links):group.links.new(div.outputs[0],link.to_socket)
preserve_coordinates(bpy.data.materials['main_terrain'].node_tree)
meta=json.loads((out/'terrain_meta.json').read_text());x0,y0,x1,y1=meta['planar']
uv[:,0]=(world[lv,0]-x0)/(x1-x0)*2-tx;uv[:,1]=(world[lv,1]-y0)/(y1-y0)*2-ty
keep=[list(t.loops) for t in m.loop_triangles if all(uv[list(t.loops)].max(axis=0)>0) and all(uv[list(t.loops)].min(axis=0)<1)]
base.HOME=(*base.HOME[:2],json.loads((out/'manifest.json').read_text())['home_ground_z'])
# Where the source's ground layers tile from (its Generated coordinates: the original terrain's
# bounds), for the renderer's terrain detail layers.
(out/'terrain_generated.json').write_text(json.dumps({'low':lo.tolist(),'span':span.tolist()}))
for kind,size in [('diff',4096),('normal',2048),('mask',1024)]:
 if (out/f'terrain_{tx}_{ty}_{kind}.png').exists():continue
 base.BAKE_SIZE=size
 old=bpy.data.images.get('ironlark_terrain_bake')
 if old:bpy.data.images.remove(old)
 base.bake_terrain(obj,m,keep,lv,world,uv,kind={'normal':'NORMAL','mask':'MASK'}.get(kind,'DIFFUSE'))
 img=bpy.data.images['ironlark_terrain_bake'];img.filepath_raw=str(out/f'terrain_{tx}_{ty}_{kind}.png');img.file_format='PNG'
 img.save();print('TERRAIN_TILE',tx,ty,kind,flush=True)
bpy.data.meshes.remove(m)
