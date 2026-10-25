"""Generates packaging/icon.ico, the Seventh OS card icon (dist/se7en-iptv-player.png) and the installer artwork.

se7en IPTV Player mark, in the se7en IA « Studio Glass » palette: a deep teal to blush glass tile, the white Anton « 7 »,
broadcast waves and a coral play badge. Drawn in code, no binary asset.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
TEAL_DARK = (23, 48, 54)
TEAL = (74, 120, 128)
BLUSH = (233, 161, 154)
BLUSH_LIGHT = (246, 206, 200)
CORAL = (255, 138, 107)
FONTS = ROOT / "frontend" / "node_modules" / "@fontsource"


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    files = {"anton": FONTS / "anton" / "files" / "anton-latin-400-normal.woff",
             "manrope": FONTS / "manrope" / "files" / "manrope-latin-800-normal.woff"}
    f = files.get(name)
    if f and f.exists():
        return ImageFont.truetype(str(f), size)
    return ImageFont.truetype("arialbd.ttf", size)


def tile(s: int) -> Image.Image:
    """Fond de verre : teal profond en bas à gauche, lueur rose en haut à droite (comme l'icône de Seventh OS)."""
    g = Image.new("RGB", (s, s), TEAL_DARK)
    px = g.load()
    for y in range(s):
        for x in range(s):
            t = (x + (s - y)) / (2 * s)  # 0 en bas à gauche, 1 en haut à droite
            u = max(0.0, min(1.0, (t - 0.35) / 0.65))
            c0 = tuple(int(TEAL_DARK[i] + (TEAL[i] - TEAL_DARK[i]) * min(1, t * 1.6)) for i in range(3))
            px[x, y] = tuple(int(c0[i] + (BLUSH[i] - c0[i]) * u * u) for i in range(3))
    glow = Image.new("L", (s, s), 0)
    ImageDraw.Draw(glow).ellipse((s * 0.45, -s * 0.25, s * 1.25, s * 0.55), fill=120)
    return Image.composite(Image.new("RGB", (s, s), BLUSH_LIGHT), g, glow.filter(ImageFilter.GaussianBlur(s * 0.12)))


def render(size: int) -> Image.Image:
    s = max(size * 4, 256)  # supersample
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=255)
    img = tile(s).convert("RGBA")
    d = ImageDraw.Draw(img)
    # le 7 (Anton), blanc
    f = font("anton", int(s * 0.8))
    box = d.textbbox((0, 0), "7", font=f)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text(((s - w) / 2 - box[0] - s * 0.07, (s - h) / 2 - box[1]), "7", font=f, fill=(255, 255, 255, 255))
    # ondes de diffusion en haut à droite
    lw = max(3, s // 40)
    cx, cy = s * 0.77, s * 0.25
    for k, r in enumerate((0.075, 0.13)):
        rr = s * r
        d.arc((cx - rr, cy - rr, cx + rr, cy + rr), start=-80, end=-10 + 0, fill=(255, 255, 255, 235 - 50 * k), width=lw)
        d.arc((cx - rr, cy - rr, cx + rr, cy + rr), start=100, end=170, fill=(255, 255, 255, 235 - 50 * k), width=lw)
    d.ellipse((cx - s * 0.035, cy - s * 0.035, cx + s * 0.035, cy + s * 0.035), fill=(255, 255, 255, 255))
    # pastille corail avec le triangle de lecture, en bas à droite
    r = s * 0.17
    bx, by = s * 0.76, s * 0.76
    shadow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse((bx - r, by - r + s * 0.02, bx + r, by + r + s * 0.02), fill=(10, 25, 30, 120))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(s * 0.02)))
    d = ImageDraw.Draw(img)
    d.ellipse((bx - r, by - r, bx + r, by + r), fill=CORAL + (255,), outline=(255, 255, 255, 235), width=max(2, s // 90))
    t = r * 0.5
    d.polygon([(bx - t * 0.7, by - t), (bx - t * 0.7, by + t), (bx + t * 1.05, by)], fill=(255, 255, 255, 255))
    # liseré de verre
    d.rounded_rectangle((1, 1, s - 2, s - 2), radius=int(s * 0.22), outline=(255, 255, 255, 120), width=max(2, s // 110))
    out = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out.resize((size, size), Image.Resampling.LANCZOS)


def wizard_images(folder: Path) -> None:
    """Installer artwork: the same teal / blush glass, the wordmark with the blush 7."""
    for name, (w, h) in (("wizard-large.bmp", (410, 797)), ("wizard-small.bmp", (138, 140))):
        side = max(w, h)
        img = tile(side).crop(((side - w) // 2, side - h, (side - w) // 2 + w, side)).convert("RGB")
        d = ImageDraw.Draw(img)
        if name == "wizard-small.bmp":
            mark = render(int(w * 0.74))
            img.paste(mark, ((w - mark.width) // 2, (h - mark.height) // 2), mark)
        else:
            mark = render(int(w * 0.46))
            img.paste(mark, ((w - mark.width) // 2, int(h * 0.2)), mark)
            f = font("anton", 64)
            parts = [("SE", (255, 255, 255)), ("7", BLUSH_LIGHT), ("EN", (255, 255, 255))]
            x = (w - sum(d.textlength(t, font=f) for t, _ in parts)) / 2
            y = int(h * 0.5)
            for t, col in parts:
                d.text((x, y), t, font=f, fill=col)
                x += d.textlength(t, font=f)
            f2 = font("anton", 46)
            d.text(((w - d.textlength("IPTV PLAYER", font=f2)) / 2, y + 76), "IPTV PLAYER", font=f2, fill=BLUSH_LIGHT)
            f3 = font("manrope", 17)
            sub = "Ton abonnement IPTV, en grand"
            d.text(((w - d.textlength(sub, font=f3)) / 2, y + 140), sub, font=f3, fill=(255, 255, 255))
        img.save(folder / name)


if __name__ == "__main__":
    out = Path(__file__).with_name("icon.ico")
    render(256).save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    wizard_images(Path(__file__).parent)
    png = ROOT / "dist" / "se7en-iptv-player.png"
    png.parent.mkdir(exist_ok=True)
    render(512).save(png)
    print("wrote", out, png, "and installer artwork")
