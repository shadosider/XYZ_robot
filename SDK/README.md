# Robonix XYZ 绘图 SDK

把 PNG 图片二值化、转成 XYZ 三轴绘图轨迹，通过 TCP 下发到 PLC（MatrixBox
MX4001），由 PLC 轴组功能块驱动三轴机器人绘图。

## 目录

```
SDK/
├── robonix_sdk/              # SDK 包
│   ├── protocol.py           # 二进制帧编解码 + CRC16
│   ├── client.py             # RobonixClient（TCP 客户端）
│   ├── image.py              # PNG 读取 + Otsu 二值化
│   ├── trajectory.py         # 二值图 -> 路径点（raster / outline）
│   ├── text.py               # 单线文字排版（Hershey）
│   └── hershey_simplex.py    # Hershey Simplex 字形数据
├── tests/                    # 测试与示例脚本
│   ├── draw.py               # 绘图测试脚本（png / text 子命令）
│   ├── mock_plc.py           # 无硬件联调的假 PLC
│   ├── plc_framing_test.py   # PLC 分帧 / 协议测试
│   └── test_text.py          # 文字模块单元测试
└── docs/
    ├── protocol.md           # 通信协议（核心文档）
    ├── plc_wiring.md         # PLC 侧参考接线
    └── text.md               # 文字书写说明
```

依赖：`numpy` + `Pillow`（标准库 `socket` / `struct`）。

## 快速上手

### 1. 离线预览（无硬件）

```bash
python tests/draw.py png --input drawing.png --dry-run
```

生成轨迹并打印帧结构，不连接 PLC。PNG 模式还会打印轨迹实际范围，并与工作区
（默认 `280×160 mm`，可用 `--work-width/--work-height/--margin` 调整）比较：
dry-run 越界只告警，实际下发会拒绝发送。

### 2. 端到端（先起假 PLC，再连）

```bash
# 终端 A：假 PLC
python tests/mock_plc.py --port 2000

# 终端 B：下发轨迹
python tests/draw.py png --input drawing.png --host 127.0.0.1 --port 2000
```

绘制结束后会自动执行 `GroupHome`；不需要时加 `--no-home`（会同时跳过绘制前/后的回零）。

### 3. 连接真实 PLC

```bash
python tests/draw.py png --input drawing.png --host <PLC_IP> --port 2000
```

## 测试

```bash
# 协议分帧/粘包测试（先启动 mock_plc 或连接真实 PLC）
python tests/plc_framing_test.py --host 127.0.0.1 --port 2000 --run-all
```

## 文字书写

```bash
# 预览
python tests/draw.py text "Hello 123" --dry-run

# 写到 PLC（写完自动回零）
python tests/draw.py text "Hello 123" --host 192.168.33.130 --port 2000

# 调整字号、重置到左上角
python tests/draw.py text "ABC" --font-size-mm 15 --reset
```

- 纸张：`X 0~280 mm`，`Y 0~160 mm`，原点左下角；
- `z_up=-40` 抬笔，`z_down=-50` 落笔；
- 默认从左上角开始，自动换行，游标跨次保存；
- 支持大小写英文、数字、空格和换行；
- 详细说明见 `docs/text.md`。

## 常用参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--strategy` | `raster` | `raster` 色块填充 / `outline` 线稿轮廓 |
| `--scale` | `0.1` | 像素→毫米比例 |
| `--offset-x` / `--offset-y` | `0.0` | 图像左上角的世界坐标偏移 (mm) |
| `--work-width` / `--work-height` | `280` / `160` | 工作区范围 (mm)，PNG 下发前检查 |
| `--margin` | `0` | 工作区四周留白 (mm) |
| `--debug-binary [PATH]` | 关 | 保存二值化图片（省略 PATH 时输出 `<input>_binary.png`） |
| `--simplify-mm` | `0.05` | 轨迹 RDP 简化容差 (mm)，`0` 关闭；大幅减少点数 |

> 点数过多时：优先调大 `--simplify-mm`（如 `0.1~0.5`，单位 mm）；复杂图片也可先降低
> 图片分辨率，并同步放大 `--scale`，保持实际绘制尺寸不变。
| `--z-up` / `--z-down` | `-40` / `-50` | 抬笔 / 落笔高度 (mm) |
| `--vel` | `20.0` | 落笔速度 |
| `--threshold` | Otsu | 手动二值化阈值（0~255） |
| `--invert` | 关 | 亮像素视为墨迹 |
| `--flip-x` / `--flip-y` | 关 | 坐标轴翻转 |

## 编程接口

```python
from robonix_sdk import RobonixClient, PlotConfig, load_binary, to_waypoints

binary = load_binary("drawing.png")
cfg = PlotConfig(pixel_to_mm=0.1, z_up=-40.0, z_down=-50.0, strategy="raster")
points = to_waypoints(binary, cfg)

with RobonixClient("192.168.1.10") as plc:
    plc.set_param(cfg.vel, cfg.acc, cfg.dec, cfg.jerk, cfg.z_up, cfg.z_down)
    plc.home()
    plc.draw_waypoints(points)
```

更多协议细节见 `docs/protocol.md`，PLC 侧接线见 `docs/plc_wiring.md`。
