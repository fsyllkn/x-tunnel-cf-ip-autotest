# X-Tunnel + Cloudflare IP 测速 · PVE LXC（`lxc` 分支）

**本分支将 X-Tunnel 节点、CF 测速、Provider 路径、进程身份、Telegram 凭据统一存入 `x-tunnel.yaml`。** 适用于 Debian/Ubuntu systemd 和 PVE 非特权 LXC；保留 systemd 定时器及 ShellCrash 主 GID 兼容。测速器只读检查现有路由，以避免透明代理回环；**X-Tunnel 不部署或管理 sing-box 的 TPROXY、eBPF、DNS、nftables 或策略路由。**

- [LXC 部署与网络验证](LXC.md)
- [ShellCrash GID 7890 兼容](SHELLCRASH-COMPATIBILITY.md)
- [对应 OpenWrt 分支](https://github.com/fsyllkn/x-tunnel-cf-ip-autotest/tree/openwrt)
- [配套 sing-box LXC](https://github.com/fsyllkn/linux_openwrt-sing-box/tree/lxc)

## 1. 配置已完全统一

**唯一需要编辑的项目配置是 `x-tunnel.yaml`。** 旧 `project.env`、`speedtest-bot.env` 不再是运行时配置来源。最新安装器先运行 `migrate_config.py`，再执行内核检查和部署：如果 YAML 中仍是新版模板默认值，会优先迁入旧 `.env` 的真实字段；已经自定义的 YAML 值优先级更高。即使旧 `.env` 已经不存在，迁移器仍会补齐 YAML 缺失的 `settings` / `telegram` 区块。只有迁移并持久化成功后，才将旧文件归档到 `cfip-data/config-backups/legacy-env-*/`。YAML 和私有备份均使用限制权限；勿提交真实 Token、节点配置及测速历史。

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

新配置首次创建后需替换 YAML 中的示例地址/密钥再安装。旧的 `project.env`/Bot 文件会在**下载或修改内核之前**迁移。迁移失败则不重新部署 systemd 服务。建议更新前自行备份 YAML。

**专门验证旧配置是否已合并**（不会打印节点密钥或 Bot Token）：

```sh
cd /opt/x-tunnel-cf-ip-autotest
sudo python3 migrate_config.py
python3 - <<'PY'
import yaml
with open("x-tunnel.yaml", encoding="utf-8") as stream:
    config = yaml.safe_load(stream) or {}
print("settings keys:", ", ".join(sorted((config.get("settings") or {}).keys())))
print("telegram keys:", ", ".join(sorted((config.get("telegram") or {}).keys())))
print("node count:", len(config.get("nodes") or []))
PY
ls -l project.env speedtest-bot.env 2>/dev/null || true
ls -ld cfip-data/config-backups/* 2>/dev/null | tail -n 5
```

正常状态下项目根目录不再有这两份旧 `.env`，完整配置信息保存在 `x-tunnel.yaml`，旧文件只在受限权限的备份目录里保留。

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

## 6. 安全卸载与彻底清理

卸载程序首先核实 systemd unit 的 `WorkingDirectory`、`ExecStart` 和项目标记，检查专属 drop-in 及快捷命令归属。如果同名文件实际属于别的程序，它会**拒绝卸载**，不会调用系统的批量服务清理，也不会删除 ShellCrash/sing-box 规则。

```sh
cd /opt/x-tunnel-cf-ip-autotest

# 只读预览，推荐先运行
sudo sh uninstall.sh --dry-run

# 移除 X-Tunnel 自身的服务、运行中的单 IP 测速任务、
# 定时器、项目 drop-in 和 x-t / xtunnel-menu 快捷命令
# 保留所有私有配置、历史与共享二进制
sudo sh uninstall.sh

# 完整删除项目源码、x-tunnel.yaml、历史数据、备份、Git 工作树
# 要求在删除任何服务前交互输入 DELETE
sudo sh uninstall.sh --purge

# 如果二进制确由本安装器下载并存在匹配 SHA256 所有权标记，
# 且确认其他服务不使用 /usr/bin/x-tunnel，可明确选择连内核一并删除
sudo sh uninstall.sh --purge --remove-kernel
```

`--purge` 为不可恢复的目录删除，勿在未备份私有数据时使用。旧版本已存在的共享 `/usr/bin/x-tunnel` 不一定具有所有权标记，故不会被卸载器推测归属并自动删除。系统包、ShellCrash/sing-box 配置、别的项目路由和 journald 全局日志均不受影响。项目数据可能还存在于自行设置的外部 Provider 路径，卸载器不会跨目录清理共享文件。

> `openwrt` 是 procd 路由器分支；`lxc` 是 systemd 容器分支。本次只修复 X-Tunnel 的 `lxc` 分支，没有修改配套的 sing-box 或 ShellCrash 仓库。
