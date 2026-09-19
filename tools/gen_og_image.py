# -*- coding: utf-8 -*-
"""生成 static/og-image.png（1200x630 社交分享图）。只需本地跑一次，产物入库。

用法：python3 tools/gen_og_image.py
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / 'static' / 'og-image.png'
W, H = 1200, 630


def font(name_candidates, size):
    for n in name_candidates:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main():
    img = Image.new('RGB', (W, H))
    top, bottom = (79, 70, 229), (99, 102, 241)  # indigo-600 -> indigo-500
    px = img.load()
    for y in range(H):
        t = y / (H - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(W):
            px[x, y] = (r, g, b)

    draw = ImageDraw.Draw(img)
    f_big = font(['C:/Windows/Fonts/arialbd.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'], 96)
    f_mid = font(['C:/Windows/Fonts/arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'], 40)
    f_small = font(['C:/Windows/Fonts/arialbd.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'], 30)

    def center(text, f, y, fill=(255, 255, 255)):
        w = draw.textlength(text, font=f)
        draw.text(((W - w) / 2, y), text, font=f, fill=fill)

    center('PDF  →  Markdown', f_big, 175)
    center('Free online converter with OCR', f_mid, 330)
    center('Scanned PDFs · Tables & TOC preserved · 200 pages/month free', f_small, 430,
           fill=(224, 231, 255))
    center('pdf.my99ai.com', f_small, 540)

    OUT.write_bytes(b'')
    img.save(OUT, 'PNG', optimize=True)
    print(f'og-image.png generated -> {OUT}')


if __name__ == '__main__':
    sys.exit(main())
