# X-Tunnel OpenWrt / iStoreOS 轻量分支

本分支专门针对 OpenWrt/iStoreOS（procd、BusyBox、apk/opkg）。**Debian/Ubuntu 使用 [main 分支](https://github.com/fsyllkn/x-tunnel-cf-ip-autotest/tree/main)**。

## 优化原则

- 默认只启动 X-Tunnel supervisor 与用户配置的各 x-tunnel 进程，生成 ShellCrash/sing-box 本地 Provider。
- Telegram Bot、自动测速与相关 Python HTTPS/IDNA/路由依赖按需启用；避免默认增加常驻 Bot 进程。
- supervisor 的 Telegram urllib 模块改为发生端口冲突通知时才加载；配置监视周期由 2 秒调整为 5 秒，降低空闲轮询频率（节点配置生效可能最多等待约 5 秒）。
- 保留原有 cfip_speedtest.py 功能，但测速启动时仍会使用 CPU、RAM、网络吞吐和临时路由；**不是无 Python 的纯 Shell 实现**。
- 仅支持 x86_64 / aarch64 下载现有 X-Tunnel Linux 内核；内核还需满足 musl/ABI 的实际运行要求。

## 首次安装：交互式菜单（推荐）

支持 OpenWrt 24.x / ImmortalWrt 等旧版 `opkg` 固件，也兼容使用 `apk` 的新版 OpenWrt。安装入口只需 BusyBox 的 `sh`、`tar` 和系统自带的 `wget` 或 `curl`，**打开菜单时不需要提前安装 Python**；选定功能后才由安装器检查和补齐相关依赖。

在 root SSH 终端运行：

```sh
wget -O /tmp/xtunnel-openwrt.sh https://raw.githubusercontent.com/fsyllkn/x-tunnel-cf-ip-autotest/openwrt/quick-install.sh
sh /tmp/xtunnel-openwrt.sh
```

运行后会下载最新 `openwrt` 分支代码、保留已有节点与变量配置、创建缺少的示例模板，并直接进入管理菜单。**不会因为进入菜单就自动安装或重启服务。**

首次部署顺序推荐：先选择 **5 编辑节点配置**，填入真实的服务地址/Token；再选择 **1 安装核心服务**。需要 Telegram Bot 时，选择 **3 设置 Bot**，并在 `project.env` 中填写可用代理，然后选择 **13 安装/启用 Bot**。不使用 Bot 的低内存设备只需安装核心服务。

菜单包括核心安装、GitHub 更新、Bot 设置、变量/节点编辑、服务启停、日志、定时测速及卸载。主菜单现增加 **`15` 一键重启 X-Tunnel** 和 **`16` 一键重启 Telegram Bot**：均需确认；重启核心会短暂中断所有节点连接，而单独重启 Bot 不影响 X-Tunnel 服务。配置编辑通过路由器自带的 `vi` 进行，编辑前自动备份。管理菜单显示当前 `opkg`/`apk` 和两个服务状态。

安装目录默认 `/overlay/x-tunnel-cf-ip-autotest`；如果没有 `/overlay`，会使用 `/root/x-tunnel-cf-ip-autotest`。已有 Git checkout 需要位于 `openwrt` 分支，安装器不会擅自将 `main` 切换为 `openwrt`。可通过 `X_TUNNEL_INSTALL_DIR` 指定其他绝对路径。

安装后可在任何目录执行（旧的 `xtunnel-menu` 入口将在更新时清理）：

```sh
x-t
```

也可以从源码目录打开：

```sh
cd /overlay/x-tunnel-cf-ip-autotest
sh menu.sh
```

### 非交互式安装（自动化部署）

明确传入参数时不会打开菜单：

```sh
sh /tmp/xtunnel-openwrt.sh --core            # 仅核心服务
sh /tmp/xtunnel-openwrt.sh --with-bot        # 核心 + Telegram Bot
sh /tmp/xtunnel-openwrt.sh --with-speedtest  # 核心 + 测速依赖，不启动 Bot
sh /tmp/xtunnel-openwrt.sh --enable-timer    # 核心 + 每日 18:00 定时测速
sh /tmp/xtunnel-openwrt.sh --disable-bot     # 停用已有 Bot，保留核心
```

非交互式执行时，首次生成示例节点配置后会退出，需补充实际参数再运行。启用 Bot 前还需要正确填写 `speedtest-bot.env` 的 `TG_TOKEN`、`TG_CHATID`。没有 SSH 交互终端时应使用上述显式参数。

## 软件源更新失败时的行为（OpenWrt 24.x / opkg）

部分第三方 feed 失效时，`opkg update` 可能返回非零状态，例如 `immortalwrtluci`、`passwall` 的 `Packages.gz` 下载失败，但官方 `base/packages` 仍然可用。安装器现在**最多刷新一次索引**，若刷新不完整，发出警告后继续尝试安装必需包；仅当依赖包本身安装失败时才停止。它不会擅自禁用、替换或删除用户的软件源。

如依赖仍无法安装，可检查具体 feed，而不要直接删除或重建 opkg 数据库：

```sh
opkg update
cat /etc/opkg/distfeeds.conf
cat /etc/opkg/customfeeds.conf 2>/dev/null
opkg list-installed | grep -E '^python3|^ip-full'
```

不同发行版第三方源未必能从通用 OpenWrt 镜像取得，需使用与**当前固件和架构**一致的软件源。安装完成后检查 `/etc/init.d/x-tunnel status`、`/etc/init.d/x-tunnel-speedtest-bot status` 和 `logread`。尚未在每个发行版实机完整验证。

## 从旧 main 部署迁移（尤其重要）

不要直接在运行中的同一目录盲目切换分支。建议先备份 `x-tunnel.yaml`、`speedtest-bot.env`、`project.env` 和需要保留的测速记录，确认当前 Git 工作区无未提交修改。若已克隆仓库：

```sh
cd /overlay/x-tunnel-cf-ip-autotest
/etc/init.d/x-tunnel-speedtest-bot stop 2>/dev/null || true
/etc/init.d/x-tunnel stop 2>/dev/null || true
git fetch origin openwrt
git switch openwrt
sh install.sh --disable-bot
```

上述命令默认部署轻量核心，并停止旧 Bot。**旧版已设置的 cron 自动测速不会被这一安装命令自动删除**，如要彻底停用须检查并清理本项目的 cron 项（管理菜单中可关闭）。手动切换到 `openwrt` 后，菜单中的更新才会从正确分支拉取。

## 运行与日志

```sh
/etc/init.d/x-tunnel status
/etc/init.d/x-tunnel-speedtest-bot status
logread | tail -n 80
cat /overlay/x-tunnel-cf-ip-autotest/runtime/x-tunnel-provider.json
```

核心安装并不会自动创建 Bot 服务。默认 Provider 位于 `runtime/x-tunnel-provider.json`；ShellCrash 需要自行引用，不会被本项目自动修改或重启。

## 低内存设备排查

如果启动 5 个节点后内存明显增加，先检查 **每个 x-tunnel 子进程** 的常驻内存（RSS）。独立进程存在共享内存重复统计的可能，因此 RSS 总和不等于实际额外物理占用；以 `free` 的前后差值辅助判断。

```sh
free
for pid in $(pidof x-tunnel 2>/dev/null); do
  echo "PID: $pid"
  grep -E '^(Name|VmRSS|VmSize|Threads):' "/proc/$pid/status"
done
```

`x-tunnel.yaml` 中 `defaults.connections` 通常为 3，5 个节点意味着每个节点各自创建相应连接；可以先备份后把该值临时调至 1，观察内存、SMUX 稳定性及实际吞吐是否变化。节点行中显式设置的连接数会覆盖默认值。**减小连接数不保证减少相同比例的内存**；独立 Go 进程、Geo 数据加载及内部缓冲区可能仍占主要部分。

同时启动多个内核时，如果同时检测到 `geoip.dat` 缺失，可能竞争下载同名临时文件，出现 `rename geoip.dat.tmp geoip.dat: no such file or directory`。日志后续显示 GeoIP/GeoSite 已成功加载时通常不影响当前代理服务。不要在服务运行时盲目删除或反复清理 Geo 文件；这一共享文件竞争仍待内核侧修复。

## 卸载

```sh
cd /overlay/x-tunnel-cf-ip-autotest
sh uninstall.sh
```

卸载服务与本项目计划任务，但保留项目目录、私有配置和共享 `/usr/bin/x-tunnel` 内核。**优化内容仍需 OpenWrt 实机验证，尤其是 Python 发行包拆分和内核 ABI 兼容性。**
