# PVE LXC 配套 X-Tunnel + CF IP 测速（lxc 分支）

本分支使用最新的 OpenWrt **统一 `x-tunnel.yaml` 格式**，不再依赖 `project.env` 或 `speedtest-bot.env`。旧文件由 `migrate_config.py` 自动迁移并备份。

本分支配合 [sing-box LXC 分支](https://github.com/fsyllkn/linux_openwrt-sing-box/tree/lxc) 使用，保留 Linux `main` 的 systemd、多节点 SOCKS5、Provider 导出、Cloudflare IP 测速和每日 `HH:MM` 定时器。

LXC 内先完成 sing-box `lxc/install.sh proxy` 安装，再配置 X-Tunnel：

```bash
cd /opt
git clone -b lxc https://github.com/fsyllkn/x-tunnel-cf-ip-autotest.git
cd x-tunnel-cf-ip-autotest
cp -n x-tunnel.example.yaml x-tunnel.yaml
chmod 600 x-tunnel.yaml
nano x-tunnel.yaml
# 按实际环境设置服务端 WSS、token、cfip；默认 127.0.0.1:32322
sudo sh install.sh --with-speedtest

systemctl status x-tunnel.service --no-pager
ss -lntp | grep ':32322'

sudo python3 systemd/speedtest-timer.py enable 18:00
sudo python3 systemd/speedtest-timer.py status
```

本分支只运行 X-Tunnel SOCKS5 和 CF 测速，不需要自行部署 eBPF、TPROXY 或修改 sing-box 的网关规则。CF 测速的相关检查仅用于识别系统已有代理的路由回环。非特权 LXC 不应取消 AppArmor/seccomp。X-Tunnel Go 核心默认在非 root UID 29002 下运行，supervisor 使用 systemd。无 ShellCrash 时主 GID 同样为 29002；如果存在精确的 `shellcrash:x:7890` 系统组，默认自动选择主 GID 7890，让子进程能匹配 ShellCrash 的 GID 旁路规则。该设置不会增加容器特权或改动 ShellCrash 配置。

### 可选：共存 ShellCrash 的主 GID 兼容

在 `x-tunnel.yaml` 的 `settings.process_gid` 中保留默认的 `auto`（旧配置尚未写此字段也默认 auto）。检测到标准 ShellCrash 组时，Go 核心与独立 CF 测速 worker 保持 **UID 29002 / 主 GID 7890**，否则回退为 **UID 29002 / GID 29002**。也可设置 `settings.process_gid: uid` 强制关闭兼容，或设置 `7890` 并要求该 ShellCrash 组实际存在。

之前安装器设置的 systemd `Group=7890` 只影响主服务自身；Python 明确传入 `subprocess.Popen(group=29002)` 后，Go 子进程会丢失该 GID。这次修复独立解决了子进程身份覆盖的问题。测试与配置均不使用补充组替代主 GID。

如果 ShellCrash 是**后来安装**的，重跑 `install.sh` 可同步测速 systemd unit 的 Group drop-in；但重跑安装器可能重新部署并重启 X-Tunnel 服务，请在维护窗口执行。默认自动选择 GID 也不代表 ShellCrash 当前实际的 OUTPUT 豁免规则已经生效；如仍有 DoH fake-IP 或节点监听失败，要继续核实当前防火墙规则和 SOCKS 连接。完整操作见 [SHELLCRASH-COMPATIBILITY.md](SHELLCRASH-COMPATIBILITY.md)。

测速有两种路由情形：

- **仅 HTTP/SOCKS5 mixed 代理**：不存在 sing-box IP 策略路由和 local eBPF；使用标准 direct route 进行 CF RTT 与下载测速。
- **纯 TPROXY 网关**：存在 `fwmark 0x1536 -> table 53136`，只有检查 `singbox_one_arm`、`singbox_lxc_guard`、`systemd-detect-virt --container` 返回 `lxc`、活动 sing-box 服务及无 eBPF 配置都通过，才允许使用本机 OUTPUT 路径测试；不覆盖或清理 sing-box 的 policy rule。如果检查失败会拒绝测速。

显式 HTTP/SOCKS5 代理使用 LAN IP:7890 时，Linux 自己的普通 OUTPUT 并不自动经过这个代理。LXC TPROXY 只拦截从 `eth0` 进入的受控 LAN 流量。

**安全边界：** 本方案的“直连测速”是对容器自己的路由路径进行验证，不保证上游物理路由器不会再次代理或劫持连接。如果额外安装了其他全局代理/OUTPUT 重定向服务，应先明确关闭冲突服务再测。

更多 PVE 非特权 LXC 建立、网关启停及故障阻断见 [sing-box LXC 使用说明](https://github.com/fsyllkn/linux_openwrt-sing-box/tree/lxc)。

Ubuntu/Debian 普通服务器请继续使用 `main` 分支；OpenWrt/iStoreOS 使用 `openwrt` 分支。


## 最新配置迁移与安全卸载

旧项目中 `project.env`、`speedtest-bot.env` 的配置会在安装器运行时合并到唯一的 `x-tunnel.yaml`。旧值覆盖模板默认值，但不覆盖已自定义的 YAML 字段；即使旧 `.env` 已不存在，也会补齐缺失的 `settings` / `telegram` 区块。迁移在内核下载和 systemd 部署之前运行，失败不启停现有服务。

```sh
cd /opt/x-tunnel-cf-ip-autotest
sudo python3 migrate_config.py
sudo sh install.sh --with-speedtest
sudo sh uninstall.sh --dry-run # 只读检查将卸载的项目资源
```

卸载分三种模式：`sh uninstall.sh` 只移除本项目服务和定时任务、保留历史与共享内核；`sh uninstall.sh --purge` 经交互确认后删掉项目文件和历史；`sh uninstall.sh --purge --remove-kernel` 只在 SHA256 证明二进制由本安装器下载且没有进程继续使用时删除该二进制。**不同服务的 unit、ShellCrash、sing-box、其他路由规则不能删除。** 详细说明见 [README](README.md#6-安全卸载与彻底清理)。
