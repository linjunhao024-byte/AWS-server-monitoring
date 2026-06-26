#!/usr/bin/env bash
# =============================================================================
#  AWS Lightsail 服务器监控系统 v3.0 — 安装向导
#
#  用法: sudo bash install.sh
#
#  功能:
#    1. 检测系统环境和依赖
#    2. 交互式配置（钉钉、邮件、LLM 等）
#    3. 安装文件到 /opt/bandwidth_monitor
#    4. 配置 systemd 服务
#    5. 启动服务
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# 颜色常量
# ---------------------------------------------------------------------------
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# ---------------------------------------------------------------------------
# 配置变量（将被用户输入覆盖）
# ---------------------------------------------------------------------------
CFG_SERVER_ALIAS=""
CFG_INTERFACE=""
CFG_DATA_DIR="/var/log/bandwidth"
CFG_INSTALL_DIR="/opt/bandwidth_monitor"
CFG_DINGTALK_WEBHOOK=""
CFG_DINGTALK_SECRET=""
CFG_XFYUN_API_KEY=""
CFG_XFYUN_ENABLED="false"
CFG_EMAIL_ENABLED="false"
CFG_SMTP_USERNAME=""
CFG_SMTP_PASSWORD=""
CFG_EMAIL_RECIPIENTS=""
CFG_ROUTE_TARGET="114.114.114.114"
CFG_ROUTE_INTERVAL=300

# 脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ---------------------------------------------------------------------------
# UI 工具
# ---------------------------------------------------------------------------

W=73  # 内容区宽度

frame_top() {
    echo -e "${CYAN}╔$(printf '═%.0s' $(seq 1 $W))╗${NC}"
}

frame_title() {
    local text="$1"
    printf "${CYAN}║${NC}${BOLD}  %- $((${W}-2))s${NC}${CYAN}║${NC}\n" "$text"
}

frame_sep() {
    echo -e "${CYAN}╠$(printf '═%.0s' $(seq 1 $W))╣${NC}"
}

frame_bottom() {
    echo -e "${CYAN}╚$(printf '═%.0s' $(seq 1 $W))╝${NC}"
}

frame_row() {
    local text="$1"
    printf "${CYAN}║${NC}  %- $((${W}-2))s${CYAN}║${NC}\n" "$text"
}

step_top() {
    echo -e "${CYAN}┌$(printf '─%.0s' $(seq 1 $W))┐${NC}"
}

step_title() {
    local text="$1"
    printf "${CYAN}│${NC}${BOLD}  %s${NC}%*s${CYAN}│${NC}\n" "$text" $((${W} - 2 - ${#text})) ""
}

step_sep() {
    echo -e "${CYAN}├$(printf '─%.0s' $(seq 1 $W))┤${NC}"
}

step_bottom() {
    echo -e "${CYAN}└$(printf '─%.0s' $(seq 1 $W))┘${NC}"
}

step_row() {
    local text="$1"
    printf "${CYAN}│${NC}  %- $((${W}-2))s${CYAN}│${NC}\n" "$text"
}

ok_msg() {
    echo -e "  ${GREEN}✓${NC} $1"
}

warn_msg() {
    echo -e "  ${YELLOW}⚠${NC} $1"
}

err_msg() {
    echo -e "  ${RED}✗${NC} $1"
}

info_msg() {
    echo -e "  ${DIM}$1${NC}"
}

ask_input() {
    local prompt="$1"
    local default="${2:-}"
    local result=""
    if [ -n "$default" ]; then
        printf "  ${YELLOW}%s${NC} [${GREEN}%s${NC}]: " "$prompt" "$default"
    else
        printf "  ${YELLOW}%s${NC}: " "$prompt"
    fi
    read -r result
    echo "${result:-$default}"
}

ask_yes_no() {
    local prompt="$1"
    local default="${2:-n}"
    local result=""
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
# 系统检测
# ---------------------------------------------------------------------------

check_root() {
    if [ "$EUID" -ne 0 ]; then
        err_msg "请使用 sudo 运行此脚本"
        echo "  用法: sudo bash install.sh"
        exit 1
    fi
}

detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS_ID="$ID"
        OS_VERSION="$VERSION_ID"
    else
        OS_ID="unknown"
        OS_VERSION="unknown"
    fi
    echo -e "  ${DIM}操作系统: ${OS_ID} ${OS_VERSION}${NC}"
}

detect_interface() {
    # 获取第一个物理/虚拟网卡，排除回环、VPN、容器、WARP 等虚拟接口
    CFG_INTERFACE=$(ip link show 2>/dev/null \
        | grep -E "^[0-9]+" \
        | awk -F: '{print $2}' \
        | tr -d ' ' \
        | grep -vE '^(lo|docker[0-9]*|br-[a-f0-9]+|veth[a-f0-9]+|virbr[0-9]*|vmnet[0-9]*|tun[0-9]*|tap[0-9]*|ppp[0-9]*|wg[0-9]*|CloudflareWARP|warp[0-9]*|zt[a-z0-9]+|tailscale[0-9]*|utun[0-9]*)$' \
        | head -1)
    if [ -z "$CFG_INTERFACE" ]; then
        CFG_INTERFACE="eth0"
    fi
}

check_python() {
    if command -v python3 &>/dev/null; then
        PYTHON_VERSION=$(python3 --version 2>&1)
        echo -e "  ${GREEN}✓${NC} ${PYTHON_VERSION}"
        return 0
    else
        echo -e "  ${RED}✗${NC} Python3 未安装"
        return 1
    fi
}

check_vnstat() {
    if command -v vnstat &>/dev/null; then
        echo -e "  ${GREEN}✓${NC} vnstat 已安装"
        return 0
    else
        echo -e "  ${YELLOW}⚠${NC} vnstat 未安装（流量报表功能需要）"
        return 1
    fi
}

check_traceroute() {
    if command -v traceroute &>/dev/null; then
        echo -e "  ${GREEN}✓${NC} traceroute 已安装"
        return 0
    else
        echo -e "  ${YELLOW}⚠${NC} traceroute 未安装（路由监测功能需要）"
        return 1
    fi
}

install_missing_deps() {
    local need_install=false

    step_title "安装依赖"
    step_sep

    if ! check_vnstat; then
        need_install=true
    fi
    if ! check_traceroute; then
        need_install=true
    fi

    if $need_install; then
        step_bottom
        if ask_yes_no "是否自动安装缺失的依赖？" "y"; then
            echo ""
            echo -e "  ${DIM}正在更新软件包列表...${NC}"
            apt update -qq 2>/dev/null || true

            if ! command -v vnstat &>/dev/null; then
                echo -e "  ${DIM}安装 vnstat...${NC}"
                apt install -y -qq vnstat 2>/dev/null && ok_msg "vnstat 已安装" || warn_msg "vnstat 安装失败"
                systemctl enable vnstat 2>/dev/null || true
                systemctl start vnstat 2>/dev/null || true
            fi

            if ! command -v traceroute &>/dev/null; then
                echo -e "  ${DIM}安装 traceroute...${NC}"
                apt install -y -qq traceroute 2>/dev/null && ok_msg "traceroute 已安装" || warn_msg "traceroute 安装失败"
            fi
            echo ""
        fi
    else
        step_row ""
        step_row "${GREEN}所有依赖已满足${NC}"
        step_bottom
    fi
}

# ---------------------------------------------------------------------------
# 安装流程
# ---------------------------------------------------------------------------

install_files() {
    step_title "安装文件"
    step_sep

    # 创建目录
    step_row "创建安装目录..."
    mkdir -p "$CFG_INSTALL_DIR"
    mkdir -p "$CFG_DATA_DIR"

    # 复制 Python 文件
    step_row "复制监控模块..."
    local files=(
        config.py utils.py stats.py notifications.py
        data_sources.py analyzer.py reporter.py
        monitor_daemon.py route_daemon.py main.py
    )
    for f in "${files[@]}"; do
        if [ -f "${SCRIPT_DIR}/$f" ]; then
            cp "${SCRIPT_DIR}/$f" "${CFG_INSTALL_DIR}/"
        else
            step_row "${RED}✗ 缺少文件: $f${NC}"
            step_bottom
            return 1
        fi
    done

    # 设置权限
    step_row "设置文件权限..."
    chmod 755 "${CFG_INSTALL_DIR}"/*.py

    step_row "${GREEN}✓ 文件安装完成${NC}"
    step_bottom
}

write_config() {
    step_title "写入配置"
    step_sep

    # 构建收件人 JSON 数组
    local recipients_json="[]"
    if [ -n "$CFG_EMAIL_RECIPIENTS" ]; then
        recipients_json="["
        local first=true
        IFS=',' read -ra RECIPIENTS <<< "$CFG_EMAIL_RECIPIENTS"
        for r in "${RECIPIENTS[@]}"; do
            r=$(echo "$r" | xargs)  # trim
            if $first; then
                first=false
            else
                recipients_json+=", "
            fi
            recipients_json+="\"$r\""
        done
        recipients_json+="]"
    fi

    cat > "${CFG_INSTALL_DIR}/settings.json" << JSONEOF
{
  "SERVER_ALIAS": "${CFG_SERVER_ALIAS}",
  "INTERFACE": "${CFG_INTERFACE}",
  "DATA_DIR": "${CFG_DATA_DIR}",
  "DINGTALK_WEBHOOK": "${CFG_DINGTALK_WEBHOOK}",
  "DINGTALK_SECRET": "${CFG_DINGTALK_SECRET}",
  "XFYUN_API_KEY": "${CFG_XFYUN_API_KEY}",
  "XFYUN_ENABLED": ${CFG_XFYUN_ENABLED},
  "EMAIL_ENABLED": ${CFG_EMAIL_ENABLED},
  "SMTP_USERNAME": "${CFG_SMTP_USERNAME}",
  "SMTP_PASSWORD": "${CFG_SMTP_PASSWORD}",
  "EMAIL_RECIPIENTS": ${recipients_json},
  "ROUTE_TARGET": "${CFG_ROUTE_TARGET}",
  "ROUTE_INTERVAL": ${CFG_ROUTE_INTERVAL}
}
JSONEOF

    chmod 600 "${CFG_INSTALL_DIR}/settings.json"

    step_row "${GREEN}✓ 配置已写入 settings.json${NC}"
    step_bottom
}

install_systemd() {
    step_title "配置 systemd 服务"
    step_sep

    # 复制 service 文件
    local services=(
        "bandwidth-monitor.service"
        "bandwidth-analyzer.service"
        "bandwidth-analyzer.timer"
        "route-monitor.service"
    )

    for svc in "${services[@]}"; do
        if [ -f "${SCRIPT_DIR}/systemd/${svc}" ]; then
            cp "${SCRIPT_DIR}/systemd/${svc}" "/etc/systemd/system/"
            step_row "  ${GREEN}✓${NC} ${svc}"
        else
            step_row "  ${YELLOW}⚠${NC} ${svc} 未找到"
        fi
    done

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

    step_bottom
}

start_services() {
    step_title "启动服务"
    step_sep

    # 带宽采集
    systemctl start bandwidth-monitor.service 2>/dev/null && \
        step_row "  ${GREEN}●${NC} 带宽采集       ${GREEN}running${NC}" || \
        step_row "  ${RED}●${NC} 带宽采集       ${RED}failed${NC}"

    # 每日分析定时器
    systemctl start bandwidth-analyzer.timer 2>/dev/null && \
        step_row "  ${GREEN}●${NC} 每日分析定时器 ${GREEN}running${NC}" || \
        step_row "  ${RED}●${NC} 每日分析定时器 ${RED}failed${NC}"

    # 路由监测
    systemctl start route-monitor.service 2>/dev/null && \
        step_row "  ${GREEN}●${NC} 路由监测       ${GREEN}running${NC}" || \
        step_row "  ${RED}●${NC} 路由监测       ${RED}failed${NC}"

    step_bottom
}

# ---------------------------------------------------------------------------
# 交互式配置
# ---------------------------------------------------------------------------

interactive_config() {
    echo ""
    step_title "交互式配置"
    step_row ""
    step_row "以下配置将写入 settings.json，安装完成后仍可通过"
    step_row "菜单 '5. 配置管理' 随时修改。"
    step_row ""
    step_row "${DIM}直接回车跳过可选项，稍后配置。${NC}"
    step_bottom

    # 1. 服务器别名
    echo ""
    step_title "1/5  基本信息"
    step_sep
    CFG_SERVER_ALIAS=$(ask_input "服务器别名" "${CFG_SERVER_ALIAS:-My-Server}")
    step_bottom

    # 2. 钉钉
    echo ""
    step_title "2/5  钉钉机器人 ${DIM}(可选)${NC}"
    step_sep
    step_row "${DIM}用于推送积分分析报告、流量日报、路由变化告警${NC}"
    step_sep
    CFG_DINGTALK_WEBHOOK=$(ask_input "Webhook URL" "")
    if [ -n "$CFG_DINGTALK_WEBHOOK" ]; then
        CFG_DINGTALK_SECRET=$(ask_input "加签密钥 (SEC开头)" "")
    fi
    step_bottom

    # 3. 讯飞星火 LLM
    echo ""
    step_title "3/5  讯飞星火大模型 ${DIM}(可选)${NC}"
    step_sep
    step_row "${DIM}用于积分分析的 AI 深度解读${NC}"
    step_sep
    CFG_XFYUN_API_KEY=$(ask_input "API Key" "")
    if [ -n "$CFG_XFYUN_API_KEY" ]; then
        CFG_XFYUN_ENABLED="true"
    fi
    step_bottom

    # 4. 邮件
    echo ""
    step_title "4/5  邮件周报 ${DIM}(可选)${NC}"
    step_sep
    step_row "${DIM}每周日发送 HTML 格式的流量周报${NC}"
    step_sep
    if ask_yes_no "是否启用邮件功能？" "n"; then
        CFG_EMAIL_ENABLED="true"
        CFG_SMTP_USERNAME=$(ask_input "SMTP 用户名 (发件人)" "")
        CFG_SMTP_PASSWORD=$(ask_input "SMTP 密码" "")
        CFG_EMAIL_RECIPIENTS=$(ask_input "收件人 (多个用逗号分隔)" "")
    fi
    step_bottom

    # 5. 路由监测
    echo ""
    step_title "5/5  路由监测"
    step_sep
    CFG_ROUTE_TARGET=$(ask_input "监测目标" "${CFG_ROUTE_TARGET}")
    CFG_ROUTE_INTERVAL=$(ask_input "检测间隔(秒)" "${CFG_ROUTE_INTERVAL}")
    step_bottom
}

# ---------------------------------------------------------------------------
# 完成信息
# ---------------------------------------------------------------------------

show_completion() {
    echo ""
    frame_top
    frame_title "🎉  安装完成！"
    frame_sep
    frame_row ""
    frame_row "  安装目录:    ${GREEN}${CFG_INSTALL_DIR}${NC}"
    frame_row "  数据目录:    ${GREEN}${CFG_DATA_DIR}${NC}"
    frame_row "  配置文件:    ${GREEN}${CFG_INSTALL_DIR}/settings.json${NC}"
    frame_row ""
    frame_sep
    frame_title "  快速开始"
    frame_sep
    frame_row ""
    frame_row "  终端菜单:    ${YELLOW}python3 ${CFG_INSTALL_DIR}/main.py${NC}"
    frame_row ""
    frame_row "  服务管理:    ${DIM}systemctl status bandwidth-monitor${NC}"
    frame_row "               ${DIM}systemctl status route-monitor${NC}"
    frame_row "               ${DIM}systemctl status bandwidth-analyzer.timer${NC}"
    frame_row ""
    frame_row "  查看日志:    ${DIM}journalctl -u bandwidth-monitor -f${NC}"
    frame_row "               ${DIM}journalctl -u route-monitor -f${NC}"
    frame_row ""
    frame_row "  数据文件:    ${DIM}${CFG_DATA_DIR}/traffic_log_YYYYMMDD_${CFG_INTERFACE}.csv${NC}"
    frame_row "               ${DIM}${CFG_DATA_DIR}/route_log_YYYYMMDD.txt${NC}"
    frame_row ""
    frame_sep
    frame_row "  ${DIM}配置可通过菜单 '5. 配置管理' 随时修改${NC}"
    frame_row "  ${DIM}或直接编辑 ${CFG_INSTALL_DIR}/settings.json${NC}"
    frame_row ""
    frame_bottom
    echo ""
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

main() {
    clear

    # 欢迎界面
    echo ""
    frame_top
    frame_title "AWS Lightsail 服务器监控系统 v3.0 — 安装向导"
    frame_sep
    frame_row ""
    frame_row "  功能:  1Hz 带宽采集 · 积分机制反推 · 路由监测"
    frame_row "         流量日报 · 邮件周报 · 钉钉推送 · LLM 分析"
    frame_row ""
    frame_bottom

    # 检查 root
    check_root

    # 系统检测
    echo ""
    step_title "系统环境检测"
    step_sep
    detect_os
    detect_interface
    step_sep
    check_python || { err_msg "Python3 是必须的，请先安装"; exit 1; }
    step_bottom

    # 安装缺失依赖
    install_missing_deps

    # 网卡确认
    echo ""
    step_title "网卡确认"
    step_sep
    frame_row ""
    frame_row "  检测到网卡: ${GREEN}${CFG_INTERFACE}${NC}"
    frame_row ""
    step_sep

    # 显示可用网卡（区分物理和虚拟）
    step_row "可用网卡:"
    local virtual_pattern="^(lo|docker[0-9]*|br-[a-f0-9]+|veth[a-f0-9]+|virbr[0-9]*|vmnet[0-9]*|tun[0-9]*|tap[0-9]*|ppp[0-9]*|wg[0-9]*|CloudflareWARP|warp[0-9]*|zt[a-z0-9]+|tailscale[0-9]*|utun[0-9]*)$"
    ip -o link show 2>/dev/null | awk -F': ' '{print $2}' | while read iface; do
        if echo "$iface" | grep -qE "$virtual_pattern"; then
            step_row "  ${DIM}- ${iface} (虚拟/已排除)${NC}"
        else
            step_row "  ${GREEN}- ${iface}${NC}"
        fi
    done
    step_bottom

    CFG_INTERFACE=$(ask_input "要监控的网卡" "$CFG_INTERFACE")

    # 检查网卡是否存在
    if [ ! -f "/sys/class/net/${CFG_INTERFACE}/statistics/rx_bytes" ]; then
        err_msg "网卡 '${CFG_INTERFACE}' 不存在"
        exit 1
    fi

    # 交互式配置
    interactive_config

    # 确认安装
    echo ""
    step_title "安装确认"
    step_sep
    step_row "  服务器:      ${GREEN}${CFG_SERVER_ALIAS}${NC}"
    step_row "  网卡:        ${GREEN}${CFG_INTERFACE}${NC}"
    step_row "  安装目录:    ${CFG_INSTALL_DIR}"
    step_row "  数据目录:    ${CFG_DATA_DIR}"
    step_row ""

    if [ -n "$CFG_DINGTALK_WEBHOOK" ]; then
        step_row "  钉钉:        ${GREEN}已配置${NC}"
    else
        step_row "  钉钉:        ${DIM}未配置${NC}"
    fi

    if [ "$CFG_XFYUN_ENABLED" = "true" ]; then
        step_row "  讯飞星火:    ${GREEN}已启用${NC}"
    else
        step_row "  讯飞星火:    ${DIM}未启用${NC}"
    fi

    if [ "$CFG_EMAIL_ENABLED" = "true" ]; then
        step_row "  邮件周报:    ${GREEN}已启用${NC}"
    else
        step_row "  邮件周报:    ${DIM}未启用${NC}"
    fi

    step_row ""
    step_bottom

    if ! ask_yes_no "确认开始安装？" "y"; then
        echo ""
        info_msg "安装已取消"
        exit 0
    fi

    # 执行安装
    echo ""
    install_files
    write_config
    install_systemd
    start_services

    # 完成
    show_completion
}

main "$@"
