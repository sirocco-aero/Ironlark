"""Clip the shared terrain mesh into texture tiles without changing its surface."""
from pathlib import Path
import json
import shutil
import numpy as np
from PIL import Image


def clip_polygon(polygon, axis, bound, keep_greater):
    """Sutherland-Hodgman in UV space, interpolating position and normal."""
    result=[]
    for a,b in zip(polygon,polygon[1:]+polygon[:1]):
        da,db=a[6+axis]-bound,b[6+axis]-bound
        ina=da>=-1e-10 if keep_greater else da<=1e-10
        inb=db>=-1e-10 if keep_greater else db<=1e-10
        if ina:result.append(a)
        if ina!=inb:result.append(a+(b-a)*(da/(da-db)))
    return result


def write_tile(path, faces):
    with path.open('w') as f:
        f.write('# Source terrain, clipped along material tile boundaries; ENU.\n')
        for face in faces:
            for p in face:f.write('v %.6f %.6f %.6f\n'%(p[0],-p[2],p[1]))
        for face in faces:
            for p in face:f.write('vt %.7f %.7f\n'%(p[6],p[7]))
        for face in faces:
            for p in face:f.write('vn %.6f %.6f %.6f\n'%(p[3],-p[5],p[4]))
        for i in range(len(faces)):
            f.write('f '+' '.join(f'{j}/{j}/{j}' for j in range(i*3+1,i*3+4))+'\n')


def build_terrain_tiles(build,out):
    verts=[];uvs=[];norms=[];triangles=[]
    for line in (build/'terrain.obj').read_text().splitlines():
        parts=line.split()
        if not parts:continue
        if parts[0]=='v':verts.append(list(map(float,parts[1:])))
        elif parts[0]=='vt':uvs.append(list(map(float,parts[1:])))
        elif parts[0]=='vn':norms.append(list(map(float,parts[1:])))
        elif parts[0]=='f':triangles.append([tuple(int(i)-1 for i in p.split('/')) for p in parts[1:]])
    tiles={(x,y):[] for x in range(2) for y in range(2)}
    for tri in triangles:
        points=[np.array(verts[v]+norms[n]+uvs[t]) for v,t,n in tri]
        uv=np.array([p[6:] for p in points])
        for (x,y),faces in tiles.items():
            if (uv[:,0].max()<x/2 or uv[:,0].min()>(x+1)/2 or uv[:,1].max()<y/2 or uv[:,1].min()>(y+1)/2):continue
            polygon=points
            for axis,bound,greater in [(0,x/2,True),(0,(x+1)/2,False),(1,y/2,True),(1,(y+1)/2,False)]:
                polygon=clip_polygon(polygon,axis,bound,greater)
                if not polygon:break
            polygon=[p.copy() for p in polygon]
            for p in polygon:p[6:]=p[6:]*2-[x,y]
            for i in range(1,len(polygon)-1):
                face=[polygon[0],polygon[i],polygon[i+1]]
                if np.linalg.norm(np.cross(face[1][:3]-face[0][:3],face[2][:3]-face[0][:3]))>1e-12:faces.append(face)
    nodes=[];backdrop=[]
    for (x,y),faces in tiles.items():
        prefix=f'terrain_{x}_{y}'
        write_tile(out/'meshes'/f'{prefix}.obj',faces)
        albedo=build/f'{prefix}_floor.png'
        if not albedo.exists():albedo=build/f'{prefix}_diff.png'
        # Unlit albedo: the sun's shadow map shades the ground at runtime.
        shutil.copyfile(albedo,out/'meshes'/f'{prefix}_diff.png')
        shutil.copyfile(build/f'{prefix}_normal.png',out/'meshes'/f'{prefix}_normal.png')
        appearance=f'''appearance PBRAppearance {{
              baseColorMap ImageTexture {{ url "meshes/{prefix}_diff.png" repeatS FALSE repeatT FALSE }}
              normalMap ImageTexture {{ url "meshes/{prefix}_normal.png" repeatS FALSE repeatT FALSE }}
              roughness 0.95 metalness 0
            }}
            geometry Mesh {{ url "meshes/{prefix}.obj" }}'''
        nodes.append(f'''Shape {{
            {appearance}
            castShadows TRUE
        }}''')
        # The same tile, mirrored into every backdrop tile (rows written with the river's).
        backdrop.append(f'''Shape {{
            {appearance}
            instancesUrl [ "meshes/instances_backdrop.bin" ]
        }}''')
    return '\n'.join(nodes),backdrop
