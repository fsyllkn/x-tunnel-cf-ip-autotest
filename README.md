# X-Tunnel 节点、测速与部署手册

本项目管理 x-tunnel 内核进程、生成 sing-box 本地节点 Provider、Cloudflare IP 测速与 Telegram 通知。支持 systemd Linux 与 OpenWrt procd；ShellCrash 与 X-Tunnel 服务互相独立，ShellCrash 只需由用户自行配置为读取生成的 Provider。项目目录可整体迁移；安装脚本按实际目录生成服务路径。

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
- `openwrt/`：OpenWrt procd 服务模板与 cron 测速入口。
- `x-tunnel.example.yaml`、`speedtest-bot.env.example`、`project.env.example`：不含真实凭据的初始配置模板。
- `x-tunnel.md`：旧版内核配置参考文件；当前服务不读取它。

项目不会维护 `/etc/ShellCrash/jsons/config.json`、`outbounds.json`，不会重启 ShellCrash，也不会修改其防火墙。默认 Provider 输出到项目 `runtime/`；用户可修改 `X_TUNNEL_PROVIDER_PATH` 自定义输出路径。请自行在 ShellCrash 配置中添加 local provider 和所需节点组，并将路径指向同一个 Provider 文件。

公开源码包为 `x-tunnel-source.tar.gz`。其中不包含当前节点配置、Bot 凭据、运行报告或生成 Provider；发布到 GitHub 前仍需自行选择并添加开源许可证。

`X-Tunnel-ShellCrash-Loop-Fix.md` 是旧版自动联动方案的历史文档；当前项目不再自动改写 ShellCrash 路由、防火墙或重启服务。

## 一键安装与卸载（OpenWrt / Linux）

支持新版 OpenWrt `apk`、旧版 `opkg` 和 Debian/Ubuntu `apt-get`。自动补齐常用 Python 依赖，下载仓库并保留已有私有配置。首次使用时会创建节点与 Telegram 配置模板，**必须编辑并再次执行安装命令**，才会注册服务。

在 root 终端运行（建议先查看脚本内容）：

```sh
curl -fL https://raw.githubusercontent.com/fsyllkn/x-tunnel-cf-ip-autotest/main/quick-install.sh -o /tmp/x-tunnel-quick-install.sh
sh /tmp/x-tunnel-quick-install.sh
```

OpenWrt 默认部署到 `/overlay/x-tunnel-cf-ip-autotest`，Linux systemd 默认 `/opt/x-tunnel-cf-ip-autotest`；可通过 `X_TUNNEL_INSTALL_DIR` 指定绝对路径。已有 Git 克隆目录会执行 `git pull --ff-only`，不覆盖私有 `x-tunnel.yaml`、`speedtest-bot.env` 或 `project.env`。

清理已部署的服务和项目 cron（保留配置及目录）：

```sh
cd /overlay/x-tunnel-cf-ip-autotest
sh uninstall.sh
```

需要**额外删除本项目目录和私有配置**时可执行 `sh uninstall.sh --purge` 并输入 `DELETE` 确认。两种方式均不删除共享的 `/usr/bin/x-tunnel` 内核、不卸载 Python 依赖、不改动 ShellCrash 配置。此脚本尚未在所有 OpenWrt 设备上实机验证，安装后请检查 procd 状态与日志。

## 部署

适用环境：systemd Linux 或 OpenWrt、root 权限、Python 3 与 PyYAML。ShellCrash 不是 X-Tunnel 安装器的依赖。安装器自动识别 `x86_64/amd64` 或 `aarch64/arm64`。若 `/usr/bin/x-tunnel` 不存在，会从官方 GitHub Releases 下载匹配架构的 Linux 内核，校验 ELF 架构并运行 `-h` 启动检查后安装；若内核已存在则保留原文件，仅校验架构并补齐 `0755` 权限。自动下载目前支持上述两种 64 位架构，其他架构需自行准备兼容内核。

1. 创建运行配置：

   ```sh
   cd /path/to/x-tunnel
   cp x-tunnel.example.yaml x-tunnel.yaml
   chmod 0600 x-tunnel.yaml
   editor x-tunnel.yaml
   ```

   每行节点格式为 `名称|服务地址|密钥token|cfip|连接数|端口`。可省略末尾可选字段，默认值来自 `defaults`。`max_nodes` 控制最多读取多少个节点，默认值为 5，可设为任意正整数；如果 `nodes` 中的行数超过该值，只读取前 `max_nodes` 行，其余忽略并在日志中警告，不会因此阻止服务启动。`first_port` 是第一个节点的首选端口，后续节点按顺序递增。服务启动前会检查所有监听地址的端口是否可绑定；若首选端口已被占用，或节点间配置了重复端口，会自动向后寻找可用端口并同步实际端口到 Provider。重启时若首选端口仍被旧 X-Tunnel 监听，会短暂等待释放后再顺延；若检测到其他进程占用并触发顺延，会向测速 Bot 发送通知。配置文件变化由 supervisor 每 2 秒检测并自动重载。监听地址默认本机回环。若要 LAN 接入，在 `project.env` 设置 `X_TUNNEL_LISTEN_HOSTS=127.0.0.1,<本机实际LAN地址>`，服务会绑定这些地址；空值只绑定 `127.0.0.1`。每个节点实例的地址由此变量统一管理，不需要在 YAML 里写死局域网地址。

2. 设置 Telegram 专用 Bot。先在 Telegram 对专用 Bot 发送 `/start`，然后：

   ```sh
   cp speedtest-bot.env.example speedtest-bot.env
   chmod 0600 speedtest-bot.env
   editor speedtest-bot.env
   python3 telegram_speedtest_bot.py --show-chat-ids
   ```

   将 BotFather 提供的 token 填入 `TG_TOKEN`，将命令显示的私聊数字 ID 填入 `TG_CHATID`。不要把 token 发到聊天、日志或工单中。

   Telegram 出站代理在 `project.env` 中配置。默认值 `X_TUNNEL_LOCAL_PROXY=http://127.0.0.1:7890` 假设本机已有 HTTP 代理服务监听 `127.0.0.1:7890`，通常是 ShellCrash 的 HTTP/mixed 代理端口；X-Tunnel 项目本身不会创建这个代理监听。如果当前设备没有 ShellCrash，或 ShellCrash 使用其他端口，请改成设备上实际可用的代理地址，例如：

   ```sh
   X_TUNNEL_LOCAL_PROXY=http://127.0.0.1:你的代理端口
   # 或使用局域网内其他代理服务：
   X_TUNNEL_LOCAL_PROXY=http://代理服务器的局域网IP:代理端口
   ```

   `X_TUNNEL_TG_PROXY` 是可选的 Telegram 专用代理，设置后优先尝试该地址，失败时回退到 `X_TUNNEL_LOCAL_PROXY`；留空则直接使用本机代理。至少要有一个可连接的代理，否则 Bot 命令轮询和测速通知无法连接 Telegram。修改代理变量后重启 Bot 服务和测速服务使其生效。

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

## OpenWrt 安装

OpenWrt 使用 procd 管理常驻的 X-Tunnel supervisor 与 Telegram Bot；测速任务由 Bot 直接后台启动，运行日志写入 `cfip-data/openwrt-speedtest.log`。安装器会检测 OpenWrt 并安装 `/etc/init.d/x-tunnel` 与 `/etc/init.d/x-tunnel-speedtest-bot`，不调用 systemd。当前内核下载和 ELF 校验仅支持 `x86_64` 与 `aarch64`；其他架构暂不支持此安装器。

先通过设备现有包管理器安装依赖。包名会随 OpenWrt 分支和架构略有不同，可先检查对应 feed 是否提供这些包：

```sh
# opkg 系统示例
opkg update
opkg install python3-light python3-codecs python3-yaml python3-urllib python3-openssl python3-logging ip-full curl ca-bundle

# apk 系统示例
apk update
apk add python3-light python3-codecs python3-yaml python3-urllib python3-openssl python3-logging ip-full curl ca-bundle
```

OpenWrt 精简 Python 环境必须具备 `encodings.idna` 编码器，否则 Telegram Bot 使用 HTTPS 代理时可能出现 `LookupError: unknown encoding: idna`。安装前可执行 `python3 -c 'import codecs; codecs.lookup("idna")'` 验证。缺失时通过 `apk add python3-codecs`（或旧版 `opkg install python3-codecs`）补齐。安装器也会在启动服务前检查该编码器并报告明确错误。

建议把项目放到有足够可写空间的持久化存储中（例如 USB/extroot），然后按常规步骤准备 `x-tunnel.yaml`、`speedtest-bot.env` 和 `project.env`，执行：

```sh
chmod +x install.sh
./install.sh
```

每天 18:00（设备本地时区）运行测速可执行 `./install.sh --enable-timer`。安装器会将任务加入 `/etc/crontabs/root` 并启用 cron。OpenWrt 安装中通过 Bot 启动的测速进程输出到 `cfip-data/openwrt-speedtest.log`。

```sh
/etc/init.d/x-tunnel status
/etc/init.d/x-tunnel restart
/etc/init.d/x-tunnel-speedtest-bot status
logread -e x-tunnel
tail -f /home/x-tunnel/cfip-data/openwrt-speedtest.log
```

若项目不在 `/home/x-tunnel`，查看日志时将示例路径替换为实际项目目录。测速需要可用的 `ip` 路由工具及足够空间保存 Python 运行环境和测速数据；低存储路由器建议只运行 X-Tunnel 节点服务，不安装 Bot 与自动测速。

## 测速与分流行为

自动测速另设 50 Mbps 写入硬门槛：若未凑够 100 Mbps 达标 IP，可回退使用 50–99 Mbps 的结果；低于 50 Mbps 或 1 秒窗口波动系数（CV）超过 25% 的结果只写入测速报告，不会替换节点 IP。测速连接未覆盖完整窗口时按无效结果处理，不进入候选选择。

测速目标默认 300 Mbps；不足时按 50 Mbps 逐档回退至 100 Mbps（例如目标 400 时为 400、350、300、250、200、150、100）。通过 `project.env` 设置 `X_TUNNEL_TARGET_MBPS`，或在 Telegram 发送 `/topspeed 400` 设置目标；发送 `/topspeed` 查看当前值。默认选取数按 `max_nodes` 生效后的节点数计算，最多选 3 个不同测速 IP；可用 `--target-count` 显式指定数量。每次自动测速启动前都会重新下载所选 IP 家族的 CF IP 段列表，再从最新列表抽样；控制面数据下载先直连，失败时重试并回退到 `project.env` 配置的代理，所有下载失败会报告具体来源且不会静默使用旧 IP 段。每轮最多测速 2 个新候选，最多 3 轮（最多 6 个不同 IP），默认每个候选最多 30 秒（可用 `--speed-duration` 设为 15–30 秒），前 5 秒作为热身窗口不纳入均速；连续窗口波动较小的候选优先。由于 CF IP 采用 anycast，IP 段不能可靠地预先标成香港、日本或北美；程序在 RTT 探测时读取 CF-RAY colo，依据目标节点地区选择待测候选，优先覆盖 HK、北美和其他已配置亚洲地区。每轮后同时检查速度档、地区匹配和每节点备用 IP 覆盖，未满足时继续补测，最多三轮。测速后为每个可匹配节点写入最多两个通过稳定性及 50 Mbps 最低门槛的不同 CF IP，内核按列表自动切换连接；若本次合格候选不足两个，会写入可用地址并在报告/通知中标明缺少备用 IP。香港节点只接受实际测速 colo 为 HKG 的 IP；没有合格 HKG 时跳过该香港节点写入，不会回退到 LAX/SJC。日韩、新加坡等节点在本地 colo 缺少时按邻近规则回退，优先使用 HKG；HKG 候选可同时分配给香港及邻近亚洲节点，不会因被香港节点占用而改用更远的 LAX/SJC。美加节点使用可用北美 colo。实际测速只使用 Cloudflare 返回的 colo，不会虚构 SIN 或日本 colo；每个节点最终分配的 colo、直配状态和备用 IP 都会记录在报告及通知中。

每次自动测速都会把当前节点正在使用的 IP，以及最近测速中合格的历史 IP 作为补充候选；这些历史地址只用于补充地区覆盖，仍必须在本轮重新通过 RTT、速度和稳定性检查后才会作为测速优选写入。写入时保留节点原有 IP 与 `defaults.cfip` 地址作为后备，因为 HTTP 下载测速合格不代表该 CF 边缘一定适用于 X-Tunnel 的 WebSocket 隧道连接。

Telegram 也支持 `/speedtest 104.17.76.76` 单独测试指定 IP。它与批量测速使用相同的测速 URL、传输协议（默认 HTTP）和 30 秒窗口，按第 5–30 秒窗口计算均速、中位数和变异系数；单测不会执行自动候选筛选或前 3 秒低速淘汰，以便完整观察该 IP。只有完整采样、均速至少 100 Mbps 且窗口 CV 不超过 25% 时，单测才会自动更新匹配地区节点；其他结果会保留原配置并说明原因。批量测速可通过 `--speed-duration` 缩短到 15–29 秒，因此比较结果时应保持批量窗口为默认 30 秒。香港节点仍只接受 HKG colo。测速与节点写入结果通过 Bot 返回，并单独保存在 `cfip-data/single-<IP>-<时间>.json`。单 IP 测速与自动测速共用互斥锁和临时直连路由；若测速路由不能确认直连，任务会拒绝开始。命令行也可用 `python3 cfip_speedtest.py --single-ip 104.17.76.76` 执行；`--tls` 可让两种模式都使用 HTTPS。

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
