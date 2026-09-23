"""Inspect the source scene without evaluating its geometry nodes."""
import bpy,json
from pathlib import Path
scene=bpy.context.scene
print('SCENES', [(s.name,len(s.objects))for s in bpy.data.scenes],flush=True)
print('TEXTS',[(t.name,t.as_string()[:6000])for t in bpy.data.texts],flush=True)
rows=[]
for o in bpy.data.objects:
 row={'name':o.name,'type':o.type,'location':list(o.location),'dimensions':list(o.dimensions),'hide':o.hide_render,'collections':[c.name for c in o.users_collection],'materials':[m.name if m else None for m in o.data.materials]if hasattr(o.data,'materials')else [],'polys':len(o.data.polygons)if o.type=='MESH'else 0,'mods':[]}
 for m in o.modifiers:
  row['mods'].append({'name':m.name,'type':m.type,'show_viewport':m.show_viewport,'show_render':m.show_render,'properties':dict(m.items()) if m.type=='NODES'else {}})
 rows.append(row)
Path('/tmp/loiter-blender-objects.json').write_text(json.dumps(rows,indent=2,default=str))
print('OBJECTS',len(rows),flush=True)
for r in rows:
 if r['mods']or r['polys']>100000:print(json.dumps(r,default=str),flush=True)
print('COLLECTIONS',[(c.name,len(c.objects))for c in bpy.data.collections],flush=True)
