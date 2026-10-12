# X-Tunnel 节点、测速与部署手册

本项目管理 x-tunnel 内核进程、生成 sing-box 本地节点 Provider、Cloudflare IP 测速与 Telegram 通知。ShellCrash 与 X-Tunnel 服务互相独立；ShellCrash 只需由用户自行配置为读取生成的 Provider。项目目录可整体迁移；安装脚本按实际目录生成 systemd 服务路径。

## 组成与目录

- `supervisor.py`：读取节点配置，启动每节点独立的 x-tunnel 进程；配置变化后重载节点，异常退出自动重启。
- `sync_singbox.py`、`x-tunnel-singbox-template.json`：只生成 X-Tunnel 节点 Provider JSON，不读取或修改 ShellCrash 配置。
- `cfip_speedtest.py`：测速 Cloudflare IP、按可配置目标速度逐档回退选取、更新节点 CF IP、发送动态进度与结果通知。
- `telegram_speedtest_bot.py`：专用 Telegram Bot 轮询器，支持完整测速、`/speedtest <IP>` 单 IP 测速、`/topspeed`、`/restartxtunnel`（兼容 `/restart`）。
- `x-tunnel.yaml`：当前运行配置，含服务器/令牌等敏感参数，权限应为 `0600`，不要提交到代码仓库。
- `speedtest-bot.env`：Telegram 凭据，权限必须为 `0600`。
- `cfip-data/`：候选 IP 段、测速地址缓存、完整测速报告。测速时创建，目录权限为 `0700`。
- `runtime/`：默认的 sing-box 节点 Provider。将 ShellCrash 的 local provider 手动指向此文件，或在 `project.env` 指定自定义位置。
- `project.env`：可选运行变量。`X_TUNNEL_PROVIDER_PATH` 自定义 Provider 输出位置，`X_TUNNEL_LISTEN_HOSTS` 设置本机/LAN 监听 IP，`X_TUNNEL_TG_PROXY` 设置可选 Telegram 代理，`X_TUNNEL_TARGET_MBPS` 设置首选测速目标（整数 Mbps，默认 300）。
- `systemd/`：服务与定时器模板。`@PROJECT_DIR@` 在安装时替换为真实项目目录。
- `x-tunnel.example.yaml`、`speedtest-bot.env.example`、`project.env.example`：不含真实凭据的初始配置模板。
- `x-tunnel.md`：旧版内核配置参考文件；当前服务不读取它。

项目不会维护 `/etc/ShellCrash/jsons/config.json`、`outbounds.json`，不会重启 ShellCrash，也不会修改其防火墙。默认 Provider 输出到项目 `runtime/`；用户可修改 `X_TUNNEL_PROVIDER_PATH` 自定义输出路径。请自行在 ShellCrash 配置中添加 local provider 和所需节点组，并将路径指向同一个 Provider 文件。

公开源码包为 `x-tunnel-source.tar.gz`。其中不包含当前节点配置、Bot 凭据、运行报告或生成 Provider；发布到 GitHub 前仍需自行选择并添加开源许可证。

`X-Tunnel-ShellCrash-Loop-Fix.md` 是旧版自动联动方案的历史文档；当前项目不再自动改写 ShellCrash 路由、防火墙或重启服务。

## 部署

适用环境：systemd Linux、root 权限、Python 3 与 PyYAML。ShellCrash 不是 X-Tunnel 安装器的依赖。安装器自动识别 `x86_64/amd64` 或 `aarch64/arm64`。若 `/usr/bin/x-tunnel` 不存在，会从官方 GitHub Releases 下载匹配架构的 Linux 内核，校验 ELF 架构并运行 `-h` 启动检查后安装；若内核已存在则保留原文件，仅校验架构并补齐 `0755` 权限。其他架构会明确报错，不会下载错误二进制。

1. 创建运行配置：

   ```sh
   cd /path/to/x-tunnel
   cp x-tunnel.example.yaml x-tunnel.yaml
   chmod 0600 x-tunnel.yaml
   editor x-tunnel.yaml
   ```

   每行节点格式为 `名称|服务地址|密钥token|cfip|连接数|端口`。可省略末尾可选字段，默认值来自 `defaults`。`first_port` 是第一个节点的首选端口，后续节点按顺序递增；最多 5 个节点。服务启动前会检查所有监听地址的端口是否可绑定；若首选端口已被其他进程占用，或节点间配置了重复端口，会自动向后寻找可用端口并同步实际端口到 Provider。配置文件变化由 supervisor 每 2 秒检测并自动重载。监听地址默认本机回环。若要 LAN 接入，在 `project.env` 设置 `X_TUNNEL_LISTEN_HOSTS=127.0.0.1,<本机实际LAN地址>`，服务会绑定这些地址；空值只绑定 `127.0.0.1`。每个节点实例的地址由此变量统一管理，不需要在 YAML 里写死局域网地址。

2. 设置 Telegram 专用 Bot。先在 Telegram 对专用 Bot 发送 `/start`，然后：

   ```sh
   cp speedtest-bot.env.example speedtest-bot.env
   chmod 0600 speedtest-bot.env
   editor speedtest-bot.env
   python3 telegram_speedtest_bot.py --show-chat-ids
   ```

   将 BotFather 提供的 token 填入 `TG_TOKEN`，将命令显示的私聊数字 ID 填入 `TG_CHATID`。不要把 token 发到聊天、日志或工单中。Telegram 代理通过 `project.env` 配置：`X_TUNNEL_TG_PROXY` 可留空，留空时使用 `X_TUNNEL_LOCAL_PROXY`（默认本机 ShellCrash HTTP 代理 `http://127.0.0.1:7890`）；需要指定网络代理时填写完整 URL。修改代理变量后重启 Bot 服务和测速服务使其生效。

3. 安装服务：

   ```sh
   apt install python3-yaml iproute2 curl
   ./install.sh
   ```

   默认下载当前最新正式版。需要固定版本时可在运行安装器时指定，例如 `X_TUNNEL_KERNEL_VERSION=v0.5.0 ./install.sh`。安装器检查 PyYAML 与私有配置，安装并启动 `x-tunnel.service` 和 Bot 服务。supervisor 只生成 X-Tunnel Provider，不读取/修改 ShellCrash 配置或重启 ShellCrash。部署目录可不同于当前目录，服务路径由脚本自动生成。

   安装时若没有 `project.env`，会从示例复制默认配置。自定义 Provider 路径时编辑该文件中的 `X_TUNNEL_PROVIDER_PATH`；用户需自行确保 ShellCrash 能遍历并读取 Provider 文件。

4. （可选）开启每天中国时间 18:00 自动测速：

   ```sh
   systemctl enable --now x-tunnel-speedtest.timer
   ```

   安装脚本默认不改变 timer 的启用状态。定时器执行 `x-tunnel-speedtest.service`，也可以从 Telegram 发送 `/speedtest` 手动启动完整自动测速。专用 Bot 私聊白名单还可使用 `/restartxtunnel`（或 `/restart`）重启 `x-tunnel.service`。

## 测速与分流行为

测速默认寻找两个稳定候选达到 300 Mbps；不足时按 50 Mbps 逐档回退至 100 Mbps（例如目标 400 时为 400、350、300、250、200、150、100）。通过 `project.env` 设置 `X_TUNNEL_TARGET_MBPS`，或在 Telegram 发送 `/topspeed 400` 设置目标；发送 `/topspeed` 查看当前值。达标 IP 数量默认仍为 2。每次自动测速启动前都会重新下载所选 IP 家族的 CF IP 段列表，再从最新列表抽样；自动流程中每个候选测速最长 20 秒，前 5 秒作为热身窗口不纳入均速；连续窗口波动较小的候选优先。达到同一速度档后，会在合格候选中按节点名旗帜/地区与测试返回的 Cloudflare colo 做地区匹配，再以稳定速度、波动和延迟排序；例如香港节点优先 HKG，日本优先日本 colo、没有时回退 HKG，San Jose 节点优先 SJC/LAX。未知地区不阻断测速，按速度质量回退。默认每轮只选两个 IP，因此最多自动更新两个匹配节点；其他节点保持原 IP。

Telegram 也支持 `/speedtest 104.17.76.76` 单独测试指定 IP。该模式只复用下载测速核心：总窗口 30 秒，第 5–30 秒的 1 秒窗口用于计算平均速度、中位数和变异系数；不进行前 3 秒低速淘汰，不更新 IP 段、不运行候选筛选、不写入节点配置。结果会通过 Bot 返回，并单独保存在 `cfip-data/single-<IP>-<时间>.json`。单 IP 测速与自动测速共用互斥锁和临时直连路由；若测速路由不能确认直连，任务会拒绝开始。命令行也可用 `python3 cfip_speedtest.py --single-ip 104.17.76.76` 执行。

测速期间 ShellCrash 服务不会被停止或重启。测速程序通过精确候选 IP 的临时路由尝试直连，不会改写 ShellCrash 配置或防火墙规则；无法确认直连时拒绝测速。临时路由在结束、异常或 SIGTERM/SIGINT 时清理；若进程被强杀，下一轮会按 `/run/lock/` 中的状态记录清理残留路由。Telegram 进度通过编辑同一条消息约每 3 秒更新，IP 以行内代码显示，方便复制。

ShellCrash 需要的 local provider、节点组、AI 过滤、路由规则及必要的直连规则由用户自行维护。X-Tunnel 只更新 provider JSON；节点上游可能触发的代理回环需在 ShellCrash 自身配置/网络侧单独处理。

需要 AI 分流时，请在 ShellCrash 配置中自行维护 AI 节点组与规则，并设置节点筛选表达式。X-Tunnel 同步器不改动这些配置。

## 日常运维

```sh
systemctl status x-tunnel.service x-tunnel-speedtest-bot.service
journalctl -u x-tunnel.service -f
journalctl -u x-tunnel-speedtest.service -f
systemctl start x-tunnel-speedtest.service
systemctl list-timers x-tunnel-speedtest.timer
```

编辑 `x-tunnel.yaml` 后 supervisor 会自动重载相应节点。测速完整结果保存在 `cfip-data/latest-results.json` 和带时间戳的 `cfip-data/scan-*.json`。Bot 命令无回应时，先检查 bot service、`speedtest-bot.env` 权限/Chat ID、代理连通性和 journal 日志。

## 迁移

停止服务后整体复制项目目录，保留 `x-tunnel.yaml`、`speedtest-bot.env` 和 `project.env`；若通过 Git 获取项目，私有配置不会随仓库下载，需要从示例模板重新创建。目标机器安装 `/usr/bin/x-tunnel` 与 PyYAML，再执行 `./install.sh`。若不需要搬迁历史测速报告，可不复制 `cfip-data/`；`runtime/` 是可重新生成的运行状态，也可以不复制。迁移后检查 Bot Chat ID、Provider 输出路径、监听地址和端口；ShellCrash 中的 Provider 路径由用户自行配置。

```sh
systemctl stop x-tunnel-speedtest-bot.service x-tunnel.service
# 将整个项目目录复制到目标机器
# 在目标机器按上文安装依赖与内核后执行：
./install.sh
```
