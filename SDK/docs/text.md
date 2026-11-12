# 文字书写（Hershey 单线字体）

SDK 提供 `robonix_sdk.text`，把英文/数字排版成绝对坐标轨迹，并直接下发到 PLC。

## 1. 坐标与纸张

| 项 | 约定 |
|---|---|
| 工作范围 | X `0 ~ 250 mm`（25 cm），Y `0 ~ 150 mm`（15 cm），**硬上限** |
| 原点 | 纸张左下角 |
| 方向 | +X 向右，+Y 向上 |
| 抬笔 | `z_up = 0` |
| 落笔 | `z_down = -33`（压在纸上） |
| 默认起点 | 纸的左上角 |
| 默认字号 | 10 mm（大写字母高度） |

工作范围超过 250 × 150 会在构造 `TextConfig` / `TextWriter` 时直接报错，不会下发任何点。

第一条线的基线：

```
baseline_y = work_height - margin - (22 - 21) * scale
scale      = font_size_mm / 21
```

即基线就在纸张顶边下方约 `0.048 * font_size_mm`（10 mm 字号约 0.48 mm），
大写字母顶部与纸张上边对齐。

## 2. 快速使用

```bash
# 预览轨迹，不连接 PLC
python examples/write_text.py "Hello 123" --dry-run

# 写到 PLC（写完自动 HOME）
python examples/write_text.py "Hello 123" --host 192.168.33.130 --port 2000

# 调整字号
python examples/write_text.py "ABC" --font-size-mm 15

# 重置到左上角
python examples/write_text.py "ABC" --reset

# 指定状态文件
python examples/write_text.py "ABC" --state ./writer_state.json
```

## 3. 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `text` | 必填 | 要写的文字（大小写英文、数字、空格、换行） |
| `--font-size-mm` | 10.0 | 大写字母高度（mm） |
| `--line-spacing` | 1.5 | 行高 = 字号 × 该倍数 |
| `--letter-spacing` | 0.5 | 字间距（mm） |
| `--space-advance` | 0.6×字号 | 空格宽度（mm） |
| `--work-width` | 250 | 书写区宽度（mm），最大 250 |
| `--work-height` | 150 | 书写区高度（mm），最大 150 |
| `--margin` | 0 | 四周留白（mm） |
| `--z-up` / `--z-down` | 0 / -30 | 抬笔/落笔 Z（绝对坐标，需按实机实测） |
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
- 如果整体超出书写区（字号太大、行数太多、或 XY 越界），抛 `TextOverflowError`，不会下发任何点；
- 字号大于等于书写区高度时，直接抛 `TextOverflowError`，提示换小字号。

典型容量（250 × 150 mm 书写区，字号 10 mm、行高 15 mm）：

| 项 | 数值 |
|---|---|
| 每行字符数 | 约 35~40 个（视字符宽度） |
| 行数 | 最多 10 行 |
| 单字高度 | 10 mm，行距 15 mm |

## 5. 与 PLC 的配合

- PLC 侧当前使用 **绝对运动**，SDK 直接发送绝对坐标；
- 正式书写必须保证 PLC 侧 `MotionSim = FALSE`，否则只会回 ACK、不会真正运动；
- 写完最后一个笔画后会先抬笔，再执行 `HOME`；
- 建议每次开始书写前轴组已回零，保证绝对坐标有效；
- 单点 `MOVE_LINEAR` 帧长 40 B，远小于 PLC 解析上限 512 B；
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

cfg = TextConfig(font_size_mm=10.0, z_up=0.0, z_down=-30.0)   # 默认 250 x 150
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

- 工作区最大 250 × 150 mm，超出直接报错（机器行程约束）；
- 暂不支持标点符号（`!?.,` 等），遇到会抛 `ValueError`；
- 暂不支持中文字符；
- 状态文件记录的是**文字排版游标**（基线坐标），不是机器实际位置；机器位置由写完后的 HOME 回到 `(0,0,0)`；
- 如果大幅修改字号，而游标已经很靠上，可能触发溢出；可用 `--reset` 回到左上角；
- 单线字体的笔画粗细由笔迹决定，与字体大小无关；
- 逐点 ACK 的协议下，长文本速度受运动时间和 TCP 往返限制。后续可以考虑 PLC 批量路径命令。
