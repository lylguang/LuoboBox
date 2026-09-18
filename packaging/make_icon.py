"""生成萝卜盒图标。

产物：
  assets/icon.ico        —— exe / 任务栏 / 安装包用（圆角方底，多尺寸）
  assets/tray_on.png     —— 托盘：运行中（透明底，彩色萝卜）
  assets/tray_off.png    —— 托盘：已停止（透明底，灰萝卜）
  assets/tray_busy.png   —— 托盘：过渡中（透明底，琥珀色萝卜）
  assets/logo.png        —— 关于页用（512px）

纯 Pillow 手绘，不依赖外部素材。
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "assets"
OUT.mkdir(parents=True, exist_ok=True)

BG_TOP = (44, 47, 52)
BG_BOTTOM = (26, 28, 31)
BG_EDGE = (72, 78, 86)

BODY_TOP = (235, 126, 96)
BODY_BOTTOM = (186, 66, 38)

LEAF_MAIN = (94, 205, 168)
LEAF_DARK = (66, 168, 136)


def _vertical_gradient(size: tuple[int, int], top, bottom) -> Image.Image:
    w, h = size
    grad = Image.new("RGB", (1, h))
    px = grad.load()
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return grad.resize((w, h), Image.BILINEAR).convert("RGBA")


def _horizontal_gradient(size: tuple[int, int], left, right) -> Image.Image:
    w, h = size
    grad = Image.new("RGB", (w, 1))
    px = grad.load()
    for x in range(w):
        t = x / max(1, w - 1)
        px[x, 0] = tuple(int(left[i] + (right[i] - left[i]) * t) for i in range(3))
    return grad.resize((w, h), Image.BILINEAR).convert("RGBA")


def _leaf_polygon(cx: float, cy: float, length: float, width: float, angle_deg: float,
                  steps: int = 40) -> list[tuple[float, float]]:
    """一片尖头叶子：基准点在 (cx,cy)，向上生长 length，旋转 angle_deg。"""
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    pts: list[tuple[float, float]] = []
    for side in (1, -1):
        rng = range(steps + 1) if side == 1 else range(steps, -1, -1)
        for i in rng:
            t = i / steps
            y = -length * t
            x = side * (width / 2) * math.sin(math.pi * t) ** 0.85
            pts.append((cx + x * ca - y * sa, cy + x * sa + y * ca))
    return pts


def radish(size: int, gray: bool = False, tint=None) -> Image.Image:
    """画一根萝卜。透明底，居中。"""
    S = size
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    cx = S / 2.0

    body_top_v, body_bot_v = BODY_TOP, BODY_BOTTOM
    leaf_v, leaf_d = LEAF_MAIN, LEAF_DARK
    if gray:
        body_top_v, body_bot_v = (150, 150, 152), (104, 104, 106)
        leaf_v = leaf_d = (128, 128, 130)
    if tint:
        def mix(c, t, k=0.5):
            return tuple(int(c[i] * (1 - k) + t[i] * k) for i in range(3))
        body_top_v, body_bot_v = mix(body_top_v, tint), mix(body_bot_v, tint)
        leaf_v, leaf_d = mix(leaf_v, tint), mix(leaf_d, tint)

    # ---------------------------------------------------------- 叶子
    leaves = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ld = ImageDraw.Draw(leaves)
    body_top_y = S * 0.400
    base_y = body_top_y + S * 0.055
    ld.polygon(_leaf_polygon(cx, base_y, S * 0.255, S * 0.105, -36), fill=leaf_d + (255,))
    ld.polygon(_leaf_polygon(cx, base_y, S * 0.255, S * 0.105, 36), fill=leaf_d + (255,))
    ld.polygon(_leaf_polygon(cx, base_y, S * 0.150, S * 0.090, 0), fill=leaf_v + (255,))
    ld.polygon(_leaf_polygon(cx, base_y, S * 0.290, S * 0.112, 0), fill=leaf_v + (255,))
    img.alpha_composite(leaves)

    # ---------------------------------------------------------- 根身
    top_y, bot_y = body_top_y, S * 0.865
    half_w = S * 0.185
    r = half_w                      # 肩部半径 = 半宽 → 顶部是半圆，不是平口
    shoulder_y = top_y + r
    steps = 140

    def body_points() -> list[tuple[float, float]]:
        pts: list[tuple[float, float]] = []
        # 半圆肩：从左肩绕顶到右肩（圆心在 shoulder_y，半径 r）
        for i in range(steps + 1):
            th = math.pi * (1 - i / steps)
            pts.append((cx + r * math.cos(th), shoulder_y - r * math.sin(th)))
        # 右侧锥面：肩 → 尖
        for i in range(1, steps + 1):
            t = i / steps
            y = shoulder_y + (bot_y - shoulder_y) * t
            pts.append((cx + half_w * (1 - t) ** 0.5, y))
        # 左侧锥面：尖 → 肩（回到起点闭合）
        for i in range(steps - 1, -1, -1):
            t = i / steps
            y = shoulder_y + (bot_y - shoulder_y) * t
            pts.append((cx - half_w * (1 - t) ** 0.5, y))
        return pts

    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).polygon(body_points(), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(S * 0.003))

    body = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    body.paste(_vertical_gradient((S, S), body_top_v, body_bot_v), (0, 0), mask)

    # 圆柱感：横向明暗叠在形体上（左侧受光、右侧转入暗部）
    cyl = _horizontal_gradient((S, S), (255, 226, 208), (108, 22, 6))
    cyl.putalpha(mask.point(lambda v: int(v * 0.52)))
    body.alpha_composite(cyl)

    img.alpha_composite(body)

    # ---------------------------------------------------------- 根须
    d = ImageDraw.Draw(img)
    w_main = max(1, int(S * 0.016))
    d.line([cx, bot_y - S * 0.015, cx + S * 0.006, bot_y + S * 0.035],
           fill=body_bot_v + (255,), width=w_main)
    d.line([cx - S * 0.015, bot_y - S * 0.075, cx - S * 0.085, bot_y - S * 0.012],
           fill=body_bot_v + (215,), width=max(1, int(S * 0.012)))
    d.line([cx + S * 0.022, bot_y - S * 0.085, cx + S * 0.082, bot_y - S * 0.022],
           fill=body_bot_v + (215,), width=max(1, int(S * 0.012)))
    return img


def boxed(size: int) -> Image.Image:
    """圆角方底 + 居中萝卜（exe 图标用）。"""
    S, ss = size, 4
    W = S * ss
    grad = _vertical_gradient((W, W), BG_TOP, BG_BOTTOM)
    mask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, W - 1, W - 1],
                                           radius=int(W * 0.235), fill=255)
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    ring = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    ImageDraw.Draw(ring).rounded_rectangle(
        [W // 128, W // 128, W - 1 - W // 128, W - 1 - W // 128],
        radius=int(W * 0.235), outline=BG_EDGE + (255,), width=max(2, W // 150),
    )
    img.alpha_composite(ring)
    img = img.resize((S, S), Image.LANCZOS)

    inner = radish(int(S * 0.94))
    img.alpha_composite(inner, (int(S * 0.03), int(S * 0.028)))
    return img


def main() -> None:
    boxed(512).save(OUT / "logo.png")
    boxed(512).save(OUT / "icon.ico", sizes=[(16, 16), (20, 20), (24, 24), (32, 32),
                                             (40, 40), (48, 48), (64, 64), (128, 128),
                                             (256, 256)])

    tray = radish(256)
    tray.save(OUT / "tray_on.png")
    radish(256, gray=True).save(OUT / "tray_off.png")
    radish(256, tint=(239, 159, 39)).save(OUT / "tray_busy.png")

    for f in sorted(OUT.iterdir()):
        print(f"{f.name:16s} {f.stat().st_size:>8,} bytes")


if __name__ == "__main__":
    main()
