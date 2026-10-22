# X-Tunnel for OpenWrt / iStoreOS

> **OpenWrt 轻量分支：** [openwrt](https://github.com/fsyllkn/x-tunnel-cf-ip-autotest/tree/openwrt)（BusyBox / procd，兼容 `opkg` 和 `apk`）。Debian、Ubuntu 等 systemd 系统请使用 [main 分支](https://github.com/fsyllkn/x-tunnel-cf-ip-autotest/tree/main)。两条分支的一键安装链接不要混用。

X-Tunnel 在 OpenWrt 上运行独立的 SOCKS5 节点，同时导出 sing-box JSON、Mihomo/Clash YAML、Xray/V2Ray SOCKS 出站和 SOCKS5 分享链接。**默认只安装核心节点管理服务**；Telegram Bot、CF IP 测速及每日计划任务按需启用。

## 1. 一键安装（推荐）

在 OpenWrt / iStoreOS 的 **root SSH 终端**运行：

```sh
wget -O /tmp/xtunnel-openwrt.sh https://raw.githubusercontent.com/fsyllkn/x-tunnel-cf-ip-autotest/openwrt/quick-install.sh
sh /tmp/xtunnel-openwrt.sh
```

如果固件没有 `wget`，但已有 `curl`，也可以先下载：

```sh
curl -fL https://raw.githubusercontent.com/fsyllkn/x-tunnel-cf-ip-autotest/openwrt/quick-install.sh -o /tmp/xtunnel-openwrt.sh
sh /tmp/xtunnel-openwrt.sh
```

**执行流程：** 检测 OpenWrt → 下载完整 `openwrt` 分支源码压缩包（不包含 Git 历史）→ 解压到默认目录 → 创建缺失的配置模板 → 注册 `x-t` 命令 → 打开交互式管理菜单。

**进入菜单本身不会安装 Python 依赖、启动或重启服务。** 只有选择安装、更新、重启或测速等具体操作才会执行相应任务。下载源码需要可用网络，以及 `sh`、`tar` 和 `wget` / `curl`；管理菜单依赖 BusyBox 常见工具（如 `vi`、`grep`）。

当前自动下载 X-Tunnel Linux 内核支持 **x86_64/amd64 和 aarch64/arm64**。二进制还必须兼容当前固件的 libc / ABI；安装器会检查 ELF 架构并尝试运行 `-h`。低内存或低存储固件请优先安装核心服务。

### 第一次安装：菜单操作顺序

1. 选择 **5 编辑 X-Tunnel 节点**，填写真实节点地址、Token 和 CF IP。
2. 选择 **1 安装/修复核心服务**。安装器按需补齐 Python 核心依赖，并启动 `x-tunnel` procd 服务。
3. 需要 Telegram Bot 时，再选 **3 设置 Telegram Bot**，选 **4 编辑项目变量**设置实际可用的代理地址，最后选 **13 安装/启用 Telegram Bot**。
4. 使用菜单 **8 查看日志**，或在 SSH 中查看服务状态。不要把“procd 运行中”直接等同于所有节点均已连通。

不需要 Bot 的设备无需填写 `TG_TOKEN`、`TG_CHATID`，也无需安装 Bot/测速专用依赖。

## 2. SSH 管理菜单与快捷入口

安装入口下载完成后即可使用：

```sh
x-t
```

`x-t` 实际调用项目目录中的 `menu.sh`，**不需要每次通过 GitHub 重新下载**。没有全局命令时也可运行：

```sh
cd /overlay/x-tunnel-cf-ip-autotest
sh menu.sh
```

当前菜单选项：

```text
 1) 安装/修复核心服务       2) GitHub 更新 OpenWrt 分支
 3) 设置 Telegram Bot      4) 编辑项目变量配置
 5) 编辑 X-Tunnel 节点     6) X-Tunnel 服务管理
 7) Telegram Bot 服务管理  8) 查看日志
 9) 定时测速管理          10) 立即运行完整测速
11) 配置检查              12) 卸载服务（保留配置）
13) 安装/启用 Telegram Bot 14) 关闭 Bot 节省内存
15) 一键重启 X-Tunnel     16) 一键重启 Telegram Bot
 0) 退出
```

- **6 / 7**：启动、停止、重启及开机自启管理。
- **15**：仅重启 X-Tunnel 核心与其节点，现有连接会短暂中断。
- **16**：仅重启 Telegram Bot，不重启 X-Tunnel 节点。
- **2 GitHub 更新**：下载最新代码并调用核心安装程序，**会重启核心服务**；不会自动替你重新配置或重启 Bot。
- **14 关闭 Bot**：停止 Bot 并取消其开机自启，不重启 X-Tunnel 核心服务。

配置编辑使用 `vi`；修改前会备份到 `cfip-data/config-backups/`，涉及密钥的文件使用 `0600` 权限。节点配置由 supervisor 监测后自动重载（通常在数秒内）；Bot/项目代理变量变更后请使用相应的服务重启选项。

## 3. 默认目录与安装文件

默认项目路径为：

```text
/overlay/x-tunnel-cf-ip-autotest/
├── quick-install.sh            # GitHub 一键安装/更新入口
├── install.sh                  # 安装 procd 服务与按需依赖
├── menu.sh                     # x-t 调用的菜单
├── uninstall.sh                # 卸载服务
├── x-tunnel.yaml               # 节点配置（私有，首次生成）
├── speedtest-bot.env           # Bot 凭据（选配，首次使用时创建）
├── project.env                 # 项目变量（首次生成）
├── supervisor.py               # 多节点守护进程
├── sync_singbox.py             # 本地 Provider 生成
├── telegram_speedtest_bot.py   # Bot 程序
├── cfip_speedtest.py           # CF IP 测速程序
├── openwrt/                    # procd 服务模板及测速脚本
├── runtime/                    # 生成的 Provider（运行后）
└── cfip-data/                  # 测速记录、日志与配置备份
```

`runtime/`、`cfip-data/` 与 `speedtest-bot.env` 在首次下载时不一定存在，由相应安装或运行步骤生成。

系统级路径：

| 路径 | 作用 |
| --- | --- |
| `/usr/bin/x-t` | 菜单快捷命令 |
| `/usr/bin/x-tunnel` | X-Tunnel 内核二进制 |
| `/etc/init.d/x-tunnel` | 核心 procd 服务 |
| `/etc/init.d/x-tunnel-speedtest-bot` | Bot procd 服务（选配） |
| `/etc/crontabs/root` | 选配的每日测速任务 |

若系统没有 `/overlay`，默认项目目录改为 `/root/x-tunnel-cf-ip-autotest`。可通过环境变量 `X_TUNNEL_INSTALL_DIR` 指定其他**绝对路径**，如：

```sh
X_TUNNEL_INSTALL_DIR=/mnt/storage/x-tunnel-cf-ip-autotest sh /tmp/xtunnel-openwrt.sh
```

一键安装下载的是**完整源码快照**，不是仅包含运行文件的精简包；按需安装指的是运行服务及其系统依赖。减少源码体积主要节省存储空间，不等于减少 X-Tunnel 内核的运行内存。

## 4. 配置节点、Bot 与项目变量

### 4.1 节点配置：`x-tunnel.yaml`

从 `x-tunnel.example.yaml` 创建。节点行格式为：

```text
名称|服务地址|token|cfip|连接数|端口
```

示例结构（以下是占位符，不可直接用于连接）：

```yaml
defaults:
  token: "替换为真实密钥"
  cfip: "替换为可用的 Cloudflare IP"
  connections: 3
  ech: "cloudflare-ech.com"
  dns: "https://doh.pub/dns-query"
  listen_hosts:
    - "127.0.0.1"
  first_port: 32322
max_nodes: 5
nodes:
  - "香港|wss://example.com/about:443||||"
```

未填写的可选字段采用 `defaults` 中的值。具体连接格式以实际内核及可用配置为准。每个节点通常对应独立的 X-Tunnel 内核进程；默认 `connections: 3`，在低内存环境可**备份后实验性降低至 1**，并比较连接稳定性与吞吐。节点行中显式填写的连接数优先于默认值。

### 4.2 Telegram Bot：`speedtest-bot.env`

通过菜单选项 **3** 修改：

```sh
TG_TOKEN='你的BotFather令牌'
TG_CHATID='你的私聊数字ChatID'
```

必须填写有效的 Token 与数字 Chat ID，选项 **13** 才能启用 Bot。不要公开此文件、推送到仓库或粘贴到日志中。

### 4.3 项目变量：`project.env`

通过菜单选项 **4** 修改。常用变量：

| 变量 | 用途 |
| --- | --- |
| `X_TUNNEL_LOCAL_PROXY` | Telegram 请求使用的本地或局域网 HTTP 代理；模板默认 `http://127.0.0.1:7890` |
| `X_TUNNEL_TG_PROXY` | 可选 Telegram 专用代理，优先于本地代理 |
| `X_TUNNEL_PROVIDER_PATH` | Provider 输出位置，默认 `runtime/x-tunnel-provider.json` |
| `X_TUNNEL_LISTEN_HOSTS` | SOCKS5 监听 IP，默认仅本机 `127.0.0.1` |
| `X_TUNNEL_TARGET_MBPS` | CF IP 测速目标速度，默认 300 Mbps |

例如 ShellCrash HTTP 代理在其他 LAN 设备的 `192.168.68.21:7890`：

```sh
X_TUNNEL_LOCAL_PROXY=http://192.168.68.21:7890
```

上述地址仅为示例，必须改成你的**实际可连通**代理。如果要让局域网其他设备访问 SOCKS5，还需正确设置监听 IP 和系统防火墙；请勿无意开放到 WAN。

## 5. 安装模式和资源占用

| 模式 | 调用方式 | 行为 |
| --- | --- | --- |
| 核心（默认） | 菜单 **1** 或 `--core` | 安装 Python 核心依赖、X-Tunnel 内核、supervisor 与 Provider；不要求 Bot 凭据 |
| 核心 + Bot | 菜单 **13** 或 `--with-bot` | 增加 Telegram Bot 和测速需要的 HTTPS/IDNA、`ip-full` 等依赖，并启动 Bot |
| 测速依赖 | `--with-speedtest` | 安装测速依赖，不启用 Bot |
| 定时测速 | 菜单 **9** 或 `--enable-timer` | 设置设备**本地时间 18:00** 的 cron 任务 |
| 关闭 Bot | 菜单 **14** 或 `--disable-bot` | 停用已安装 Bot；菜单选项 14 不重启核心，安装器的 `--disable-bot` 会重新安装/启动核心 |

无交互终端（自动化脚本/远程任务）时，使用明确参数：

```sh
sh /tmp/xtunnel-openwrt.sh --core
sh /tmp/xtunnel-openwrt.sh --with-bot
sh /tmp/xtunnel-openwrt.sh --with-speedtest
sh /tmp/xtunnel-openwrt.sh --enable-timer
```

这些命令会更新源码并执行安装；**不是只修改当前运行模式而不重启核心**。如果第一次生成的是示例节点配置，安装入口会提示编辑并退出，配置完成后再次执行。仅查看菜单可以运行 `x-t`，不会触发更新。

注意：低内存设备的主要 RAM 消耗可能来自**每个节点各自运行的 X-Tunnel 内核进程**。不启用 Bot 可减少一个常驻程序，但不意味着 5 个核心进程能压缩成一个。

### 配合 reF1nd sing-box eBPF 的专用 UID

OpenWrt 分支的 `supervisor.py` **默认仅将 X-Tunnel Go 子进程以 UID/GID 29002 运行**；supervisor 仍是 root，负责节点管理、Provider 写入，Telegram Bot 和 CF IP 测速仍使用自己的 root 服务/任务。因此不必在 sing-box 的 eBPF 中排除全部 root (UID 0)。

- 不要求 `adduser`，Linux 数字 UID 可以直接用于进程；安装前确认 29002 未被其他系统用户占用。
- 每个节点以 `runtime/engines/<hash>-<port>/` 作为独立工作目录，归 UID 29002 所有，便于写入 GeoIP 等本地缓存且避免并发临时文件冲突。
- 在 `project.env` 中可设置 `X_TUNNEL_PROCESS_UID=29002`（默认为 29002）；更换 UID 后需同步调整 sing-box `inbounds[].local.exclude_uid`，并重启 X-Tunnel 核心。
- **仅更新源码不会自动替换正在运行的 root 内核进程**。安排维护窗口，使用 `x-t` 菜单 **15** 单独重启核心，Bot 无需重启。切换时 SOCKS5 连接会暂时中断。

在 OpenWrt 检查子进程实际 UID：

```sh
for pid in $(pidof x-tunnel 2>/dev/null); do
    awk -v pid="$pid" '/^Uid:/ { print "X-Tunnel PID=" pid, "UID=" $2 }' "/proc/$pid/status"
done
```

正常预期每个子进程 UID 都是 `29002`，不是 `0`。如果仍为 root，说明还没有部署新版 supervisor 或没有重启核心。观察 `logread -e x-tunnel` 排查工作目录权限或二进制兼容性。

在 sing-box 的实际配置 `/opt/sing-box/config/config.json` 里设置 `inbounds[].local.exclude_uid: [29002]`，不要为了排除 X-Tunnel 而把 `0` 放进去。配置生效前建议先确认对应 UID 已切换。此豁免只影响 X-Tunnel 内核子进程；**root 权限的 CF IP 测速进程仍可能被 sing-box 的 eBPF 本机接管**，需要另行验证测速直连路由和防回环策略。

## 6. 更新、重装与迁移

### 正常更新

**只打开本地菜单：**

```sh
x-t
```

**更新 GitHub 源码并重新部署：** 在菜单选择 **2**，需确认；更新会重启核心服务。若要从系统原始一键入口更新菜单源码，也可重新运行第 1 节的下载命令。

一键入口行为：

- 目标目录不存在：下载并解压 `openwrt` 分支完整源码快照。
- 目标目录包含 Git 仓库且已安装 Git：要求当前处于 `openwrt` 分支，随后执行 `git pull --ff-only`；如果是 `main` 分支则拒绝自动切换。
- 目标目录没有 Git 仓库：复制更新源码文件；独立的私有配置和运行数据不会作为仓库源码下载，但更新前仍建议备份。

### 从旧 `main` 部署迁移

若原有目录是 Git 仓库且处于 `main`，**不要用新的一键入口直接覆盖**。先备份私有配置和需要保留的测速报告，确认无未提交修改，再手动切换：

```sh
cd /overlay/x-tunnel-cf-ip-autotest
mkdir -p /overlay/x-tunnel-config-backup
cp -p x-tunnel.yaml project.env /overlay/x-tunnel-config-backup/
[ ! -f speedtest-bot.env ] || cp -p speedtest-bot.env /overlay/x-tunnel-config-backup/
git status --short
git fetch origin openwrt
git switch openwrt
```

如有本地 Git 修改，先处理冲突，不要强制切换。切换后运行 `sh menu.sh`，按需安装。重新部署前如需停止原服务，先在维护窗口进行，以避免中断正在使用的 SOCKS5 节点。

### 卸载

在菜单选择 **12**，或运行：

```sh
cd /overlay/x-tunnel-cf-ip-autotest
sh uninstall.sh
```

这会停止、禁用并移除本项目 procd 服务及其计划任务，同时清理匹配的 `x-t` 菜单快捷方式；**保留项目目录、私有配置、测速记录和共享的 `/usr/bin/x-tunnel` 内核**，不改动 ShellCrash。

如确实要删除整个项目和私有配置（不可恢复），可在备份后执行 `sh uninstall.sh --purge`，并在交互提示中输入 `DELETE`。这不会删除共享内核或自动卸载系统软件包。

## 7. 验证安装与问题排查

### 核心与 Bot 是否启动

```sh
/etc/init.d/x-tunnel status
/etc/init.d/x-tunnel-speedtest-bot status
logread | tail -n 80
```

只安装核心时，Bot 服务不存在属于正常情况。procd 显示 `running` 说明服务进程存在，还需结合 SOCKS5 端口及连通性检测。OpenWrt 的 `logread` 将某些子进程输出归入 `daemon.err`，但含 `INFO`、ECH 查询或通道就绪的行不必然是故障。

`x-t` → **8 查看日志** 改为独立四个入口：**1 X-Tunnel 连接日志**、**2 CF IP 测速日志**、**3 Telegram Bot 日志**、**4 服务状态**（5 为测速环境自检）。核心和 Bot 的 OpenWrt 日志按当前服务脚本对应的 Python PID 精确筛选 syslog 标签 `python3[PID]:`，不会再显示 sing-box 或 dnsmasq 的日志；CF 测速只显示 `cfip-data/openwrt-speedtest.log`。因为 procd 默认按 `python3[PID]` 标记继承的 Go 内核 stdout，这个筛选也能显示 X-Tunnel 的 smux/客户端连接行。**如果核心已停止，无法通过当前 PID 确定旧 syslog 的归属，菜单会提示而不会显示混杂的系统日志**；不会因为查看日志而启动服务或重新测速。


### 多格式 SOCKS5 Provider 导出（纯节点，无网络接管）

核心启动、节点配置变更时，`sync_singbox.py` 会自动从同一批已分配的 SOCKS5 端口生成以下文件（默认位于 `/overlay/x-tunnel-cf-ip-autotest/runtime/`）：

| 文件 | 格式 | 用途 |
| --- | --- | --- |
| `x-tunnel-provider.json` | sing-box `{"outbounds":[{"type":"socks", ...}]}` | 保留原生本地 Provider，向后兼容 |
| `x-tunnel-clash.yaml` | Mihomo/Clash `proxies:`，`type: socks5` | 作为 **proxy-provider** 文件导入 Clash/Mihomo |
| `x-tunnel-v2ray.json` | Xray/V2Ray `outbounds`，`protocol: socks` | 合并到现有 Xray/V2Ray 配置中的出站片段；不是完整可运行配置 |
| `x-tunnel-socks5.txt` | 每行 `socks5://host:port#name` | 支持此 URI 的客户端可尝试导入；兼容性视客户端版本而定 |

示例 Clash/Mihomo YAML：

```yaml
proxies:
  - name: "🇭🇰HKG_x-tunnel"
    type: socks5
    server: 127.0.0.1
    port: 32322
```

示例 Xray/V2Ray 出站片段：

```json
{
  "outbounds": [{
    "tag": "🇭🇰HKG_x-tunnel",
    "protocol": "socks",
    "settings": {"servers": [{"address": "127.0.0.1", "port": 32322}]}
  }]
}
```

**`ss://` 是 Shadowsocks 协议链接，不是 SOCKS5。** X-Tunnel 当前仅监听 SOCKS5，不能把端口伪装成 `ss://` 供 V2Ray 工具导入。已导出 `socks5://` 链接；不增加额外 Shadowsocks 桥接进程、不对 OpenWrt 做 TUN、TProxy、DNS 或分流规则修改。

`127.0.0.1` 只在 **运行 X-Tunnel 的同一台机器** 有效。Windows、手机或局域网其他设备若要连接，须显式配置安全的监听地址及防火墙访问控制，并将其导入配置中的 host 调整为可达的 LAN IP；默认不向 LAN 暴露无认证 SOCKS5，切勿暴露公网。

手动只刷新伴随导出文件（**不会重启节点，不会重分配端口，也不会修改原始 Provider**）：

```sh
cd /overlay/x-tunnel-cf-ip-autotest
python3 sync_singbox.py --export-only
```

也可以在 `x-t` 主菜单选择 **18) 导出 Clash/V2Ray/SOCKS5 节点**。如果自定义了 `X_TUNNEL_PROVIDER_PATH`，伴随文件会保存在该 JSON 的相同目录下。使用测试命令 `python3 -m unittest discover -s tests -p test_multi_export.py` 可检查格式有效性（需要 PyYAML；测试不会启动 X-Tunnel）。

**停止服务快捷项：** 主菜单 **17) 停止 X-Tunnel 核心** 会先询问确认，随后停止核心 supervisor 和全部 SOCKS5 子进程；不会停止 Telegram Bot、关闭定时测速、删除节点或修改 DNS/路由。需要重新启动可进入主菜单 **6) X-Tunnel 服务管理** → **1) 启动**。

### Provider 文件在哪里

默认在：

```text
/overlay/x-tunnel-cf-ip-autotest/runtime/x-tunnel-provider.json
```

需要用户自行在 ShellCrash / sing-box 中添加本地 Provider 引用。本项目不会自动修改 ShellCrash 配置、重启 ShellCrash 或更改其防火墙。

### `opkg update` 部分软件源失败

旧版 OpenWrt 的第三方 feed 可能返回 HTTP 错误。安装器最多尝试刷新一次包索引；若部分 feed 失败，会先警告并继续尝试从可用索引安装缺失软件包，**只有实际依赖安装失败才中止**。不会自动改写或禁用系统软件源。

```sh
opkg update
cat /etc/opkg/distfeeds.conf
cat /etc/opkg/customfeeds.conf 2>/dev/null
opkg list-installed | grep -E '^python3|^ip-full'
```

新 `apk` 固件也需要使用与自身发行版和架构匹配的软件包源。安装器不会自动替换源地址。

### Bot 回复“已启动测速”，但未实际测速

精简 OpenWrt 的 Python 3.11 可能没有 `decimal`，旧版 `cfip_speedtest.py` 引用标准库 `statistics` 时会在启动阶段崩溃：

```text
ModuleNotFoundError: No module named 'decimal'
```

`openwrt` 分支已将测速所用的均值、中位数和总体标准差计算改为不依赖 `statistics`，**不需要单独安装 `python3-decimal` 来解决这一个错误**。安装器在启用测速/Bot 时会进行模块导入预检，Bot 启动任务后会短暂确认后台进程是否立即退出。

更新代码后（仅运行一键入口、进入菜单不会重启现有核心服务），先在 `x-t` → **8 查看日志** → **4 测速环境自检** 中检查。也可以在 SSH 手动检查，不会启动测速或修改路由：

```sh
cd /overlay/x-tunnel-cf-ip-autotest
python3 -c 'import cfip_speedtest; print("测速模块导入正常")'
```

若通过，再在菜单选择 **16** 单独重启 Bot，使其加载新版命令处理程序；然后向 Telegram 发送 `/speedtest`。检查实际后台任务日志：

```sh
tail -n 80 /overlay/x-tunnel-cf-ip-autotest/cfip-data/openwrt-speedtest.log
logread | grep -E 'Bot received|Speed-test bot|Telegram polling' | tail -n 30
```

### 不安装 ShellCrash 的 OpenWrt 也可以测速

测速器已经支持 **native-direct**：当 `ip -4 rule show` 只有默认的 `local/main/default` 规则、`ip route get <候选IP>` 指向普通网口且没有异常策略路由时，测速直接沿现有默认路由发送请求，**不新增策略路由，不要求 ShellCrash 的表 100/101**。

例如无 ShellCrash 的旁路由（`br-lan` → `192.168.68.1`）可直接测速；但路由检查**无法排除上游网关代理或隐藏的 nftables/iptables 重定向**。如果设备存在其他未知策略路由，程序将拒绝自动处理。遇到带已知 `fwmark` 的 ShellCrash 模式则沿用原有的临时策略路由逻辑。

在运行完整测速之前，可通过 `x-t` → **8 查看日志** → **4 测速环境自检** 进行只读检查；或者执行：

```sh
cd /overlay/x-tunnel-cf-ip-autotest
ip -4 rule show
ip -4 route get 1.1.1.1
python3 -c 'from cfip_speedtest import DirectCandidateRoutes; print(DirectCandidateRoutes.routing_mode("-4", "100"))'
```

如显示 `('native-direct', None)`，表示**本机 Linux 路由规则**符合原生路由测速条件；不代表上级路由器一定直连公网。保持原有防泄漏原则，勿为通过测试而盲目修改防火墙和路由表。

### 核心安装报 `No module named 'urllib'`（Python 3.11 / core-only）

OpenWrt 的 `python3-light` 不是完整标准库。Python 3.11 `pathlib` 导入 `urllib.parse`；`supervisor.py` 在读取 `x-tunnel.yaml` **之前**便导入 `pathlib`，所以缺少 `python3-urllib` 时出现 `ModuleNotFoundError: No module named 'urllib'`，不表示节点配置错误。

旧版安装器仅在测速或 Bot 模式安装 `python3-urllib`，导致 `core-only` 缺少依赖。新版 `openwrt/install.sh` 已修复：核心模式也会检查/安装 `python3-urllib`，以及独立 UID 节点目录需要的 `python3-hashlib`；同时将“导入依赖失败”和“节点 YAML 配置错误”分开提示。**不改变已存在的 X-Tunnel Go 二进制、节点设置或测速功能。**

已安装的 OpenWrt 可以直接在 SSH 中补齐依赖，无需立即重新部署整个项目：

```sh
opkg update
opkg install python3-urllib
python3 -c 'import urllib.parse; from pathlib import Path; print("urllib/pathlib OK")'
python3 -c 'import hashlib; print(hashlib.sha256(b"test").hexdigest())'

cd /overlay/x-tunnel-cf-ip-autotest
python3 -c 'import supervisor; supervisor.read_specs(check_port_availability=False); print("core supervisor preflight OK")'
```

如 `hashlib` 检查也失败，再按需执行 `opkg install python3-hashlib`。若设备使用 `apk`，分别改为 `apk update`、`apk add python3-urllib` 和必要时的 `apk add python3-hashlib`。不要通过安装 `python3-decimal` 修复 `urllib` 缺失，也不要仅因这条 traceback 修改 `x-tunnel.yaml`。

以上检查全部只读，不会重启五个节点。需要重新安装 procd 服务时，再使用 `x-t` 菜单 1；**该安装动作会重启 X-Tunnel 核心，请在允许 SOCKS5 连接短暂中断时执行。**

### Bot 报 `unknown encoding: idna`

OpenWrt 的精简 Python 可能缺少标准库 IDNA 编码器；Bot 使用 HTTPS 代理时可能触发此报错。检查：

```sh
python3 -c 'import codecs; codecs.lookup("idna"); print("IDNA OK")'
```

旧版 `opkg` 固件可根据实际 feed 安装 `python3-codecs`；使用菜单选项 **13** 时安装器也会检查相应依赖。

### 低内存与 GeoIP 下载竞争

启动多个节点后可检查每个内核进程的常驻内存：

```sh
awk '/^(MemTotal|MemAvailable):/ {printf "%-17s %.1f MiB\n", $1, $2/1024}' /proc/meminfo
for pid in $(pidof x-tunnel 2>/dev/null); do
  echo "PID=$pid"
  grep -E '^(VmRSS|VmSize|Threads):' "/proc/$pid/status"
done
```

`VmSize` 是虚拟地址空间，不是实际物理内存。多进程 RSS 会包含共享页，不能简单相加作为精确的总占用。某些 OpenWrt 内核不提供 `/proc/PID/smaps_rollup`，属于统计接口限制。

如果启动时多个进程同时下载 `geoip.dat` / `geosite.dat`，偶尔可能发生同名临时文件的重命名竞争。若后续日志显示 Geo 文件成功加载、节点就绪，通常不影响当前运行；共享下载竞争仍需内核侧进一步修复。

---

本分支重点是 **轻量安装与按需运行**，而非取消 Python 或保证固定内存占用。不同 OpenWrt 固件的 Python 拆包、软件源及 X-Tunnel 二进制 ABI 存在差异，仍应以实机服务状态和日志为准。
