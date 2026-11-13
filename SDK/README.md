# Robonix XYZ 绘图 SDK

把 PNG 图片二值化、转成 XYZ 三轴绘图轨迹，通过 TCP 下发到 PLC（MatrixBox
MX4001），由 PLC 轴组功能块驱动三轴机器人绘图。

## 目录

```
SDK/
├── toolbox.txt               # PLC 功能块说明书（保留）
├── robonix_sdk/              # SDK 包
│   ├── protocol.py           # 二进制帧编解码 + CRC16
│   ├── client.py             # RobonixClient（TCP 客户端）
│   ├── image.py              # PNG 读取 + Otsu 二值化
│   └── trajectory.py         # 二值图 -> 路径点（raster / outline）
├── examples/
│   ├── draw_png.py           # 简单测试脚本
│   └── mock_plc.py           # 无硬件联调的假 PLC
└── docs/
    ├── protocol.md           # 通信协议（核心文档）
    └── plc_wiring.md         # PLC 侧参考接线
```

依赖：`numpy` + `Pillow`（标准库 `socket` / `struct`）。

## 快速上手

### 1. 离线预览（无硬件）

```bash
python examples/draw_png.py --input drawing.png --dry-run
```

生成轨迹并打印帧结构，不连接 PLC。

### 2. 端到端（先起假 PLC，再连）

```bash
# 终端 A：假 PLC
python examples/mock_plc.py --port 2000

# 终端 B：下发轨迹
python examples/draw_png.py --input drawing.png --host 127.0.0.1 --port 2000
```

### 3. 连接真实 PLC

```bash
python examples/draw_png.py --input drawing.png --host <PLC_IP> --port 2000
```

## 文字书写

```bash
# 预览
python examples/write_text.py "Hello 123" --dry-run

# 写到 PLC（写完自动回零）
python examples/write_text.py "Hello 123" --host 192.168.33.130 --port 2000

# 调整字号、重置到左上角
python examples/write_text.py "ABC" --font-size-mm 15 --reset
```

- 书写区：`X 0~250 mm`（25 cm）、`Y 0~150 mm`（15 cm），原点左下角，超出直接报错；
- `z_up=0` 抬笔，`z_down=-30` 落笔（需按实机实测）；
- 默认从左上角开始，自动换行，游标跨次保存；
- 支持大小写英文、数字、空格和换行；
- 详细说明见 `docs/text.md`。

## 常用参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--strategy` | `raster` | `raster` 色块填充（每段墨迹只发首尾两点）/ `outline` 线稿轮廓 |
| `--scale` | `0.1` | 像素→毫米比例 |
| `--z-up` / `--z-down` | `5.0` / `0.0` | 抬笔 / 落笔高度 (mm) |
| `--vel` | `20.0` | 落笔速度 |
| `--threshold` | Otsu | 手动二值化阈值（0~255） |
| `--invert` | 关 | 亮像素视为墨迹 |
| `--flip-x` / `--flip-y` | 关 | 坐标轴翻转 |
| `--simplify-px` | `0.5` | 轮廓抽稀容差（像素），0 = 不抽稀 |

## 编程接口

```python
from robonix_sdk import RobonixClient, PlotConfig, load_binary, to_waypoints

binary = load_binary("drawing.png")
cfg = PlotConfig(pixel_to_mm=0.1, z_up=5.0, z_down=0.0, strategy="raster")
points = to_waypoints(binary, cfg)

with RobonixClient("192.168.1.10") as plc:
    plc.set_param(cfg.vel, cfg.acc, cfg.dec, cfg.jerk, cfg.z_up, cfg.z_down)
    plc.enable(True)
    plc.home()
    plc.draw_waypoints(points, vel=cfg.vel)
```

更多协议细节见 `docs/protocol.md`，PLC 侧接线见 `docs/plc_wiring.md`。
