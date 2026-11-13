"""单线笔画文字排版与书写。

工作坐标：
    - 纸张左下角为 (0, 0)
    - +X 向右，+Y 向上
    - 默认从左上角开始写
    - z_up 抬笔（默认 -40），z_down 落笔（默认 -50）

字形数据来自 Hershey Simplex（futural），详见 ``hershey_simplex.py``。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import hershey_simplex as font

# ---------------------------------------------------------------------------
# 配置 / 游标 / 异常
# ---------------------------------------------------------------------------
DEFAULT_STATE_PATH = Path.home() / ".robonix" / "writer_state.json"


@dataclass
class TextConfig:
    font_size_mm: float = 10.0       # 大写字母高度（cap height）
    line_spacing: float = 1.5        # 行高 = font_size_mm * line_spacing
    letter_spacing_mm: float = 0.5   # 字间距
    space_advance_mm: float | None = None  # 空格宽度；None = 0.6 * font_size
    work_width_mm: float = 280.0
    work_height_mm: float = 160.0
    margin_mm: float = 0.0
    z_up: float = -40.0
    z_down: float = -50.0
    vel: float = 20.0
    acc: float = 300.0
    dec: float = 300.0
    jerk: float = 3000.0

    def validate(self) -> None:
        if self.font_size_mm <= 0:
            raise ValueError("font_size_mm 必须大于 0")
        if self.work_width_mm <= 0 or self.work_height_mm <= 0:
            raise ValueError("工作范围必须大于 0")
        if self.line_spacing <= 0:
            raise ValueError("line_spacing 必须大于 0")
        if self.margin_mm < 0:
            raise ValueError("margin_mm 不能为负")

    @property
    def scale(self) -> float:
        """字体单位 -> mm。"""
        return self.font_size_mm / font.CAP_HEIGHT_UNITS

    @property
    def line_height_mm(self) -> float:
        return self.font_size_mm * self.line_spacing

    @property
    def space_advance(self) -> float:
        if self.space_advance_mm is not None:
            return self.space_advance_mm
        return 0.6 * self.font_size_mm


@dataclass
class Cursor:
    """当前书写位置（基线坐标，单位 mm）。"""

    x_mm: float
    y_mm: float

    def to_dict(self) -> dict:
        return {"x_mm": self.x_mm, "y_mm": self.y_mm}


class TextOverflowError(ValueError):
    """文字超出纸张范围。"""


def default_cursor(cfg: TextConfig) -> Cursor:
    """默认游标：纸的左上角（考虑 margin）。

    按字形最高点（例如 i/j 的点）对齐，基线在最高点下方
    ``(BASELINE_Y_UNITS - TOP_UNITS) * scale`` 处。
    """
    top_units = _TOP_UNITS
    baseline = (
        cfg.work_height_mm
        - cfg.margin_mm
        - (font.BASELINE_Y_UNITS - top_units) * cfg.scale
    )
    return Cursor(cfg.margin_mm, baseline)


# ---------------------------------------------------------------------------
# 字形解析
# ---------------------------------------------------------------------------
_STROKE_CACHE: dict[str, list[list[tuple[float, float]]]] = {}


def glyph_strokes(ch: str) -> list[list[tuple[float, float]]]:
    """把 "M x,y L x,y ..." 解析成若干条折线。"""
    if ch in _STROKE_CACHE:
        return _STROKE_CACHE[ch]
    if ch not in font.GLYPHS:
        raise ValueError("不支持的字符: %r" % ch)

    path = font.GLYPHS[ch][0]
    strokes: list[list[tuple[float, float]]] = []
    for part in path.split("M")[1:]:
        nums = [float(v) for v in re.findall(r"-?\d+\.?\d*", part)]
        pts = list(zip(nums[0::2], nums[1::2]))
        if len(pts) >= 2:
            strokes.append(pts)
    _STROKE_CACHE[ch] = strokes
    return strokes


def glyph_advance_units(ch: str) -> float:
    return font.GLYPHS[ch][1] * font.ADVANCE_SCALE


def glyph_advance_mm(ch: str, cfg: TextConfig) -> float:
    if ch == " ":
        return cfg.space_advance
    return glyph_advance_units(ch) * cfg.scale + cfg.letter_spacing_mm


def _descender_units() -> float:
    max_y = font.BASELINE_Y_UNITS
    for ch in font.GLYPHS:
        for stroke in glyph_strokes(ch):
            for _, gy in stroke:
                if gy > max_y:
                    max_y = gy
    return max_y - font.BASELINE_Y_UNITS


_DESCENDER_MM_UNITS = _descender_units()


def _top_units() -> float:
    min_y = font.BASELINE_Y_UNITS
    for ch in font.GLYPHS:
        for stroke in glyph_strokes(ch):
            for _, gy in stroke:
                if gy < min_y:
                    min_y = gy
    return min_y


_TOP_UNITS = _top_units()


# ---------------------------------------------------------------------------
# 排版
# ---------------------------------------------------------------------------
def _emit_glyph(
    ch: str,
    x0: float,
    baseline: float,
    cfg: TextConfig,
    points: list[tuple[float, float, float]],
) -> None:
    scale = cfg.scale
    for stroke in glyph_strokes(ch):
        world = [
            (x0 + gx * scale, baseline + (font.BASELINE_Y_UNITS - gy) * scale)
            for gx, gy in stroke
        ]
        points.append((world[0][0], world[0][1], cfg.z_up))
        points.append((world[0][0], world[0][1], cfg.z_down))
        for x, y in world[1:]:
            points.append((x, y, cfg.z_down))
        points.append((world[-1][0], world[-1][1], cfg.z_up))


def layout_text(
    text: str,
    cfg: TextConfig,
    cursor: Cursor | None = None,
) -> tuple[list[tuple[float, float, float]], Cursor]:
    """把字符串排版成轨迹点，并返回新的游标。

    自动换行在发送 TCP 之前完成；如果整体超出纸张，抛 ``TextOverflowError``。
    """
    cfg.validate()
    if cursor is None:
        cursor = default_cursor(cfg)

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for ch in text:
        if ch not in "\n " and ch not in font.GLYPHS:
            raise ValueError("不支持的字符: %r" % ch)

    scale = cfg.scale
    min_x = cfg.margin_mm
    max_x = cfg.work_width_mm - cfg.margin_mm
    min_y = cfg.margin_mm
    max_y = cfg.work_height_mm - cfg.margin_mm
    descender_mm = _DESCENDER_MM_UNITS * scale

    x = float(cursor.x_mm)
    baseline = float(cursor.y_mm)
    points: list[tuple[float, float, float]] = []

    def wrap() -> None:
        nonlocal x, baseline
        x = 0.0
        baseline -= cfg.line_height_mm
        if baseline - descender_mm < min_y - 1e-9:
            raise TextOverflowError(
                "文字超出纸张底部：需要至少再增加 %.2f mm 高度"
                % (min_y - (baseline - descender_mm))
            )

    # 起点已经在右边界外，先换行
    if x > max_x + 1e-9:
        wrap()

    lines = text.split("\n")
    for line_idx, line in enumerate(lines):
        for token in re.findall(r" +|[^ ]+", line):
            if token.isspace():
                adv = cfg.space_advance * len(token)
                if x + adv > max_x + 1e-9 and x > 0:
                    wrap()
                else:
                    x += adv
                continue

            # 先判断整个单词是否放得下；放不下就在单词前换行
            word_adv = sum(glyph_advance_mm(ch, cfg) for ch in token)
            if x + word_adv > max_x + 1e-9 and x > 0:
                wrap()

            for ch in token:
                adv = glyph_advance_mm(ch, cfg)

                if x + adv > max_x + 1e-9 and x > 0:
                    wrap()

                if x + adv > max_x + 1e-9:
                    raise TextOverflowError(
                        "字符 %r 在字号 %.2f mm 下放不进一行（最大宽度 %.2f mm）"
                        % (ch, cfg.font_size_mm, max_x)
                    )

                _emit_glyph(ch, x, baseline, cfg, points)
                x += adv

        if line_idx < len(lines) - 1:
            wrap()

    if points:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        if min(xs) < min_x - 1e-6 or max(xs) > max_x + 1e-6:
            raise TextOverflowError(
                "文字超出纸张 X 范围：x=[%.3f, %.3f]，允许 [%.3f, %.3f]"
                % (min(xs), max(xs), min_x, max_x)
            )
        if min(ys) < min_y - 1e-6 or max(ys) > max_y + 1e-6:
            raise TextOverflowError(
                "文字超出纸张 Y 范围：y=[%.3f, %.3f]，允许 [%.3f, %.3f]"
                % (min(ys), max(ys), min_y, max_y)
            )

    return points, Cursor(x, baseline)


def text_to_waypoints(
    text: str,
    cfg: TextConfig | None = None,
    cursor: Cursor | None = None,
) -> tuple[list[tuple[float, float, float]], Cursor]:
    """便捷入口：``layout_text`` 的别名。"""
    return layout_text(text, cfg or TextConfig(), cursor)


# ---------------------------------------------------------------------------
# 游标持久化
# ---------------------------------------------------------------------------
def load_cursor(path: str | os.PathLike | None, cfg: TextConfig) -> Cursor:
    p = Path(path) if path is not None else DEFAULT_STATE_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        cur = Cursor(float(data["x_mm"]), float(data["y_mm"]))
    except (OSError, ValueError, KeyError, TypeError):
        return default_cursor(cfg)

    # 状态文件里的位置明显越界时，回到默认起点
    if not (0.0 <= cur.x_mm <= cfg.work_width_mm and 0.0 <= cur.y_mm <= cfg.work_height_mm):
        return default_cursor(cfg)
    return cur


def save_cursor(path: str | os.PathLike | None, cursor: Cursor) -> None:
    p = Path(path) if path is not None else DEFAULT_STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(cursor.to_dict(), ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------------------
# 高层写入器
# ---------------------------------------------------------------------------
class TextWriter:
    """维护跨次运行的书写游标，并负责把文本写到 PLC。"""

    def __init__(self, cfg: TextConfig | None = None, state_path=None, reset: bool = False):
        self.cfg = cfg or TextConfig()
        self.cfg.validate()
        self.state_path = Path(state_path) if state_path is not None else DEFAULT_STATE_PATH
        self.cursor = default_cursor(self.cfg) if reset else load_cursor(self.state_path, self.cfg)

    def plan(self, text: str):
        """只排版，不发 TCP；返回 (points, new_cursor)。"""
        return layout_text(text, self.cfg, self.cursor)

    def reset(self) -> Cursor:
        self.cursor = default_cursor(self.cfg)
        return self.cursor

    def write(self, client, text: str, home: bool = True):
        """排版、下发、保存游标，并在最后执行 HOME。"""
        points, new_cursor = self.plan(text)

        client.set_param(
            self.cfg.vel,
            self.cfg.acc,
            self.cfg.dec,
            self.cfg.jerk,
            self.cfg.z_up,
            self.cfg.z_down,
        )
        if points:
            client.draw_waypoints(points)

        # 文本已经写完，先保存游标；即使后面的 HOME 失败，续写位置仍然正确
        self.cursor = new_cursor
        save_cursor(self.state_path, new_cursor)

        if home:
            client.home()

        return new_cursor