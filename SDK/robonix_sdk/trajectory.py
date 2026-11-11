"""二值图 -> XYZ 绘图路径点（waypoints）。

路径点格式：``[(x_mm, y_mm, z_mm), ...]``，z 值直接编码笔状态：
``z_down`` 落笔、``z_up`` 抬笔。PLC 无需区分提笔/落笔，逐点执行
``MC_MoveLinearAbsolute`` 即可。

提供两种策略：
- ``raster``（默认）：逐行蛇形扫描，适合色块/填充。
- ``outline``：marching-squares 提取外轮廓，适合细线/文字/logo。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
@dataclass
class PlotConfig:
    pixel_to_mm: float = 0.1          # 1 像素对应的世界坐标距离 (mm)
    offset_x: float = 0.0             # 世界坐标原点偏移 (mm)
    offset_y: float = 0.0
    z_up: float = 5.0                 # 抬笔高度 (mm)
    z_down: float = 0.0               # 落笔高度 (mm)
    vel: float = 20.0                 # 落笔运动速度
    travel_vel: float = 40.0          # 抬笔移动速度
    acc: float = 300.0
    dec: float = 300.0
    jerk: float = 3000.0
    flip_x: bool = False
    flip_y: bool = False              # 图像 row 默认映射到 +y
    strategy: str = "raster"          # raster | outline
    # 落笔绘制时对连续黑色像素的抽稀步长（1 = 逐像素）
    down_step: int = 1
    # 抬笔跳转的抽稀步长（>1 可减少空跑点，但会降低行程顺滑度）
    up_step: int = 1


def _pixel_to_world(col: float, row: float, cfg: PlotConfig) -> tuple[float, float]:
    x = col * cfg.pixel_to_mm + cfg.offset_x
    y = row * cfg.pixel_to_mm + cfg.offset_y
    if cfg.flip_x:
        x = -x
    if cfg.flip_y:
        y = -y
    return x, y


def _pt(col: int, row: int, z: float, cfg: PlotConfig) -> tuple[float, float, float]:
    x, y = _pixel_to_world(col + 0.5, row + 0.5, cfg)
    return x, y, z


# ---------------------------------------------------------------------------
# raster 策略
# ---------------------------------------------------------------------------
def raster_waypoints(binary: np.ndarray, cfg: PlotConfig) -> list[tuple[float, float, float]]:
    """逐行蛇形扫描。黑色游程内逐像素落笔，游程间抬笔跳转。"""
    rows, cols = binary.shape
    points: list[tuple[float, float, float]] = []
    for r in range(rows):
        line = binary[r]
        col_range = range(cols) if r % 2 == 0 else range(cols - 1, -1, -1)
        in_run = False
        run_count = 0
        for c in col_range:
            is_ink = bool(line[c])
            if is_ink and not in_run:
                # 进入游程：先抬笔移到起点
                points.append(_pt(c, r, cfg.z_up, cfg))
                points.append(_pt(c, r, cfg.z_down, cfg))
                in_run = True
                run_count = 0
            elif is_ink and in_run:
                run_count += 1
                if run_count % cfg.down_step:
                    continue
                points.append(_pt(c, r, cfg.z_down, cfg))
            elif not is_ink and in_run:
                # 游程结束：抬笔
                points.append(_pt(c, r, cfg.z_up, cfg))
                in_run = False
        if in_run:
            points.append(_pt(cols - 1, r, cfg.z_up, cfg))
    return points


# ---------------------------------------------------------------------------
# outline 策略（marching squares）
# ---------------------------------------------------------------------------
_EDGE_TABLE = [
    [],                       # 0
    [(0, 1)],                 # 1
    [(1, 2)],                 # 2
    [(0, 2)],                 # 3
    [(2, 3)],                 # 4
    [(0, 3), (1, 2)],         # 5  saddle
    [(1, 3)],                 # 6
    [(0, 3)],                 # 7
    [(0, 3)],                 # 8
    [(1, 3)],                 # 9
    [(0, 1), (2, 3)],         # 10 saddle
    [(2, 3)],                 # 11
    [(0, 2)],                 # 12
    [(0, 2)],                 # 13
    [(1, 2)],                 # 14
    [],                       # 15
]
_CASE = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]


def _vertex(edge: int, x0: float, y0: float) -> tuple[float, float]:
    # 边 0: 下, 1: 右, 2: 上, 3: 左
    if edge == 0:
        return x0 + 0.5, y0
    if edge == 1:
        return x0 + 1.0, y0 + 0.5
    if edge == 2:
        return x0 + 0.5, y0 + 1.0
    return x0, y0 + 0.5


def _marching_squares_segments(binary: np.ndarray) -> list[list[tuple[float, float]]]:
    """对二值图做 marching squares，返回所有轮廓线段（每条线段两顶点）。"""
    rows, cols = binary.shape
    segments: list[list[tuple[float, float]]] = []
    for r in range(rows - 1):
        for c in range(cols - 1):
            idx = 0
            if binary[r, c]:
                idx |= 1
            if binary[r, c + 1]:
                idx |= 2
            if binary[r + 1, c + 1]:
                idx |= 4
            if binary[r + 1, c]:
                idx |= 8
            for e0, e1 in _EDGE_TABLE[idx]:
                segments.append([_vertex(e0, c, r), _vertex(e1, c, r)])
    return segments


def _segments_to_polylines(
    segments: list[list[tuple[float, float]]],
) -> list[list[tuple[float, float]]]:
    """把无序线段串成闭合折线（贪心端点匹配）。"""
    eps = 1e-9
    remaining = [s for s in segments]
    polylines: list[list[tuple[float, float]]] = []
    while remaining:
        start = remaining.pop()
        poly = [start[0], start[1]]
        closed = False
        while not closed:
            head, tail = poly[0], poly[-1]
            matched = False
            for i, seg in enumerate(remaining):
                a, b = seg
                if _close(a, tail, eps):
                    poly.append(b)
                    matched = True
                elif _close(b, tail, eps):
                    poly.append(a)
                    matched = True
                elif _close(a, head, eps):
                    poly.insert(0, b)
                    matched = True
                elif _close(b, head, eps):
                    poly.insert(0, a)
                    matched = True
                if matched:
                    remaining.pop(i)
                    break
            if not matched:
                break
            if _close(poly[0], poly[-1], eps):
                closed = True
        # 去重（移除首尾重复点外的连续重复点）
        dedup = [poly[0]]
        for p in poly[1:]:
            if not _close(p, dedup[-1], eps):
                dedup.append(p)
        polylines.append(dedup)
    return polylines


def _close(a: tuple[float, float], b: tuple[float, float], eps: float) -> bool:
    return abs(a[0] - b[0]) < eps and abs(a[1] - b[1]) < eps


def outline_waypoints(binary: np.ndarray, cfg: PlotConfig) -> list[tuple[float, float, float]]:
    """提取外轮廓并按最近邻排序，逐轮廓落笔绘制。"""
    segments = _marching_squares_segments(binary)
    polylines = _segments_to_polylines(segments)
    if not polylines:
        return []

    # 每个折线转换为 (x_mm, y_mm) 点序
    world = [
        [_pixel_to_world(px, py, cfg) for px, py in poly]
        for poly in polylines
    ]

    # 最近邻排序，减少空跑
    ordered: list[list[tuple[float, float]]] = []
    current = world.pop(0)
    ordered.append(current)
    while world:
        end = current[-1]
        nearest = min(
            range(len(world)),
            key=lambda i: _dist2(end, world[i][0]),
        )
        current = world.pop(nearest)
        ordered.append(current)

    points: list[tuple[float, float, float]] = []
    for poly in ordered:
        x0, y0 = poly[0]
        points.append((x0, y0, cfg.z_up))
        for x, y in poly:
            points.append((x, y, cfg.z_down))
        # 闭合
        points.append((poly[0][0], poly[0][1], cfg.z_down))
        points.append((x0, y0, cfg.z_up))
    return points


def _dist2(a: tuple[float, float], b: tuple[float, float]) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def to_waypoints(
    binary: np.ndarray, cfg: PlotConfig | None = None
) -> list[tuple[float, float, float]]:
    cfg = cfg or PlotConfig()
    if cfg.strategy == "outline":
        return outline_waypoints(binary, cfg)
    return raster_waypoints(binary, cfg)
