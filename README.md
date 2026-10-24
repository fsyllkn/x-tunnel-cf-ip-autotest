# X-Tunnel + Cloudflare IP 测速 · PVE LXC（`lxc` 分支）

**本分支已基于 `openwrt` 分支的统一 YAML 版本重新整合。** 在 Debian/Ubuntu **systemd**、PVE 非特权 LXC 中使用；保留此前 `lxc` 分支已验证的纯 TPROXY/标准出站路径检查、systemd 定时器、独立 UID 和 ShellCrash 主 GID 兼容。不会安装 OpenWrt 的 procd 脚本，也不会自动修改其他服务的路由、DNS、ShellCrash 防火墙或 LXC 特权设置。

- [LXC 部署与网络验证](LXC.md)
- [ShellCrash GID 7890 兼容](SHELLCRASH-COMPATIBILITY.md)
- [对应 OpenWrt 分支](https://github.com/fsyllkn/x-tunnel-cf-ip-autotest/tree/openwrt)
- [配套 sing-box LXC](https://github.com/fsyllkn/linux_openwrt-sing-box/tree/lxc)

## 1. 配置已完全统一

**唯一需要编辑的项目配置是 `x-tunnel.yaml`。** 原 `project.env`、`speedtest-bot.env` 不再是运行时配置来源；安装程序使用 `migrate_config.py` 将旧文件的字段、Bot Token、监听地址导入 YAML，在 `cfip-data/config-backups/legacy-env-*/` 留下权限为 0600 的私有备份后才归档旧文件。YAML 必须为 `0600`；不要提交真实 Token、节点配置及测速历史。

```yaml
defaults:
  token: "YOUR_X_TUNNEL_TOKEN"
  cfip: "162.159.1.1"
  connections: 3
  ech: "cloudflare-ech.com"
  dns: "https://doh.pub/dns-query"
  listen_hosts:
    - "127.0.0.1"
  first_port: 32322

max_nodes: 5
nodes:
  - "hk|wss://example.org/about:443||||"

settings:
  provider_path: "runtime/x-tunnel-provider.json"
  process_uid: 29002
  process_gid: auto
  local_proxy: "http://127.0.0.1:7890"
  telegram_proxy: ""
  target_mbps: 300

telegram:
  token: ""
  chat_id: ""
```

`defaults.listen_hosts` 是唯一的 SOCKS5 监听地址来源；增加 LAN IP 时，务必限制防火墙允许的客户端。Bot 凭据可留空，不妨碍核心服务安装。测速阈值、Provider 路径、进程身份和 Telegram 代理均由此 YAML 读取，**不再被旧 .env 或 systemd EnvironmentFile 覆盖**。

## 2. 安装与更新

要求 Debian/Ubuntu、systemd、Python 3.9+、PyYAML、iproute2。非特权 LXC 不需要 eBPF，不应关闭 AppArmor/seccomp 或添加特权能力。

已授权的 Git 克隆：

```sh
cd /opt
git clone --branch lxc --single-branch git@github.com:fsyllkn/x-tunnel-cf-ip-autotest.git
cd x-tunnel-cf-ip-autotest
cp -n x-tunnel.example.yaml x-tunnel.yaml
chmod 600 x-tunnel.yaml
vi x-tunnel.yaml
sudo sh install.sh --with-speedtest
```

如果原目录已是 `lxc` 的 Git 克隆：

```sh
cd /opt/x-tunnel-cf-ip-autotest
git branch --show-current
git pull --ff-only origin lxc
sudo sh install.sh --with-speedtest
```

**私有仓库 ZIP 手动上传：** 从 GitHub 选择 `lxc` 分支 → Code → Download ZIP，上传至 `/tmp/x-tunnel-lxc.zip`。如果目标目录不是 Git 工作树，可以在已有项目目录内执行：

```sh
sudo sh quick-install.sh --core --archive=/tmp/x-tunnel-lxc.zip
```

该操作只读取本地 ZIP，不向公开 GitHub 请求私有仓库源码，会自动同步源码、迁移旧配置并按既有安装器重新部署 systemd；核心服务可能重启。**Git 工作树不要用 ZIP 强行覆盖，使用 `git pull --ff-only`。** 已安装且正常运行的 `/usr/bin/x-tunnel` 内核不因常规更新被替换；如需主动修复内核使用 `--repair-kernel`。

新配置首次创建后需替换 YAML 中的示例地址/密钥再安装。旧的 `project.env`/Bot 文件会由安装器迁移，迁移失败则不部署系统服务或重启。重新安装前可自行备份私有 YAML。

## 3. 新版菜单：与 OpenWrt 编号对齐

`x-t`（或 `xtunnel-menu`）：

```text
【安装与常用】
 1 安装/修复    2 更新 lxc
 3 重启核心     4 查看日志
 5 核心管理     6 停止核心

【配置管理】
 7 编辑统一 YAML   8 配置检查
 9 配置字段说明   10 YAML 备份

【CF IP 测速】
11 完整测速     12 systemd 定时测速

【Telegram Bot】
13 安装/启用    14 服务管理
15 重启 Bot     16 关闭 Bot

【导出维护】
17 导出 SOCKS/Clash/V2Ray
18 修复内核
19 卸载（保留配置）
 0 退出
```

定时测速由 `systemd/speedtest-timer.py` 管理，每天本地时间 `HH:MM`，不会为了改时间而重启 X-Tunnel 核心：

```sh
sudo python3 systemd/speedtest-timer.py enable 18:00
python3 systemd/speedtest-timer.py status
sudo python3 systemd/speedtest-timer.py disable
```

Bot 的 `/topspeed` 会修改 YAML 的 `settings.target_mbps`；CF 测速同时使用文件锁保护写入。按最新 OpenWrt 行为，每个节点最多保留 **2 个 CF IP**，优先本轮新测速 IP；只有不足 2 个才用原节点或默认 IP 补位。没有合格新结果时保留旧配置，不再无限累加预设 IP。

## 4. 进程 UID 与 ShellCrash 兼容

`settings.process_uid` 默认 `29002`；`settings.process_gid`：

- `auto`：如果 `/etc/group` 精确存在 `shellcrash:x:7890`，Go 子进程及隔离 CF worker 的主 GID 使用 7890；否则 GID=UID。
- `uid`：始终 GID=UID，关闭 ShellCrash 组兼容。
- `7890`：明确要求该组存在，否则配置校验拒绝。

systemd 层的 X-Tunnel 服务和 CF 测速服务是否启用 `Group=7890` drop-in 也遵循这个配置。不要用 **GID** 替代 reF1nd eBPF 的 `local.exclude_uid`（这是 UID 规则）。

验证：

```sh
systemctl is-active x-tunnel.service
grep '^shellcrash:' /etc/group || true
for pid in $(pidof x-tunnel); do
  echo "PID=$pid"; grep -E '^(Uid|Gid):' "/proc/$pid/status"
done
ss -lntp | grep ':32322' || true
journalctl -u x-tunnel.service -n 80 --no-pager
```

组存在不等于防火墙已按该 GID 放行。与 ShellCrash 共存但仍遇到 fake-IP、SOCKS5 无监听时，要继续检查当前代理出站回环和真实 OUTPUT 规则，不能直接修改未知路由。

## 5. LXC 测速安全性

LXC 纯 TPROXY 网关的特殊检测保留：核对 `singbox_one_arm`、`singbox_lxc_guard`、`fwmark 0x1536 lookup 53136`、活动 sing-box 服务、LXC 标记且无 local eBPF，才允许本机 OUTPUT 路径。其他模式会检查默认路由、非主表与未知策略；不盲目清空第三方路由。

```sh
sudo python3 cfip_speedtest.py --no-apply --limit 20 --top 5
sudo python3 cfip_speedtest.py --single-ip 1.1.1.1
sudo python3 sync_singbox.py --export-only
python3 -m unittest discover -s tests -p 'test_*.py'
```

测速写入、测速报告、systemd Bot、历史 JSON、Provider 导出和多节点监听均基于最新统一 YAML；不部署新的 Web 服务或代理内核。默认测速报告保存在 `cfip-data/`，请妥善保护历史数据。

> `openwrt` 是 procd 路由器分支；`lxc` 是 systemd 容器分支。不要把 `openwrt` 的安装脚本直接覆盖到 LXC。本次整合只升级 X-Tunnel 代码，不修改配套 sing-box 或 ShellCrash 私有仓库。
