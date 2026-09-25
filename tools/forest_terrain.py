"""Clip the shared terrain mesh into texture tiles without changing its surface."""
from pathlib import Path
import json
import shutil
import numpy as np
from PIL import Image


def clip_polygon(polygon, axis, bound, keep_greater, offset=6):
    """Sutherland-Hodgman on component offset+axis (default: UV space), interpolating
    position, normal and UV."""
    result=[]
    for a,b in zip(polygon,polygon[1:]+polygon[:1]):
        da,db=a[offset+axis]-bound,b[offset+axis]-bound
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


def inner_rect(build):
    """Largest axis-aligned rectangle (world xmin, xmax, ymin, ymax) the terrain covers
    everywhere. The source terrain's edges are ragged by up to 3 m; the backdrop mirrors
    about this rectangle, so its seams close exactly."""
    verts=[];edges={}
    for line in (build/'terrain.obj').read_text().splitlines():
        parts=line.split()
        if not parts:continue
        if parts[0]=='v':verts.append((float(parts[1]),-float(parts[3])))  # legacy (x, y-up, z) -> world x, y
        elif parts[0]=='f':
            f=[int(p.split('/')[0])-1 for p in parts[1:]]
            for i in range(len(f)):
                e=tuple(sorted((f[i],f[(i+1)%len(f)])));edges[e]=edges.get(e,0)+1
    boundary=np.array([verts[v] for v in {v for e,n in edges.items() if n==1 for v in e}])
    lo,hi=boundary.min(0),boundary.max(0)
    # Each boundary vertex belongs to the side of the bounding box it is nearest.
    gaps=np.stack([boundary[:,0]-lo[0],hi[0]-boundary[:,0],boundary[:,1]-lo[1],hi[1]-boundary[:,1]],1)
    side=gaps.argmin(1)
    return (float(boundary[side==0,0].max()),float(boundary[side==1,0].min()),
            float(boundary[side==2,1].max()),float(boundary[side==3,1].min()))


def decimate_obj(src, dst, cell, keep=None):
    """Vertex clustering for far copies: vertices in each cell-sized grid square merge
    (position, UV and normal averaged); degenerate triangles go. keep(xy) marks vertices
    left as they are, so seams between copies stay closed."""
    positions, uvs, normals, faces = [], [], [], []
    for line in src.read_text().splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "v":
            positions.append(list(map(float, parts[1:4])))
        elif parts[0] == "vt":
            uvs.append(list(map(float, parts[1:3])))
        elif parts[0] == "vn":
            normals.append(list(map(float, parts[1:4])))
        elif parts[0] == "f":
            faces.append([tuple(int(i) - 1 for i in c.split("/")) for c in parts[1:]])
    corners = np.array([c for f in faces for c in f])  # (v, t, n) per corner
    p, uv, n = np.array(positions)[corners[:, 0]], np.array(uvs)[corners[:, 1]], np.array(normals)[corners[:, 2]]
    key = np.floor(p[:, :2] / cell).astype(np.int64)
    fixed = keep(p[:, :2]) if keep else np.zeros(len(p), bool)
    # Kept vertices are their own cluster: key them by their exact position.
    labels_input = np.where(fixed[:, None], np.round(p[:, :2] * 1000).astype(np.int64) + (1 << 40), key)
    _, label = np.unique(labels_input, axis=0, return_inverse=True)
    label = label.ravel()
    count = np.bincount(label).astype(float)[:, None]
    mean = lambda values: np.stack([np.bincount(label, values[:, k]) for k in range(values.shape[1])], 1) / count
    cp, cuv, cn = mean(p), mean(uv), mean(n)
    cn /= np.maximum(np.linalg.norm(cn, axis=1, keepdims=True), 1e-9)
    tri = label.reshape(-1, 3)
    tri = tri[(tri[:, 0] != tri[:, 1]) & (tri[:, 1] != tri[:, 2]) & (tri[:, 0] != tri[:, 2])]
    with dst.open("w") as f:
        f.write(f"# {src.name}, vertices clustered to {cell} m for far copies.\n")
        f.writelines(f"v {x:.3f} {y:.3f} {z:.3f}\n" for x, y, z in cp)
        f.writelines(f"vt {u:.6f} {v:.6f}\n" for u, v in cuv)
        f.writelines(f"vn {x:.4f} {y:.4f} {z:.4f}\n" for x, y, z in cn)
        f.writelines(f"f {a + 1}/{a + 1}/{a + 1} {b + 1}/{b + 1}/{b + 1} {c + 1}/{c + 1}/{c + 1}\n" for a, b, c in tri)
    return len(tri)


def build_terrain_tiles(build,out,rect):
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
            # To the backdrop's rectangle (legacy position: x, and z = -world y).
            for axis,bound,greater in [(0,rect[0],True),(0,rect[1],False),(2,-rect[2],False),(2,-rect[3],True)]:
                if not polygon:break
                polygon=clip_polygon(polygon,axis,bound,greater,offset=0)
            if not polygon:continue
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
        # The same tile, mirrored into every backdrop tile (rows written with the river's);
        # past the first ring, coarser, with its edges kept so the seams stay closed.
        backdrop.append(f'''Shape {{
            {appearance}
            instancesUrl [ "meshes/instances_backdrop.bin" ]
        }}''')
        points=np.array([p[:3] for f in faces for p in f])
        lo=np.array([points[:,0].min(),-points[:,2].max()]);hi=np.array([points[:,0].max(),-points[:,2].min()])
        edge=lambda xy:(np.abs(xy-lo)<0.01).any(1)|(np.abs(xy-hi)<0.01).any(1)
        decimate_obj(out/'meshes'/f'{prefix}.obj',out/'meshes'/f'{prefix}_far.obj',4.0,edge)
        backdrop.append(f'''Shape {{
            {appearance.replace(f'meshes/{prefix}.obj', f'meshes/{prefix}_far.obj')}
            instancesUrl [ "meshes/instances_backdrop_far.bin" ]
        }}''')
    return '\n'.join(nodes),backdrop
