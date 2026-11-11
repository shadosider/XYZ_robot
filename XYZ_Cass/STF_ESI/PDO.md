# PDO

## RxPDO（PLC → STF05-EC）

| Index  | SubIndex | Bit Length | Object Name          | Data Type | Description |
|--------|----------|------------|----------------------|-----------|-------------|
| 0x6040 | 0        | 16         | Controlword          | UINT      | 控制字      |
| 0x6060 | 0        | 8          | Modes of operation   | SINT      | 运行模式    |
| 0x607A | 0        | 32         | Target position      | DINT      | 目标位置    |
| 0x6081 | 0        | 32         | Profile velocity     | UDINT     | 轮廓速度    |
| 0x6083 | 0        | 32         | Profile acceleration | UDINT     | 轮廓加速度  |
| 0x6084 | 0        | 32         | Profile deceleration | UDINT     | 轮廓减速度  |
| 0x60FF | 0        | 32         | Target velocity      | DINT      | 目标速度    |
| 0x60FE | 1        | 32         | Digital outputs      | UDINT     | 数字输出    |
| 0x60B8 | 0        | 16         | Touch probe function | UINT      | 触摸探针功能|

## 各字段解释

### （1）0x6040 — Controlword（控制字）

- 类型：UINT，16 bit。
- 作用：控制驱动器的状态机。
- 常见操作：
  - Shutdown：进入 Ready to Switch On。
  - Switch On：进入 Switched On。
  - Enable Operation：使能驱动器。
  - Disable Voltage：关闭功率输出。
  - Quick Stop：快速停止。
它是 PLC 控制驱动器最重要的字段之一。

### （2）0x6060 — Modes of operation（运行模式）

- 类型：SINT，8 bit。
- 作用：告诉驱动器采用哪种运动控制模式。
常见 CiA 402 模式：

| 数值 | 模式 | 说明 |
| ---- | ---- | ---- |
| 1 | Profile Position (PP) | 轮廓位置模式 |
| 3 | Profile Velocity (PV) | 轮廓速度模式 |
| 6 | Homing | 回零模式 |
| 8 | Cyclic Synchronous Position (CSP) | 周期同步位置模式 |

实际支持情况需要以 STF05-EC 手册为准。

### （3）0x607A — Target position（目标位置）

- 类型：DINT，32 bit，有符号整数。
- 作用：设置电机需要到达的目标位置。
- 使用场景：位置控制。
例如，PLC 写入目标位置后，驱动器按照当前运行模式执行运动。

注意：这里的数值不一定直接代表机械角度或毫米，需要根据驱动器的位置单位、电子齿轮比等参数换算。

### （4）0x6081 — Profile velocity（轮廓速度）

- 类型：UDINT，32 bit，无符号整数。
- 作用：设置位置运动过程中的速度。
- 使用场景：PP 位置模式。
例如，设置目标位置为 10000，同时设置轮廓速度为 5000。

驱动器按照配置的速度曲线执行位置运动。

### （5）0x6083 — Profile acceleration（轮廓加速度）

- 类型：UDINT，32 bit。
- 作用：设置电机加速阶段的加速度。
- 使用场景：控制启动时的加速过程。
数值越大，理论上加速越快，但需要考虑电机负载和机械结构承受能力。

### （6）0x6084 — Profile deceleration（轮廓减速度）

- 类型：UDINT，32 bit。
- 作用：设置电机减速阶段的减速度。
- 使用场景：控制电机接近目标位置时的减速过程。
与 0x6083 配合，可以控制运动过程中的速度变化。

### （7）0x60FF — Target velocity（目标速度）

- 类型：DINT，32 bit，有符号整数。
- 作用：设置目标运行速度。
- 使用场景：速度控制，例如 PV 模式。
与 0x6081 的区别：

| 对象 | 作用 |
| ------ | ---- |
| 0x6081 | PP 模式中的轮廓速度 |
| 0x60FF | PV 模式中的目标速度 |

### （8）0x60FE:01 — Digital outputs（数字输出）

- 类型：UDINT，32 bit。
- 作用：控制驱动器的数字输出信号。
通常采用位操作控制不同的输出，例如：
- Bit 0：控制某个数字输出。
- Bit 1：控制另一个数字输出。
具体哪些位有效，需要查看 STF 的对象字典和硬件定义。

### （9）0x60B8 — Touch probe function（触摸探针功能）

- 类型：UINT，16 bit。
- 作用：配置触摸探针（Touch Probe）功能。
该功能可以用于捕获外部输入事件发生时的电机位置。

例如：

- 检测外部传感器触发。
- 捕获触发瞬间的位置。
- 用于位置检测和同步控制。

## TxPDO（STF05-EC → PLC）

TxPDO（Transmit Process Data Object）表示驱动器发送的数据，即 STF05-EC 通过 EtherCAT 周期性向 PLC 返回的状态和反馈信息。

| Index  | SubIndex | Bit Length | Object Name                  | Data Type | Description |
|--------|----------|------------|------------------------------|-----------|-------------|
| 0x603F | 0        | 16         | ErrorCode                    | UINT      | 错误代码 |
| 0x6041 | 0        | 16         | StatusWord                   | UINT      | 状态字 |
| 0x6061 | 0        | 8          | Modes of operation display   | SINT      | 当前运行模式 |
| 0x6064 | 0        | 32         | Position actual value        | DINT      | 实际位置 |
| 0x606C | 0        | 32         | Velocity actual value        | DINT      | 实际速度 |
| 0x60FD | 0        | 32         | Digital inputs               | UDINT     | 数字输入状态 |
| 0x60B9 | 0        | 16         | Touch probe status           | UINT      | 触摸探针状态 |
| 0x60BA | 0        | 32         | Touch probe pos1 pos value   | DINT      | 探针1正向位置捕获值 |
| 0x60BB | 0        | 32         | Touch probe pos1 neg value   | DINT      | 探针1负向位置捕获值 |
| 0x60BC | 0        | 32         | Touch probe pos2 pos value   | DINT      | 探针2正向位置捕获值 |
| 0x60BD | 0        | 32         | Touch probe pos2 neg value   | DINT      | 探针2负向位置捕获值 |

## 各字段解释

### （1）0x603F — ErrorCode（错误代码）

- 类型：UINT，16 bit。
- 作用：返回驱动器当前的错误代码。
- 用途：PLC 根据错误代码判断驱动器是否发生故障。
例如：
- 电机过流
- 过压
- 过温
- 通信或其他驱动器故障
具体错误代码的含义需要查阅 STF05-EC 错误代码表。

### （2）0x6041 — StatusWord（状态字）

- 类型：UINT，16 bit。
- 作用：反映驱动器当前的状态机状态。
这是非常重要的反馈字段。

CiA 402 中常见状态包括：
状态 含义
Not Ready to Switch On 未准备好使能
Switch On Disabled 禁止使能
Ready to Switch On 已准备好使能
Switched On 已上电但未完全使能
Operation Enabled 运行使能
Quick Stop Active 快速停止状态
Fault Reaction Active 故障响应中
Fault 故障状态

PLC 可以根据 StatusWord 判断驱动器是否真正进入了 Operation Enabled 状态，而不是仅仅发送了使能命令。

### （3）0x6061 — Modes of operation display（当前运行模式）

- 类型：SINT，8 bit。
- 作用：反馈驱动器当前实际采用的运行模式。
它与 RxPDO 中的 0x6060 对应：
对象 方向 作用
0x6060 RxPDO PLC 设置运行模式
0x6061 TxPDO 驱动器反馈当前运行模式

例如，PLC 请求 PP 模式（1），驱动器可以通过该字段反馈当前运行模式。

### （4）0x6064 — Position actual value（实际位置）

- 类型：DINT，32 bit，有符号整数。
- 作用：反馈电机当前位置。
例如：
Target Position  = 10000
Position Actual  = 8500

表示 PLC 设置的目标位置为 10000，而驱动器当前反馈的位置为 8500。

可以利用它监控运动过程、判断位置偏差。

注意：实际位置的单位及换算方式需要参考 STF 的参数配置。

### （5）0x606C — Velocity actual value（实际速度）

- 类型：DINT，32 bit，有符号整数。
- 作用：反馈电机当前实际运行速度。
例如：
Target Velocity  = 5000
Velocity Actual  = 4800

表示目标速度为 5000，实际反馈速度为 4800。

可以用于速度监控和运动状态判断。

### （6）0x60FD — Digital inputs（数字输入状态）

- 类型：UDINT，32 bit。
- 作用：反馈驱动器数字输入端口的状态。
通常通过位操作读取：
- Bit 0：某个数字输入状态。
- Bit 1：另一个数字输入状态。
具体每个 Bit 对应哪个物理输入端口，需要查看 STF05-EC 的对象字典。

用途包括：

- 读取限位开关。
- 读取原点传感器。
- 读取外部控制信号。
- 监控外部输入状态。
（7）0x60B9 — Touch probe status（触摸探针状态）
- 类型：UINT，16 bit。
- 作用：反馈触摸探针功能的状态。
例如，可以用于判断：
- 是否检测到触发事件。
- 是否完成位置捕获。
- 哪个探针通道触发。
- 正向或负向捕获是否有效。
（8）0x60BA — Touch probe pos1 pos value
- 类型：DINT，32 bit。
- 作用：返回探针通道 1 正向触发时捕获的位置。
（9）0x60BB — Touch probe pos1 neg value
- 类型：DINT，32 bit。
- 作用：返回探针通道 1 负向触发时捕获的位置。
（10）0x60BC — Touch probe pos2 pos value
- 类型：DINT，32 bit。
- 作用：返回探针通道 2 正向触发时捕获的位置。
（11）0x60BD — Touch probe pos2 neg value
- 类型：DINT，32 bit。
- 作用：返回探针通道 2 负向触发时捕获的位置。
其中：
- pos 表示正向边沿捕获。
- neg 表示负向边沿捕获。
- pos1、pos2 表示不同的探针通道。
这些对象主要用于高精度位置捕获，例如通过外部传感器触发记录电机位置。
