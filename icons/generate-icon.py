"""Draw the application icon (icons/eve_modem.ico + .png): Venus as a disc with the
DSES teal, a comb of tones below it. Pillow only."""
from pathlib import Path

from PIL import Image, ImageDraw

TEAL, NAVY, GOLD, WHITE = (21, 96, 130), (10, 47, 64), (184, 106, 24), (255, 255, 255)
here = Path(__file__).resolve().parent


def draw(size: int) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    r = size * 0.46
    c = size / 2
    d.rounded_rectangle([1, 1, size - 2, size - 2], radius=size * 0.18, fill=NAVY)
    # Venus disc
    vr = size * 0.22
    d.ellipse([c - vr, c * 0.62 - vr, c + vr, c * 0.62 + vr], fill=GOLD)
    d.ellipse([c - vr * 0.55, c * 0.62 - vr * 0.8, c + vr * 0.15, c * 0.62 - vr * 0.1], fill=(230, 160, 70))
    # Earth arc at the bottom
    er = size * 0.9
    d.ellipse([c - er, size * 0.85, c + er, size * 0.85 + 2 * er], fill=TEAL)
    # a comb of tones (one lit)
    n = 9
    x0, x1 = size * 0.18, size * 0.82
    for i in range(n):
        x = x0 + (x1 - x0) * i / (n - 1)
        h = size * (0.10 if i != 5 else 0.28)
        w = max(1, int(size * 0.035))
        col = WHITE if i != 5 else GOLD
        d.rectangle([x - w / 2, size * 0.88 - h, x + w / 2, size * 0.88], fill=col)
    return im


sizes = [16, 24, 32, 48, 64, 128, 256]
imgs = [draw(s) for s in sizes]
imgs[-1].save(here / "eve_modem.png")
imgs[-1].save(here / "eve_modem.ico", sizes=[(s, s) for s in sizes], append_images=imgs[:-1])
print("wrote", here / "eve_modem.ico", "and .png")
