# ShellCrash 兼容：独立 UID + 可选主 GID 7890

本说明适用于在同一台 Debian/Ubuntu systemd 机器（包括非特权 LXC）上同时运行 X-Tunnel 与 ShellCrash 的场景。只讨论本机 socket 组身份；它不更改 ShellCrash 的防火墙、DNS、配置文件、策略路由或服务状态。

## 问题根因

部分 ShellCrash Linux 配置使用 **socket 主 GID 7890** 识别需要跳过本机透明代理的进程。若 X-Tunnel 上游被 ShellCrash 自己的出站规则重新代理，可能发生代理回环、DoH fake-IP 异常或 SOCKS5 节点始终无法监听。

此前安装器可以通过 systemd drop-in 设置 `Group=7890`，但 `supervisor.py` 使用 `subprocess.Popen(user=29002, group=29002, extra_groups=())` 创建 Go 子进程。这会覆盖父服务的主 GID，因此 systemd 设置**并不能使 X-Tunnel 核心自动使用 7890**；CF 测速独立 worker 同样存在这一问题。

## 新行为：只切换子进程主 GID

`project.env` 提供：

```ini
X_TUNNEL_PROCESS_UID=29002
X_TUNNEL_PROCESS_GID=auto
```

| `X_TUNNEL_PROCESS_GID` | Go 核心 / 隔离测速 worker 的主 GID | 适用场景 |
|---|---|---|
| `auto`（默认） | `/etc/group` 中**精确**存在 `shellcrash:x:7890` 时使用 7890，否则使用 UID 值 | 推荐 |
| `uid` | 始终使用 UID 值（如 29002） | 不想授予 ShellCrash 组权限 |
| `7890` | 强制要求精确的 `shellcrash:7890` 组存在，否则配置校验失败 | 明确需要使用兼容组 |

不接受 root UID、任意 GID 或把 7890 只加进 `extra_groups`。节点工作目录仍属于原 UID/同 UID GID，权限 `0700`，不会把存储权限改给 ShellCrash 组。root supervisor、Provider 同步管理逻辑仍保持不变。

**重要：** `/etc/group` 中存在 shellcrash 组不等于 ShellCrash 正在运行，也不保证当前 iptables/nftables 规则确实存在 GID 7890 放行路径。若 ShellCrash 实际使用另一种防火墙后端、未启用该组豁免、或出站连接被其他透明代理接管，单靠设置 GID 不能解决；应以实际规则和连接测试为准。

组文件发生变化后 supervisor 会重新检查节点身份并按需更新进程。通过 `project.env` 明确设置 `uid` 可以随时关闭此兼容行为；修改配置后请重启 `x-tunnel.service`。

## systemd 服务级 Group 与测速

安装器保留原有检测：发现准确的 ShellCrash GID 7890 时，为 `x-tunnel.service`、`x-tunnel-speedtest.service`、`x-tunnel-speedtest-single@.service` 写入项目自身的 `shellcrash-bypass.conf`。没有该组时，仅移除项目自身管理的同名 drop-in。管理/root 测速进程因此可使用相应主 GID，但独立 Go 核心和隔离测速子进程仍会显式选择上述身份。若 ShellCrash 是在 X-Tunnel 安装以后添加的，应重新运行安装器以刷新 **systemd 的 Group 设置**。

在 ShellCrash + 无 eBPF 的模式下，CF 测速可能由 root 控制进程直接建立连接；从交互式 shell 手动运行 `python3 cfip_speedtest.py` **不会自动继承 systemd 的 Group=7890**。这类场景应优先使用已正确配置 GID 的测速 service，并验证实际的 ShellCrash 放行规则及测速出口，不能将策略路由验证视为网关一定直连的证明。

## 只读核对

```sh
grep '^shellcrash:' /etc/group || true
systemctl show x-tunnel.service x-tunnel-speedtest.service -p Group
cd /opt/x-tunnel-cf-ip-autotest
python3 - <<'PY'
from supervisor import configured_process_uid
from project_settings import configured_process_gid
uid = configured_process_uid()
print("expected X-Tunnel UID=%d, primary GID=%d" % (uid, configured_process_gid(uid)))
PY
for pid in $(pidof x-tunnel); do
    echo "X-Tunnel PID=$pid"
    grep -E '^(Uid|Gid):' "/proc/$pid/status"
done
```

在有 ShellCrash 兼容组的设备上，Go 子进程预期为 `Uid: 29002 ...` 和 `Gid: 7890 ...`。如果 `Gid` 仍是 29002，请检查是否拉取 `lxc` 最新分支，以及服务是否重启。

以上检查不会删除或修改 ShellCrash 文件和策略路由。对 GID 放行效果的确认还需要结合当前 ShellCrash 防火墙规则与节点连接日志。

其他场景：OpenWrt/procd 参见 `openwrt` 分支；reF1nd eBPF 的 `local.exclude_uid` 是 **UID 规则**，不能使用 GID 7890 替代。
