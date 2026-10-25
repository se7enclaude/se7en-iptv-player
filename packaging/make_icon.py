"""Generates packaging/icon.ico, the Seventh OS card icon (dist/se7en-iptv-player.png) and the installer artwork.

se7en IPTV Player mark: a black tile lit red from below, a TV (red screen, two antennas) with the white Anton « 7 »
on the screen, in the red of the IPTV Player design system. Drawn in code, no binary asset.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
INK = (14, 14, 18)
INK_RED = (58, 10, 14)
RED = (238, 46, 55)  # accent of the IPTV Player design system
RED_LIGHT = (255, 120, 126)
BLUSH_LIGHT = RED_LIGHT  # installer wordmark « 7 »
FONTS = ROOT / "frontend" / "node_modules" / "@fontsource"


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    files = {"anton": FONTS / "anton" / "files" / "anton-latin-400-normal.woff",
             "manrope": FONTS / "manrope" / "files" / "manrope-latin-800-normal.woff"}
    f = files.get(name)
    if f and f.exists():
        return ImageFont.truetype(str(f), size)
    return ImageFont.truetype("arialbd.ttf", size)


def tile(s: int) -> Image.Image:
    """Fond : noir d'écran éteint, avec la lueur rouge d'une télé allumée qui monte du bas."""
    g = Image.new("RGB", (s, s), INK)
    px = g.load()
    for y in range(s):
        t = (y / s) ** 1.6
        c = tuple(int(INK[i] + (INK_RED[i] - INK[i]) * t) for i in range(3))
        for x in range(s):
            px[x, y] = c
    glow = Image.new("L", (s, s), 0)
    ImageDraw.Draw(glow).ellipse((s * 0.05, s * 0.62, s * 0.95, s * 1.3), fill=150)
    return Image.composite(Image.new("RGB", (s, s), RED), g, glow.filter(ImageFilter.GaussianBlur(s * 0.14)))


def render(size: int) -> Image.Image:
    s = max(size * 4, 256)  # supersample
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=255)
    img = tile(s).convert("RGBA")
    d = ImageDraw.Draw(img)
    lw = max(3, s // 26)
    # la télé : écran arrondi rouge, deux antennes
    x0, y0, x1, y1 = s * 0.13, s * 0.3, s * 0.87, s * 0.84
    ax = s * 0.5
    for dx in (-0.17, 0.17):
        d.line((ax, y0 + lw * 0.2, ax + s * dx, s * 0.12), fill=(255, 255, 255, 255), width=lw)
        d.ellipse((ax + s * dx - lw, s * 0.12 - lw, ax + s * dx + lw, s * 0.12 + lw), fill=(255, 255, 255, 255))
    shadow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((x0, y0 + s * 0.025, x1, y1 + s * 0.025), radius=int(s * 0.1), fill=(0, 0, 0, 150))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(s * 0.03)))
    screen = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    sm = Image.new("L", (s, s), 0)
    ImageDraw.Draw(sm).rounded_rectangle((x0, y0, x1, y1), radius=int(s * 0.1), fill=255)
    grad = Image.new("RGBA", (s, s))
    gp = grad.load()
    for y in range(int(y0), int(y1) + 1):
        t = (y - y0) / (y1 - y0)
        c = tuple(int(RED_LIGHT[i] + (RED[i] - RED_LIGHT[i]) * min(1, t * 1.3)) for i in range(3))
        for x in range(int(x0), int(x1) + 1):
            gp[x, y] = c + (255,)
    screen.paste(grad, (0, 0), sm)
    img.alpha_composite(screen)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x0, y0, x1, y1), radius=int(s * 0.1), outline=(255, 255, 255, 255), width=lw)
    # reflet de verre en haut de l'écran
    hl = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(hl).rounded_rectangle((x0 + lw * 2, y0 + lw * 1.6, x1 - lw * 2, y0 + (y1 - y0) * 0.36), radius=int(s * 0.07), fill=(255, 255, 255, 40))
    img.alpha_composite(hl)
    d = ImageDraw.Draw(img)
    # le 7 (Anton), blanc, au centre de l'écran
    f = font("anton", int(s * 0.46))
    box = d.textbbox((0, 0), "7", font=f)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text(((x0 + x1 - w) / 2 - box[0], (y0 + y1 - h) / 2 - box[1]), "7", font=f, fill=(255, 255, 255, 255))
    # liseré de verre
    d.rounded_rectangle((1, 1, s - 2, s - 2), radius=int(s * 0.22), outline=(255, 255, 255, 70), width=max(2, s // 110))
    out = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out.resize((size, size), Image.Resampling.LANCZOS)


def wizard_images(folder: Path) -> None:
    """Installer artwork: the same black and red tile, the wordmark with the red 7."""
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
