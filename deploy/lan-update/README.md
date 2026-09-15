# PythonVNA 局域网更新工具

面向 Windows Server 2019：常开服务器提供下载，使用 U 盘导入版本。
客户端仍然点击软件内的“检查更新”。不需要外网、Python、Docker 或 7-Zip。
开发端需先通过原有流程生成完整 ZIP、增量 ZIP 和发布清单。本工具不修改公网产物。

## 1. 开发电脑导出

运行 `export_lan_update.bat`，输入原发布清单的完整路径，例如：

```text
D:\SynologyDrive\codex\vna\dist\manifest.json
```

默认输出到清单旁的 `LAN_v版本号`，已有同名目录时停止，避免覆盖。
也可以在 PowerShell 指定新的输出目录：

```powershell
.\export.ps1 -ManifestPath 'D:\SynologyDrive\codex\vna\dist\manifest.json' -OutputPath 'D:\LANRelease\v3.2.23'
```

成功后，将整个输出目录复制到 U 盘，必须包括全部 ZIP、manifest.json 和 tools 文件夹。
只有出现 `LAN export verified` 才表示导出成功；失败的输出目录不可用于发布。

导出会重新生成 ZIP，移除任何位置的 update_config.json 及删除清单中的对应项，
重新计算 SHA-256。清单使用相对下载地址，文件名包含内容哈希，与公网包互不混用。
包内 VERSION.txt、exe 和业务代码不修改。更早版本没有匹配增量包时会下载完整 ZIP。

## 2. 服务器首次部署

先由管理员确认固定 IPv4 地址及允许访问的客户端网段。端口默认 8095。
在服务器上右键 tools\install_server.bat，选择“以管理员身份运行”，输入 IP 和 CIDR 网段。
例如服务器 192.168.1.100、客户端网段 192.168.1.0/24。不要照抄示例，按现场设置。

默认服务目录 C:\PythonVNAUpdate，必须是本地普通空目录，不能是盘符根目录、
共享目录、符号链接或云同步目录。脚本拒绝覆盖其他已有目录和 IIS 站点。
网站名及应用池名为 PythonVNA-LAN-Update。只允许 GET/HEAD 下载 JSON、ZIP；
禁止目录列表、脚本执行和网页上传。IIS IP 限制及防火墙只允许指定网段，
另允许服务器自身的 IP 进行本机诊断。Windows 服务和站点设为开机启动。

可指定目录、端口和离线组件来源：

```powershell
.\install-server.ps1 -ServerIP '192.168.1.100' -AllowedSubnet '192.168.1.0/24' -Port 8095 -ServerRoot 'C:\PythonVNAUpdate'
```

启用组件使用 LimitAccess，不向 Windows Update 请求下载。若系统组件不齐，
挂载与当前 Server 2019 匹配的安装介质，确认镜像索引后重跑，例如增加：
`-FeatureSource 'WIM:E:\sources\install.wim:2'`。索引 2 只是示例。
提示需要重启时先重启，再用相同参数重跑。脚本不自动重启电脑。
安装失败时保留错误文本；组件可能已部分启用，但不应删除既有业务网站。

## 3. 每次从 U 盘导入

以管理员身份运行 tools\import_from_usb.bat，输入 U 盘上包含 manifest.json 的目录。
若服务器目录不是默认值，用：

```powershell
.\import.ps1 -BundlePath 'E:\LAN_v3.2.23' -ServerRoot 'C:\PythonVNAUpdate'
```

导入先校验所有文件，复制到服务器暂存区，再次校验后放入下载目录，最后原子替换清单。
导入期间原清单不变，之前的下载仍可使用。损坏包、越界路径、配置覆盖、降级被拒绝。
相同版本、相同内容可重复导入；同版本不同内容必须另发新版本。
导入进程中断后直接重跑，不要手动删除 import.lock；锁随进程退出自动释放。
staging 中未完成的目录及 public 内 .pending 文件不公开、不自动递归删除。
确认无导入进程后，管理员可手动清理残留暂存文件。

成功后保留最近两个发布集合引用的包，才清理更早的受管理包；删除不进入回收站，
需要恢复时从保留的 U 盘备份取回。不要清理 history、根目录标记或当前清单。

## 4. 每台客户端首次配置

关闭两个软件，运行 tools\configure_client.bat：

1. 输入服务器地址，例如 http://192.168.1.100:8095 。
2. 输入软件安装目录（里面应有 PythonVNATest.exe 或 VIanalysis.exe）。
3. 脚本验证清单和完整包可访问，备份旧配置后写入内网地址。
4. 重新打开软件，点击“检查更新”。

Program Files 等受保护目录需要以管理员身份运行。原配置保存在同目录的
update_config.json.<唯一编号>.bak 中。已设置 PYTHON_VNA_UPDATE_MANIFEST_URL
环境变量时它会优先于配置文件，需先由管理员取消旧变量。

从内网完整 ZIP 首次安装后也必须执行此配置步骤。手工用公网全量包覆盖安装，
可能重新带入公网配置，此时再次运行配置工具。

## 现场验收与故障处理

- 允许网段电脑访问 `http://服务器IP:端口/pythonvna/manifest.json`，应看到版本清单。
- 重启服务器后再次访问；禁止网段访问应失败，上传 PUT 请求和目录列表应被拒绝。
- 未安装 7-Zip、不能联网的客户端分别测试匹配增量和旧版本全量更新。
- 更新后检查 update_config.json 仍为内网地址，且两个软件均能打开。
- 404/404.3：检查是否已导入、网址是否正确、静态内容组件和 MIME 是否启用。
- 403：检查客户端实际源 IP 是否处于允许网段。
- 401.3：检查应用池身份对 public 目录及文件的读取权限。
- 连接超时：检查固定 IP、端口、防火墙和其他安全软件；脚本不修改路由器。
- 坏包导入应拒绝，旧清单仍可访问；中断导入后重跑应成功。

默认 HTTP 仅适合可信、隔离的局域网。哈希只能检查传输完整性，不能防御攻击者
同时替换清单与安装包。单位要求 HTTPS 时由管理员在独立站点绑定内部 CA 证书，
所有客户端先信任该 CA，然后用 HTTPS 清单地址重新配置；工具不会关闭证书验证。

本地自动化测试验证导出、导入和客户端更新兼容性，不等同于 Server 2019 现场验收。
