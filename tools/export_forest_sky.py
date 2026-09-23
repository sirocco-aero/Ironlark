"""Convert the source HDR sky to Webots background and lighting cubemaps."""
import argparse, math, sys
from pathlib import Path
import bpy
import numpy as np

p=argparse.ArgumentParser();p.add_argument('--out',required=True)
a=p.parse_args(sys.argv[sys.argv.index('--')+1:]);out=Path(a.out)
source=out.resolve().parent/'pine-forest/source/textures/evening_road_01_puresky_8k.exr'
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.context.scene.view_settings.view_transform='Standard'
bpy.context.scene.view_settings.look='None'
img=bpy.data.images.load(str(source));img.scale(2048,1024)
buf=np.empty(2048*1024*4,dtype=np.float32);img.pixels.foreach_get(buf);env=buf.reshape(1024,2048,4)
size=512
u,v=np.meshgrid(np.linspace(-1,1,size),np.linspace(-1,1,size))
one=np.ones_like(u)
faces={'right':(one,v,-u),'left':(-one,v,u),'top':(u,one,-v),
       'bottom':(u,-one,v),'front':(-u,v,-one),'back':(u,v,one)}
for name,parts in faces.items():
 d=np.stack(parts,axis=-1);d/=np.linalg.norm(d,axis=-1,keepdims=True)
 # Background cube is expressed in its conventional Y-up coordinate system.
 x,y,z=d[:,:,0],-d[:,:,2],d[:,:,1]
 sx=((np.arctan2(y,x)/(2*math.pi)+.5)*2048)%2048
 sy=np.clip((np.arcsin(z)/math.pi+.5)*1024,0,1023)
 ix,iy=sx.astype(int),sy.astype(int);fx=(sx-ix)[...,None];fy=(sy-iy)[...,None]
 color=(env[iy,ix]*(1-fx)+env[iy,(ix+1)%2048]*fx)*(1-fy)+(env[np.minimum(iy+1,1023),ix]*(1-fx)+env[np.minimum(iy+1,1023),(ix+1)%2048]*fx)*fy
 face=bpy.data.images.new(name,width=size,height=size,float_buffer=True)
 face.pixels.foreach_set(color.astype(np.float32).ravel());face.file_format='HDR';face.filepath_raw=str(out/f'sky_{name}.hdr');face.save()
 # Render the background with the same exposure rolloff as WREN. Irradiance
 # retains the original linear radiance, rather than this display transform.
 color[:,:,:3]=1-np.exp(-color[:,:,:3]);face.pixels.foreach_set(color.astype(np.float32).ravel())
 bpy.context.scene.render.image_settings.file_format='PNG';face.save_render(str(out/f'sky_{name}.png'))
 bpy.data.images.remove(face)
 print('SKY',name,flush=True)
