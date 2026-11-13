#!/usr/bin/env python3
"""绘图测试脚本：PNG 图片或 Hershey 单线文字 -> 轨迹 -> TCP 下发到 PLC。

用法::

    # PNG -> 轨迹（先离线预览，再连 PLC / 假 PLC）
    python tests/draw.py png --input drawing.png --dry-run
    python tests/draw.py png --input drawing.png --host 127.0.0.1 --port 2000
    python tests/draw.py png --input logo.png --strategy outline --scale 0.05

    # 文字书写（写完自动回零）
    python tests/draw.py text "Hello 123" --dry-run
    python tests/draw.py text "Hello 123" --host 192.168.33.130 --port 2000
    python tests/draw.py text "ABC" --font-size-mm 15 --reset
"""

from __future__ import annotations

import argparse
import os
import sys

# 允许从任意目录直接运行本脚本（把 SDK 根目录加入 sys.path）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from robonix_sdk import (
    PlotConfig,
    RobonixClient,
    build_frame,
    load_binary,
    to_waypoints,
)
from robonix_sdk import protocol as proto
from robonix_sdk.text import TextConfig, TextOverflowError, TextWriter


# ---------------------------------------------------------------------------
# 公共参数
# ---------------------------------------------------------------------------
def add_common_args(p: argparse.ArgumentParser, z_down: float) -> None:
    p.add_argument("--host", default="192.168.33.130", help="PLC 地址")
    p.add_argument("--port", type=int, default=2000, help="PLC 端口")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--z-up", type=float, default=0.0, help="抬笔高度 (mm)")
    p.add_argument("--z-down", type=float, default=z_down, help="落笔高度 (mm)")
    p.add_argument("--vel", type=float, default=20.0, help="运动速度 (mm/s)")
    p.add_argument("--acc", type=float, default=300.0)
    p.add_argument("--dec", type=float, default=300.0)
    p.add_argument("--jerk", type=float, default=3000.0)
    p.add_argument("--dry-run", action="store_true", help="只生成轨迹，不连接 PLC")
    p.add_argument("--no-home", action="store_true", help="不执行回零")


# ---------------------------------------------------------------------------
# PNG -> 轨迹
# ---------------------------------------------------------------------------
def make_plot_config(args: argparse.Namespace) -> PlotConfig:
    return PlotConfig(
        pixel_to_mm=args.scale,
        offset_x=args.offset_x,
        offset_y=args.offset_y,
        z_up=args.z_up,
        z_down=args.z_down,
        vel=args.vel,
        travel_vel=args.travel_vel,
        acc=args.acc,
        dec=args.dec,
        jerk=args.jerk,
        flip_x=args.flip_x,
        flip_y=args.flip_y,
        strategy=args.strategy,
        down_step=args.down_step,
    )


def print_progress(done: int, total: int, last: tuple[float, float, float]) -> None:
    if done == 1 or done == total or done % 200 == 0:
        print(
            "  进度 %d/%d  最后点 (%.3f, %.3f, %.3f)"
            % (done, total, last[0], last[1], last[2]),
            flush=True,
        )


def run_png(args: argparse.Namespace) -> int:
    cfg = make_plot_config(args)
    binary = load_binary(args.input, threshold=args.threshold, invert=args.invert)
    points = to_waypoints(binary, cfg)

    h, w = binary.shape
    ink = int(binary.sum())
    down = sum(1 for pt in points if abs(pt[2] - cfg.z_down) < 1e-6)
    up = len(points) - down
    print(
        "图像 %dx%d px  墨迹 %d px  阈值(Otsu/手动)  策略=%s"
        % (w, h, ink, args.strategy)
    )
    print(
        "轨迹点数 %d (落笔 %d / 抬笔 %d)  scale=%.4g mm/px"
        % (len(points), down, up, cfg.pixel_to_mm)
    )

    # 打印首帧结构便于核对协议
    if points:
        x0, y0, z0 = points[0]
        frame = build_frame(proto.CMD_MOVE_LINEAR, 0, proto.pack_move_linear(x0, y0, z0))
        print("示例 MOVE_LINEAR 帧(%dB): %s" % (len(frame), frame.hex()))

    if args.dry_run:
        print("dry-run 结束（未连接 PLC）")
        return 0

    with RobonixClient(args.host, args.port, timeout=args.timeout) as plc:
        print("已连接 %s:%d" % (args.host, args.port))
        plc.set_param(cfg.vel, cfg.acc, cfg.dec, cfg.jerk, cfg.z_up, cfg.z_down)
        if not args.no_home:
            plc.home()
        plc.draw_waypoints(points, on_progress=print_progress)

    print("完成")
    return 0


# ---------------------------------------------------------------------------
# 文字书写
# ---------------------------------------------------------------------------
def make_text_config(args: argparse.Namespace) -> TextConfig:
    return TextConfig(
        font_size_mm=args.font_size_mm,
        line_spacing=args.line_spacing,
        letter_spacing_mm=args.letter_spacing,
        space_advance_mm=args.space_advance,
        work_width_mm=args.work_width,
        work_height_mm=args.work_height,
        margin_mm=args.margin,
        z_up=args.z_up,
        z_down=args.z_down,
        vel=args.vel,
        acc=args.acc,
        dec=args.dec,
        jerk=args.jerk,
    )


def print_text_plan(text: str, points, cursor_old, cursor_new, cfg: TextConfig) -> None:
    print("文字    : %r" % text)
    print(
        "字号    : %.2f mm, 行高 %.2f mm, 字间距 %.2f mm"
        % (cfg.font_size_mm, cfg.line_height_mm, cfg.letter_spacing_mm)
    )
    print(
        "范围    : X[0, %.1f] Y[0, %.1f], z_up=%.1f z_down=%.1f"
        % (cfg.work_width_mm, cfg.work_height_mm, cfg.z_up, cfg.z_down)
    )
    print("起点    : (%.3f, %.3f)" % (cursor_old.x_mm, cursor_old.y_mm))

    if points:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        print(
            "轨迹点  : %d, 边界 X[%.3f, %.3f] Y[%.3f, %.3f]"
            % (len(points), min(xs), max(xs), min(ys), max(ys))
        )
        print("首点    : (%.3f, %.3f, %.3f)" % points[0])
        print("末点    : (%.3f, %.3f, %.3f)" % points[-1])
    else:
        print("轨迹点  : 0（空文本/纯空格）")

    print("新游标  : (%.3f, %.3f)" % (cursor_new.x_mm, cursor_new.y_mm))


def run_text(args: argparse.Namespace) -> int:
    cfg = make_text_config(args)
    writer = TextWriter(cfg, state_path=args.state, reset=args.reset)
    old_cursor = writer.cursor

    try:
        points, new_cursor = writer.plan(args.text)
    except TextOverflowError as e:
        print("[OVERFLOW] %s" % e, file=sys.stderr)
        return 2
    except ValueError as e:
        print("[ERROR] %s" % e, file=sys.stderr)
        return 2

    print_text_plan(args.text, points, old_cursor, new_cursor, cfg)

    if args.dry_run:
        print("dry-run 结束（未连接 PLC，未保存游标）")
        return 0

    with RobonixClient(args.host, args.port, timeout=args.timeout) as plc:
        print("已连接 %s:%d" % (args.host, args.port))
        writer.write(plc, args.text, home=not args.no_home)

    print("完成，游标已保存")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="XYZ 绘图测试脚本（PNG / 文字 -> PLC）")
    sub = p.add_subparsers(dest="mode", required=True)

    png = sub.add_parser("png", help="PNG 图片 -> 二值化 -> 轨迹")
    add_common_args(png, z_down=-30.0)
    png.add_argument("--input", required=True, help="输入 PNG 路径")
    png.add_argument("--strategy", choices=["raster", "outline"], default="raster")
    png.add_argument("--scale", type=float, default=0.1, help="像素->毫米 比例")
    png.add_argument("--offset-x", type=float, default=0.0)
    png.add_argument("--offset-y", type=float, default=0.0)
    png.add_argument("--travel-vel", type=float, default=40.0, help="抬笔移动速度")
    png.add_argument("--threshold", type=float, default=None, help="二值化阈值，默认 Otsu")
    png.add_argument("--invert", action="store_true", help="亮像素视为墨迹")
    png.add_argument("--flip-x", action="store_true")
    png.add_argument("--flip-y", action="store_true")
    png.add_argument("--down-step", type=int, default=1, help="落笔抽稀步长")

    txt = sub.add_parser("text", help="Hershey 单线文字")
    add_common_args(txt, z_down=-25.0)
    txt.add_argument("text", help="要写的文字（英文、数字、空格、换行）")
    txt.add_argument("--font-size-mm", type=float, default=10.0, help="大写字母高度 (mm)")
    txt.add_argument("--line-spacing", type=float, default=1.5, help="行高倍数")
    txt.add_argument("--letter-spacing", type=float, default=0.5, help="字间距 (mm)")
    txt.add_argument("--space-advance", type=float, default=None, help="空格宽度 (mm)")
    txt.add_argument("--work-width", type=float, default=280.0)
    txt.add_argument("--work-height", type=float, default=160.0)
    txt.add_argument("--margin", type=float, default=0.0)
    txt.add_argument("--state", default=None, help="游标状态文件路径")
    txt.add_argument("--reset", action="store_true", help="重置游标到左上角")

    args = p.parse_args(argv)
    if args.mode == "png":
        return run_png(args)
    return run_text(args)


if __name__ == "__main__":
    sys.exit(main())