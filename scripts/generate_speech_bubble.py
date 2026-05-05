"""Generate badge and marker icons for the atlas webmap sprite.

Outputs to templates/icons/:
  speech_bubble.png  — conversation badge overlay (blue, 80x80)
  camera.png         — photos layer marker (bright green, 80x80)
  eye.png            — notes layer marker (bright blue, 80x80)
  map_marker.png     — generic map pin (black/white, 80x80)

Run from repo root: python3 scripts/generate_speech_bubble.py
"""

from pathlib import Path
from PIL import Image, ImageDraw

SIZE = 80
ICONS_DIR = Path(__file__).parent.parent / 'templates' / 'icons'


def draw_speech_bubble():
    img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    blue = (33, 150, 243, 255)
    pad = 5
    tail_h = 16
    bubble = [pad, pad, SIZE - pad, SIZE - pad - tail_h]
    draw.rounded_rectangle(bubble, radius=11, fill=blue)

    bx0, by0, bx1, by1 = bubble
    tail_pts = [
        (bx0 + 8, by1),
        (bx0 + 22, by1),
        (bx0 + 2, SIZE - pad),
    ]
    draw.polygon(tail_pts, fill=blue)

    dot_r = 4
    dot_y = (by0 + by1) // 2
    cx = (bx0 + bx1) // 2
    white = (255, 255, 255, 230)
    for dx in (-13, 0, 13):
        x = cx + dx
        draw.ellipse([x - dot_r, dot_y - dot_r, x + dot_r, dot_y + dot_r], fill=white)

    return img


def draw_camera():
    img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    green = (0, 200, 83, 255)
    white = (255, 255, 255, 255)

    # Camera body
    draw.rounded_rectangle([8, 24, 72, 66], radius=8, fill=green)

    # Viewfinder bump at top-left
    draw.rounded_rectangle([15, 14, 34, 26], radius=4, fill=green)

    # Lens: white outer ring + green inner disc + white highlight
    cx, cy = 40, 45
    draw.ellipse([cx - 16, cy - 16, cx + 16, cy + 16], fill=white)
    draw.ellipse([cx - 11, cy - 11, cx + 11, cy + 11], fill=green)
    draw.ellipse([cx + 3, cy - 9, cx + 9, cy - 3], fill=white)

    return img


def draw_eye():
    img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    blue = (33, 150, 243, 255)
    white = (255, 255, 255, 255)

    # Almond eye shape — pointed at left and right tips
    eye_pts = [
        (4, 40),
        (18, 24),
        (40, 15),
        (62, 24),
        (76, 40),
        (62, 56),
        (40, 65),
        (18, 56),
    ]
    draw.polygon(eye_pts, fill=blue)

    # Iris (white) + pupil (blue) + highlight
    cx, cy = 40, 40
    draw.ellipse([cx - 14, cy - 14, cx + 14, cy + 14], fill=white)
    draw.ellipse([cx - 9,  cy - 9,  cx + 9,  cy + 9],  fill=blue)
    draw.ellipse([cx + 2,  cy - 7,  cx + 7,  cy - 2],  fill=white)

    return img


def draw_map_marker():
    img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    black = (20, 20, 20, 255)
    white = (255, 255, 255, 255)

    # Teardrop body: filled circle on top + triangle tail pointing down
    # Circle: center (40, 26), radius 22
    draw.ellipse([18, 4, 62, 48], fill=black)
    # Triangle from the equator of the circle down to a point
    draw.polygon([(18, 26), (62, 26), (40, 70)], fill=black)

    # Inner white circle — hollow centre of the pin
    draw.ellipse([29, 15, 51, 37], fill=white)

    return img


icons = [
    ('speech_bubble.png', draw_speech_bubble),
    ('camera.png',        draw_camera),
    ('eye.png',           draw_eye),
    ('map_marker.png',    draw_map_marker),
]

for filename, fn in icons:
    out = ICONS_DIR / filename
    fn().save(out)
    print(f'Saved {out}')
