# VNA 项目审查与低风险优化（2026-09-10）

## 第二轮修复：更新失败恢复与安全关闭

本节更新第一轮风险 1 和 4 的处理状态；下方第一轮记录保留作为历史。

### 已实施

- updater.py 新增 apply_update：完整解析删除/覆盖计划，先备份所有受影响的旧文件，再开始修改。应用失败或用户取消时恢复删除/覆盖文件，并移除新建文件与空目录。
- 目标目录使用独占创建的 .python_vna_update.lock 阻止本版本更新器并发应用。包内不能删除/覆盖此锁；正在运行的更新器也不会因删除清单被删除。
- 回滚失败时保留目标同级的 .python_vna_rollback_* 备份目录、RECOVERY.json 映射和更新锁，提示具体恢复位置，避免下一次更新覆盖现场。
- 备份和锁的清理失败只记录警告，不掩盖原始异常、不把已经成功应用的更新误报为失败。一般更新失败也写入 UPDATE_LOG.txt。
- 提交完成后取消自动启动，不再误报“更新已取消”；明确告知更新已应用、需要手动启动。
- main_window.py：stop 抛异常仍继续 close 并记录原因；close 或线程清理抛异常时拒绝关闭、显示错误，保留已有取消/托盘/线程超时策略。
- 新增故障注入及界面生命周期测试，覆盖备份失败、部分写入、删除后取消、最后一步取消、回滚失败、锁冲突/篡改、元数据与运行器保护、清理失败、主入口失败不重启和关闭异常。

### 兼容性与运行要求

- 不改变更新包字段、命令行参数或产品版本；既有 apply_removed_files/copy_tree_overlay 接口保留，主入口改用事务应用路径。
- 需要目标父目录可写，以及足以备份所有受影响旧文件的额外空间；备份失败会在修改业务文件前终止。
- 此处是应用期间异常/取消的回滚保护，不是文件系统原子交换，也不保证断电、进程强杀、第三方同时修改文件时自动恢复。旧版本更新器不会遵守新锁。
- 发现遗留锁时不自动按 PID 删除，避免 PID 重用或恢复现场被破坏。应先确认没有更新进程，再根据错误日志和 RECOVERY.json 人工核验/恢复，最后删除锁后重试。恢复映射中的 null 表示该目标原先不存在；不应盲目把备份整目录覆盖套件。
- 7z 应用层解压预检、签名、safe_overlay 元数据统一、大型 GUI 拆分及采样率兼容提示仍未在本轮实施；不能把回滚保护视作归档沙箱或供应链安全方案。

### 第二轮验证

- 采集界面与更新模块首轮定向测试：225 passed，2 subtests passed；清理边界修复后的更新模块：27 passed，2 subtests passed。
- 预检、pip check、compileall、git diff --check 通过。
- 最终全套测试：561 passed，12 subtests passed，232.12 秒；比第一轮增加 16 个测试方法。日志：build/repair_round2_final_tests.log。
- 隔离 PyInstaller 构建通过，约 247 秒；日志：build/repair_round2_build.log。产物：build/repair_round2/dist/PythonVNA_Suite。沿用已有 app-local VC 运行库复制步骤，三个 EXE 的 --help 均退出 0。
- 已将打包的 updater.pyc、main_window.pyc 与最终源码按 optimize=1 编译的代码对象比较（仅忽略文件名），结果一致。没有覆盖 dist 中的正式版本，没有发布更新。
- 构建保留上一轮同类可选模块/旧 OpenGL 依赖警告；没有真实 NI 硬件、断电恢复或正式在线更新验收。

## 范围和限制

本次以当前工作目录为准，审查 Python 产品代码、测试、构建/更新脚本，并对 dsa MATLAB 遗留目录做结构性检索。大型 GUI 使用定向检索，不代表每行代码均完成形式化验证。没有读取 recovered_chats、会话数据库、凭据或旧任务内容；所给 codex 任务链接未通过任务连接器读取。没有发布更新、操作 NI 硬件或更改版本。

开始时已有改动：PythonVNA_Suite.spec、dsa/vna/default.vna、pyproject.toml、python_vna/__init__.py、scripts/build_vna_suite.ps1、tests/test_repository_config.py；均未主动修改或回退。检查和构建基于包含这些现有改动的工作区。

## 结构、技术栈与业务流程

- Python >=3.11，setuptools；NumPy/SciPy 做数值处理，h5py 提供 HDF5。GUI 使用 PySide6、pyqtgraph、PyOpenGL；openpyxl 提供表格支持；nidaqmx 对接 NI 驱动；pytest 驱动 unittest 风格测试；PowerShell 和 PyInstaller 构建 Windows 程序。
- config/vna_suite.json 定义 shared、python_vna_test、vianalysis 三个责任区；预检报告 52 个受管理 Python 文件。三个发布入口为 PythonVNATest、VIanalysis、PythonVNAUpdater。
- 采集：app/UI 设置 SessionConfig → VnaController → NI/模拟后端 → BackendFrame → 可选重叠切窗 → FrameProcessor 的窗函数、谱分析、平均、FRF/相干性 → MeasurementSet → 绘图/导出。
- 连续记录：采集帧 → ContinuousDatWriter 分段落盘 → 异步压缩 → manifest → 分析端加载。硬件停止事件、Qt 工作线程、文件压缩线程构成主要生命周期边界。
- 分析：文件格式识别 → AnalysisDataset/Series → 筛选、滤波、PSD/传递函数/MIMO 推导 → 曲线编辑/工作区 → 图表和导出。
- 更新：读取配置/manifest → 选包 → 下载和 SHA256 校验 → staging 解压 → 删除清单/覆盖文件 → 日志/重启。
- dsa 是 MATLAB 兼容来源，不是 Python 构建主入口；.worktrees 是迁移/开发来源；build、dist 是生成物。

## 已完成修改

| 文件 | 问题与改动 | 兼容性 |
| --- | --- | --- |
| python_vna/storage.py | JSON 改为同目录临时文件写入、flush/fsync、os.replace，finally 清理临时文件；写入或替换失败时旧文件保持不变 | JSON 结构、函数签名、返回路径不变；目标文件的文件身份/ACL 继承行为可能随替换变化，需要特殊权限部署单独验证 |
| python_vna/signal_pipeline.py | FFT 显式拒绝非有限/非正采样率、空样本和非二维数组 | 合法帧的数值行为不变；无效输入统一得到 ValueError |
| python_vna/analysis_data.py | 时间列识别不再先丢掉 NaN/Inf 再判断单调性，避免识别成功后仍使用原始 NaN 时间计算采样率 | 不合法时间列沿用已有普通数据列和 fs_hint 回退，不偷偷删除信号样本 |
| python_vna/updater.py | 文件覆盖目标和重启路径复用 _resolve_within；重启仅接受实际文件 | 正常套件内路径不变；逃逸目标目录的路径被拒绝；不等同于完整更新沙箱 |
| python_vna/analysis_algorithms.py | 每频点一次求解全部输出右端，奇异矩阵也只计算一次伪逆 | 保留原来的互谱方向、正则化、返回形状和数值含义 |

测试修改：tests/test_storage.py、tests/test_signal_pipeline.py、tests/test_update_client.py、tests/test_analysis_viewer.py。新增 9 个测试方法，涵盖磁盘同步/替换失败、临时文件清理、NaN 时间列、非法 FFT 输入、路径逃逸/正常重启、相位相关 MIMO 输入和奇异输入回退。

## 复核时排除的误报

- save_session_json 原来先计算 json.dumps 再调用 write_text，因此“序列化失败会截断旧文件”不成立；本次修复的是真正的写入/落盘失败窗口。
- 时间列原有 _is_time_like 已检查递增和近似均匀，重复/倒序时间并不会直接进入时间列分支；实际缺口是过滤 NaN 后检查、再使用未过滤原列。
- MIMO 的直接转置修复不成立：SciPy csd 使用 conj(X)*Y，本项目 sxx 的存储约定使现有 solve 方向正确。增加相位相关输入回归测试，避免被误改。对应官方说明：SciPy signal.csd 文档的 Notes。

## 性能与可维护性

- MIMO 去掉每个输出重复分解同一矩阵。单机微基准：16×16 复数正定矩阵、8 输出、每次 1000 调用、3 次取最小，逐输出约 0.0633 秒，多右端约 0.00803 秒，约 7.9 倍。只是求解内核，不代表完整 Welch/GUI 提速；结果以 allclose 验证。
- ui/analysis_viewer.py、ui/main_window.py、diagnostic/pages.py 各约 7千至9千非空行，界面、业务编排和数值操作耦合明显。不进行大规模拆分，以免改变 Qt 信号、状态保存与旧 VNA 兼容行为。
- 部分模块重复延迟导入和频率/数组校验；目前避免为少量重复新增全局抽象。后续先明确单位、频谱约定和错误策略，再提取共享纯函数。
- 模型已有 dataclass/类型标注，但 metadata 和界面状态仍包含大量动态 dict。尚无配置化的类型检查、格式化和覆盖率门槛；本机没有 coverage 模块，不编造覆盖率百分比。

## 未实施项与风险分级

### 高优先级，需独立设计和兼容性验证

1. updater.py 的删除清单和逐文件覆盖没有事务回滚。中途权限错误、磁盘错误、取消可能留下混合版本；应设计备份/回滚、更新锁和故障恢复测试。整体目录交换涉及运行中的 EXE/DLL、增量包语义和用户配置，不作为本次低风险变更。
2. ZIP 已有路径及符号链接检查，7z 分支直接委托外部 7z，没有等价的应用层逐条验证。本次未构造实际 7z 利用样本，不将其描述为已证实可利用漏洞；需要兼容现有发布包的路径/链接预检、解压容量限制及恶意归档测试。
3. 更新包 SHA256 与包地址来自同一 manifest，不是独立发布签名；需评估 HTTPS/签名、信任根与凭据配置，未进行在线供应链漏洞扫描。

### 中优先级

4. main_window.py 的 closeEvent 吞没 stop/close 异常，可能失去资源释放失败的诊断；建议分阶段清理并记录异常，补线程退出/NI 断连测试。未改变关闭交互。
5. analysis_viewer.py 的工作区拼合平滑使用 log10，局部路径未统一清理非正频率；需对实际 UI 曲线入口做 DC/空曲线复现后修复，当前只列风险，不宣称已证实输出损坏。
6. generate_update_manifest.ps1 的 safe_overlay 写死为 true，而增量构建按删除文件数计算安全性；需统一元数据来源、测试并明确客户端策略。
7. build_vna_suite.ps1 同版本构建会删除已有发布目录。此次直接使用相同 spec 并指定独立 dist/work 路径，没有执行正式发布覆盖/归档步骤。
8. recover_codex_sessions.ps1 手工拼接 JSON 索引字符串存在转义风险。属于辅助会话恢复工具，未读取其处理的私密数据，未修改。
9. NI 超时/采样参数需要配置边界与驱动语义验证，不能未经验证就将零超时钳制为正值。
10. 模拟端允许任意采样率，但旧 VNA 导出按 _legacy_sample_index_for_rate 选择离散采样率。首次 1024 Hz 回读相等断言失败，实际返回 1280 Hz；使用兼容的 1280 Hz 后端到端通过。该既有量化行为未改动，后续应明确提示/拒绝不兼容采样率，避免用户误以为任意采样率都能无损往返。

## 验证记录

- 修改前启动的基线全套测试：536 passed，3 subtests passed，299.34 秒。
- 首批定向回归：91 passed，9 subtests passed，22.15 秒。
- MIMO 等算法定向回归：28 passed，1.91 秒。
- 仓库 preflight：通过；pip check：No broken requirements found；compileall：通过；git diff --check：通过（仅 Git 的 LF/CRLF 提示）。
- scripts/*.ps1 PowerShell 语法解析通过。模拟采集 3 帧平均 → 旧 VNA 保存 → 回读，1280 Hz/256 点通过；1024 Hz 的首次严格采样率断言失败见风险 10。
- 最终执行 scripts/test_vna_product.ps1 -Product All -Quiet：545 passed，12 subtests passed，212.72 秒，退出码 0；日志：build/audit_20260910_tests.log。
- 隔离构建命令：`.venv/Scripts/pyinstaller.exe --noconfirm --distpath build/audit_20260910/dist --workpath build/audit_20260910/work PythonVNA_Suite.spec`；日志：build/audit_20260910_build.log。构建成功，退出码 0，约 264 秒。
- 产物在 build/audit_20260910/dist/PythonVNA_Suite；按现有构建脚本补齐 7 个 app-local VC 运行库 DLL，三个 EXE 的 --help 冒烟全部退出 0。未运行正式归档、版本发布或在线更新。
- 构建保留警告：pyqtgraph.jupyter/jupyter_rfb、flowchart 模板、部分 SciPy 隐藏导入，以及旧 OpenGL DLL 的 MSVCR90 依赖。spec 已过滤旧 OpenGL/DLLS 条目，但不能仅凭 --help 宣称全部 GUI/3D 功能在干净机器上通过，应安排真实发布环境验收。
- 未验证真实 NI USB-4431、实际传感器/激励、长时间记录、真实在线更新、MATLAB 全流程和发布 EXE 的完整交互；模拟/GUI 测试与构建通过不能替代这些验收。
