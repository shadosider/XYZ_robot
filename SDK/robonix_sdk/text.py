"""单线笔画文字排版与书写。

工作坐标：
    - 纸张左下角为 (0, 0)
    - +X 向右，+Y 向上
    - 默认从左上角开始写
    - 机器零点 (0,0,0) = 写字区左下角上方 50 mm（笔尖初始位）
    - 纸面 z = -50，落笔 z_down = -50，抬笔 z_up = -48（离纸 2 mm）
    - 写完回 HOME，即回到 (0,0,0) 待机位

字形来源（两套，都是单线，共用同一条基线）：

    - 西文 / 数字：Hershey Simplex（futural），见 hershey_simplex.py；
    - 汉字：笔画中线（medians），见 hanzi_data.py。

汉字一笔一条折线，笔画之间抬笔跳转，所以画出来是单线字，不是空心轮廓。
汉字墨迹高度 = 字号，字格宽约 1.17 倍字号，字距固定为 CJK_TRACKING_MM。

"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import hanzi_data
from . import hershey_simplex as font

# ---------------------------------------------------------------------------
# 配置 / 游标 / 异常
# ---------------------------------------------------------------------------
DEFAULT_STATE_PATH = Path.home() / ".robonix" / "writer_state.json"

# 汉字墨迹普遍比大写字母最高点再高一点点（实测「年」最高 0.51 单位），
# 排版留出的顶部余量用这个常量兜住。
TOP_SAFETY_UNITS = 1.0

@dataclass
class TextConfig:
    font_size_mm: float = 15.0       # 大写字母高度（cap height）；汉字墨迹高同此值
    line_spacing: float = 1.5        # 行高 = font_size_mm * line_spacing
    letter_spacing_mm: float = 0.5   # 字间距
    space_advance_mm: float | None = None  # 空格宽度；None = 0.6 * font_size
    work_width_mm: float = 220.0     # 机器 X 行程 220 mm
    work_height_mm: float = 150.0    # 机器 Y 行程 150 mm
    margin_mm: float = 0.0
    reset_y_mm: float | None = None  # reset 后文字块顶边的绝对 Y；None = 纸高 - margin
    z_up: float = -48.0            # 抬笔：纸面(-50) 上方 2 mm
    z_down: float = -50.0          # 落笔：压在纸上
    vel: float = 20.0
    acc: float = 300.0
    dec: float = 300.0
    jerk: float = 3000.0

    def validate(self) -> None:
        if self.work_width_mm <= 0 or self.work_height_mm <= 0:
            raise ValueError("工作范围必须大于 0")
        # 机器行程硬上限：X 22 cm / Y 15 cm，落笔点不允许出去
        if self.work_width_mm > 220.0 + 1e-9 or self.work_height_mm > 150.0 + 1e-9:
            raise ValueError(
                "工作范围超出机器行程：X 最长 220 mm、Y 最长 150 mm，当前 %.0f x %.0f mm"
                % (self.work_width_mm, self.work_height_mm)
            )
        if self.font_size_mm < 2.0:
            raise ValueError(
                "字号 %.2f mm 太小：低于 2 mm 时笔画间隙小于笔宽，画不清"
                % self.font_size_mm
            )
        upper = self.work_height_mm - 2.0
        if self.font_size_mm > upper:
            raise TextOverflowError(
                "字号 %.2f mm 放不进纸高：上限 %.2f mm（纸高 %.1f mm - 2 mm）"
                % (self.font_size_mm, upper, self.work_height_mm)
            )
        if self.line_spacing <= 0:
            raise ValueError("line_spacing 必须大于 0")
        if self.margin_mm < 0:
            raise ValueError("margin_mm 不能为负")
        if self.reset_y_mm is not None and not (
            0.0 < self.reset_y_mm <= self.work_height_mm + 1e-9
        ):
            raise ValueError(
                "reset_y_mm 必须在 (0, %.1f] 之间，当前 %s"
                % (self.work_height_mm, self.reset_y_mm)
            )

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
    """默认游标：文字块左上角。

    横向贴左边距；纵向由 ``reset_y_mm`` 决定（文字块顶边的绝对 Y，
    默认 150 mm —— 从机器左下角起点往 +Y 走 15 cm）。
    基线在顶边下方 ``(BASELINE_Y_UNITS - TOP_UNITS + TOP_SAFETY_UNITS) * scale`` 处。
    """
    top_units = _TOP_UNITS
    drop = (font.BASELINE_Y_UNITS - top_units + TOP_SAFETY_UNITS) * cfg.scale
    top_y = (
        cfg.work_height_mm - cfg.margin_mm
        if cfg.reset_y_mm is None
        else cfg.reset_y_mm
    )
    return Cursor(cfg.margin_mm, top_y - drop)


# ---------------------------------------------------------------------------
# 字形解析
# ---------------------------------------------------------------------------
_STROKE_CACHE: dict[str, list[list[tuple[float, float]]]] = {}


def glyph_strokes(ch: str) -> list[list[tuple[float, float]]]:
    """把 "M x,y L x,y ..." 解析成若干条折线。"""
    if ch in _STROKE_CACHE:
        return _STROKE_CACHE[ch]
    if ch not in font.GLYPHS:
        raise ValueError("不支持的字符: %r（西文仅 Hershey 字表，汉字见 hanzi_data）" % ch)

    path = font.GLYPHS[ch][0]
    strokes: list[list[tuple[float, float]]] = []
    for part in path.split("M")[1:]:
        nums = [float(v) for v in re.findall(r"-?\d+\.?\d*", part)]
        pts = list(zip(nums[0::2], nums[1::2]))
        if len(pts) >= 2:
            strokes.append(pts)
    _STROKE_CACHE[ch] = strokes
    return strokes


# 汉字：字距固定 1.5 mm（加在步进上，末字后也多走这一小段）
CJK_TRACKING_MM = 1.5

# 源数据字格边长（见 hanzi_data.py 头注释）
CJK_EM_UNITS = 1024.0

_CJK_STROKE_CACHE: dict[str, list[list[tuple[float, float]]]] = {}


def is_cjk(ch: str) -> bool:
    """是否在收录的汉字字库里。"""
    return ch in hanzi_data.GLYPHS


def _cjk_scale_units() -> float:
    """源单位 -> Hershey 单位（等比）。

    让全字库最高的一笔正好落在 Hershey 的 cap 顶（y = 1），
    即汉字墨迹高度 = 字号，与西文大写字母等高。
    """
    return font.CAP_HEIGHT_UNITS / hanzi_data.Y_MAX


def _cjk_strokes(ch: str) -> list[list[tuple[float, float]]]:
    """汉字笔画（Hershey 单位：y 向下、基线 22），一条折线一笔。"""
    if ch not in _CJK_STROKE_CACHE:
        k = _cjk_scale_units()
        strokes = []
        for part in hanzi_data.GLYPHS[ch].split("M")[1:]:
            nums = [float(v) for v in re.findall(r"-?\d+\.?\d*", part)]
            pts = list(zip(nums[0::2], nums[1::2]))
            if len(pts) >= 2:
                strokes.append(
                    [(gx * k, font.BASELINE_Y_UNITS - gy * k) for gx, gy in pts]
                )
        _CJK_STROKE_CACHE[ch] = strokes
    return _CJK_STROKE_CACHE[ch]


def _strokes(ch: str) -> list[list[tuple[float, float]]]:
    """字形折线（Hershey 单位）。西文用 Hershey，汉字用笔画中线。"""
    if ch in hanzi_data.GLYPHS:
        return _cjk_strokes(ch)
    return glyph_strokes(ch)


def cjk_width_mm(cfg: TextConfig) -> float:
    """汉字字格宽 (mm) = 1024 源单位折算，约 1.2 倍字号。"""
    return CJK_EM_UNITS * _cjk_scale_units() * cfg.scale


def glyph_advance_units(ch: str) -> float:
    if ch in font.GLYPHS:
        return font.GLYPHS[ch][1] * font.ADVANCE_SCALE
    raise ValueError("不支持的字符: %r" % ch)


def glyph_advance_mm(ch: str, cfg: TextConfig) -> float:
    if ch == " ":
        return cfg.space_advance
    if ch in hanzi_data.GLYPHS:
        return cjk_width_mm(cfg) + CJK_TRACKING_MM
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
    for stroke in _strokes(ch):
        world = [
            (x0 + gx * scale, baseline + (font.BASELINE_Y_UNITS - gy) * scale)
            for gx, gy in stroke
        ]
        points.append((world[0][0], world[0][1], cfg.z_up))
        points.append((world[0][0], world[0][1], cfg.z_down))
        for x, y in world[1:]:
            points.append((x, y, cfg.z_down))
        points.append((world[-1][0], world[-1][1], cfg.z_up))


def _iter_tokens(line: str):
    """把一行切成 token：("space" | "cjk" | "word", 文本)。

    汉字逐字成 token（任意字间都能断行），西文按空格成词（整词不拆）。
    """
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if ch == " ":
            j = i
            while j < n and line[j] == " ":
                j += 1
            yield "space", line[i:j]
            i = j
        elif ch in hanzi_data.GLYPHS:
            yield "cjk", ch
            i += 1
        else:
            j = i
            while j < n and line[j] != " " and line[j] not in hanzi_data.GLYPHS:
                j += 1
            yield "word", line[i:j]
            i = j


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
        if ch in "\n ":
            continue
        if ch in font.GLYPHS or ch in hanzi_data.GLYPHS:
            continue
        raise ValueError(
            "不支持的字: %r（汉字仅限字库内的 %d 字）" % (ch, len(hanzi_data.GLYPHS))
        )

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
        x = min_x
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
        for kind, token in _iter_tokens(line):
            if kind == "space":
                adv = cfg.space_advance * len(token)
                if x + adv > max_x + 1e-9 and x > 0:
                    wrap()
                else:
                    x += adv
                continue

            # 汉字：逐字断行，放不下就换行（不需要空格）
            if kind == "cjk":
                adv = glyph_advance_mm(token, cfg)
                if x + adv > max_x + 1e-9 and x > 0:
                    wrap()
                if x + adv > max_x + 1e-9:
                    raise TextOverflowError(
                        "汉字 %r 在字号 %.2f mm 下放不进一行（最大宽度 %.2f mm）"
                        % (token, cfg.font_size_mm, max_x)
                    )
                _emit_glyph(token, x, baseline, cfg, points)
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
            client.draw_waypoints(points, vel=self.cfg.vel)

        # 文本已经写完，先保存游标；即使后面的 HOME 失败，续写位置仍然正确
        self.cursor = new_cursor
        save_cursor(self.state_path, new_cursor)

        if home:
            client.home()

        return new_cursor