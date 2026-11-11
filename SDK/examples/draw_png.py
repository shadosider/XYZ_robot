#!/usr/bin/env python3
"""简单测试脚本：PNG -> 二值化 -> 轨迹 -> TCP 下发到 PLC。

用法示例::

    # 离线预览（不连接 PLC，仅生成轨迹并打印帧结构）
    python draw_png.py --input drawing.png --dry-run

    # 端到端（连接 PLC / 假 PLC）
    python draw_png.py --input drawing.png --host 192.168.1.10 --port 2000

    # 线稿模式 + 缩放
    python draw_png.py --input logo.png --strategy outline --scale 0.05
"""

from __future__ import annotations

import argparse
import os
import struct
import sys

# 允许从任意目录直接运行本脚本（把 SDK 根目录加入 sys.path）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from robonix_sdk import (
    RobonixClient,
    PlotConfig,
    build_frame,
    load_binary,
    to_waypoints,
)


def make_config(args: argparse.Namespace) -> PlotConfig:
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="PNG -> XYZ 绘图轨迹 -> TCP 下发")
    p.add_argument("--input", required=True, help="输入 PNG 路径")
    p.add_argument("--host", default="127.0.0.1", help="PLC 地址")
    p.add_argument("--port", type=int, default=2000, help="PLC 端口")
    p.add_argument("--strategy", choices=["raster", "outline"], default="raster")
    p.add_argument("--scale", type=float, default=0.1, help="像素->毫米 比例")
    p.add_argument("--offset-x", type=float, default=0.0)
    p.add_argument("--offset-y", type=float, default=0.0)
    p.add_argument("--z-up", type=float, default=5.0)
    p.add_argument("--z-down", type=float, default=0.0)
    p.add_argument("--vel", type=float, default=20.0)
    p.add_argument("--travel-vel", type=float, default=40.0)
    p.add_argument("--acc", type=float, default=300.0)
    p.add_argument("--dec", type=float, default=300.0)
    p.add_argument("--jerk", type=float, default=3000.0)
    p.add_argument("--threshold", type=float, default=None, help="二值化阈值，默认 Otsu")
    p.add_argument("--invert", action="store_true", help="亮像素视为墨迹")
    p.add_argument("--flip-x", action="store_true")
    p.add_argument("--flip-y", action="store_true")
    p.add_argument("--down-step", type=int, default=1, help="落笔抽稀步长")
    p.add_argument("--dry-run", action="store_true", help="只生成轨迹，不连接 PLC")
    p.add_argument("--no-home", action="store_true", help="下发前不执行回零")
    p.add_argument("--no-enable", action="store_true", help="下发前不执行使能")
    args = p.parse_args(argv)

    cfg = make_config(args)
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
        frame = build_frame(0x01, 0, struct.pack("<4d", x0, y0, z0, cfg.vel))
        print("示例 MOVE_LINEAR 帧(%dB): %s" % (len(frame), frame.hex()))

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
