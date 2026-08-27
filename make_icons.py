# -*- coding: utf-8 -*-
"""产生两个 .ico：麦克风(VoiceInput) 与 历史纪录(VoiceHistory)"""
import os
from PIL import Image, ImageDraw

ROOT = os.path.join(os.path.expanduser("~"), ".voicehist")
N = 256
SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]


def rounded_bg(c1, c2):
    """由上而下的双色渐层圆角底"""
    img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    grad = Image.new("RGBA", (N, N))
    gd = ImageDraw.Draw(grad)
    for y in range(N):
        t = y / (N - 1)
        gd.line([(0, y), (N, y)],
                fill=tuple(int(a + (b - a) * t) for a, b in zip(c1, c2)) + (255,))
    mask = Image.new("L", (N, N), 0)
    ImageDraw.Draw(mask).rounded_rectangle([6, 6, N - 7, N - 7], radius=56, fill=255)
    img.paste(grad, (0, 0), mask)
    return img


def mic_icon():
    img = rounded_bg((92, 106, 232), (58, 66, 168))
    d = ImageDraw.Draw(img)
    W = (255, 255, 255, 255)
    # 麦克风本体（胶囊）
    d.rounded_rectangle([104, 56, 152, 148], radius=24, fill=W)
    # 支架弧线
    d.arc([78, 96, 178, 186], start=0, end=180, fill=W, width=14)
    # 立柱 + 底座
    d.rectangle([121, 178, 135, 202], fill=W)
    d.rounded_rectangle([94, 200, 162, 212], radius=6, fill=W)
    return img


def hist_icon():
    img = rounded_bg((48, 163, 108), (26, 112, 74))
    d = ImageDraw.Draw(img)
    W = (255, 255, 255, 255)
    # 左侧清单三条
    for i, y in enumerate((72, 118, 164)):
        d.rounded_rectangle([44, y, 60, y + 16], radius=8, fill=W)
        d.rounded_rectangle([74, y + 3, 74 + (86, 66, 78)[i], y + 13], radius=5, fill=W)
    # 右下时钟
    d.ellipse([148, 140, 226, 218], fill=W)
    d.ellipse([158, 150, 216, 208], fill=(26, 112, 74, 255))
    d.line([187, 179, 187, 160], fill=W, width=8)
    d.line([187, 179, 202, 188], fill=W, width=8)
    return img


for name, fn in (("voiceinput.ico", mic_icon), ("voicehistory.ico", hist_icon)):
    p = os.path.join(ROOT, name)
    fn().save(p, format="ICO", sizes=SIZES)
    print("已产生", p)
