"""Photograph the source's small floor plants and retain their exact scatter.

Low-growing moss and needle litter contribute to tiled ground materials. The
original 3D cover meshes remain in the world as well. No painted blob textures
or random replacement placements are used.
"""
import argparse,gzip,json,sys
from pathlib import Path
import bpy
import numpy as np
from mathutils import Vector
sys.path.insert(0,str(Path(__file__).parent))
from export_forest import REGION,HOME,CLEAR_RADIUS
from forest_materials import asset_textures


def prune(group,seen=None):
 seen=set() if seen is None else seen
 if group.name in seen:return
 seen.add(group.name);needed=set()
 def walk(node):
  if node in needed:return
  needed.add(node)
  for inp in node.inputs:
   for link in inp.links:walk(link.from_node)
 for node in group.nodes:
  if node.type=='GROUP_OUTPUT':walk(node)
 for node in list(group.nodes):
  if node not in needed:group.nodes.remove(node)
 for node in group.nodes:
  if node.type=='GROUP':prune(node.node_tree,seen)


def main():
 p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args(sys.argv[sys.argv.index('--')+1:]);out=Path(a.out).resolve()
 source=out.parent/'pine-forest/source/polyhaven_pine_fir_forest.blend'
 bpy.ops.wm.read_factory_settings(use_empty=True)
 with bpy.data.libraries.load(str(source),link=False) as (_,loaded):loaded.objects=['terrain_main']
 terrain=loaded.objects[0];bpy.context.scene.collection.objects.link(terrain)
 g=bpy.data.node_groups['terrain'];join=g.nodes['Join Geometry']
 for link in list(join.inputs[0].links):
  if link.from_node.name not in ('Group.004','Group.001','landscape_subdivide'):g.links.remove(link)
 prune(g)
 for group in bpy.data.node_groups:
  for n in list(group.nodes):
   if n.bl_idname=='GeometryNodeIsViewport':
    value=group.nodes.new('FunctionNodeInputBool');value.boolean=False
    for link in list(n.outputs[0].links):group.links.new(value.outputs[0],link.to_socket)
    group.nodes.remove(n)
 bpy.data.orphans_purge(do_local_ids=True,do_linked_ids=True,do_recursive=True)
 for img in bpy.data.images:
  small=out/'texture-source-2k'/Path(img.filepath).name
  if small.exists():img.buffers_free();img.filepath=str(small)
 bpy.context.view_layer.update();dg=bpy.context.evaluated_depsgraph_get()
 count=0;names=set();xmin,xmax,ymin,ymax=REGION
 with gzip.open(out/'floor_cover.jsonl.gz','wt') as stream:
  for inst in dg.object_instances:
   if not inst.is_instance or inst.instance_object is None:continue
   obj=inst.instance_object;name=obj.name
   if not (name.startswith('moss_patch') or name.startswith('pine_cover')):continue
   pos=inst.matrix_world.translation
   if not (xmin<=pos.x<=xmax and ymin<=pos.y<=ymax):continue
   if (pos.x-HOME[0])**2+(pos.y-HOME[1])**2<CLEAR_RADIUS**2:continue
   mat=inst.matrix_world
   record=[name,round(pos.x,4),round(pos.y,4),*[round(float(mat[i][j]),5) for i in range(2) for j in range(2)]]
   stream.write(json.dumps(record,separators=(',',':'))+'\n');names.add(name);count+=1
 print('FLOOR_SCATTER',count,sorted(names),flush=True)
 # Copy only the necessary evaluated assets; subsequent renders contain one
 # small plant at a time, never the scatter or the original forest.
 meshes={name:bpy.data.meshes.new_from_object(bpy.data.objects[name].evaluated_get(dg),preserve_all_data_layers=True,depsgraph=dg) for name in names}
 textures={name:asset_textures(bpy.data.objects[name]) for name in names}
 scene=bpy.data.scenes.new('Loiter floor photographs');bpy.context.window.scene=scene
 scene.render.engine='CYCLES';scene.cycles.samples=8;scene.cycles.device='CPU';scene.cycles.transparent_max_bounces=16
 scene.render.film_transparent=True;scene.render.resolution_x=512;scene.render.resolution_y=512
 scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGBA'
 scene.view_settings.view_transform='Standard';scene.view_settings.look='None'
 camera=bpy.data.objects.new('camera',bpy.data.cameras.new('camera'));scene.collection.objects.link(camera);camera.data.type='ORTHO';scene.camera=camera
 meta={}
 for name,mesh in meshes.items():
  obj=bpy.data.objects.new('floor asset',mesh);scene.collection.objects.link(obj)
  xyz=np.array([v.co[:] for v in mesh.vertices]);lo,hi=xyz.min(0),xyz.max(0);center=(lo+hi)/2
  size=float(max(hi[0]-lo[0],hi[1]-lo[1])*1.04)
  camera.location=(float(center[0]),float(center[1]),float(hi[2]+3));camera.rotation_euler=(0,0,0);camera.data.ortho_scale=size
  tex=next(iter(textures[name].values()));mat=bpy.data.materials.new('floor photo');mat.use_nodes=True
  ns,ls=mat.node_tree.nodes,mat.node_tree.links;ns.clear()
  output=ns.new('ShaderNodeOutputMaterial');emit=ns.new('ShaderNodeEmission');transparent=ns.new('ShaderNodeBsdfTransparent');mix=ns.new('ShaderNodeMixShader')
  color=ns.new('ShaderNodeTexImage');color.image=bpy.data.images.load(str(out/'texture-source-2k'/tex['diff']),check_existing=True);ls.new(color.outputs['Color'],emit.inputs[0])
  if tex.get('alpha'):
   alpha=ns.new('ShaderNodeTexImage');alpha.image=bpy.data.images.load(str(out/'texture-source-2k'/tex['alpha']),check_existing=True)
   alpha.image.colorspace_settings.name='Non-Color'
   ls.new(alpha.outputs['Color'],mix.inputs[0])
  else:mix.inputs[0].default_value=1
  ls.new(emit.outputs[0],mix.inputs[1 if tex.get('invert_alpha') else 2]);ls.new(transparent.outputs[0],mix.inputs[2 if tex.get('invert_alpha') else 1]);ls.new(mix.outputs[0],output.inputs['Surface'])
  mesh.materials.clear();mesh.materials.append(mat)
  for poly in mesh.polygons:poly.material_index=0
  scene.render.filepath=str(out/f'floor_{name}.png');bpy.ops.render.render(write_still=True)
  meta[name]={'image':f'floor_{name}.png','center':center[:2].tolist(),'size':size}
  bpy.data.objects.remove(obj,do_unlink=True)
  print('FLOOR_PHOTO',name,flush=True)
 (out/'floor_cover_assets.json').write_text(json.dumps(meta,indent=1))
if __name__=='__main__':main()
