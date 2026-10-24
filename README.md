# PVE LXC 专用 X-Tunnel + Cloudflare IP 测速（lxc）

**这是 PVE 非特权 Debian/Ubuntu LXC 的配套分支。** 推荐先阅读 [LXC.md](LXC.md) 和 [sing-box LXC 部署指南](https://github.com/fsyllkn/linux_openwrt-sing-box/tree/lxc)。

其余 Linux systemd 功能与 main 的文档一致。下面保留完整功能介绍与管理说明。

---

# X-Tunnel + Cloudflare IP 测速（Debian / Ubuntu main）

本分支面向 **Debian / Ubuntu 的 systemd 环境**，承接 [OpenWrt 分支](https://github.com/fsyllkn/x-tunnel-cf-ip-autotest/tree/openwrt) 已验证的 CF IP RTT/下载测速、自动候选筛选、多节点 SOCKS5、Provider 导出、独立 UID 防 sing-box eBPF 回环及可配置定时测速。两条分支的服务管理不同：本分支使用 systemd，不修改路由器 nftables 或 sing-box 规则。

## 功能

- X-Tunnel 多个节点分别运行 SOCKS5 子进程，支持按节点管理、监控和配置热重载；端口由实际节点配置决定。
- supervisor 管理节点进程、端口冲突和异常重启。root supervisor 只管理进程/配置，**Go SOCKS5 子进程**使用专用非 root UID（默认 `29002`），主 GID 根据 ShellCrash 组按需选择（普通 LXC 为 `29002`，检测到 `shellcrash:7890` 时为 `7890`）。
- Cloudflare IPv4/IPv6 候选来源、1 秒 RTT/TLS/CF-RAY 初筛、下载测速、分档速度目标、地区分配与备用 IP；符合规则的候选写入 `x-tunnel.yaml`，保留原 IP 作为兜底。
- 支持生成 sing-box SOCKS5 Provider JSON、Clash/Mihomo YAML、Xray/V2Ray 出站片段、SOCKS5 分享链接。不生成虚构的 SS 节点。
- Telegram Bot 可选部署：通知、单 IP 测速和完整测速。**无需 Bot 凭据即可部署核心节点。**
- 使用 **systemd timer** 每日测速，默认设备本地时间 `18:00`，支持 `HH:MM` 修改与关闭，不重新启动 X-Tunnel 核心。
- 与 reF1nd sing-box 配合时，测速以 eBPF 排除的专用 UID 建立测试连接；识别已知单网口路由 `fwmark 0x1536 / table 53136`，不增删 sing-box 网关路由。

## 1. 系统与依赖

- Debian 11/12/13、Ubuntu 22.04/24.04+，systemd，x86_64/amd64 或 aarch64/arm64。
- root/sudo、Python **3.9+**、PyYAML、iproute2、CA 证书及 curl/wget。Python 3.9+ 用于不带 root 权限启动测试和节点子进程。
- Linux 上 `/usr/bin/x-tunnel` 可使用既有可用二进制；新装默认拉取 `fsyllkn/x-tunnel` 已验证的 `openwrt-stable-go126-v1` Linux 版本，校验 ELF 架构与 SOCKS5 握手，已安装的内核不会被无故替换。
- 该项目不是 VPN，也不接管主机透明代理路由；**不要同时让 X-Tunnel 与 sing-box 分别修改相同的策略路由/防火墙。**

## 2. 一键部署与菜单

```bash
# 查看安装入口后在 Debian/Ubuntu root shell 中运行
curl -fL https://raw.githubusercontent.com/fsyllkn/x-tunnel-cf-ip-autotest/main/quick-install.sh -o /tmp/xtunnel-linux.sh
sudo sh /tmp/xtunnel-linux.sh
```

无参数默认进入交互菜单，**不直接安装或重启后台服务**；文件下载到 `/opt/x-tunnel-cf-ip-autotest`，生成缺失的私有配置模板。更新已有 Git 仓库前须在 `main` 分支，且没有妨碍 fast-forward 的未提交改动。

先使用菜单 **5** 编辑真实的 `x-tunnel.yaml`，然后菜单 **1** 安装核心服务。也可命令行执行：

```bash
cd /opt/x-tunnel-cf-ip-autotest
sudo sh install.sh                 # 核心 SOCKS5 + Provider（不强制 Bot）
sudo sh install.sh --with-speedtest
sudo sh install.sh --with-bot      # 需要先填 speedtest-bot.env
sudo sh install.sh --disable-bot   # 不关闭核心服务
sudo sh install.sh --repair-kernel  # 显式修复内核，预检通过并备份后替换
```

首次安装会备份/保留私有配置，服务路径根据源码实际安装目录生成。建议系统级快捷入口 `x-t` 或兼容的 `xtunnel-menu`（通过一键安装入口创建）。

手动查看服务：

```bash
systemctl status x-tunnel.service
journalctl -u x-tunnel.service -n 80 --no-pager
sudo ss -lntp | grep x-tunnel
```

常用菜单：**6** 核心启停、**7** Bot 启停、**8** 日志与测速环境检查、**9** 定时测速、**10** 立即完整测速、**15** 重启核心、**16** 单独重启 Bot、**17** 停止 SOCKS5 节点、**18** 导出多格式 Provider。停止核心或重启节点会短暂影响依赖 SOCKS5 的代理连接，不会停止 sing-box 服务。

## 3. 每日 CF IP 测速时间：默认 18:00，可自定义

系统使用 **主机的本地时区**，不再强制 Asia/Shanghai。默认 `18:00`；支持 `00:00`、`06:45`、`21:30`、`23:59`，不接受 `8:30`、`24:00` 或 `18:60`。

菜单 `x-t` → **9** → **1** 可输入 `HH:MM`，直接回车使用保存的时间（未设置过时为 `18:00`）。也可直接修改，无须重新安装或重启 X-Tunnel：

```bash
cd /opt/x-tunnel-cf-ip-autotest

# 验证 HH:MM
python3 systemd/speedtest-timer.py validate 21:30

# 启用或修改每天 21:30，修改的是本项目的 systemd timer drop-in
sudo python3 systemd/speedtest-timer.py enable 21:30

# 当前状态；显示时间或 disabled
python3 systemd/speedtest-timer.py status

# 关闭自动测速，保存自定义时间以备再次开启
sudo python3 systemd/speedtest-timer.py disable
```

也支持首次安装：

```bash
sudo sh install.sh --enable-timer       # 默认 18:00 或已经保存的时间
sudo sh install.sh --timer-at=06:45     # 每天 06:45
```

定时器 override 文件：`/etc/systemd/system/x-tunnel-speedtest.timer.d/50-xtunnel-schedule.conf`。仅改写自己的 drop-in，保留其他 systemd timer；设置采用 `Persistent=false`，不会因为启用时间晚于当天计划时间就立即补跑一次。

验证：

```bash
systemctl list-timers --all x-tunnel-speedtest.timer
systemctl cat x-tunnel-speedtest.timer
timedatectl
journalctl -u x-tunnel-speedtest.service --no-pager -n 70
```

## 4. 与 sing-box eBPF 防回环

在 `project.env` 设置 `X_TUNNEL_PROCESS_UID=29002`（默认值）；UID 不应与已有系统用户冲突。`X_TUNNEL_PROCESS_GID=auto` 默认根据 `/etc/group` 精确匹配 `shellcrash:7890`，匹配时让 Go 核心及隔离测速 worker 使用主 GID `7890`，否则仍使用 GID `29002`。可设为 `uid` 禁止 GID 切换，或设为 `7890` 要求组存在才启动。即使 Linux 上只启用了 sing-box 本机 eBPF、尚未开启单网口 LAN 网关，测速程序也会检查正在运行的 sing-box 服务，并使用 eBPF 排除的专用 UID 测试直连网络，避免 root 流量被代理而导致 CF 测速虚高。**SOCKS5 Go 核心与隔离 CF RTT/下载子进程保持非 root UID，主 GID 单独选择；管理进程依然是 root**。测速前还会检查 sing-box 的网关策略路由与 eBPF `local.exclude_uid` 配置，检查失败会拒绝继续，避免测速数据经过代理失真。

```bash
getent passwd 29002 || true
getent group 29002 || true
pidof x-tunnel
for pid in $(pidof x-tunnel); do echo "PID=$pid"; grep -E '^(Uid|Gid):' /proc/$pid/status; done

# 在 sing-box 主机配置中排除真实运行的 UID
sudo sbctl filter exclude-user 29002
sudo sbctl check
sudo sbctl restart
```

更推荐先启动 X-Tunnel，再调用 sing-box 的 `align-xtunnel-ebpf.sh` 根据实际进程 UID 更新并备份 `exclude_uid`。**不要排除 root UID 0**。sing-box 的 X-Tunnel Provider 引用路径要与 X-Tunnel 导出的 `X_TUNNEL_PROVIDER_PATH` 一致。

## 5. CF IP 测速与 Provider

```bash
cd /opt/x-tunnel-cf-ip-autotest
sudo python3 cfip_speedtest.py --no-apply --limit 20 --top 5
sudo python3 cfip_speedtest.py --single-ip 1.1.1.1
sudo python3 sync_singbox.py --export-only
```

测速结果默认保存到 `cfip-data/`，可由 Bot 和 systemd timer 自动触发。测速程序会区分普通 Linux 默认路由、已识别的 sing-box 网关策略路由、旧 ShellCrash 风格标记策略路由；未知策略规则下不会盲目改动系统路由。运行在代理网关上的直连成绩仍要用实际出口核实。

维护私有文件权限：

```bash
chmod 600 x-tunnel.yaml speedtest-bot.env
chmod 700 cfip-data
```

不要将 `x-tunnel.yaml`、Bot Token、实际节点密钥、测速报告等上传公共仓库。

## 6. 维护、更新及兼容限制

```bash
x-t
# 选项 2 GitHub 更新 main 分支，可能重启 SOCKS5 核心
sudo sh uninstall.sh         # 只删除本项目服务和定时器，保留运行配置
```

此 `lxc` 分支使用 systemd，普通 Linux 使用 `main` 分支，OpenWrt/iStoreOS 使用 `openwrt` 分支。**新版本已修复 systemd `Group=7890` 被 Python `subprocess.Popen(group=29002)` 覆盖的问题。** GID 7890 兼容仅在本机 `/etc/group` 存在准确的 ShellCrash 组时自动启用，不能仅凭 GID 判断 ShellCrash 放行规则是否生效，更不能用 GID 替代 reF1nd eBPF `local.exclude_uid` 的 UID 豁免。详见 [ShellCrash 兼容说明](SHELLCRASH-COMPATIBILITY.md)。

GitHub Actions 覆盖 Python 编译、节点/测速路由回归、systemd 定时器 HH:MM 与导出格式的测试。**CI 不会验证你的真实 VPS、Cloudflare 出口、系统 eBPF 运行权限或实际带宽表现**；部署后仍需结合服务日志和实际网络检查。
