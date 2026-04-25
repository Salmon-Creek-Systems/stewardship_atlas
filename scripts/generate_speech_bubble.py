"""Generate speech_bubble.png for the conversations badge indicator.

Output: templates/icons/speech_bubble.png (80x80 RGBA, transparent background)
Run from the repo root: python3 scripts/generate_speech_bubble.py
"""

from pathlib import Path
from PIL import Image, ImageDraw

SIZE = 80
img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

blue = (33, 150, 243, 255)

# Bubble body — rounded rectangle leaving room at bottom for tail
pad = 5
tail_h = 16
bubble = [pad, pad, SIZE - pad, SIZE - pad - tail_h]
draw.rounded_rectangle(bubble, radius=11, fill=blue)

# Tail — triangle at bottom-left, pointing down-left
bx0, by0, bx1, by1 = bubble
tail_pts = [
    (bx0 + 8, by1),        # left anchor on bubble bottom
    (bx0 + 22, by1),       # right anchor on bubble bottom
    (bx0 + 2, SIZE - pad), # tip
]
draw.polygon(tail_pts, fill=blue)

# Three white dots inside bubble
dot_r = 4
dot_y = (by0 + by1) // 2
cx = (bx0 + bx1) // 2
white = (255, 255, 255, 230)
for dx in (-13, 0, 13):
    x = cx + dx
    draw.ellipse([x - dot_r, dot_y - dot_r, x + dot_r, dot_y + dot_r], fill=white)

out = Path(__file__).parent.parent / 'templates' / 'icons' / 'speech_bubble.png'
img.save(out)
print(f'Saved {out}')
