# X-Tunnel OpenWrt / iStoreOS 轻量分支

本分支专门针对 OpenWrt/iStoreOS（procd、BusyBox、apk/opkg）。**Debian/Ubuntu 使用 [main 分支](https://github.com/fsyllkn/x-tunnel-cf-ip-autotest/tree/main)**。

## 优化原则

- 默认只启动 X-Tunnel supervisor 与用户配置的各 x-tunnel 进程，生成 ShellCrash/sing-box 本地 Provider。
- Telegram Bot、自动测速与相关 Python HTTPS/IDNA/路由依赖按需启用；避免默认增加常驻 Bot 进程。
- supervisor 的 Telegram urllib 模块改为发生端口冲突通知时才加载；配置监视周期由 2 秒调整为 5 秒，降低空闲轮询频率（节点配置生效可能最多等待约 5 秒）。
- 保留原有 cfip_speedtest.py 功能，但测速启动时仍会使用 CPU、RAM、网络吞吐和临时路由；**不是无 Python 的纯 Shell 实现**。
- 仅支持 x86_64 / aarch64 下载现有 X-Tunnel Linux 内核；内核还需满足 musl/ABI 的实际运行要求。

## 一键安装

在 OpenWrt root SSH 终端中：

```sh
wget -O /tmp/xtunnel-openwrt.sh https://raw.githubusercontent.com/fsyllkn/x-tunnel-cf-ip-autotest/openwrt/quick-install.sh
sh /tmp/xtunnel-openwrt.sh
```

默认部署到 `/overlay/x-tunnel-cf-ip-autotest`。首次安装会生成 `x-tunnel.yaml`，请编辑真实节点配置后重新执行一键安装命令。已存在的私有配置不会主动被覆盖。

**可选功能：**

```sh
# 仅核心服务（默认）
sh /tmp/xtunnel-openwrt.sh

# 额外安装 Telegram Bot + 测速依赖（先配置 TG_TOKEN 和 TG_CHATID）
sh /tmp/xtunnel-openwrt.sh --with-bot

# 不启动 Bot，只准备测速依赖
sh /tmp/xtunnel-openwrt.sh --with-speedtest

# 开启每天本地时间 18:00 自动测速
sh /tmp/xtunnel-openwrt.sh --enable-timer

# 停止/禁用之前安装的 Bot
sh /tmp/xtunnel-openwrt.sh --disable-bot
```

首次启用 Bot 前，请在项目目录创建 `speedtest-bot.env`（可从 `speedtest-bot.env.example` 复制），设置 Token 与 Chat ID，并在 `project.env` 配置实际可用的 HTTP 代理。安装器会检查必需组件，使用 `apk` 或 `opkg` 补装缺失依赖，不自动卸载已安装的系统包。

## SSH 菜单

```sh
cd /overlay/x-tunnel-cf-ip-autotest
sh menu.sh
# 安装服务后也可以：
xtunnel-menu
```

菜单支持核心安装/修复、更新、编辑节点与项目变量、配置 Telegram Bot、控制服务、日志、定时测速及卸载。菜单新增安装/启用 Bot 和关闭 Bot 两个选项。编辑私有配置前会备份。ShellCrash 配置由用户单独管理。

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

## 卸载

```sh
cd /overlay/x-tunnel-cf-ip-autotest
sh uninstall.sh
```

卸载服务与本项目计划任务，但保留项目目录、私有配置和共享 `/usr/bin/x-tunnel` 内核。**优化内容仍需 OpenWrt 实机验证，尤其是 Python 发行包拆分和内核 ABI 兼容性。**
