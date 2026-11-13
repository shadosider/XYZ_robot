#!/usr/bin/env python3
"""PNG -> 二值化 -> 轨迹 -> TCP 下发。图像自动在纸上居中，长宽可指定。

用法::

    # 指定宽度，高度按比例，居中（离线预览）
    python draw_png.py --input logo.png --width-mm 120 --dry-run

    # 指定长宽，等比缩放后居中
    python draw_png.py --input test_drawing.png --width-mm 120 --height-mm 80 --dry-run

    # 不指定长宽：铺满纸张可用区（留边距）
    python draw_png.py --input test_drawing.png --dry-run

    # 联机
    python draw_png.py --input test_drawing.png --width-mm 120 --height-mm 80 --host 192.168.1.135
"""

from __future__ import annotations

import argparse
import os
import struct
import sys

import numpy as np

# 允许从任意目录直接运行本脚本（把 SDK 根目录加入 sys.path）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from robonix_sdk import (
    RobonixClient,
    PlotConfig,
    build_frame,
    load_binary,
    to_waypoints,
)


def fit_and_center(
    img_w_px: int,
    img_h_px: int,
    paper_w: float,
    paper_h: float,
    margin: float,
    width_mm: float | None,
    height_mm: float | None,
    stretch: bool = False,
) -> tuple[float, float, float, float, float]:
    """算出 (px_to_mm, offset_x, offset_y, draw_w, draw_h)，图像在纸上居中。

    - 只给宽或只给高：另一边按比例跟着走
    - 宽高都给了：等比缩放到刚好放得进这个框（除非 stretch）
    - 都不给：铺满纸张可用区
    - 无论哪种，超出可用区都会自动缩回来
    """
    usable_w = paper_w - 2.0 * margin
    usable_h = paper_h - 2.0 * margin
    if usable_w <= 0 or usable_h <= 0:
        raise ValueError("边距太大，纸张没有可用区域")

    box_w = float(width_mm) if width_mm else usable_w
    box_h = float(height_mm) if height_mm else usable_h

    if width_mm and height_mm and stretch:
        # 强行拉伸到指定长宽（会变形）
        scale_x = box_w / img_w_px
        scale_y = box_h / img_h_px
        draw_w = box_w
        draw_h = box_h
        offset_x = (paper_w - draw_w) / 2.0
        offset_y = (paper_h - draw_h) / 2.0
        # 轨迹层只有一个等比 scale，这里不支持非等比，交给调用方报错
        if abs(scale_x - scale_y) > 1e-9:
            raise ValueError("--stretch 需要非等比缩放，当前轨迹层只支持等比")
        return scale_x, offset_x, offset_y, draw_w, draw_h

    if width_mm and height_mm:
        scale = min(box_w / img_w_px, box_h / img_h_px)
    elif width_mm:
        scale = box_w / img_w_px
    elif height_mm:
        scale = box_h / img_h_px
    else:
        scale = min(box_w / img_w_px, box_h / img_h_px)

    draw_w = img_w_px * scale
    draw_h = img_h_px * scale

    # 超出可用区就整体缩回来
    over = max(draw_w / usable_w, draw_h / usable_h, 1.0)
    if over > 1.0:
        scale /= over
        draw_w /= over
        draw_h /= over

    offset_x = (paper_w - draw_w) / 2.0
    offset_y = (paper_h - draw_h) / 2.0
    return scale, offset_x, offset_y, draw_w, draw_h


def make_config(args: argparse.Namespace, binary: np.ndarray) -> tuple[PlotConfig, dict]:
    img_h_px, img_w_px = binary.shape
    scale, off_x, off_y, draw_w, draw_h = fit_and_center(
        img_w_px,
        img_h_px,
        args.paper_width,
        args.paper_height,
        args.margin,
        args.width_mm,
        args.height_mm,
        args.stretch,
    )
    cfg = PlotConfig(
        pixel_to_mm=scale,
        offset_x=off_x,
        offset_y=off_y,
        z_up=args.z_up,
        z_down=args.z_down,
        vel=args.vel,
        acc=args.acc,
        dec=args.dec,
        jerk=args.jerk,
        flip_x=args.flip_x,
        flip_y=False,
        strategy=args.strategy,
        down_step=args.down_step,
    )
    info = {
        "img_px": (img_w_px, img_h_px),
        "scale": scale,
        "draw_w": draw_w,
        "draw_h": draw_h,
        "offset_x": off_x,
        "offset_y": off_y,
    }
    return cfg, info


def render_preview(
    points, paper_w: float, paper_h: float, margin: float, path: str, px_per_mm: float = 4.0
) -> None:
    """把轨迹画成 PNG，方便确认是否居中。"""
    from PIL import Image, ImageDraw

    pad = 30
    w = int(paper_w * px_per_mm) + pad * 2
    h = int(paper_h * px_per_mm) + pad * 2
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)

    def to_px(x: float, y: float) -> tuple[float, float]:
        return pad + x * px_per_mm, pad + (paper_h - y) * px_per_mm

    d.rectangle([to_px(0, paper_h), to_px(paper_w, 0)], outline=(180, 180, 180))
    d.rectangle(
        [to_px(margin, paper_h - margin), to_px(paper_w - margin, margin)],
        outline=(220, 220, 220),
    )
    # 中心十字
    cx, cy = to_px(paper_w / 2, paper_h / 2)
    d.line([cx - 8, cy, cx + 8, cy], fill=(240, 170, 170), width=1)
    d.line([cx, cy - 8, cx, cy + 8], fill=(240, 170, 170), width=1)

    for a, b in zip(points, points[1:]):
        if a[2] == b[2] == 0.0:
            d.line([to_px(a[0], a[1]), to_px(b[0], b[1])], fill=(20, 20, 20), width=2)
        else:
            d.line([to_px(a[0], a[1]), to_px(b[0], b[1])], fill=(205, 225, 255), width=1)
    img.save(path)


def print_progress(done: int, total: int, last: tuple[float, float, float]) -> None:
    if done == 1 or done == total or done % 200 == 0:
        print(
            "  进度 %d/%d  最后点 (%.3f, %.3f, %.3f)"
            % (done, total, last[0], last[1], last[2]),
            flush=True,
        )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="PNG -> XYZ 绘图轨迹 -> TCP 下发")
    p.add_argument("--input", required=True, help="输入 PNG 路径")
    p.add_argument("--host", default="192.168.1.135", help="PLC 地址")
    p.add_argument("--port", type=int, default=2000, help="PLC 端口")
    p.add_argument("--strategy", choices=["raster", "outline"], default="raster")

    # ---- 纸张与尺寸 ----
    p.add_argument("--paper-width", type=float, default=200.0, help="纸张宽 (mm)")
    p.add_argument("--paper-height", type=float, default=160.0, help="纸张高 (mm)")
    p.add_argument("--margin", type=float, default=5.0, help="四周边距 (mm)")
    p.add_argument("--width-mm", type=float, default=None, help="图形宽度 (mm)，不填则铺满可用区")
    p.add_argument("--height-mm", type=float, default=None, help="图形高度 (mm)，不填则按比例")
    p.add_argument("--stretch", action="store_true", help="强行拉伸到指定长宽（目前不支持非等比）")

    # ---- 笔与运动 ----
    p.add_argument("--z-up", type=float, default=-20.0)
    p.add_argument("--z-down", type=float, default=-35.5)
    p.add_argument("--vel", type=float, default=30.0)
    p.add_argument("--acc", type=float, default=300.0)
    p.add_argument("--dec", type=float, default=300.0)
    p.add_argument("--jerk", type=float, default=3000.0)

    # ---- 图像处理 ----
    p.add_argument("--threshold", type=float, default=None, help="二值化阈值，默认 Otsu")
    p.add_argument("--invert", action="store_true", help="亮像素视为墨迹")
    p.add_argument("--flip-x", action="store_true", help="左右镜像")
    p.add_argument("--upside-down", action="store_true", help="上下颠倒（默认已按正向显示）")
    p.add_argument("--down-step", type=int, default=1, help="落笔抽稀步长")

    # ---- 运行控制 ----
    p.add_argument("--dry-run", action="store_true", help="只生成轨迹，不连接 PLC")
    p.add_argument("--preview", default=None, help="把轨迹渲染成 PNG 保存到该路径")
    p.add_argument("--no-home", action="store_true", help="下发前不执行回零")
    p.add_argument("--no-enable", action="store_true", help="下发前不执行使能")
    args = p.parse_args(argv)

    binary = load_binary(args.input, threshold=args.threshold, invert=args.invert)
    if not args.upside_down:
        # 图像第 0 行是顶部，纸面 +Y 向上，翻过来才是正向
        binary = np.ascontiguousarray(binary[::-1])

    cfg, info = make_config(args, binary)
    points = to_waypoints(binary, cfg)

    img_w_px, img_h_px = info["img_px"]
    ink = int(binary.sum())
    down = sum(1 for pt in points if abs(pt[2] - cfg.z_down) < 1e-6)
    up = len(points) - down

    print("图像    : %d x %d px，墨迹 %d px，策略=%s" % (img_w_px, img_h_px, ink, args.strategy))
    print(
        "纸张    : %.1f x %.1f mm，边距 %.1f mm"
        % (args.paper_width, args.paper_height, args.margin)
    )
    print(
        "图形    : %.1f x %.1f mm  缩放 %.4f mm/px"
        % (info["draw_w"], info["draw_h"], info["scale"])
    )
    print(
        "居中左下: (%.2f, %.2f)  右上 (%.2f, %.2f)"
        % (
            info["offset_x"],
            info["offset_y"],
            info["offset_x"] + info["draw_w"],
            info["offset_y"] + info["draw_h"],
        )
    )
    print("轨迹点数: %d（落笔 %d / 抬笔 %d）" % (len(points), down, up))
    if points:
        xs = [pt[0] for pt in points]
        ys = [pt[1] for pt in points]
        print("实际边界: X[%.2f, %.2f]  Y[%.2f, %.2f]" % (min(xs), max(xs), min(ys), max(ys)))
        x0, y0, z0 = points[0]
        frame = build_frame(0x01, 0, struct.pack("<4d", x0, y0, z0, cfg.vel))
        print("示例帧  : %dB %s" % (len(frame), frame.hex()))

    if args.preview:
        render_preview(points, args.paper_width, args.paper_height, args.margin, args.preview)
        print("预览已保存: %s" % os.path.abspath(args.preview))

    if args.dry_run:
        print("dry-run 结束（未连接 PLC）")
        return 0

    with RobonixClient(args.host, args.port) as plc:
        print("已连接 %s:%d" % (args.host, args.port))
        plc.set_param(cfg.vel, cfg.acc, cfg.dec, cfg.jerk, cfg.z_up, cfg.z_down)
        if not args.no_enable:
            plc.enable(True)
        if not args.no_home:
            plc.home()
        plc.draw_waypoints(points, vel=cfg.vel, on_progress=print_progress)
        x, y, z, busy = plc.get_position()
        print("结束位置: x=%.3f y=%.3f z=%.3f busy=%d" % (x, y, z, busy))

    print("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
