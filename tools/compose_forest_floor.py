"""Rasterize photographed source plants at their exact authored transforms."""
import argparse,gzip,json,math
from pathlib import Path
import numpy as np
from PIL import Image


def main():
 p=argparse.ArgumentParser();p.add_argument('--build',required=True);args=p.parse_args();build=Path(args.build)
 meta=json.loads((build/'floor_cover_assets.json').read_text())
 images={name:Image.open(build/a['image']).convert('RGBA').resize((128,128),Image.Resampling.LANCZOS) for name,a in meta.items()}
 terrain=json.loads((build/'terrain_meta.json').read_text());x0,y0,x1,y1=terrain['planar']
 tiles={(x,y):Image.open(build/f'terrain_{x}_{y}_diff.png').convert('RGBA') for x in range(2) for y in range(2)}
 tile_size=tiles[0,0].width;full=2*tile_size
 density=np.diag([full/(x1-x0),-full/(y1-y0)])
 count=0
 with gzip.open(build/'floor_cover.jsonl.gz','rt') as stream:
  for line in stream:
   name,x,y,a,b,c,d=json.loads(line);asset=meta[name];matrix=np.array([[a,b],[c,d]])
   width=asset['size'];cx,cy=asset['center'];n=images[name].width
   affine=density@matrix@np.diag([width/n,-width/n])
   offset=density@(matrix@np.array([cx-width/2,cy+width/2])+[x-x0,y-y1])
   corners=np.array([[0,0],[n,0],[n,n],[0,n]])@affine.T+offset
   lo=np.floor(corners.min(0)).astype(int);hi=np.ceil(corners.max(0)).astype(int)
   w,h=hi-lo
   if w<1 or h<1 or abs(np.linalg.det(affine))<1e-9:continue
   inv=np.linalg.inv(affine);shift=inv@(lo-offset)
   photo=images[name].transform((int(w),int(h)),Image.Transform.AFFINE,(*inv[0],shift[0],*inv[1],shift[1]),Image.Resampling.BICUBIC)
   for xx in range(max(0,lo[0]//tile_size),min(1,hi[0]//tile_size)+1):
    for yy in range(max(0,lo[1]//tile_size),min(1,hi[1]//tile_size)+1):
     tiles[xx,1-yy].alpha_composite(photo,(int(lo[0]-xx*tile_size),int(lo[1]-yy*tile_size)))
   count+=1
   if count%100000==0:print('FLOOR_COMPOSITE',count,flush=True)
 for (x,y),img in tiles.items():img.convert('RGB').save(build/f'terrain_{x}_{y}_floor.png')
 print('FLOOR_COMPOSITE complete',count,flush=True)
if __name__=='__main__':main()
