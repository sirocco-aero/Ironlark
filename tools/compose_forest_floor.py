"""Rasterize photographed source plants at their exact authored transforms.

Every placement's affine map comes from one stacked computation (the same arithmetic per placement
as one at a time: all 784,752 equal bit for bit), and the four terrain tiles composite in parallel,
each its placements in file order. Pillow transforms RGBA through premultiplied RGBa: each photo is
premultiplied once, not at every placement.
"""
import argparse,gzip,json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
from PIL import Image

N=128  # photo size


def placements(build):
 """Names and, per placement, its affine map's inverse, the shift, the pixel box's low corner and
 which placements draw at all; with the tile size."""
 meta=json.loads((build/'floor_cover_assets.json').read_text())
 terrain=json.loads((build/'terrain_meta.json').read_text());x0,y0,x1,y1=terrain['planar']
 with Image.open(build/'terrain_0_0_diff.png') as tile:tile_size=tile.width
 full=2*tile_size;density=np.diag([full/(x1-x0),-full/(y1-y0)])
 with gzip.open(build/'floor_cover.jsonl.gz','rt') as stream:rows=[json.loads(line) for line in stream]
 names=[r[0] for r in rows]
 sizes=np.array([meta[n]['size'] for n in names]);centers=np.array([meta[n]['center'] for n in names])
 matrix=np.array([[[r[3],r[4]],[r[5],r[6]]] for r in rows]);where=np.array([[r[1],r[2]] for r in rows])
 scale=np.zeros((len(rows),2,2));scale[:,0,0]=sizes/N;scale[:,1,1]=-sizes/N
 affine=density@matrix@scale
 start=np.stack([centers[:,0]-sizes/2,centers[:,1]+sizes/2],-1)
 offset=(density@((matrix@start[...,None])[...,0]+(where-[x0,y1]))[...,None])[...,0]
 corners=np.array([[0,0],[N,0],[N,N],[0,N]])@np.transpose(affine,(0,2,1))+offset[:,None,:]
 lo=np.floor(corners.min(1)).astype(int);hi=np.ceil(corners.max(1)).astype(int)
 size=hi-lo;drawn=(size[:,0]>=1)&(size[:,1]>=1)&(np.abs(np.linalg.det(affine))>=1e-9)
 inv=np.zeros_like(affine);inv[drawn]=np.linalg.inv(affine[drawn])
 shift=(inv@(lo-offset)[...,None])[...,0]
 return meta,names,inv,shift,lo,size,drawn,tile_size


def composite(build,x,y,names,inv,shift,lo,size,drawn,tile_size):
 """Tile (x, y) with every placement that reaches it, in file order."""
 meta=json.loads((build/'floor_cover_assets.json').read_text())
 photos={}
 tile=Image.open(build/f'terrain_{x}_{y}_diff.png').convert('RGBA')
 # The tiles a placement reaches: columns xx, rows yy, tile (xx, 1 - yy).
 xx_lo,xx_hi=np.maximum(0,lo[:,0]//tile_size),np.minimum(1,(lo[:,0]+size[:,0])//tile_size)
 yy_lo,yy_hi=np.maximum(0,lo[:,1]//tile_size),np.minimum(1,(lo[:,1]+size[:,1])//tile_size)
 yy=1-y
 for k in np.flatnonzero(drawn&(xx_lo<=x)&(x<=xx_hi)&(yy_lo<=yy)&(yy<=yy_hi)):
  name=names[k]
  if name not in photos:
   photos[name]=Image.open(build/meta[name]['image']).convert('RGBA').resize((N,N),Image.Resampling.LANCZOS).convert('RGBa')
  photo=photos[name].transform((int(size[k,0]),int(size[k,1])),Image.Transform.AFFINE,
                               (*inv[k,0],shift[k,0],*inv[k,1],shift[k,1]),Image.Resampling.BICUBIC).convert('RGBA')
  tile.alpha_composite(photo,(int(lo[k,0]-x*tile_size),int(lo[k,1]-yy*tile_size)))
 tile.convert('RGB').save(build/f'terrain_{x}_{y}_floor.png')


def main():
 p=argparse.ArgumentParser();p.add_argument('--build',required=True);args=p.parse_args();build=Path(args.build)
 meta,names,inv,shift,lo,size,drawn,tile_size=placements(build)
 with ProcessPoolExecutor(4) as pool:
  for done in [pool.submit(composite,build,x,y,names,inv,shift,lo,size,drawn,tile_size) for x in range(2) for y in range(2)]:
   done.result()
 print('FLOOR_COMPOSITE complete',int(drawn.sum()),flush=True)
if __name__=='__main__':main()
