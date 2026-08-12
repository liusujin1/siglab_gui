# python_samba

免原厂 SAMBA 安装的 IDE TC-MFD / OPTICON 主机侧工具。
通过官方 **Remote Command Interface (RCI)** 串口协议控制设备，不依赖
`Rci32.dll`、原厂 CommServer 或 COM 注册。本项目自带独立的
Communication Server，让 SAMBA 与 SIDMAT 可安全共享同一个物理串口。

## 架构

```
SAMBA UI/CLI ─┐
SIDMAT UI/CLI ├─ shared Communication Server (global FIFO)
              │
              └─ serial | mock diagnostic backends
   ↓
services (session, safety)
   ↓
protocol (frame + commands)
   ↓
transport: server (default) | serial | mock
   ↓
controller firmware
```

## 快速开始

```bash
cd path\to\python_samba
# 若 pip install 因 SSL/时间问题失败，可直接用 PYTHONPATH
set PYTHONPATH=src

# 无硬件：mock 连接
py -3 -m python_samba.cli connect --backend mock
py -3 -m python_samba.cli status --backend mock
py -3 -m python_samba.cli filter --backend mock
py -3 -m python_samba.cli position --backend mock
py -3 -m python_samba.cli ff --backend mock
py -3 -m python_samba.cli diag --backend mock

# 真机共享模式（默认；首次连接会自动启动后台服务器）
py -3 -m python_samba.cli connect --port COM1 --baud 57600
py -3 -m python_samba.cli status --port COM1

# 自动发现局域网和 Tailscale 中的独立服务端
py -3 -m python_samba.cli discover

# 仅诊断时绕过服务器、独占直连
py -3 -m python_samba.cli connect --backend serial --port COM1 --baud 57600

# GUI（PySide6 + 记录曲线分析依赖）
py -3 -m pip install -e .[gui]
py -3 -m python_samba.cli gui
# 或
py -3 -m python_samba.app

# 测试
py -3 -m pytest -q
```

## 共享 Communication Server

- 默认监听 `127.0.0.1:47619`，只有服务器进程占用 COM 口；所有客户端的完整
  RCI 请求/响应按全局 FIFO 执行，二进制 DGTBB 数据也不会与状态刷新串包。
- 第一个客户端确定 COM 口和波特率；后续客户端必须使用同一配置。任一客户端
  退出不会中断其他客户端，最后一个客户端退出时物理串口释放，托盘服务器保持运行。
- 共享模式不缓存参数、不虚拟化状态、也不设测量独占锁；两个软件看到的都是真机
  当前值，冲突写入遵循“后写生效”。
- 手动启动：`python-samba-comm-server --port COM1 --baud 57600 --tray`。
- 可发现可信网络服务：
  `python-samba-comm-server --auth trusted-network --discover --port COM1 --baud 57600 --tray`。
  服务使用 TCP `47619`，每两秒通过 UDP `47620` 广播并响应发现请求；客户端通过
  **Discover Server** 选择后直接连接，不需要填写 IP。Tailscale 不转发广播，客户端会
  从 `tailscale status --json` 获取在线节点并进行单播发现。
- Tailscale：同时传入回环和 Tailscale 地址，例如
  `python-samba-comm-server --listen 127.0.0.1:47619 --listen 100.x.y.z:47619 --tray`。
  非回环连接强制使用 `%LOCALAPPDATA%\python_samba\communication_server.token`；
  客户端通过 `--token-file` 指定安全复制后的令牌。服务器拒绝普通局域网/公网监听地址。
- 轮转日志位于 `%LOCALAPPDATA%\python_samba\communication_server.log`。

### 高延迟远程连接

通过 Tailscale/异地网络使用时，单条 RCI 命令仍会受真实网络往返延迟限制。
服务端模式会将页面状态、Logging 通道定义及 DGTBB 采样块合并为批量请求，
界面定时刷新在后台执行并合并过期刷新，不会再阻塞 Qt 界面线程。
Logging 准备期也可直接点击 **Stop** 取消；采集期点击 **Stop** 后，在当前真机
读取结束时停止，不会继续自动启动或无限等待。

### 独立服务端程序

运行 `build_comm_server.bat` 可生成单文件
`dist\PythonSambaCommServer.exe`。把该文件复制到连接控制器的任意 Windows 电脑并
双击运行即可；程序会按 USB 硬件身份恢复串口、自动启动共享服务、广播自身并缩入系统
托盘。配置保存在 `%LOCALAPPDATA%\python_samba\communication_server.json`。

独立程序默认使用 **Trusted LAN / Tailscale** 模式：只接受回环、RFC1918 局域网和
Tailscale `100.64.0.0/10` 来源，但不校验用户身份。同一局域网中的其他用户也能控制
真机，因此只应在可信实验室网络中启用；需要鉴权时可在服务端切换为 Access token。
首次运行可安装仅允许 `LocalSubnet` 和 Tailscale 来源访问 TCP `47619`、UDP `47620`
的 Windows 防火墙规则。

## GUI 导航（对照 SAMBA19xUI）

窗口采用与原版相同的信息层级：顶部应用标题栏、左侧固定主导航、左下
Update Page/回路状态/连接区，以及右侧页面内容和水平二级页签。活动日志默认
收起，可由右上角 **Console** 按钮展开。

主页面包括 Connect、Controller、Status、Velocity、Position、Pneumatic、
Feed Forward、Pneum. SFF、Save/Load、Logging 和 Special。Special 下方另有
**Real-time curve** 动作按钮，点击后打开独立实时曲线窗口而不切换当前参数页。
参数密集页面自动提供
横向或纵向滚动，不会因小屏幕裁掉控制项。

Logging 页的 **Records / Plot** 会打开独立的非模态图窗。图窗支持 40 通道曲线
显隐、时域/频域切换、游标、数据提示、A/B 标记、框选缩放、平移、图片复制和曲线
导出；去趋势、移动平均、Butterworth 滤波、FFT 与 Welch PSD 均生成不修改原始记录的
内存派生曲线。不规则时间轴会先提示重采样，关闭图窗后派生曲线不会自动写回记录文件。

Real-time Curve 可从固件支持的 Sensor、Temperature、Actuator、Velocity、Position、
Pneumatic、Excitation、FF/PFF、Polynom 和 Proximity Correction 信号中选择最多 40 条。
左侧选择项会一对一同步到 Selected Signals 和图像，不再设置独立的 Show 开关；单列
信号树会为完整传感器名称保留初始宽度。
窗口按 100 ms（可调 20–5000 ms）请求 DGMSV，显示实际平均周期、迟到次数与完整会话
样本；曲线以 10 Hz 上限刷新并支持跟随、游标、数据提示、A/B 标记、框选缩放、平移和
图片复制。启动前会把全部 40 个 DGMOS 槽保存到端点绑定的恢复文件，停止、关闭窗口、
断开或退出时恢复并逐项验证；恢复失败时只允许连接同一端点后执行 **Retry Restore**。
实时曲线运行期间，Logging 和 Status/Signals Display 的 Monitor 槽操作会被协议层租约
拦截。保存的 UTF-8-SIG CSV 可直接交给 Records / Plot 做重采样、滤波、FFT 和 PSD。

当前页面注册表中的可见功能均已接入 RCI 读写链路，覆盖系统回路、性能监视、切换条件、
电机保护、速度/位置矩阵与滤波器、气浮、FF/PFF、诊断、DAC/ADC、事件记录、
NVRAM 和 Raw RCI。正式 GUI 启动时会校验扩展页面加载结果，CI/打包检查可用
严格模式阻止缺页版本继续启动。

界面连接控制器后可直接写入普通参数；高风险动作仍会先保存本地快照并二次确认。

## 协议要点（来自官方 RCI 文档）

命令帧：

```
: <len_hex2> <msg_id> <crl> <CMD5> [params...] <crc_hex2> \r
```

- `prefix` = `:`
- `length` = 前缀与 CRC 之间的字符数（两位十六进制）；也可用 `##` 跳过长度校验
- `msg_id` = 任意可打印 ASCII（示例用 `?` / `$`）
- `data` = `<crl> <mnemonic> [params]`（空格分隔）
- `CRC` = 前缀与 CRC 之间字节的 XOR，两位十六进制；也可用 `##` 跳过
- `terminator` = `\r`（CR）
- 响应：`: <len> <msg_id> <status0|1> <crl> <status_code> <CMD> [data...] <crc> \r`
- 默认串口：57600, 8N1，无流控
- 控制器从不主动上报，只应答主机命令

## 与原厂软件关系

| 能力 | 本项目 | 原 SAMBA |
|------|--------|----------|
| 调参 / 读状态 / 矩阵 | 目标替代 | 可选对照 |
| RCI DLL / 原厂 CommServer | **不需要**（自带共享服务） | 需要 |
| 固件 | 仍运行在控制器上 | 同左 |
| USB 虚拟串口驱动 | 若用 USB 口，可能仍需 FTDI 等通用驱动 | 同左 |

开发期可用本机已装的 SAMBA 文档做对照；**部署目标机无需安装 IDE GmbH 套件**。

## 当前完成度

- 帧编解码、共享服务/串口/mock 三种传输、CLI、会话和安全写入链路已贯通。
- 23 个可见主/子页面均有对应 RCI 读写或状态刷新入口；Mock 回归用于防止重构断链。
- Samba 自动化回归共 204 项，SIDMAT 共 133 项；真实 V3.3.127 控制器已完成远程界面刷新、40 通道 Logging、Real-time Curve GUI、4096 点记录下载/交互分析及 5000 点 DGTBB 采集验收。
- GUI 已完成原版层级布局适配，并保留 `main_tabs` 兼容接口供扩展页面使用。
- 运行时页面扩展有明确加载报告；测试可用严格模式检查每个模块是否真正绑定。

完整真机范围、恢复检查以及明确未测试的 Clear NVRAM，见
[`HARDWARE_VALIDATION.md`](HARDWARE_VALIDATION.md)。

## 安全

界面连接后普通参数可直接写入。写参数前请确认现场允许整定；NVRAM 保存/清除有二次确认。
