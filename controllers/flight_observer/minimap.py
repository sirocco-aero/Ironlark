"""Player-only world map and spectator cameras. No data goes to navigation."""
import math
from pathlib import Path
import json


def look_at(eye, target):
    """Orientation mapping default forward (+X) onto eye->target. Roll-free."""
    fx, fy, fz = [b - a for a, b in zip(eye, target)]
    n = math.sqrt(fx*fx + fy*fy + fz*fz)
    fx, fy, fz = fx/n, fy/n, fz/n
    rx, ry, rz = fy, -fx, 0.0  # right = forward x world up (+Z)
    rn = math.sqrt(rx*rx + ry*ry + rz*rz)
    if rn < 1e-8:
        rx, ry, rz, rn = 0.0, -1.0, 0.0, 1.0
    rx, ry, rz = rx/rn, ry/rn, rz/rn
    ux, uy, uz = (ry*fz-rz*fy, rz*fx-rx*fz, rx*fy-ry*fx)
    wx, wy, wz = (fy*uz-fz*uy, fz*ux-fx*uz, fx*uy-fy*ux)
    m = [[fx, -rx, ux], [fy, -ry, uy], [fz, -rz, uz]]  # local +X forward, +Z up
    tr = m[0][0] + m[1][1] + m[2][2]
    if tr > 0:
        s = math.sqrt(tr+1.0)*2
        qw, qx, qy, qz = 0.25*s, (m[2][1]-m[1][2])/s, (m[0][2]-m[2][0])/s, (m[1][0]-m[0][1])/s
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0+m[0][0]-m[1][1]-m[2][2])*2
        qw, qx, qy, qz = (m[2][1]-m[1][2])/s, 0.25*s, (m[0][1]+m[1][0])/s, (m[0][2]+m[2][0])/s
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0+m[1][1]-m[0][0]-m[2][2])*2
        qw, qx, qy, qz = (m[0][2]-m[2][0])/s, (m[0][1]+m[1][0])/s, 0.25*s, (m[1][2]+m[2][1])/s
    else:
        s = math.sqrt(1.0+m[2][2]-m[0][0]-m[1][1])*2
        qw, qx, qy, qz = (m[1][0]-m[0][1])/s, (m[0][2]+m[2][0])/s, (m[1][2]+m[2][1])/s, 0.25*s
    ang = 2*math.acos(max(-1, min(1, qw)))
    s = math.sqrt(max(1e-12, 1-qw*qw))
    return [qx/s, qy/s, qz/s, ang]


class WorldView:
    WIDTH, HEIGHT = 320, 294

    def __init__(self, robot, scene_path, run):
        self.robot = robot
        self.scene = json.loads(Path(scene_path).read_text())
        self.run = Path(run)
        self.display = robot.getDevice('world map')
        self.keyboard = robot.getKeyboard()
        self.keyboard.enable(100)
        self.view = robot.getFromDef('IRONLARK_VIEW')
        self.trail = []
        self.state = 'INITIALIZING'
        self.camera = 'drone'
        self.make_background()
        self.set_camera('drone')
        robot.setLabel(0, '1  DRONE    2  OVERVIEW    3  FOREST    4  BACKLIT', 0.025, 0.025, 0.038, 0xE8EFEC, 0, 'Arial')

    @staticmethod
    def ground(position):
        return (position[0], position[1])  # ENU: X east, Y north, Z up

    def project(self, x, z):
        x0, z0, x1, z1 = self.scene['bounds']
        scale = min(288 / (x1 - x0), 216 / (z1 - z0))
        return (round(160 + (x - (x0+x1)/2)*scale), round(145 - (z-(z0+z1)/2)*scale))

    def make_background(self):
        d = self.display
        d.setAlpha(0)
        d.fillRectangle(0, 0, self.WIDTH, self.HEIGHT)
        d.setAlpha(0.94)
        d.setColor(0x141E23)
        d.fillRectangle(8, 8, 304, 266)
        d.setAlpha(1)
        d.setFont('Arial', 13, True)
        d.setColor(0xE6EDE8)
        d.drawText('P I N E  F O R E S T', 22, 16)
        d.setColor(0x71B4A0)
        d.fillOval(294, 23, 3, 3)
        d.setColor(0x465653)
        d.fillRectangle(16, 36, 288, 216)
        terrain = self.scene.get('terrain', {})
        heights = terrain.get('heights', [])
        if heights:
            n = len(heights)
            low, high = min(map(min, heights)), max(map(max, heights))
            for j, row in enumerate(heights):
                for i, height in enumerate(row):
                    shade = 61 + round(26 * (height-low) / max(1, high-low))
                    d.setColor((shade << 16) + ((shade+10) << 8) + shade+8)
                    d.fillRectangle(16+round(i*288/n), 36+round((n-1-j)*216/n), math.ceil(288/n), math.ceil(216/n))
        d.setColor(0x314341)
        for tree in self.scene['trees']:
            x, y = self.project(tree['position'][0], tree['position'][1])
            if 19 < x < 301 and 39 < y < 249:
                d.fillOval(x, y, 2, 2)
        d.setColor(0xADBBB2)
        d.drawRectangle(16, 36, 287, 215)
        d.setFont('Arial', 11, True)
        d.drawText('N', 287, 43)
        x, y = self.project(0, 0)
        d.setColor(0xD3BA7C)
        d.drawRectangle(x-5, y-5, 10, 10)
        d.drawText('HOME', x+9, y-5)
        self.background = d.imageCopy(0, 0, self.WIDTH, self.HEIGHT)

    def set_camera(self, mode):
        camera = self.scene['cameras'][mode]
        self.view.getField('follow').setSFString('Ironlark Iris' if mode == 'drone' else '')
        self.view.getField('position').setSFVec3f(camera['eye'])
        self.view.getField('orientation').setSFRotation(look_at(camera['eye'], camera['target']))
        self.camera = mode

    def update(self, position, orientation):
        key = self.keyboard.getKey()
        while key != -1:
            views = {ord('1'): 'drone', ord('2'): 'overview', ord('3'): 'forest', ord('4'): 'backlit'}
            if key in views:
                self.set_camera(views[key])
            key = self.keyboard.getKey()
        gpos = self.ground(position)
        if not self.trail or math.dist(gpos, self.trail[-1]) > 0.5:
            self.trail.append(gpos)
            self.trail = self.trail[-2000:]
        status = self.run / 'flight-state.json'
        previous = self.state
        if status.exists():
            try:
                self.state = json.loads(status.read_text())['state']
            except (OSError, ValueError, KeyError):
                pass
        d = self.display
        d.imagePaste(self.background, 0, 0, False)
        d.setColor(0x80D6C0)
        for a, b in zip(self.trail, self.trail[1:]):
            ax, ay = self.project(*a)
            bx, by = self.project(*b)
            if 17 <= ax < 303 and 37 <= ay < 251 and 17 <= bx < 303 and 37 <= by < 251:
                d.drawLine(ax, ay, bx, by)
        x, y = self.project(*gpos)
        # Marker stays at the edge if the aircraft leaves the loaded region.
        x, y = max(24, min(295, x)), max(44, min(243, y))
        # Nose direction: body +X axis in world XY (orientation is row-major).
        heading = math.atan2(-orientation[3], orientation[0])
        points = [(round(x + r*math.cos(heading+a)), round(y + r*math.sin(heading+a)))
                  for a, r in [(0, 8), (2.5, 6), (-2.5, 6)]]
        d.setColor(0xF4F5F0)
        d.fillPolygon([p[0] for p in points], [p[1] for p in points])
        d.setColor(0x182225)
        d.drawPolygon([p[0] for p in points], [p[1] for p in points])
        d.setColor(0xD5E2DC)
        d.setFont('Arial', 11, True)
        state = 'LANDED' if self.state == 'COMPLETED' else self.state.replace('_', ' ')
        agl = max(0, position[2] - self.scene.get('pad_top', 0))
        d.drawText(f'{agl:04.1f} m   /   {state}', 22, 258)
        if self.state != previous:  # keep the map of each run beside its logs
            d.imageSave(None, str(self.run / 'world-map.png'))
