#!/usr/bin/env python3
"""在 XYZ 绘图机上书写英文/数字（Hershey 单线字体）。

坐标：纸张左下角 (0,0)，+X 向右，+Y 向上；
默认从左上角开始，自动换行，写完自动回零。

用法::

    # 预览轨迹，不连接 PLC
    python write_text.py "Hello 123" --dry-run

    # 写到 PLC
    python write_text.py "Hello 123" --host 192.168.33.130 --port 2000

    # 调整字号 / 重置到左上角
    python write_text.py "ABC" --font-size-mm 15 --reset

    # 指定游标状态文件
    python write_text.py "ABC" --state ./writer_state.json
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from robonix_sdk import RobonixClient
from robonix_sdk.text import (
    TextConfig,
    TextOverflowError,
    TextWriter,
    default_cursor,
    layout_text,
)


def build_config(args: argparse.Namespace) -> TextConfig:
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


def print_plan(text: str, points, cursor_old, cursor_new, cfg: TextConfig) -> None:
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Hershey 单线字体书写")
    ap.add_argument("text", help="要写的文字（英文、数字、空格、换行）")
    ap.add_argument("--host", default="192.168.33.130")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--timeout", type=float, default=30.0)

    ap.add_argument("--font-size-mm", type=float, default=10.0, help="大写字母高度 (mm)")
    ap.add_argument("--line-spacing", type=float, default=1.5, help="行高倍数")
    ap.add_argument("--letter-spacing", type=float, default=0.5, help="字间距 (mm)")
    ap.add_argument("--space-advance", type=float, default=None, help="空格宽度 (mm)")
    ap.add_argument("--work-width", type=float, default=280.0)
    ap.add_argument("--work-height", type=float, default=160.0)
    ap.add_argument("--margin", type=float, default=0.0)

    ap.add_argument("--z-up", type=float, default=0.0)
    ap.add_argument("--z-down", type=float, default=-25.0)
    ap.add_argument("--vel", type=float, default=20.0)
    ap.add_argument("--acc", type=float, default=300.0)
    ap.add_argument("--dec", type=float, default=300.0)
    ap.add_argument("--jerk", type=float, default=3000.0)

    ap.add_argument("--state", default=None, help="游标状态文件路径")
    ap.add_argument("--reset", action="store_true", help="重置游标到左上角")
    ap.add_argument("--dry-run", action="store_true", help="只排版预览，不连接 PLC")
    ap.add_argument("--no-home", action="store_true", help="写完后不回零")
    args = ap.parse_args(argv)

    cfg = build_config(args)
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

    print_plan(args.text, points, old_cursor, new_cursor, cfg)

    if args.dry_run:
        print("dry-run 结束（未连接 PLC，未保存游标）")
        return 0

    with RobonixClient(args.host, args.port, timeout=args.timeout) as plc:
        print("已连接 %s:%d" % (args.host, args.port))
        writer.write(plc, args.text, home=not args.no_home)

    print("完成，游标已保存")
    return 0


if __name__ == "__main__":
    sys.exit(main())