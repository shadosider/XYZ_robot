# 文字书写（单线：Hershey 西文 + 汉字笔画中线）

SDK 提供 `robonix_sdk.text`，把文字排版成绝对坐标轨迹，并直接下发到 PLC。

两类字形**都是单线**（不会画出空心轮廓），可混排、共用同一条基线：

| 类别 | 字形来源 | 说明 |
|------|----------|------|
| 西文 / 数字 | Hershey Simplex（内置） | 零依赖，开箱即用 |
| 汉字 | 笔画中线（内置 3755 字） | 一笔一条折线，见第 6.2 节 |

## 1. 坐标与纸张

| 项 | 约定 |
|---|---|
| 工作范围 | X `0 ~ 220 mm`，Y `0 ~ 150 mm`（机器行程） |
| 原点 | 纸张左下角 |
| 方向 | +X 向右，+Y 向上 |
| 抬笔 | `z_up = -48`（纸面 −50 上方 2 mm） |
| 落笔 | `z_down = -50`（压在纸上） |
| 机器零点 | Z 0 = 纸面上方 50 mm，也是 HOME 位（写字区左下角上方） |
| 默认起点 | 纸的左上角 |
| 默认字号 | 15 mm（西文大写高 / 汉字墨迹高同此值） |
| 字号范围 | 2 mm ~ 纸高 − 2 mm（150 mm 纸 → 2 ~ 148） |
| 汉字字格宽 | ≈ 1.17 × 字号（等比，不拉伸） |
| 汉字字距 | 固定 1.5 mm，加在字格宽之上 |

工作范围超过 **220 × 150** 会在构造 `TextConfig` / `TextWriter` 时直接报错，不会下发任何点。

第一条线的基线：

```
baseline_y = work_height - margin - (22 - 21) * scale
scale      = font_size_mm / 21
```

所以 15 mm 字号的第一个大写字母顶部正好在 `y=150`；汉字墨迹最高的一笔也贴在同一条线上。

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
| `text` | 必填 | 要写的文字（大小写英文、数字、汉字、空格、换行） |
| `--font-size-mm` | 15.0 | 字号（mm）：西文大写高，汉字墨迹高 |
| `--line-spacing` | 1.5 | 行高 = 字号 × 该倍数 |
| `--letter-spacing` | 0.5 | 字间距（mm） |
| `--space-advance` | 0.6×字号 | 空格宽度（mm） |
| `--work-width` | 220 | X 行程（mm） |
| `--work-height` | 150 | Y 行程（mm） |
| `--margin` | 0 | 四周留白（mm） |
| `--z-up` / `--z-down` | -48 / -50 | 抬笔/落笔 Z（绝对坐标；纸面 −50，抬笔离纸 2 mm） |
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
- 西文单词放不下时整词换到下一行；**汉字逐字断行**（任意字间可换行，不需要空格）；
- 如果整体超出纸张（例如字号太大、行数太多），抛 `TextOverflowError`，不会下发任何点。

## 5. 与 PLC 的配合

- PLC 侧当前使用 **绝对运动**，SDK 直接发送绝对坐标；
- 正式书写必须保证 PLC 侧 `MotionSim = FALSE`，否则只会回 ACK、不会真正运动；
- 写完最后一个笔画后会先抬笔，再执行 `HOME`；
- 建议每次开始书写前轴组已回零，保证绝对坐标有效；
- 单点 `MOVE_LINEAR` 帧长 24 B（8 B 头 + 16 B 载荷），远小于 PLC 解析上限 512 B；
- 文字按点逐条下发，受限于逐点 ACK，较长文本需要一定时间；
- SDK 客户端退出时会先发 `CMD_CLOSE`，避免 PLC 停留在旧 socket。

## 6. 字体

### 6.1 西文 / 数字：Hershey Simplex

单线笔画字体，内置在 `robonix_sdk/hershey_simplex.py`：

- 支持 `A-Z`、`a-z`、`0-9`、空格、换行；
- 数据来源：techninja/hersheytextjs（JSON 数据为 Public Domain）；
- 原始 Hershey 字体由 Dr. A. V. Hershey 设计，适合笔式绘图机；
- 字体单位：cap height = 21，基线 y = 22；
- 字号缩放：`scale = font_size_mm / 21`。

### 6.2 汉字：笔画中线（medians）

内置 `robonix_sdk/hanzi_data.py`，3755 个常用汉字（GB2312 一级字全量）。

- 一个字 = 若干条折线，**一笔一条**；笔画之间抬笔跳转，所以是单线字，不是轮廓；
- 源坐标：1024 × 1024 字格，y 向上，基线 y = 0；
- 换算：`K = 21 / Y_MAX`（让全字库最高一笔落在 cap 顶），
  于是 **汉字墨迹高 = 字号**；
- 字格宽 = `1024 × K × scale` ≈ 1.17 × 字号；
- 步进 = 字格宽 + `1.5 mm`（固定字距，末字后也多走 1.5 mm）；
- 缺字（不在 3755 字内）直接抛 `ValueError`，不会画半截；
- 数据来源：hanzi-writer-data 2.0.1（Make Me a Hanzi 笔画中线，Arphic 字体），
  许可 Arphic Public License。

## 7. 编程接口

```python
from robonix_sdk import RobonixClient
from robonix_sdk.text import TextConfig, TextWriter

cfg = TextConfig(font_size_mm=15.0, z_up=-48.0, z_down=-50.0)
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

- 暂不支持标点符号（含中文标点），遇到会抛 `ValueError`；
- 汉字仅限字库内的 3755 字，库外字会报错；
- 汉字含标点/生僻字时请先把字加进字库（格式见 `hanzi_data.py` 头注释）；
- 汉字笔画多，点数约为同尺寸西文的 3~5 倍，逐点 ACK 下耗时更长；
- 状态文件记录的是**文字排版游标**（基线坐标），不是机器实际位置；机器位置由写完后的 HOME 回到 `(0,0,0)`；
- 如果大幅修改字号，而游标已经很靠上，可能触发溢出；可用 `--reset` 回到左上角；
- 单线字体的笔画粗细由笔迹决定，与字体大小无关；
- 逐点 ACK 的协议下，长文本速度受运动时间和 TCP 往返限制。后续可以考虑 PLC 批量路径命令。
