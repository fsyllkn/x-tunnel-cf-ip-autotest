# PVE LXC 配套 X-Tunnel + CF IP 测速（lxc 分支）

本分支配合 [sing-box LXC 分支](https://github.com/fsyllkn/linux_openwrt-sing-box/tree/lxc) 使用，保留 Linux `main` 的 systemd、多节点 SOCKS5、Provider 导出、Cloudflare IP 测速和每日 `HH:MM` 定时器。

LXC 内先完成 sing-box `lxc/install.sh proxy` 安装，再配置 X-Tunnel：

```bash
cd /opt
git clone -b lxc https://github.com/fsyllkn/x-tunnel-cf-ip-autotest.git
cd x-tunnel-cf-ip-autotest
cp -n x-tunnel.example.yaml x-tunnel.yaml
nano x-tunnel.yaml
# 按实际环境设置服务端 WSS、token、cfip；默认 127.0.0.1:32322
sudo sh install.sh --with-speedtest

systemctl status x-tunnel.service --no-pager
ss -lntp | grep ':32322'

sudo python3 systemd/speedtest-timer.py enable 18:00
sudo python3 systemd/speedtest-timer.py status
```

此分支针对 **非特权 LXC**，不需要 eBPF，也不应取消 AppArmor/seccomp。X-Tunnel Go 核心默认在非 root UID 29002 下运行，supervisor 使用 systemd。

测速有两种路由情形：

- **仅 HTTP/SOCKS5 mixed 代理**：不存在 sing-box IP 策略路由和 local eBPF；使用标准 direct route 进行 CF RTT 与下载测速。
- **纯 TPROXY 网关**：存在 `fwmark 0x1536 -> table 53136`，只有检查 `singbox_one_arm`、`singbox_lxc_guard`、`systemd-detect-virt --container=lxc`、活动 sing-box 服务及无 eBPF 配置都通过，才允许使用本机 OUTPUT 路径测试；不覆盖或清理 sing-box 的 policy rule。如果检查失败会拒绝测速。

显式 HTTP/SOCKS5 代理使用 LAN IP:7890 时，Linux 自己的普通 OUTPUT 并不自动经过这个代理。LXC TPROXY 只拦截从 `eth0` 进入的受控 LAN 流量。

**安全边界：** 本方案的“直连测速”是对容器自己的路由路径进行验证，不保证上游物理路由器不会再次代理或劫持连接。如果额外安装了其他全局代理/OUTPUT 重定向服务，应先明确关闭冲突服务再测。

更多 PVE 非特权 LXC 建立、网关启停及故障阻断见 [sing-box LXC 使用说明](https://github.com/fsyllkn/linux_openwrt-sing-box/tree/lxc)。

Ubuntu/Debian 普通服务器请继续使用 `main` 分支；OpenWrt/iStoreOS 使用 `openwrt` 分支。
