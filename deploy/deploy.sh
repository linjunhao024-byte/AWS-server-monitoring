#!/usr/bin/env bash
# =============================================================================
#  快速部署脚本 — 跳过交互配置，直接安装预制版本
#
#  用法: sudo bash deploy.sh
# =============================================================================
set -euo pipefail

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

INSTALL_DIR="/opt/bandwidth_monitor"
LOG_DIR="/var/log/bandwidth"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

W=73
frame_top()    { echo -e "${CYAN}╔$(printf '═%.0s' $(seq 1 $W))╗${NC}"; }
frame_title()  { printf "${CYAN}║${NC}${BOLD}  %-$((${W}-2))s${NC}${CYAN}║${NC}\n" "$1"; }
frame_sep()    { echo -e "${CYAN}╠$(printf '═%.0s' $(seq 1 $W))╣${NC}"; }
frame_bottom() { echo -e "${CYAN}╚$(printf '═%.0s' $(seq 1 $W))╝${NC}"; }
frame_row()    { printf "${CYAN}║${NC}  %-$((${W}-2))s${CYAN}║${NC}\n" "$1"; }

step_top()     { echo -e "${CYAN}┌$(printf '─%.0s' $(seq 1 $W))┐${NC}"; }
step_title()   { printf "${CYAN}│${NC}${BOLD}  %s${NC}%*s${CYAN}│${NC}\n" "$1" $((${W} - 2 - ${#1})) ""; }
step_sep()     { echo -e "${CYAN}├$(printf '─%.0s' $(seq 1 $W))┤${NC}"; }
step_bottom()  { echo -e "${CYAN}└$(printf '─%.0s' $(seq 1 $W))┘${NC}"; }
step_row()     { printf "${CYAN}│${NC}  %-$((${W}-2))s${CYAN}│${NC}\n" "$1"; }

# Root 检查
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}请使用 sudo 运行此脚本${NC}"
    exit 1
fi

echo ""
frame_top
frame_title "AWS Lightsail 监控系统 — 快速部署"
frame_sep
frame_row ""
frame_row "  预制配置，跳过交互向导，直接安装。"
frame_row ""
frame_bottom

# 1. 创建目录
step_top
step_title "创建目录"
step_sep
mkdir -p "$INSTALL_DIR"
mkdir -p "$LOG_DIR"
step_row "${GREEN}✓${NC} ${INSTALL_DIR}"
step_row "${GREEN}✓${NC} ${LOG_DIR}"
step_bottom

# 2. 复制文件
step_top
step_title "安装文件"
step_sep
FILES=(
    config.py utils.py stats.py notifications.py
    data_sources.py analyzer.py reporter.py
    monitor_daemon.py route_daemon.py main.py
    __init__.py settings.json
)
for f in "${FILES[@]}"; do
    if [ -f "${SCRIPT_DIR}/$f" ]; then
        cp "${SCRIPT_DIR}/$f" "${INSTALL_DIR}/"
        step_row "  ${GREEN}✓${NC} $f"
    else
        step_row "  ${RED}✗${NC} $f (缺失)"
    fi
done
chmod 755 "${INSTALL_DIR}"/*.py
chmod 600 "${INSTALL_DIR}/settings.json"

# 安装全局快捷命令
cat > /usr/local/bin/monitor << 'CMDEOF'
#!/usr/bin/env bash
exec python3 /opt/bandwidth_monitor/main.py "$@"
CMDEOF
chmod +x /usr/local/bin/monitor
step_row "  ${GREEN}✓${NC} 快捷命令: ${YELLOW}monitor${NC}"

step_bottom

# 3. systemd 服务
step_top
step_title "配置 systemd 服务"
step_sep
SERVICES=(
    "bandwidth-monitor.service"
    "bandwidth-analyzer.service"
    "bandwidth-analyzer.timer"
    "route-monitor.service"
)
for svc in "${SERVICES[@]}"; do
    if [ -f "${SCRIPT_DIR}/systemd/${svc}" ]; then
        cp "${SCRIPT_DIR}/systemd/${svc}" "/etc/systemd/system/"
        step_row "  ${GREEN}✓${NC} ${svc}"
    else
        step_row "  ${YELLOW}⚠${NC} ${svc} 未找到"
    fi
done

# 日志轮转定时器
cat > /etc/systemd/system/bandwidth-maintenance.service << 'MSVCEOF'
[Unit]
Description=Daily log rotation and disk alert
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/bandwidth_monitor/notifications.py --maintenance
StandardOutput=journal
StandardError=journal
MSVCEOF

cat > /etc/systemd/system/bandwidth-maintenance.timer << 'MTMREOF'
[Unit]
Description=Run maintenance daily at 04:00

[Timer]
OnCalendar=*-*-* 04:00:00
Persistent=true

[Install]
WantedBy=timers.target
MTMREOF

step_row "  ${GREEN}✓${NC} bandwidth-maintenance.timer"
step_sep
step_row "重载 systemd..."
systemctl daemon-reload
step_row "启用开机自启..."
systemctl enable bandwidth-monitor.service 2>/dev/null && \
    step_row "  ${GREEN}✓${NC} bandwidth-monitor" || \
    step_row "  ${YELLOW}⚠${NC} bandwidth-monitor 启用失败"
systemctl enable bandwidth-analyzer.timer 2>/dev/null && \
    step_row "  ${GREEN}✓${NC} bandwidth-analyzer.timer" || \
    step_row "  ${YELLOW}⚠${NC} bandwidth-analyzer.timer 启用失败"
systemctl enable route-monitor.service 2>/dev/null && \
    step_row "  ${GREEN}✓${NC} route-monitor" || \
    step_row "  ${YELLOW}⚠${NC} route-monitor 启用失败"
systemctl enable bandwidth-maintenance.timer 2>/dev/null && \
    step_row "  ${GREEN}✓${NC} bandwidth-maintenance" || \
    step_row "  ${YELLOW}⚠${NC} bandwidth-maintenance 启用失败"
step_bottom

# 4. 启动服务
step_top
step_title "启动服务"
step_sep
FAILS=0
systemctl start bandwidth-monitor.service 2>/dev/null && \
    step_row "  ${GREEN}●${NC} 带宽采集       ${GREEN}running${NC}" || \
    { step_row "  ${RED}●${NC} 带宽采集       ${RED}failed${NC}"; FAILS=$((FAILS+1)); }
systemctl start bandwidth-analyzer.timer 2>/dev/null && \
    step_row "  ${GREEN}●${NC} 每日分析定时器 ${GREEN}running${NC}" || \
    { step_row "  ${RED}●${NC} 每日分析定时器 ${RED}failed${NC}"; FAILS=$((FAILS+1)); }
systemctl start route-monitor.service 2>/dev/null && \
    step_row "  ${GREEN}●${NC} 路由监测       ${GREEN}running${NC}" || \
    { step_row "  ${RED}●${NC} 路由监测       ${RED}failed${NC}"; FAILS=$((FAILS+1)); }
systemctl start bandwidth-maintenance.timer 2>/dev/null && \
    step_row "  ${GREEN}●${NC} 日志轮转       ${GREEN}running${NC}" || \
    { step_row "  ${RED}●${NC} 日志轮转       ${RED}failed${NC}"; FAILS=$((FAILS+1)); }
step_bottom

# 5. 完成
echo ""
frame_top
frame_title "🎉  部署完成"
frame_sep
frame_row ""
frame_row "  安装目录:  ${GREEN}${INSTALL_DIR}${NC}"
frame_row "  数据目录:  ${GREEN}${LOG_DIR}${NC}"
frame_row "  配置文件:  ${GREEN}${INSTALL_DIR}/settings.json${NC}"
frame_row ""
frame_sep
frame_row "  终端菜单:  ${YELLOW}monitor${NC}"
frame_row "  快速自检:  ${YELLOW}monitor --check${NC}"
frame_row ""
frame_sep
frame_row "  ${DIM}配置可通过菜单 '5. 配置管理' 修改${NC}"
frame_row ""

if [ "$FAILS" -gt 0 ]; then
    frame_sep
    frame_row "  ${RED}⚠  有 ${FAILS} 个服务启动失败: journalctl -xe${NC}"
    frame_row ""
fi

frame_bottom
echo ""
