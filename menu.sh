#!/bin/sh
# X-Tunnel SSH management menu; compatible with BusyBox ash.
set -u
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P) || exit 1
cd "$PROJECT_DIR" || exit 1
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
    if [ -f /etc/crontabs/root ] && grep -Fq '# x-tunnel-cf-ip-autotest daily speedtest' /etc/crontabs/root; then printf '已开启'; else printf '未开启'; fi
  elif [ "$PLATFORM" = systemd ]; then
    if systemctl is-enabled --quiet x-tunnel-speedtest.timer 2>/dev/null; then printf '已开启'; else printf '未开启'; fi
  else printf '未知'; fi
}
prepare() {
  file=$1; example=$2
  if [ ! -f "$file" ]; then
    [ -f "$example" ] || { say "缺少模板 $example"; return 1; }
    (umask 077; cp "$example" "$file") || return 1
  fi
  case "$file" in x-tunnel.yaml|speedtest-bot.env) chmod 600 "$file";; project.env) chmod 644 "$file";; esac
}
can_check_config() {
  command -v python3 >/dev/null 2>&1 || return 1
  case "$1" in
    x-tunnel.yaml) python3 -c 'import yaml, logging' >/dev/null 2>&1 ;;
    speedtest-bot.env) python3 -c 'import urllib.request, codecs; codecs.lookup("idna")' >/dev/null 2>&1 ;;
    project.env) python3 -c 'import pathlib, shlex' >/dev/null 2>&1 ;;
    *) return 1 ;;
  esac
}
check_config() {
  case "$1" in
    x-tunnel.yaml) PYTHONPATH="$PROJECT_DIR" python3 -c 'import supervisor; supervisor.read_specs(check_port_availability=False)' ;;
    speedtest-bot.env) PYTHONPATH="$PROJECT_DIR" python3 -c 'import telegram_speedtest_bot as b; b.read_config()' ;;
    project.env) PYTHONPATH="$PROJECT_DIR" python3 -c 'import project_settings as p; p.load_project_env(); p.get_target_mbps()' >/dev/null ;;
  esac
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
  case "$file" in x-tunnel.yaml|speedtest-bot.env) chmod 600 "$file";; project.env) chmod 644 "$file";; esac
  if ! can_check_config "$file"; then
    say '配置已保存。此设备尚缺少对应 Python 模块，安装所需功能时将检查配置。'
  elif check_config "$file"; then
    say '校验成功。节点配置会自动重载；其他变量修改后请重启相关服务。'
  else
    say '配置校验失败，暂不要重启服务。'
    if ask '恢复编辑前的备份？'; then
      cp "$backup" "$file"
      case "$file" in x-tunnel.yaml|speedtest-bot.env) chmod 600 "$file";; project.env) chmod 644 "$file";; esac
    fi
  fi
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
    if ! "/etc/init.d/$name" restart; then
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
}
install_services() {
  root_only || return
  prepare x-tunnel.yaml x-tunnel.example.yaml || return
  prepare project.env project.env.example || return
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
show_logs() {
  say '1) X-Tunnel/Bot 系统日志  2) 测速日志  3) 服务状态'
  say '4) 测速环境自检（不启动任务）  0) 返回'
  printf '选择: '; IFS= read -r option || return
  case "$option" in
    1)
      if [ "$PLATFORM" = openwrt ]; then logread | tail -n 100
      else journalctl --no-pager -n 100 -u x-tunnel.service -u x-tunnel-speedtest-bot.service; fi ;;
    2)
      if [ "$PLATFORM" = openwrt ]; then
        if [ -f cfip-data/openwrt-speedtest.log ]; then tail -n 100 cfip-data/openwrt-speedtest.log; else say '暂无测速日志。'; fi
      else journalctl --no-pager -n 100 -u x-tunnel-speedtest.service; fi ;;
    3)
      if [ "$PLATFORM" = openwrt ]; then
        /etc/init.d/x-tunnel status 2>/dev/null || true
        /etc/init.d/x-tunnel-speedtest-bot status 2>/dev/null || true
      else systemctl --no-pager status x-tunnel.service x-tunnel-speedtest-bot.service || true; fi ;;
    4)
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
  esac
}
disable_timer_ow() {
  [ -f /etc/crontabs/root ] || return 0
  tmp="/etc/crontabs/root.xtunnel-menu.$$"
  awk '
    $0=="# x-tunnel-cf-ip-autotest daily speedtest" { skip=1; next }
    skip==1 && /openwrt\/run-speedtest\.sh/ { skip=0; next }
    { skip=0; print }
  ' /etc/crontabs/root > "$tmp" || { rm -f "$tmp"; return 1; }
  cat "$tmp" > /etc/crontabs/root
  rm -f "$tmp"
  [ -x /etc/init.d/cron ] && /etc/init.d/cron restart >/dev/null 2>&1 || true
}
manage_timer() {
  root_only || return
  say "当前: $(timer_status)"
  say '1) 开启每天 18:00 测速  2) 关闭自动测速  0) 返回'
  printf '选择: '; IFS= read -r option || return
  case "$option" in
    1)
      if [ "$PLATFORM" = openwrt ]; then sh "$PROJECT_DIR/install.sh" --enable-timer || say '启用失败。'
      else systemctl enable --now x-tunnel-speedtest.timer || say '启用失败。'; fi ;;
    2)
      if [ "$PLATFORM" = openwrt ]; then disable_timer_ow && say '已关闭。'
      else systemctl disable --now x-tunnel-speedtest.timer || say '关闭失败。'; fi ;;
  esac
}
run_test() {
  root_only || return
  say '注意：测速可能更新节点 CF IP，也会临时变更候选 IP 的直连路由。'
  ask '现在执行完整测速？' || return
  if [ "$PLATFORM" = openwrt ]; then python3 "$PROJECT_DIR/cfip_speedtest.py" || say '测速失败。'
  else systemctl start x-tunnel-speedtest.service || say '测速启动失败。'; fi
}
show_menu() {
  say '=========================================================='
  say '                 X-Tunnel 管理菜单'
  say '=========================================================='
  if command -v opkg >/dev/null 2>&1; then manager=opkg
  elif command -v apk >/dev/null 2>&1; then manager=apk
  else manager='未检测到'; fi
  printf '平台: %s  包管理: %s\n' "$PLATFORM" "$manager"
  printf '项目路径: %s\n' "$PROJECT_DIR"
  printf '核心服务: %s  |  TG Bot: %s  |  定时测速: %s\n' "$(svc_status core)" "$(svc_status bot)" "$(timer_status)"
  say '----------------------------------------------------------'
  say ' 1) 安装/修复核心服务       2) GitHub 更新 OpenWrt 分支'
  say ' 3) 设置 Telegram Bot      4) 编辑项目变量配置'
  say ' 5) 编辑 X-Tunnel 节点     6) X-Tunnel 服务管理'
  say ' 7) Telegram Bot 服务管理  8) 查看日志'
  say ' 9) 定时测速管理          10) 立即运行完整测速'
  say '11) 配置检查              12) 卸载服务（保留配置）'
  say '13) 安装/启用 Telegram Bot 14) 关闭 Bot 节省内存'
  say '15) 一键重启 X-Tunnel     16) 一键重启 Telegram Bot'
  say ' 0) 退出'
}
while :; do
  show_menu
  printf '\n请输入选项: '
  IFS= read -r option || break
  case "$option" in
    1) install_services;;
    2) update_code;;
    3) edit_file speedtest-bot.env speedtest-bot.env.example;;
    4) edit_file project.env project.env.example;;
    5) edit_file x-tunnel.yaml x-tunnel.example.yaml;;
    6) manage_svc core;;
    7) manage_svc bot;;
    8) show_logs;;
    9) manage_timer;;
    10) run_test;;
    11)
      for file in x-tunnel.yaml speedtest-bot.env project.env; do
        if [ -f "$file" ]; then
          if ! can_check_config "$file"; then say "$file: 等待安装依赖后校验"
          elif check_config "$file"; then say "$file: OK"
          else say "$file: INVALID"; fi
        else say "$file: 未配置"; fi
      done;;
    13)
      root_only || continue
      prepare x-tunnel.yaml x-tunnel.example.yaml || continue
      if grep -q 'example.com\|替换为' x-tunnel.yaml; then
        say '请先在选项 5 配置真实节点，再安装 Telegram Bot。'
        continue
      fi
      prepare speedtest-bot.env speedtest-bot.env.example || continue
      prepare project.env project.env.example || continue
      if grep -q '替换为' speedtest-bot.env; then
        say '先填写 TG_TOKEN、TG_CHATID，并确认 project.env 中的 Telegram 代理地址。'
        if ask '现在编辑 Telegram Bot 配置？'; then edit_file speedtest-bot.env speedtest-bot.env.example; fi
      else
        say '将按需安装 HTTPS/IDNA 和 ip-full 等组件，然后启动 Bot。'
        if ask '安装/启用 Bot（将重启核心服务）？'; then
          sh "$PROJECT_DIR/install.sh" --with-bot || say 'Bot 安装未完成。'
        fi
      fi;;
    14)
      root_only || continue
      if [ -f /etc/init.d/x-tunnel-speedtest-bot ]; then
        /etc/init.d/x-tunnel-speedtest-bot stop >/dev/null 2>&1 || true
        /etc/init.d/x-tunnel-speedtest-bot disable >/dev/null 2>&1 || true
        say 'Bot 已停止并取消开机自启；X-Tunnel 核心服务未重启。'
      else
        say 'Bot 服务尚未安装。'
      fi;;
    12)
      root_only || continue
      if ask '卸载服务和定时任务（保留配置与内核）？'; then
        sh "$PROJECT_DIR/uninstall.sh" || say '卸载失败。'
      fi;;
    15) restart_service core;;
    16) restart_service bot;;
    0|q|Q) break;;
    *) say '无效选择。';;
  esac
  wait_menu
  printf '\033[2J\033[H'
done
say '已退出 X-Tunnel 管理菜单。'
