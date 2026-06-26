#!/usr/bin/env bash
# =============================================================================
#  AWS Lightsail 服务器监控系统 v3.0 — 卸载脚本
#
#  用法: sudo bash uninstall.sh [--purge]
#
#  --purge  同时删除数据目录和日志（默认保留）
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# 颜色
# ---------------------------------------------------------------------------
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

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

ok_msg()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn_msg() { echo -e "  ${YELLOW}⚠${NC} $1"; }
err_msg()  { echo -e "  ${RED}✗${NC} $1"; }

ask_yes_no() {
    local prompt="$1"
    local default="${2:-n}"
    if [ "$default" = "y" ]; then
        printf "  ${YELLOW}%s${NC} (Y/n): " "$prompt"
    else
        printf "  ${YELLOW}%s${NC} (y/N): " "$prompt"
    fi
    read -r result
    result="${result:-$default}"
    [[ "$result" =~ ^[Yy]$ ]]
}

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
INSTALL_DIR="/opt/bandwidth_monitor"
DATA_DIR="/var/log/bandwidth"
CRON_TAG="traffic_monitor_v2"

SERVICES=(
    "bandwidth-monitor.service"
    "bandwidth-analyzer.service"
    "bandwidth-analyzer.timer"
    "route-monitor.service"
    "bandwidth-data-check.service"
    "bandwidth-data-check.timer"
    "bandwidth-maintenance.service"
    "bandwidth-maintenance.timer"
)

PURGE=false
if [[ "${1:-}" == "--purge" ]]; then
    PURGE=true
fi

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
clear
echo ""
frame_top
frame_title "AWS Lightsail 服务器监控系统 v3.0 — 卸载向导"
frame_sep
frame_row ""
frame_row "  此脚本将："
frame_row "    1. 停止并禁用所有监控服务"
frame_row "    2. 删除 systemd 服务文件"
frame_row "    3. 清理旧版 cron 任务"
frame_row "    4. 删除安装目录"
if $PURGE; then
    frame_row "    5. ${RED}删除数据目录和日志 (--purge)${NC}"
else
    frame_row "    5. 保留数据目录和日志"
fi
frame_row ""
frame_bottom

# Root 检查
if [ "$EUID" -ne 0 ]; then
    err_msg "请使用 sudo 运行此脚本"
    exit 1
fi

if ! ask_yes_no "确认卸载？" "n"; then
    echo ""
    echo -e "  ${DIM}已取消${NC}"
    exit 0
fi

# 1. 停止服务
echo ""
step_title "停止服务"
step_sep
for svc in "${SERVICES[@]}"; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        systemctl stop "$svc" 2>/dev/null
        step_row "  ${GREEN}●${NC} 已停止: $svc"
    else
        step_row "  ${DIM}○${NC} 未运行: $svc"
    fi
done
step_bottom

# 2. 禁用服务
echo ""
step_title "禁用开机自启"
step_sep
for svc in "${SERVICES[@]}"; do
    if systemctl is-enabled --quiet "$svc" 2>/dev/null; then
        systemctl disable "$svc" 2>/dev/null
        step_row "  ${GREEN}✓${NC} 已禁用: $svc"
    else
        step_row "  ${DIM}○${NC} 未启用: $svc"
    fi
done
step_bottom

# 3. 删除 service 文件
echo ""
step_title "删除 systemd 服务文件"
step_sep
for svc in "${SERVICES[@]}"; do
    local_path="/etc/systemd/system/$svc"
    if [ -f "$local_path" ]; then
        rm -f "$local_path"
        step_row "  ${GREEN}✓${NC} 已删除: $local_path"
    else
        step_row "  ${DIM}○${NC} 不存在: $local_path"
    fi
done
step_sep
step_row "重载 systemd..."
systemctl daemon-reload
step_row "${GREEN}✓ 完成${NC}"
step_bottom

# 4. 清理旧版 cron
echo ""
step_title "清理旧版 cron 任务"
step_sep
if crontab -l 2>/dev/null | grep -q "$CRON_TAG"; then
    crontab -l 2>/dev/null | grep -v "$CRON_TAG" | crontab -
    step_row "${GREEN}✓ 已清理旧版 traffic_monitor_v2 定时任务${NC}"
else
    step_row "${DIM}未发现旧版 cron 任务${NC}"
step_bottom

# 5. 删除安装目录和快捷命令
echo ""
step_title "删除安装目录"
step_sep
if [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR"
    step_row "${GREEN}✓ 已删除: ${INSTALL_DIR}${NC}"
else
    step_row "${DIM}目录不存在: ${INSTALL_DIR}${NC}"
fi
if [ -f "/usr/local/bin/monitor" ]; then
    rm -f /usr/local/bin/monitor
    step_row "${GREEN}✓ 已删除: /usr/local/bin/monitor${NC}"
fi
step_bottom

# 6. 数据目录
if $PURGE; then
    echo ""
    step_title "删除数据目录 (--purge)"
    step_sep
    if [ -d "$DATA_DIR" ]; then
        rm -rf "$DATA_DIR"
        step_row "${GREEN}✓ 已删除: ${DATA_DIR}${NC}"
    else
        step_row "${DIM}目录不存在: ${DATA_DIR}${NC}"
    fi
    step_bottom
else
    echo ""
    step_title "数据目录"
    step_sep
    if [ -d "$DATA_DIR" ]; then
        step_row "${YELLOW}保留: ${DATA_DIR}${NC}"
        step_row "${DIM}使用 --purge 参数可一并删除${NC}"
    else
        step_row "${DIM}目录不存在: ${DATA_DIR}${NC}"
    fi
    step_bottom
fi

# 完成
echo ""
frame_top
frame_title "🎉  卸载完成"
frame_sep
frame_row ""
frame_row "  所有服务已停止并删除"
if ! $PURGE; then
    frame_row "  数据目录已保留: ${GREEN}${DATA_DIR}${NC}"
    frame_row ""
    frame_row "  ${DIM}如需删除数据: sudo bash uninstall.sh --purge${NC}"
fi
frame_row ""
frame_bottom
echo ""
