# 文字书写（Hershey 单线字体）

SDK 提供 `robonix_sdk.text`，把英文/数字排版成绝对坐标轨迹，并直接下发到 PLC。

## 1. 坐标与纸张

| 项 | 约定 |
|---|---|
| 工作范围 | X `0 ~ 280 mm`，Y `0 ~ 160 mm` |
| 原点 | 纸张左下角 |
| 方向 | +X 向右，+Y 向上 |
| 抬笔 | `z_up = -40` |
| 落笔 | `z_down = -50`（压在纸上） |
| 默认起点 | 纸的左上角 |
| 默认字号 | 10 mm（大写字母高度） |

第一条线的基线：

```
baseline_y = work_height - margin - font_size_mm
cursor = (margin, baseline_y)
```

所以 10 mm 字号的第一个大写字母顶部正好在 `y=160`。

## 2. 快速使用

```bash
# 预览轨迹，不连接 PLC
python tests/draw.py text "Hello 123" --dry-run

# 写到 PLC（写完自动 HOME）
python tests/draw.py text "Hello 123" --host 192.168.33.130 --port 2000

# 调整字号
python tests/draw.py text "ABC" --font-size-mm 15

# 重置到左上角
python tests/draw.py text "ABC" --reset

# 指定状态文件
python tests/draw.py text "ABC" --state ./writer_state.json
```

## 3. 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `text` | 必填 | 要写的文字（大小写英文、数字、空格、换行） |
| `--font-size-mm` | 10.0 | 大写字母高度（mm） |
| `--line-spacing` | 1.5 | 行高 = 字号 × 该倍数 |
| `--letter-spacing` | 0.5 | 字间距（mm） |
| `--space-advance` | 0.6×字号 | 空格宽度（mm） |
| `--work-width` | 280 | 纸张宽度（mm） |
| `--work-height` | 160 | 纸张高度（mm） |
| `--margin` | 0 | 四周留白（mm） |
| `--z-up` / `--z-down` | -40 / -50 | 抬笔/落笔 Z |
| `--vel` | 20 | 书写速度（mm/s） |
| `--acc` / `--dec` / `--jerk` | 300 / 300 / 3000 | 运动参数 |
| `--state` | `~/.robonix/writer_state.json` | 游标状态文件 |
| `--reset` | 关 | 重置游标到左上角 |
| `--dry-run` | 关 | 只排版，不连 PLC |
| `--no-home` | 关 | 写完后不回零 |

## 4. 续写与自动换行

- 每次成功写完后，游标 `(x_mm, y_mm)` 保存到状态文件；
- 下一次运行从上次结束的位置继续；
- 换行/越界在**发送 TCP 之前**完成检查；
- 单词放不下时自动换到下一行；
- 如果整体超出纸张（例如字号太大、行数太多），抛 `TextOverflowError`，不会下发任何点。

## 5. 与 PLC 的配合

- PLC 侧当前使用 **绝对运动**，SDK 直接发送绝对坐标；
- 正式书写必须保证 PLC 侧 `MotionSim = FALSE`，否则只会回 ACK、不会真正运动；
- 写完最后一个笔画后会先抬笔，再执行 `HOME`；
- 建议每次开始书写前轴组已回零，保证绝对坐标有效；
- 单点 `MOVE_LINEAR` 帧长 32 B（24 B 载荷 + 8 B 帧头/CRC），远小于 PLC 解析上限 512 B；
- 文字按点逐条下发，受限于逐点 ACK，较长文本需要一定时间；
- SDK 客户端退出时会先发 `CMD_CLOSE`，避免 PLC 停留在旧 socket。

## 6. 字体

字形来自 Hershey Simplex（`futural`），单线笔画字体：

- 支持 `A-Z`、`a-z`、`0-9`、空格、换行；
- 数据来源：techninja/hersheytextjs（JSON 数据为 Public Domain）；
- 原始 Hershey 字体由 Dr. A. V. Hershey 设计，适合笔式绘图机；
- 字体单位：cap height = 21，基线 y = 22；
- 字号缩放：`scale = font_size_mm / 21`。

## 7. 编程接口

```python
from robonix_sdk import RobonixClient
from robonix_sdk.text import TextConfig, TextWriter

cfg = TextConfig(font_size_mm=10.0, z_up=-40.0, z_down=-50.0)
writer = TextWriter(cfg)          # 自动读取上次游标

with RobonixClient("192.168.33.130") as plc:
    writer.write(plc, "Hello 123")  # 排版 + 下发 + 保存游标 + HOME
```

只排版不发送：

```python
points, new_cursor = writer.plan("Hello 123")
print(len(points), new_cursor)
```

## 8. 限制

- 暂不支持标点符号（`!?.,` 等），遇到会抛 `ValueError`；
- 暂不支持中文字符；
- 状态文件记录的是**文字排版游标**（基线坐标），不是机器实际位置；机器位置由写完后的 HOME 回到 `(0,0,0)`；
- 如果大幅修改字号，而游标已经很靠上，可能触发溢出；可用 `--reset` 回到左上角；
- 单线字体的笔画粗细由笔迹决定，与字体大小无关；
- 逐点 ACK 的协议下，长文本速度受运动时间和 TCP 往返限制。后续可以考虑 PLC 批量路径命令。