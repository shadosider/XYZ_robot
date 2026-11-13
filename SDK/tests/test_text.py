"""text 模块回归测试。

运行：``python -m pytest tests/test_text.py -q`` 或 ``python tests/test_text.py``。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from robonix_sdk import protocol as proto
from robonix_sdk.text import (
    Cursor,
    TextConfig,
    TextOverflowError,
    TextWriter,
    default_cursor,
    glyph_strokes,
    layout_text,
    load_cursor,
    save_cursor,
)

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def test_all_glyphs_exist():
    for ch in ALPHABET:
        strokes = glyph_strokes(ch)
        assert strokes, "glyph %r has no strokes" % ch
        for stroke in strokes:
            assert len(stroke) >= 2


def test_default_cursor_top_left():
    cfg = TextConfig()
    cur = default_cursor(cfg)
    assert cur.x_mm == 0.0
    # 按字形最高点对齐（i/j 的点在 y=0，略高于大写字母顶部 y=1）
    assert abs(cur.y_mm - (cfg.work_height_mm - 22.0 * cfg.scale)) < 1e-9


def test_single_char_layout():
    cfg = TextConfig()
    pts, cur = layout_text("A", cfg, default_cursor(cfg))
    assert pts
    assert {p[2] for p in pts} == {cfg.z_up, cfg.z_down}
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    assert min(xs) >= -1e-6
    assert max(xs) <= cfg.work_width_mm + 1e-6
    assert min(ys) >= -1e-6
    assert max(ys) <= cfg.work_height_mm + 1e-6
    assert max(ys) > cfg.work_height_mm - cfg.font_size_mm   # 接近顶部
    assert cur.x_mm > 0


def test_font_size_scaling():
    cfg5 = TextConfig(font_size_mm=5.0)
    cfg10 = TextConfig(font_size_mm=10.0)
    pts5, _ = layout_text("A", cfg5, default_cursor(cfg5))
    pts10, _ = layout_text("A", cfg10, default_cursor(cfg10))
    h5 = max(p[1] for p in pts5) - min(p[1] for p in pts5)
    h10 = max(p[1] for p in pts10) - min(p[1] for p in pts10)
    assert abs(h10 / h5 - 2.0) < 0.02


def test_auto_wrap():
    cfg = TextConfig()
    pts, cur = layout_text("M" * 80, cfg, default_cursor(cfg))
    assert pts
    assert max(p[0] for p in pts) <= cfg.work_width_mm + 1e-6
    assert cur.y_mm < default_cursor(cfg).y_mm
    assert min(p[1] for p in pts) >= -1e-6


def test_overflow():
    cfg = TextConfig(font_size_mm=200.0)
    try:
        layout_text("HELLO", cfg, default_cursor(cfg))
    except TextOverflowError:
        return
    raise AssertionError("expected TextOverflowError")


def test_newline_and_space():
    cfg = TextConfig()
    cur0 = default_cursor(cfg)
    _, cur_nl = layout_text("A\nB", cfg, cur0)
    assert abs(cur_nl.y_mm - (cur0.y_mm - cfg.line_height_mm)) < 1e-6
    assert cur_nl.x_mm > 0

    _, cur_ab = layout_text("AB", cfg, cur0)
    _, cur_a_b = layout_text("A B", cfg, cur0)
    assert cur_a_b.x_mm > cur_ab.x_mm


def test_unsupported_character():
    cfg = TextConfig()
    try:
        layout_text("A!", cfg, default_cursor(cfg))
    except ValueError:
        return
    raise AssertionError("expected ValueError for unsupported character")


def test_state_roundtrip():
    cfg = TextConfig()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "state.json")
        save_cursor(path, Cursor(12.5, 140.0))
        cur = load_cursor(path, cfg)
        assert abs(cur.x_mm - 12.5) < 1e-9
        assert abs(cur.y_mm - 140.0) < 1e-9

        # 缺失/损坏状态回到默认起点
        assert load_cursor(os.path.join(d, "missing.json"), cfg) == default_cursor(cfg)


def test_frame_size():
    payload = proto.pack_move_linear(1.0, 2.0, -30.0)
    frame = proto.build_frame(proto.CMD_MOVE_LINEAR, 0, payload)
    assert len(payload) == 24
    assert len(frame) == 32
    assert len(frame) <= 512


class FakeClient:
    def __init__(self):
        self.calls = []
        self.points = []

    def set_param(self, *args):
        self.calls.append(("set_param",) + args)

    def draw_waypoints(self, points, on_progress=None):
        self.points = list(points)
        self.calls.append(("draw_waypoints", len(self.points)))

    def home(self):
        self.calls.append(("home",))


def test_writer_persists_and_homes():
    cfg = TextConfig()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "state.json")
        writer = TextWriter(cfg, state_path=path, reset=True)
        fake = FakeClient()
        new_cursor = writer.write(fake, "AB")
        assert fake.points
        assert any(c[0] == "home" for c in fake.calls)
        assert load_cursor(path, cfg) == new_cursor

        # 下一次从上次结束的位置开始
        writer2 = TextWriter(cfg, state_path=path)
        assert writer2.cursor == new_cursor


def test_all_points_inside_work_area():
    cfg = TextConfig()
    pts, _ = layout_text("Hello 123", cfg, default_cursor(cfg))
    for x, y, z in pts:
        assert 0.0 - 1e-6 <= x <= cfg.work_width_mm + 1e-6
        assert 0.0 - 1e-6 <= y <= cfg.work_height_mm + 1e-6
        assert z in (cfg.z_up, cfg.z_down)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
    print("all tests passed")