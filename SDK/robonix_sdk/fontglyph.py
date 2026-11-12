"""用系统 TrueType 字体把汉字（以及 Hershey 缺的字符）转成轮廓折线。

笔是单线笔，所以渲染出来是「空心字」：字形外轮廓 + 内孔轮廓。
坐标沿用 Hershey 的字体单位（baseline = 22，大写高 = 21），
因此 text.py 的 _emit_glyph 一行都不用改。
"""

from __future__ import annotations

import os
import unicodedata

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import trajectory as _traj

CAP_UNITS = 21.0       # 大写字母高度（字体单位）
BASELINE_UNITS = 22.0  # 基线位置（字体单位）
EM_PX = 160            # 渲染分辨率：em 的像素高度
PAD = 4                # 位图四周留白，避免轮廓贴边
SIMPLIFY_PX = 0.9      # 轮廓抽稀容差（像素）

# 常见中文字体，按优先级自动挑选
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",    # 微软雅黑
    r"C:\Windows\Fonts\simhei.ttf",  # 黑体
    r"C:\Windows\Fonts\Deng.ttf",    # 等线
    r"C:\Windows\Fonts\simsun.ttc",  # 宋体
]


def find_font() -> str | None:
    """自动寻找一个可用的中文字体。"""
    env = os.environ.get("ROBONIX_FONT")
    if env and os.path.isfile(env):
        return env
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


class FontGlyphProvider:
    """把单个字符渲染成轮廓折线（Hershey 字体单位）。"""

    def __init__(self, font_path: str | None = None, em_px: int = EM_PX):
        path = font_path or find_font()
        if not path:
            raise RuntimeError(
                "找不到可用的中文字体；请用 font_path 指定一个 TTF/TTC 文件"
            )
        self.path = path
        self.em_px = int(em_px)
        self._font = ImageFont.truetype(path, self.em_px)
        ascent, descent = self._font.getmetrics()
        em = float(ascent + descent) or float(self.em_px)
        # 用「国」做基准：墨迹高度对齐大写高，底边对齐基线。
        # 否则汉字会比字母小一圈，而且整体往下沉。
        ink = 0.0
        drop_px = 0.0
        try:
            _l, top, _r, bottom = self._font.getbbox("国", anchor="ls")
            ink = float(bottom - top)
            drop_px = float(bottom)
        except Exception:
            pass
        self._units_per_px = CAP_UNITS / (ink if ink > 0.0 else em)
        self._ref_drop_units = drop_px * self._units_per_px
        self._cache: dict[str, list[list[tuple[float, float]]]] = {}

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    def advance(self, ch: str) -> float:
        """该字符的字宽（Hershey 字体单位）。"""
        return float(self._font.getlength(ch)) * self._units_per_px

    def can_render(self, ch: str) -> bool:
        # 控制符 / 私用区 / 未分配区：字体只会给出 .notdef 方框，直接拒掉
        if unicodedata.category(ch)[0] == "C":
            return False
        try:
            left, top, right, bottom = self._font.getbbox(ch, anchor="ls")
        except Exception:
            return False
        return (right - left) > 0 and (bottom - top) > 0

    def _bitmap(self, ch: str) -> tuple[np.ndarray, float, float]:
        left, top, right, bottom = self._font.getbbox(ch, anchor="ls")
        ox = PAD - left
        oy = PAD - top
        w = int(right - left) + 2 * PAD
        h = int(bottom - top) + 2 * PAD
        img = Image.new("L", (w, h), 0)
        ImageDraw.Draw(img).text(
            (ox, oy), ch, font=self._font, fill=255, anchor="ls"
        )
        # 基线左原点在位图中的像素坐标就是 (ox, oy)
        return np.asarray(img) > 127, ox, oy

    def strokes(self, ch: str) -> list[list[tuple[float, float]]]:
        """返回若干条折线，单位与 Hershey 一致：y 越大越靠下。"""
        if ch in self._cache:
            return self._cache[ch]

        binary, ox, oy = self._bitmap(ch)
        segments = _traj._marching_squares_segments(binary)
        polylines = _traj._segments_to_polylines(segments)

        k = self._units_per_px
        tol = SIMPLIFY_PX * k
        out: list[list[tuple[float, float]]] = []
        for poly in polylines:
            pts = [
                (
                    (px - ox) * k,
                    BASELINE_UNITS - ((oy - py) * k + self._ref_drop_units),
                )
                for px, py in poly
            ]
            pts = _traj._simplify_loop(pts, tol)
            if len(pts) >= 2:
                out.append(pts)

        self._cache[ch] = out
        return out


_providers: dict[str, FontGlyphProvider] = {}


def get_provider(font_path: str | None = None) -> FontGlyphProvider:
    """按字体路径缓存 provider，避免重复加载 TTF。"""
    key = font_path or find_font() or ""
    if key not in _providers:
        _providers[key] = FontGlyphProvider(font_path)
    return _providers[key]
