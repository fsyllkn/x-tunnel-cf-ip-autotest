# X-Tunnel 与 ShellCrash 主代理回环修复手册

> 历史方案，已不适用于当前项目版本。当前 X-Tunnel 服务不再修改 ShellCrash 配置、防火墙或服务状态；仅生成节点 Provider。请勿照本手册执行自动联动步骤。

本文用于在另一台 Linux/ShellCrash 设备上修复以下问题：ShellCrash 主代理选择器选择“X-Tunnel 节点组”后，组内 X-Tunnel 节点的 URLTest/延迟测速全部超时；主代理改选普通节点后，X-Tunnel 组又恢复正常。

适用条件：X-Tunnel 作为本机 SOCKS5 客户端运行；ShellCrash 使用 sing-box；本机 ShellCrash 会代理本机发出的流量；防火墙后端为 ShellCrash 的 `iptables` 模式。文末说明 `nftables` 模式的限制。

## 1. 现象与原因

常见界面现象：

- 主代理选择普通节点时，X-Tunnel 节点组显示 `URLTest 4/4`，成员有延迟。
- 主代理切换到 X-Tunnel 节点组后，组状态变为 `URLTest 0/4`，节点测速图标超时。
- 手动直连某个本机 SOCKS5 端口可能仍然成功，因此只测 SOCKS 端口不足以排除回环。

原因是 ShellCrash 本机流量转发也会处理本机进程发出的连接。X-Tunnel 进程连接自己的 CF 上游 IP 时，如果这条连接被送入当前主代理选择器，而选择器又指向 X-Tunnel 组，上游连接就会再次进入同一个 X-Tunnel 节点，形成递归链路：

```text
ShellCrash 主选择器（X-Tunnel 组）
  → 本机 X-Tunnel SOCKS 节点
  → x-tunnel 进程连接该节点的 CF 上游 IP
  → ShellCrash 本机 OUTPUT 分流再次代理
  → X-Tunnel 组
  → 回到同一个 x-tunnel 进程
```

修复要点是：**将当前各 X-Tunnel 节点的 CF 上游 IP 从 ShellCrash 本机 OUTPUT 代理链中精确放行**。项目同步器也会在 sing-box 路由规则中为这些 IP 添加 `direct` 规则，但在本机 TProxy/OUTPUT 分流场景中，单独依赖 sing-box 路由规则不足以保证 x-tunnel 上游连接不被再次捕获；iptables 层的精确放行是关键。

## 2. 修复内容

更新后的项目包含两层同步：

1. `sync_singbox.py` 根据 `x-tunnel.yaml` 当前节点的 `cfip`，在 `/etc/ShellCrash/jsons/config.json` 的 `route.rules` 开头生成精确的 `ip_cidr → direct` 规则。规则只覆盖当前节点所用 IP，不会把整个 Cloudflare 网段设为直连。
2. `supervisor.py` 在 ShellCrash `iptables` 模式的 mangle 链 `shellcrash_mark_out` 中，为每个当前 CF 上游 IP 插入 `-d <IP>/32 -j RETURN`（IPv6 使用 `/128`）规则。监督器每 10 秒检查一次；ShellCrash 重启后会重新补回；节点 CF IP 改变时添加新 IP 并清理由项目管理的旧 IP。

## 3. 部署前检查与备份

以下命令以项目目录 `/home/x-tunnel` 为例。若实际目录不同，先设置正确路径：

```sh
export PROJECT_DIR=/home/x-tunnel
```

确认 ShellCrash 的防火墙后端与本机 OUTPUT 链：

```sh
grep '^firewall_mod=' /etc/ShellCrash/configs/ShellCrash.cfg
iptables -t mangle -S shellcrash_mark_out
```

本手册的自动修复要求第一条输出为 `firewall_mod=iptables`，并且第二条命令能列出 `shellcrash_mark_out` 链。若使用原生 nftables，先看第 8 节，不要假设下文 iptables 自动同步会生效。

备份项目代码和 ShellCrash 主配置。ShellCrash 配置可能包含订阅或凭据，备份目录应只允许 root 访问：

```sh
BACKUP_DIR="/root/x-tunnel-loop-fix-$(date +%Y%m%d-%H%M%S)"
install -d -m 0700 "$BACKUP_DIR"
cp -a "$PROJECT_DIR/supervisor.py" "$PROJECT_DIR/sync_singbox.py" "$BACKUP_DIR/"
install -m 0600 /etc/ShellCrash/jsons/config.json "$BACKUP_DIR/config.json"
```

**不要**把 `x-tunnel.yaml`、`project.env`、`speedtest-bot.env` 或含凭据的 ShellCrash 配置上传到公共仓库或聊天中。

## 4. 同步修复代码

把新版本源码包复制到目标设备。源码包不含运行节点、Bot 密钥和 `project.env`。假设压缩包位于 `/root/x-tunnel-source.tar.gz`：

```sh
install -d -m 0700 /root/x-tunnel-update
tar -xzf /root/x-tunnel-source.tar.gz -C /root/x-tunnel-update
cp -a /root/x-tunnel-update/x-tunnel/. "$PROJECT_DIR/"
```

解包只覆盖项目源码和示例文件，不会从源码包生成或覆盖私有的 `x-tunnel.yaml`、`project.env`、`speedtest-bot.env`。检查新代码语法：

```sh
python3 -m py_compile \
  "$PROJECT_DIR/supervisor.py" \
  "$PROJECT_DIR/sync_singbox.py"
```

## 5. 应用配置与防火墙规则

执行顺序很重要：先停止 X-Tunnel，更新 sing-box 配置并重启 ShellCrash，再启动新版监督器。这样 ShellCrash 重建防火墙链时，不会让正在运行的 X-Tunnel 上游在修复尚未加载时持续回环。

```sh
systemctl stop x-tunnel.service
cd "$PROJECT_DIR"
python3 sync_singbox.py
/etc/ShellCrash/start.sh restart
systemctl start x-tunnel.service
```

`sync_singbox.py` 可能输出 `restart-required`、`provider-updated` 或 `unchanged`。本步骤会显式重启 ShellCrash，是为了确保刚生成的路由配置被当前核心加载；不要只运行同步脚本后就认为运行态已经更新。

新版 `x-tunnel.service` 启动后会读取现有节点的 `cfip`，并将精确 IP 放行项插入 `shellcrash_mark_out`。等待约 10 秒，然后检查服务和规则：

```sh
systemctl is-active x-tunnel.service
iptables -t mangle -S shellcrash_mark_out | grep ' -d '
```

也可以逐个验证配置中当前 CF IP 对应的放行项。下面脚本只输出节点名和 IP，不会输出 token：

```sh
cd "$PROJECT_DIR"
python3 - <<'PY'
import ipaddress
import subprocess
from supervisor import read_specs

for spec in read_specs():
    ip = ipaddress.ip_address(spec["cfip"])
    address = "%s/%d" % (ip, ip.max_prefixlen)
    result = subprocess.run(
        ["iptables" if ip.version == 4 else "ip6tables",
         "-w", "-t", "mangle", "-C", "shellcrash_mark_out",
         "-d", address, "-j", "RETURN"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    print(spec["name"], address, "OK" if result.returncode == 0 else "MISSING")
PY
```

每个当前节点都应显示 `OK`。若显示 `MISSING`，确认当前确实是 `iptables` 模式、OUTPUT 链已启动、`x-tunnel.service` 正在运行，并查看：

```sh
journalctl -u x-tunnel.service -n 80 --no-pager
```

## 6. 验证方式

1. 在 ShellCrash 面板将**主代理选择器**切换到 X-Tunnel 节点组。
2. 等待 URLTest 完成刷新；4 个节点的部署应显示 `URLTest 4/4`，节点卡片应有延迟，而不是闪电/超时状态。
3. 可再访问一个确认走 ShellCrash 本机代理的 URL，验证代理流量。若本机 HTTP 代理端口为 7890：

   ```sh
   curl -x http://127.0.0.1:7890 -I --max-time 10 \
     https://www.gstatic.com/generate_204
   ```

   返回 HTTP `204` 表示本次请求成功。

仅对 `127.0.0.1:32322` 等 X-Tunnel SOCKS 端口直接运行 curl，**不能单独证明主选择器切换后的回环已修复**；关键验证是主代理选择器实际选中 X-Tunnel 组后，ShellCrash 组内延迟恢复。

## 7. 后续节点 IP 变化与维护

- 编辑 `x-tunnel.yaml` 或测速脚本写入新 CF IP 后，supervisor 会重启对应 X-Tunnel 进程并同步 sing-box 规则。
- ShellCrash `iptables` 链中，项目管理的旧 CF IP 放行项会删除，新 IP 放行项会加入。
- ShellCrash 单独重启后，supervisor 每 10 秒检查一次并恢复缺少的放行项；短时间内若节点刚好重连，可等规则恢复后再看 URLTest 状态。
- 不要把整个 Cloudflare IP 段加入放行规则；本修复只允许节点配置中的具体上游地址。
- 修改 `supervisor.py` 后需要重启项目服务，使新代码生效：

  ```sh
  systemctl restart x-tunnel.service
  ```

## 8. nftables 模式

当前项目监督器自动维护的是 ShellCrash `iptables` 兼容链 `shellcrash_mark_out`。若设备使用 ShellCrash 原生 `nftables`，不要运行 iptables 检查后误判为已修复。需要在 ShellCrash 本机 `inet shellcrash output` 链中、通用 mark 规则之前为每个上游地址插入精确 `return` 规则，例如：

```sh
nft insert rule inet shellcrash output ip daddr 104.20.16.77/32 return comment "x-tunnel-upstream-bypass"
```

IPv6 地址应使用 `ip6 daddr <地址>/128`。该规则必须在 ShellCrash 防火墙重建后重新应用；目前此源码包尚未自动管理原生 nftables 规则。不要直接改 ShellCrash 受管脚本，否则升级时可能被覆盖。

## 9. 回滚

若更新后出现其他问题，可停止 X-Tunnel、恢复备份的代码和 ShellCrash 配置，再重启服务：

```sh
systemctl stop x-tunnel.service
cp -a "$BACKUP_DIR/supervisor.py" "$BACKUP_DIR/sync_singbox.py" "$PROJECT_DIR/"
install -m 0600 "$BACKUP_DIR/config.json" /etc/ShellCrash/jsons/config.json
```

若要删除新版监督器已经登记的 iptables 放行规则，可在服务停止时执行：

```sh
cd "$PROJECT_DIR"
python3 - <<'PY'
from supervisor import sync_firewall_bypass
sync_firewall_bypass([])
PY
```

然后重启 ShellCrash 并按需启动原版/回滚后的 X-Tunnel 服务：

```sh
/etc/ShellCrash/start.sh restart
systemctl start x-tunnel.service
```

恢复 `config.json` 前确认备份时点之后没有需要保留的 ShellCrash 配置变更；不要为了回滚而覆盖其他新配置。
