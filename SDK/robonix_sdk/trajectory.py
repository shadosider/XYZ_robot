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
    z_up: float = -20.0               # 抬笔高度 (mm)
    z_down: float = -35.5             # 落笔高度 (mm)
    vel: float = 20.0                 # 落笔运动速度
    travel_vel: float = 40.0          # 抬笔移动速度
    acc: float = 300.0
    dec: float = 300.0
    jerk: float = 3000.0
    flip_x: bool = False
    flip_y: bool = False              # 图像 row 默认映射到 +y
    strategy: str = "raster"          # raster | outline
    # raster 改为「一段连续墨迹只发首尾两点」，down_step 仅为兼容旧调用保留
    down_step: int = 1
    # 轮廓抽稀容差（像素）：Douglas-Peucker，0 = 不抽稀
    simplify_px: float = 0.5
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
    """逐行蛇形扫描。

    一段连续墨迹只输出 4 个点：抬笔到位 → 原地落笔 → 直线画到段末 → 抬笔。
    中间的像素由 PLC 的直线插补补出来，点数与游程长度无关。
    """
    rows, cols = binary.shape
    points: list[tuple[float, float, float]] = []
    for r in range(rows):
        line = binary[r]
        step = 1 if r % 2 == 0 else -1
        c = 0 if step > 0 else cols - 1
        while 0 <= c < cols:
            if not line[c]:
                c += step
                continue
            start = end = c
            while 0 <= end + step < cols and line[end + step]:
                end += step
            points.append(_pt(start, r, cfg.z_up, cfg))
            points.append(_pt(start, r, cfg.z_down, cfg))
            if end != start:
                points.append(_pt(end, r, cfg.z_down, cfg))
            points.append(_pt(end, r, cfg.z_up, cfg))
            c = end + step
    return points


# ---------------------------------------------------------------------------
# outline 策略（marching squares）
# ---------------------------------------------------------------------------
# 边编号: 0=下边中点 1=右边中点 2=上边中点 3=左边中点
# 角点编号: bit0=左上 bit1=右上 bit2=右下 bit3=左下
_EDGE_TABLE = [
    [],                        # 0  全在外
    [(3, 2)],                  # 1  左上
    [(2, 1)],                  # 2  右上
    [(3, 1)],                  # 3  上两格
    [(1, 0)],                  # 4  右下
    [(3, 2), (1, 0)],          # 5  鞍点
    [(2, 0)],                  # 6  右两格
    [(3, 0)],                  # 7  左下在外
    [(0, 3)],                  # 8  左下
    [(2, 0)],                  # 9  左两格
    [(2, 1), (0, 3)],          # 10 鞍点
    [(0, 1)],                  # 11 右下在外
    [(1, 3)],                  # 12 下两格
    [(1, 2)],                  # 13 右上在外
    [(2, 3)],                  # 14 左上在外
    [],                        # 15 全在内
]


def _vertex(edge: int, x0: float, y0: float) -> tuple[float, float]:
    # 行号向下增大：「下边」在 y0+1、「上边」在 y0
    # 边 0: 下, 1: 右, 2: 上, 3: 左
    if edge == 0:
        return x0 + 0.5, y0 + 1.0
    if edge == 1:
        return x0 + 1.0, y0 + 0.5
    if edge == 2:
        return x0 + 0.5, y0
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
    """把无序线段按端点相接串成折线（端点哈希索引，O(n)）。"""
    def key(p):
        return (round(float(p[0]), 6), round(float(p[1]), 6))

    adj: dict = {}
    for i, (a, b) in enumerate(segments):
        ka, kb = key(a), key(b)
        adj.setdefault(ka, []).append((i, kb))
        adj.setdefault(kb, []).append((i, ka))

    used = [False] * len(segments)
    polylines: list[list[tuple[float, float]]] = []
    for i0 in range(len(segments)):
        if used[i0]:
            continue
        used[i0] = True
        poly = [key(segments[i0][0]), key(segments[i0][1])]
        for at_tail in (True, False):
            while True:
                cur = poly[-1] if at_tail else poly[0]
                nxt = None
                for j, other in adj.get(cur, ()):
                    if not used[j]:
                        used[j] = True
                        nxt = other
                        break
                if nxt is None:
                    break
                if at_tail:
                    poly.append(nxt)
                else:
                    poly.insert(0, nxt)
        polylines.append(poly)
    return polylines


def _close(a: tuple[float, float], b: tuple[float, float], eps: float) -> bool:
    return abs(a[0] - b[0]) < eps and abs(a[1] - b[1]) < eps


def _rdp(points: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Douglas-Peucker 折线抽稀（迭代实现，避免深递归）。"""
    n = len(points)
    if n < 3 or tol <= 0:
        return points
    tol2 = tol * tol
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        ax, ay = points[i]
        bx, by = points[j]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        far, far_d = -1, -1.0
        for k in range(i + 1, j):
            px, py = points[k]
            if seg2 == 0.0:
                d2 = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / seg2
                t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                d2 = (px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2
            if d2 > far_d:
                far_d, far = d2, k
        if far_d > tol2:
            keep[far] = True
            stack.append((i, far))
            stack.append((far, j))
    return [p for p, k in zip(points, keep) if k]


def _simplify_loop(poly: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """闭合折线抽稀；tol <= 0 时原样返回。"""
    if tol <= 0 or len(poly) <= 4:
        return poly
    closed = _close(poly[0], poly[-1], 1e-9)
    body = poly[:-1] if closed else poly
    simple = _rdp(body, tol)
    if closed and simple:
        simple = simple + [simple[0]]
    return simple


def outline_waypoints(binary: np.ndarray, cfg: PlotConfig) -> list[tuple[float, float, float]]:
    """提取外轮廓，按最近邻排序，抽稀后逐轮廓落笔绘制。"""
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

    tol = cfg.simplify_px * cfg.pixel_to_mm
    points: list[tuple[float, float, float]] = []
    for poly in ordered:
        poly = _simplify_loop(poly, tol)
        x0, y0 = poly[0]
        closed = _close(poly[0], poly[-1], 1e-9)
        points.append((x0, y0, cfg.z_up))
        points.append((x0, y0, cfg.z_down))
        for x, y in poly[1:]:
            points.append((x, y, cfg.z_down))
        if not closed:
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
