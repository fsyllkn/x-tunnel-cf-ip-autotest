#!/bin/sh
# X-Tunnel SSH management menu; compatible with BusyBox ash.
set -u
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P) || exit 1
cd "$PROJECT_DIR" || exit 1
TIMER_SCRIPT="$PROJECT_DIR/openwrt/speedtest-timer.sh"
SYSTEMD_TIMER_CLI="$PROJECT_DIR/systemd/speedtest-timer.py"
if [ -f /etc/openwrt_release ] || { [ -f /etc/rc.common ] && [ -x /sbin/procd ]; }; then
  PLATFORM=openwrt
elif [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
  PLATFORM=systemd
else
  PLATFORM=unknown
fi
say() { printf '%s\n' "$*"; }
root_only() { [ "$(id -u)" -eq 0 ] || { say '需要 root 权限。'; return 1; }; }
ask() { printf '%s [y/N]: ' "$1"; IFS= read -r answer || return 1; case "$answer" in y|Y|yes|YES) return 0;; *) return 1;; esac; }
wait_menu() { printf '\n按回车返回...'; IFS= read -r unused || true; }
svc_name() { case "$1" in core) printf x-tunnel;; bot) printf x-tunnel-speedtest-bot;; esac; }
svc_exists() {
  name=$(svc_name "$1")
  if [ "$PLATFORM" = openwrt ]; then [ -f "/etc/init.d/$name" ];
  elif [ "$PLATFORM" = systemd ]; then [ -f "/etc/systemd/system/$name.service" ];
  else return 1; fi
}
svc_status() {
  key=$1
  if ! svc_exists "$key"; then printf '未安装'; return; fi
  name=$(svc_name "$key")
  if [ "$PLATFORM" = openwrt ]; then
    if "/etc/init.d/$name" status >/dev/null 2>&1; then printf '运行中'; else printf '已停止'; fi
  else
    if systemctl is-active --quiet "$name.service"; then printf '运行中'; else printf '已停止'; fi
  fi
}
timer_status() {
  if [ "$PLATFORM" = openwrt ]; then
    if [ -f "$TIMER_SCRIPT" ]; then
      scheduled=$(sh "$TIMER_SCRIPT" status 2>/dev/null) || scheduled=disabled
      if [ "$scheduled" = disabled ]; then printf '未开启'; else printf '已开启 (%s)' "$scheduled"; fi
    elif [ -f /etc/crontabs/root ] && grep -Fq '# x-tunnel-cf-ip-autotest daily speedtest' /etc/crontabs/root; then
      printf '已开启（旧版配置）'
    else
      printf '未开启'
    fi
  elif [ "$PLATFORM" = systemd ]; then
    if [ -f "$SYSTEMD_TIMER_CLI" ] && command -v python3 >/dev/null 2>&1; then
      scheduled=$(python3 "$SYSTEMD_TIMER_CLI" status 2>/dev/null) || scheduled=unknown
      case "$scheduled" in
        disabled) printf '未开启' ;;
        unknown) printf '状态读取失败' ;;
        *) printf '已开启 (%s)' "$scheduled" ;;
      esac
    elif systemctl is-enabled --quiet x-tunnel-speedtest.timer 2>/dev/null; then
      printf '已开启（无法读取时间）'
    else
      printf '未开启'
    fi
  else printf '未知'; fi
}
prepare() {
  file=$1; example=$2
  if [ ! -f "$file" ]; then
    [ -f "$example" ] || { say "缺少模板 $example"; return 1; }
    (umask 077; cp "$example" "$file") || return 1
  fi
  chmod 600 "$file"
}
can_check_config() {
  command -v python3 >/dev/null 2>&1 || return 1
  [ "$1" = "x-tunnel.yaml" ] && python3 -c 'import yaml' >/dev/null 2>&1
}
check_config() {
  [ "$1" = "x-tunnel.yaml" ] || return 1
  PYTHONPATH="$PROJECT_DIR" python3 - <<'PY'
import supervisor
from project_settings import get_target_mbps, read_telegram_credentials
supervisor.read_specs(check_port_availability=False)
get_target_mbps()
read_telegram_credentials(require_chat_id=False)
PY
}
edit_file() {
  root_only || return
  file=$1; example=$2
  prepare "$file" "$example" || return
  mkdir -p cfip-data/config-backups || return
  chmod 700 cfip-data cfip-data/config-backups
  backup="cfip-data/config-backups/$file.$(date +%Y%m%d-%H%M%S).$$"
  (umask 077; cp "$file" "$backup") || return
  chmod 600 "$backup"
  say "配置已备份: $backup"
  vi "$file" || { say '编辑未完成。'; return; }
  chmod 600 "$file"
  if ! can_check_config "$file"; then
    say '配置已保存。此设备尚缺少对应 Python 模块，安装所需功能时将检查配置。'
  elif check_config "$file"; then
    say '校验成功。节点配置会自动重载；其他变量修改后请重启相关服务。'
  else
    say '配置校验失败，暂不要重启服务。'
    if ask '恢复编辑前的备份？'; then
      cp "$backup" "$file"
      chmod 600 "$file"
    fi
  fi
}
config_help() {
  say '唯一配置文件: x-tunnel.yaml（必须 chmod 600）'
  say '  defaults: token, cfip, connections, ech, dns, listen_hosts, first_port'
  say '  nodes:   名称|WSS服务端|token|cfip|连接数|端口'
  say '  settings: provider_path, process_uid, local_proxy, telegram_proxy, target_mbps'
  say '  telegram: token, chat_id（可选；敏感信息不在菜单显示）'
  say '旧版 project.env / speedtest-bot.env 升级时自动迁移并归档。'
}
backup_unified_config() {
  root_only || return
  prepare x-tunnel.yaml x-tunnel.example.yaml || return
  mkdir -p cfip-data/config-backups || return
  chmod 700 cfip-data cfip-data/config-backups
  backup="cfip-data/config-backups/x-tunnel.yaml.$(date +%Y%m%d-%H%M%S).$"
  (umask 077; cp x-tunnel.yaml "$backup") || return
  chmod 600 "$backup"
  say "已备份统一配置: $backup"
}
manage_svc() {
  root_only || return
  key=$1; name=$(svc_name "$key")
  svc_exists "$key" || { say "$name 尚未安装。"; return; }
  while :; do
    printf '\n%s: %s\n' "$name" "$(svc_status "$key")"
    say '1) 启动  2) 停止  3) 重启  4) 开机自启  5) 取消自启  0) 返回'
    printf '选择: '; IFS= read -r option || return
    case "$option" in 1) action=start;; 2) action=stop;; 3) action=restart;; 4) action=enable;; 5) action=disable;; 0) return;; *) continue;; esac
    if [ "$PLATFORM" = openwrt ]; then
      "/etc/init.d/$name" "$action" || say '服务操作失败。'
    else
      systemctl "$action" "$name.service" || say '服务操作失败。'
    fi
  done
}
# Stop only X-Tunnel's procd core service and SOCKS5 children, leaving Bot
# and scheduled CF IP speedtests untouched. This does not alter routes or DNS.
stop_core_only() {
  root_only || return
  if ! svc_exists core; then
    say 'X-Tunnel 核心服务尚未安装。'
    return
  fi
  if [ "$(svc_status core)" = '已停止' ]; then
    say 'X-Tunnel 核心服务已经停止。'
    return
  fi
  ask '停止 X-Tunnel 核心和全部 SOCKS5 节点？现有连接会断开。' || return
  if [ "$PLATFORM" = openwrt ]; then
    /etc/init.d/x-tunnel stop || { say '停止核心服务失败，请检查日志。'; return 1; }
  elif [ "$PLATFORM" = systemd ]; then
    systemctl stop x-tunnel.service || { say '停止核心服务失败，请检查日志。'; return 1; }
  else
    say '不支持的服务管理系统。'
    return 1
  fi
  say "X-Tunnel 核心状态: $(svc_status core)"
}
export_socks_providers() {
  root_only || return
  if ! (cd "$PROJECT_DIR" && python3 sync_singbox.py --export-only); then
    say '导出失败。请先运行 X-Tunnel 核心生成 SOCKS5 Provider。'
    return 1
  fi
  say '导出结果（仅 SOCKS5，不生成伪造的 ss:// 链接）：'
  # Provider path is managed by x-tunnel.yaml settings.provider_path.
  python3 -c '
from sync_singbox import provider_path, CLASH_FILE, V2RAY_FILE, SOCKS_LINKS_FILE
p = provider_path()
for path in (p, p.parent / CLASH_FILE, p.parent / V2RAY_FILE, p.parent / SOCKS_LINKS_FILE):
    print(path)
'
}
# Main-menu restart shortcuts. A Bot restart does not touch X-Tunnel nodes.
restart_service() {
  root_only || return
  key=$1
  name=$(svc_name "$key")
  if ! svc_exists "$key"; then
    say "$name 尚未安装，请先安装对应服务。"
    return
  fi
  if [ "$key" = core ]; then
    ask '重启 X-Tunnel 核心及全部节点？连接会短暂中断。' || return
  else
    ask '仅重启 Telegram Bot？不会重启 X-Tunnel 节点。' || return
  fi
  say "正在重启 $name ..."
  if [ "$PLATFORM" = openwrt ]; then
    if [ "$key" = core ]; then
      # Some fw3/procd builds report "Command failed: Not found" on
      # restart despite successfully spawning the supervisor. Use an
      # explicit stop + start instead, then check real SOCKS5 listeners.
      "/etc/init.d/$name" stop >/dev/null 2>&1 || true
      sleep 1
      if ! "/etc/init.d/$name" start; then
        say '核心服务启动失败，请检查 X-Tunnel 连接日志。'
        return 1
      fi
    elif ! "/etc/init.d/$name" restart; then
      say '重启命令失败，请进入服务管理菜单查看状态并尝试停止/启动。'
      return 1
    fi
  elif [ "$PLATFORM" = systemd ]; then
    if ! systemctl restart "$name.service"; then
      say '重启命令失败，请查看服务日志。'
      return 1
    fi
  else
    say '不支持的服务管理系统。'
    return 1
  fi
  sleep 1
  printf '%s 当前状态: %s\n' "$name" "$(svc_status "$key")"
  if [ "$key" = core ] && [ "$PLATFORM" = openwrt ]; then
    if ! python3 "$PROJECT_DIR/openwrt/healthcheck.py"; then
      say 'ERROR: 核心进程运行中，但至少一个 SOCKS5 节点未监听；不能判定为可用。'
    fi
  fi
}
# Install the byte-identical Go 1.26 stable core recovered from the original single-file source.
# The installer checks ELF, runs a local SOCKS5 handshake, and backs up the old binary.
repair_kernel() {
  root_only || return
  [ "$PLATFORM" = systemd ] || { say '仅支持 Debian/Ubuntu systemd。'; return 1; }
  ask '安装已验证的 Go 1.26 稳定内核？将备份旧二进制，并短暂重启全部 SOCKS5 节点。' || return
  if sh "$PROJECT_DIR/install.sh" --repair-kernel; then
    say '已安装 Go 1.26 稳定内核，正在检查所有 SOCKS5 端口...'
    if ! python3 "$PROJECT_DIR/openwrt/healthcheck.py"; then
      say 'ERROR: 核心可能仍在运行，但节点 SOCKS5 监听未全部就绪。请检查 X-Tunnel 连接日志。'
      return 1
    fi
  else
    say '修复未成功，已停止安装流程；旧二进制在预检失败时不会被替换。'
    return 1
  fi
}
install_services() {
  root_only || return
  prepare x-tunnel.yaml x-tunnel.example.yaml || return
  if grep -q 'example.com\|替换为' x-tunnel.yaml; then
    say '检测到节点示例配置。请先填写真实 X-Tunnel 服务地址和密钥。'
    if ask '现在编辑节点配置？'; then edit_file x-tunnel.yaml x-tunnel.example.yaml; fi
    return
  fi
  say '轻量核心模式：只部署 X-Tunnel 和 Provider，不要求 TG Bot 凭据。'
  if ask '安装/修复核心服务（会重启 X-Tunnel）？'; then
    sh "$PROJECT_DIR/install.sh" || say '安装失败，请检查上方错误。'
  fi
}
update_code() {
  root_only || return
  ask '从 GitHub 更新代码并重新部署（将重启服务）？' || return
  if X_TUNNEL_INSTALL_DIR="$PROJECT_DIR" sh "$PROJECT_DIR/quick-install.sh" --core; then
    say '更新结束，重新载入管理菜单。'
    exec /bin/sh "$PROJECT_DIR/menu.sh"
  else
    say '更新失败；旧配置保留，请查看上方错误。'
  fi
}
# procd labels inherited Go stdout/stderr as python3[SUPERVISOR_PID].
# Resolve the actual script PID instead of printing the full system log:
# that would mix unrelated sing-box, dnsmasq and other services.
find_service_pids() {
  target="$PROJECT_DIR/$1"
  for entry in /proc/[0-9]*; do
    [ -r "$entry/cmdline" ] || continue
    if tr '\000' '\n' < "$entry/cmdline" 2>/dev/null | grep -Fxq "$target"; then
      printf '%s\n' "${entry##*/}"
    fi
  done
}
show_service_log_openwrt() {
  script="$1"
  label="$2"
  pids="$(find_service_pids "$script")"
  if [ -z "$pids" ]; then
    say "$label 当前未运行，无法确定现有 procd 日志所属 PID（不会混合其他服务日志）。"
    return
  fi
  for pid in $pids; do
    # The exact syslog tag identifies all Go child messages inherited via
    # supervisor stdout, without matching unrelated python3 instances.
    logread 2>/dev/null | grep -F "python3[$pid]:" || true
  done | tail -n 100
}
show_logs() {
  say '1) X-Tunnel 连接日志（仅核心）  2) CF IP 测速日志'
  say '3) Telegram Bot 日志        4) 服务状态'
  say '5) 测速环境自检（不启动任务）  0) 返回'
  printf '选择: '; IFS= read -r option || return
  case "$option" in
    1)
      if [ "$PLATFORM" = openwrt ]; then
        show_service_log_openwrt supervisor.py 'X-Tunnel 核心'
      else
        journalctl --no-pager -n 100 -u x-tunnel.service
      fi ;;
    2)
      if [ "$PLATFORM" = openwrt ]; then
        if [ -f cfip-data/openwrt-speedtest.log ]; then
          tail -n 100 cfip-data/openwrt-speedtest.log
        else
          say '暂无 CF IP 测速日志。'
        fi
      else
        journalctl --no-pager -n 100 -u x-tunnel-speedtest.service
      fi ;;
    3)
      if [ "$PLATFORM" = openwrt ]; then
        show_service_log_openwrt telegram_speedtest_bot.py 'Telegram Bot'
      else
        journalctl --no-pager -n 100 -u x-tunnel-speedtest-bot.service
      fi ;;
    4)
      if [ "$PLATFORM" = openwrt ]; then
        /etc/init.d/x-tunnel status 2>/dev/null || true
        /etc/init.d/x-tunnel-speedtest-bot status 2>/dev/null || true
      else
        systemctl --no-pager status x-tunnel.service x-tunnel-speedtest-bot.service || true
      fi ;;
    5)
      say '检查测速 Python 模块；不会修改节点配置、网络路由或启动测速。'
      if ! command -v python3 >/dev/null 2>&1; then
        say 'ERROR: python3 未安装。'
      elif (cd "$PROJECT_DIR" && python3 -c 'import cfip_speedtest; print("CF测速模块导入正常")'); then
        say '测速 Python 导入检查通过。'
      else
        say '测速 Python 导入检查失败。请查看上方 traceback。'
      fi
      if command -v ip >/dev/null 2>&1; then
        say '检测测速器的实际 IPv4 出站模式（只读）：'
        if ! (cd "$PROJECT_DIR" && python3 -c '
from cfip_speedtest import DirectCandidateRoutes
mode, mark = DirectCandidateRoutes.routing_mode("-4", "100")
if mode == "native-direct":
    DirectCandidateRoutes.verify_native_route("1.1.1.1", "-4")
    print("OK: 普通默认路由；无需安装 ShellCrash，也不会添加测速策略路由")
else:
    print("OK: 已检测到 fwmark 策略路由（mark=%s）" % mark)
'); then
          say 'ERROR: 无法确认安全的直连路由，请检查上方错误。'
        fi
      else
        say '提示：缺少 ip 命令，测速需要 ip-full。'
      fi ;;
    0) return;;
  esac
}
manage_timer() {
  root_only || return
  say "当前: $(timer_status)"
  say '1) 开启/修改每日测速时间  2) 关闭自动测速  0) 返回'
  printf '选择: '; IFS= read -r option || return
  case "$option" in
    1)
      if [ "$PLATFORM" = openwrt ]; then
        [ -f "$TIMER_SCRIPT" ] || { say '找不到定时管理脚本，请先更新 openwrt 源码。'; return; }
        saved=$(sh "$TIMER_SCRIPT" status) || { say '读取定时任务失败。'; return; }
        default_time=$saved
        [ "$default_time" != disabled ] || default_time=18:00
        printf '每日测速时间（24小时制 HH:MM）[%s]: ' "$default_time"
        IFS= read -r chosen || return
        chosen=${chosen:-$default_time}
        if ! sh "$TIMER_SCRIPT" validate "$chosen"; then
          say '时间格式错误，请输入 00:00 至 23:59，例如 06:45、18:00、23:30。'
          return
        fi
        if [ -f /etc/init.d/x-tunnel ] &&
           (cd "$PROJECT_DIR" && python3 -c 'import cfip_speedtest' >/dev/null 2>&1); then
          sh "$TIMER_SCRIPT" enable "$chosen" || say '设置定时测速失败。'
        else
          say '首次启用需安装测速依赖；安装器可能重启 X-Tunnel 核心。'
          sh "$PROJECT_DIR/install.sh" --enable-timer "--timer-at=$chosen" || say '启用失败。'
        fi
      else
        [ -f "$SYSTEMD_TIMER_CLI" ] || { say 'Linux 定时管理脚本不存在，请先更新 main 分支。'; return; }
        saved=$(python3 "$SYSTEMD_TIMER_CLI" saved) || return
        printf '每日测速时间（24小时制 HH:MM）[%s]: ' "$saved"
        IFS= read -r chosen || return
        chosen=${chosen:-$saved}
        python3 "$SYSTEMD_TIMER_CLI" validate "$chosen" || return
        # Timer management must never run the speedtest, install services, or
        # restart the X-Tunnel core. Installation is a separate explicit action.
        if [ ! -f /etc/systemd/system/x-tunnel-speedtest.service ] ||
           [ ! -f /etc/systemd/system/x-tunnel-speedtest.timer ]; then
          say '定时器或测速服务尚未安装。请先使用菜单 1 安装/修复服务，再返回设置定时时间。'
          return 1
        fi
        if python3 "$SYSTEMD_TIMER_CLI" enable "$chosen"; then
          say '仅更新每日测速计划；未执行即时测速，也未重启 X-Tunnel 核心。'
        else
          say '设置定时测速失败，请检查 systemd timer 日志。'
          return 1
        fi
      fi ;;
    2)
      if [ "$PLATFORM" = openwrt ]; then
        [ -f "$TIMER_SCRIPT" ] || { say '找不到定时管理脚本，请先更新 openwrt 源码。'; return; }
        sh "$TIMER_SCRIPT" disable || say '关闭失败。'
      elif [ "$PLATFORM" = systemd ]; then
        [ -f "$SYSTEMD_TIMER_CLI" ] || { say 'Linux 定时管理脚本不存在，请先更新 main 分支。'; return 1; }
        python3 "$SYSTEMD_TIMER_CLI" disable || say '关闭失败。'
      else
        say '不支持的服务管理系统。'
      fi ;;
    0) return ;;
  esac
}
run_test() {
  root_only || return
  say '注意：测速可能更新节点 CF IP。旧式策略路由模式会临时添加候选 IP 路由；sing-box 网关模式不会修改这些路由。'
  ask '现在执行完整测速？' || return
  if [ "$PLATFORM" = openwrt ]; then python3 "$PROJECT_DIR/cfip_speedtest.py" || say '测速失败。'
  else systemctl start x-tunnel-speedtest.service || say '测速启动失败。'; fi
}
# Menu labels are padded by terminal cell width (CJK=2), not byte count.
# Keep the second label at cell 33; do not format two columns with printf %-Ns.
show_menu() {
  say '=========================================================='
  say '                 X-Tunnel 管理菜单'
  say '=========================================================='
  if command -v opkg >/dev/null 2>&1; then manager=opkg
  elif command -v apk >/dev/null 2>&1; then manager=apk
  elif command -v apt-get >/dev/null 2>&1; then manager=apt
  else manager='未检测到'; fi
  printf '平台: %s  包管理: %s\n' "$PLATFORM" "$manager"
  printf '项目路径: %s\n' "$PROJECT_DIR"
  printf '核心服务: %s  |  TG Bot: %s  |  定时测速: %s\n' "$(svc_status core)" "$(svc_status bot)" "$(timer_status)"
  say '----------------------------------------------------------'
  say '  【安装与常用】'
  say '   1) 安装/修复核心服务           2) 更新 GitHub main 分支'
  say '   3) 重启 X-Tunnel               4) 查看日志'
  say '   5) X-Tunnel 服务管理           6) 停止 X-Tunnel 核心'
  say '  【配置管理】'
  say '   7) 编辑统一 YAML 配置          8) 配置检查'
  say '   9) 查看配置字段说明           10) 备份统一 YAML 配置'
  say '  【CF IP 测速】'
  say '  11) 立即运行完整测速           12) 定时测速管理'
  say '  【Telegram Bot】'
  say '  13) 安装/启用 Telegram Bot     14) Telegram Bot 服务管理'
  say '  15) 重启 Telegram Bot          16) 关闭 Bot 节省内存'
  say '  【导出与维护】'
  say '  17) 导出 Clash/V2Ray/SOCKS5 节点'
  say '  18) 安装已验证 Go 1.26 稳定内核'
  say '  19) 卸载服务（保留配置）'
  say '   0) 退出'
}
while :; do
  show_menu
  printf '\n请输入选项: '
  IFS= read -r option || break
  case "$option" in
    1) install_services;;
    2) update_code;;
    3) restart_service core;;
    4) show_logs;;
    5) manage_svc core;;
    6) stop_core_only;;
    7) edit_file x-tunnel.yaml x-tunnel.example.yaml;;
    8)
      if [ ! -f x-tunnel.yaml ]; then
        say 'x-tunnel.yaml: 未配置'
      elif ! can_check_config x-tunnel.yaml; then
        say 'x-tunnel.yaml: 缺少 PyYAML，无法校验'
      elif check_config x-tunnel.yaml; then
        say 'x-tunnel.yaml: OK（节点、监听、UID、测速目标和可选 Bot 均已校验）'
      else
        say 'x-tunnel.yaml: INVALID'
      fi;;
    9) config_help;;
    10) backup_unified_config;;
    11) run_test;;
    12) manage_timer;;
    13)
      root_only || continue
      prepare x-tunnel.yaml x-tunnel.example.yaml || continue
      if grep -q 'example.com\|替换为' x-tunnel.yaml; then
        say '请先在选项 7 编辑统一 YAML 配置并填写真实节点，再安装 Telegram Bot。'
        continue
      fi
      if ! python3 -c 'import telegram_speedtest_bot as b; b.read_config()' >/dev/null 2>&1; then
        say '先在 x-tunnel.yaml 的 telegram.token / telegram.chat_id 填写凭据。'
        if ask '现在编辑统一 YAML 配置？'; then edit_file x-tunnel.yaml x-tunnel.example.yaml; fi
      else
        say '将按需安装 HTTPS/IDNA 和 ip-full 等组件，然后启动 Bot。'
        if ask '安装/启用 Bot（将重启核心服务）？'; then
          sh "$PROJECT_DIR/install.sh" --with-bot || say 'Bot 安装未完成。'
        fi
      fi;;
    14) manage_svc bot;;
    15) restart_service bot;;
    16)
      root_only || continue
      if [ "$PLATFORM" = systemd ]; then
        systemctl disable --now x-tunnel-speedtest-bot.service || say 'Bot 停用失败。'
      elif [ -f /etc/init.d/x-tunnel-speedtest-bot ]; then
        /etc/init.d/x-tunnel-speedtest-bot stop >/dev/null 2>&1 || true
        /etc/init.d/x-tunnel-speedtest-bot disable >/dev/null 2>&1 || true
        say 'Bot 已停止并取消开机自启；X-Tunnel 核心服务未重启。'
      else
        say 'Bot 服务尚未安装。'
      fi;;
    17) export_socks_providers;;
    18) repair_kernel;;
    19)
      root_only || continue
      if ask '卸载服务和定时任务（保留配置与内核）？'; then
        sh "$PROJECT_DIR/uninstall.sh" || say '卸载失败。'
      fi;;
    0|q|Q) break;;
    *) say '无效选择。';;
  esac
  wait_menu
  printf '\033[2J\033[H'
done
say '已退出 X-Tunnel 管理菜单。'
