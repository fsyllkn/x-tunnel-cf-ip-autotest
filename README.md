# X-Tunnel + Cloudflare IP 优选测速（Debian / Ubuntu）

本分支 `main` 面向 **Debian 11/12/13、Ubuntu 22.04/24.04** 等使用 systemd 的 Linux 环境（Python **3.9+**）。OpenWrt / iStoreOS 请使用独立的 [openwrt 分支](https://github.com/fsyllkn/x-tunnel-cf-ip-autotest/tree/openwrt)；LXC 等特殊容器环境请使用相应专用分支。Debian 11 默认 Python 为 3.9，符合最低要求。脚本不会修改主机 DNS、ShellCrash/sing-box、系统路由或防火墙配置（测速功能可能按既有路由策略临时添加并清理自身规则）。

## 功能

- 使用 supervisor 管理多个 X-Tunnel SOCKS5 节点，自动重载配置，按照专用 UID 运行 Go 核心。
- Cloudflare IPv4/IPv6 候选发现、RTT/TLS/下载测速、分地区筛选、失败兜底 IP 自动写回。
- 导出 sing-box SOCKS5 Provider、Clash/Mihomo、Xray/V2Ray 节点配置。
- 可选 Telegram Bot 和每日自动测速，采用 systemd service/timer；无需 Bot 也能安装核心。
- 与 OpenWrt 对齐的 `x-tunnel.yaml` **单配置文件**、`x-t` 快捷菜单及配置备份。
- 对旧 `project.env`、`speedtest-bot.env` 自动执行一次性迁移；备份位于 `cfip-data/config-backups/legacy-env-*`，敏感备份文件权限为 `0600`。迁移成功后不再读取旧变量文件。

## 一键安装

使用 root 登录（或先执行 `sudo -i`），确保机器已安装 `curl` 或 `wget`、`tar` 和 CA 证书：

```bash
apt-get update
apt-get install -y curl ca-certificates tar
sh -c 'curl -fsSL https://raw.githubusercontent.com/fsyllkn/x-tunnel-cf-ip-autotest/main/quick-install.sh | sh'
```

**注意：** 以上命令对应合并到 `main` 后的正式入口；审查本 PR 期间可先下载分支脚本进行验证。交互方式需要 SSH TTY。运行后自动安装源码到 `/opt/x-tunnel-cf-ip-autotest`，先进入管理菜单；第一次安装请先编辑真实的节点地址、token 和 CF IP，再执行「安装/修复核心服务」。

在交互终端直接运行远端脚本时，推荐：

```bash
curl -fsSL https://raw.githubusercontent.com/fsyllkn/x-tunnel-cf-ip-autotest/main/quick-install.sh -o /tmp/xtunnel-quick-install.sh
sh /tmp/xtunnel-quick-install.sh
```

**非交互安装**：完成 `x-tunnel.yaml` 配置后，可从项目目录执行 `sh install.sh`（仅核心）、`sh install.sh --with-bot`（Bot）、`sh install.sh --with-speedtest`（测速依赖）、`sh install.sh --timer-at=06:45`（每日测速），或调用 `sh quick-install.sh --core` 拉取最新主分支并部署。安装失败时先看预检错误；不应通过关闭依赖/配置校验绕过故障。

## 配置管理

**唯一运行配置：** `/opt/x-tunnel-cf-ip-autotest/x-tunnel.yaml`。

```yaml
defaults:
  token: "替换为你的默认密钥"
  cfip: "替换为优选 CF IP"
  connections: 3
  ech: "cloudflare-ech.com"
  dns: "https://doh.pub/dns-query"
  listen_hosts:
    - "127.0.0.1"
  first_port: 32322

max_nodes: 5
nodes:
  - "节点1|wss://example.com/about:443||||"

settings:
  provider_path: "runtime/x-tunnel-provider.json"
  process_uid: 29002
  local_proxy: "http://127.0.0.1:7890"
  telegram_proxy: ""
  target_mbps: 300

telegram:
  token: ""
  chat_id: ""
```

节点行格式是 `名称|WSS地址|token|CF IP|连接数|SOCKS5端口`，留空列使用 defaults。SOCKS5 默认只监听本机 `127.0.0.1`；如需 LAN 访问，`defaults.listen_hosts` 填写本机实际 LAN IP，并限制防火墙允许的客户端。**不要将未认证的 SOCKS5 端口直接公开到互联网。**

`settings.process_uid` 必须为未被其他服务占用的普通 UID（默认 29002）；配合 sing-box/ShellCrash 排除代理回环。Telegram 配置留空不影响核心安装；启用 Bot 前应填写真实凭据并确保 `x-tunnel.yaml` 权限为 `0600`。配置文件包含密钥，勿提交到公开仓库。

升级前备份建议：

```bash
cd /opt/x-tunnel-cf-ip-autotest
cp -p x-tunnel.yaml x-tunnel.yaml.backup
chmod 600 x-tunnel.yaml.backup
```

## SSH 快捷菜单

安装后直接输入 `x-t`（兼容 `xtunnel-menu`）。与 OpenWrt 对齐的编号如下：

| 编号 | 功能 |
|---|---|
| 1–2 | 安装/修复核心、更新 main |
| 3–6 | 重启核心、日志、服务管理、停止核心 |
| 7–10 | 编辑统一 YAML、配置检查、字段说明、备份 |
| 11–12 | 立即测速、每日 systemd 定时测速 |
| 13–16 | 安装/管理/重启/关闭 Telegram Bot |
| 17–19 | 导出节点、修复内核、卸载服务 |

定时测速可使用 `python3 systemd/speedtest-timer.py status`、`enable 06:45`、`disable`；使用主机本地时区，不会为了修改时间而重启代理节点。

## 服务及故障检查

```bash
systemctl status x-tunnel.service --no-pager -l
systemctl status x-tunnel-speedtest-bot.service --no-pager -l
systemctl list-timers --all x-tunnel-speedtest.timer
journalctl -u x-tunnel.service -n 100 --no-pager
journalctl -u x-tunnel-speedtest-bot.service -n 100 --no-pager
cd /opt/x-tunnel-cf-ip-autotest
python3 -c 'import supervisor; supervisor.read_specs(check_port_availability=False)'
python3 -c 'import project_settings as p; print(p.get_target_mbps())'
python3 openwrt/healthcheck.py
```

`systemctl active` 仅表示管理进程在运行，**不等于节点实际可用**。请进一步测试本机 SOCKS5 监听及端到端连接：

```bash
ss -lntup | grep 32322
curl -4 --socks5-hostname 127.0.0.1:32322 --connect-timeout 8 --max-time 20 https://api.ipify.org
```

实际端口依配置和端口冲突自动顺延结果而定。发生异常时检查日志中的节点名称、目标地址、监听端口和启动错误。不要在未知原因下盲目更改系统路由。

## 更新和卸载

```bash
x-t  # 选择 2 更新 main
# 或：在项目目录
sh install.sh
# 卸载服务、定时任务、快捷菜单（保留私有配置和二进制）
sh uninstall.sh
# 明确确认后连同私有数据清理
sh uninstall.sh --purge
```

卸载仅清除本项目管理的 systemd 服务、timer、drop-in 和菜单快捷方式；不删除可能与其他项目共享的 `/usr/bin/x-tunnel`，不操作 ShellCrash、sing-box、防火墙或其他系统包。模板 unit `x-tunnel-speedtest-single@.service` 不会被错误地作为实例执行停止。

## 源码与测试

- `install.sh`：Debian/Ubuntu 依赖、核心校验、配置预检、systemd 注册
- `quick-install.sh`：首次下载、更新和快捷菜单入口
- `menu.sh`：SSH 交互菜单
- `project_settings.py` / `migrate_config.py`：统一配置及旧配置迁移
- `supervisor.py` / `cfip_speedtest.py`：核心节点和测速链路
- `systemd/`：service/timer 及调度工具

静态回归：

```bash
sh -n install.sh quick-install.sh menu.sh uninstall.sh
python3 -m compileall -q supervisor.py project_settings.py migrate_config.py telegram_speedtest_bot.py cfip_speedtest.py
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

以上测试不替代在实际 Debian/Ubuntu systemd 主机上的新装、旧版升级、Bot、timer、卸载和 SOCKS5 端到端验证。
