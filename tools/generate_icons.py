"""Generate extension icons: lightning bolt on purple gradient rounded square."""
from PIL import Image, ImageDraw
import os, math

SIZES = [16, 48, 128]
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "extension", "icons")

# Brand colors
C_TOP = (99, 102, 241)      # #6366f1 indigo-500
C_BOT = (139, 92, 246)      # #8b5cf6 violet-500
WHITE = (255, 255, 255)

def lerp_color(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))

def draw_icon(size):
    """Draw at 4x then downsample for antialiasing."""
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded rectangle background with vertical gradient
    corner = s // 5
    # Draw gradient manually row by row inside rounded rect mask
    mask = Image.new("L", (s, s), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([0, 0, s - 1, s - 1], radius=corner, fill=255)

    for y in range(s):
        t = y / max(s - 1, 1)
        color = lerp_color(C_TOP, C_BOT, t)
        draw.line([(0, y), (s - 1, y)], fill=(*color, 255))

    # Apply rounded rect mask
    bg = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    bg.paste(img, mask=mask)
    img = bg

    # Draw lightning bolt (white)
    draw = ImageDraw.Draw(img)
    cx, cy = s / 2, s / 2
    # Bolt proportions relative to icon size
    # Points of a lightning bolt polygon
    bw = s * 0.38  # bolt width span
    bh = s * 0.62  # bolt height span
    top = cy - bh / 2
    bot = cy + bh / 2
    mid_y = cy + bh * 0.02
    left = cx - bw * 0.32
    right = cx + bw * 0.32

    # Lightning bolt as two overlapping triangles forming the classic shape
    bolt_points = [
        (cx + bw * 0.05, top),                  # top point
        (left - bw * 0.02, mid_y),               # left middle
        (cx - bw * 0.02, mid_y - bh * 0.02),     # inner notch left
        (cx - bw * 0.05, bot),                    # bottom point
        (right + bw * 0.02, mid_y - bh * 0.04),  # right middle
        (cx + bw * 0.02, mid_y + bh * 0.02),     # inner notch right
    ]

    draw.polygon(bolt_points, fill=(*WHITE, 255))

    # Add subtle inner shadow / glow for depth
    # (Skip for 16px — too small to matter)
    if size >= 48:
        glow = img.copy()
        glow_draw = ImageDraw.Draw(glow)
        # Slightly offset white bolt for depth illusion
        offset_points = [(x + s * 0.005, y + s * 0.008) for x, y in bolt_points]
        glow_draw.polygon(offset_points, fill=(255, 255, 255, 60))

    # Downsample
    final = img.resize((size, size), Image.LANCZOS)
    return final


for sz in SIZES:
    icon = draw_icon(sz)
    path = os.path.join(OUT_DIR, f"icon{sz}.png")
    icon.save(path, "PNG")
    print(f"  ✓ {path} ({sz}×{sz})")

print("Done — all icons generated.")
