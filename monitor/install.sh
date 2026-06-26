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
        | grep -vE '^(lo|docker|br-|veth|virbr|vmnet|tun|tap|ppp|wg|CloudflareWARP|warp|zt|tailscale|utun)' \
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

            echo -e "  ${DIM}安装 Python requests（AI分析需要）...${NC}"
            pip3 install requests --break-system-packages 2>/dev/null || pip3 install requests 2>/dev/null || warn_msg "requests 安装失败，AI分析功能将不可用"
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

    # 安装全局快捷命令
    step_row "安装快捷命令..."
    # 创建 monitor 主命令
    cat > /usr/local/bin/monitor << 'CMDEOF'
#!/usr/bin/env bash
exec python3 /opt/bandwidth_monitor/main.py "$@"
CMDEOF
    chmod +x /usr/local/bin/monitor
    step_row "  ${GREEN}✓${NC} 主命令: ${YELLOW}monitor${NC}"

    # 自定义快捷键
    if [ -n "${CFG_SHORTCUT:-}" ] && [ "$CFG_SHORTCUT" != "monitor" ]; then
        ln -sf /usr/local/bin/monitor "/usr/local/bin/${CFG_SHORTCUT}"
        step_row "  ${GREEN}✓${NC} 快捷键: ${YELLOW}${CFG_SHORTCUT}${NC} → monitor"
    fi
    fi

    step_row "${GREEN}✓ 文件安装完成${NC}"
    step_bottom
}

json_escape() {
    # 转义字符串中的特殊字符，使其安全嵌入 JSON
    local s="$1"
    s="${s//\\/\\\\}"   # 反斜杠
    s="${s//\"/\\\"}"   # 双引号
    s="${s//$'\n'/\\n}" # 换行
    s="${s//$'\r'/\\r}" # 回车
    s="${s//$'\t'/\\t}" # 制表符
    s="${s//\$/\\$}"    # 美元符号（防 shell 展开）
    s="${s//\`/\\\`}"   # 反引号（防 shell 展开）
    echo "$s"
}

write_config() {
    step_title "写入配置"
    step_sep

    # 构建收件人 JSON 数组（过滤空值）
    local recipients_json="[]"
    if [ -n "$CFG_EMAIL_RECIPIENTS" ]; then
        recipients_json="["
        local first=true
        IFS=',' read -ra RECIPIENTS <<< "$CFG_EMAIL_RECIPIENTS"
        for r in "${RECIPIENTS[@]}"; do
            r=$(echo "$r" | xargs)  # trim
            [ -z "$r" ] && continue  # 跳过空值
            if $first; then
                first=false
            else
                recipients_json+=", "
            fi
            recipients_json+="\"$(json_escape "$r")\""
        done
        recipients_json+="]"
    fi

    # 转义所有用户输入
    local _alias _wh _secret _xfyun _smtp_user _smtp_pass _route_target _sender
    _alias=$(json_escape "$CFG_SERVER_ALIAS")
    _wh=$(json_escape "${CFG_DINGTALK_WEBHOOK:-}")
    _secret=$(json_escape "${CFG_DINGTALK_SECRET:-}")
    _xfyun=$(json_escape "${CFG_XFYUN_API_KEY:-}")
    _smtp_user=$(json_escape "${CFG_SMTP_USERNAME:-}")
    _smtp_pass=$(json_escape "${CFG_SMTP_PASSWORD:-}")
    _route_target=$(json_escape "$CFG_ROUTE_TARGET")
    _tg_token=$(json_escape "${CFG_TG_BOT_TOKEN:-}")
    _tg_chatid=$(json_escape "${CFG_TG_CHAT_ID:-}")
    _sender=$(json_escape "${CFG_SENDER_NAME:-服务器监控}")

    cat > "${CFG_INSTALL_DIR}/settings.json" << JSONEOF
{
  "SERVER_ALIAS": "${_alias}",
  "INTERFACE": "${CFG_INTERFACE}",
  "DATA_DIR": "${CFG_DATA_DIR}",
  "DINGTALK_WEBHOOK": "${_wh}",
  "DINGTALK_SECRET": "${_secret}",
  "TG_BOT_TOKEN": "${_tg_token}",
  "TG_CHAT_ID": "${_tg_chatid}",
  "XFYUN_API_KEY": "${_xfyun}",
  "XFYUN_MODEL": "${CFG_XFYUN_MODEL:-4.0Ultra}",
  "XFYUN_ENABLED": ${CFG_XFYUN_ENABLED:-false},
  "EMAIL_ENABLED": ${CFG_EMAIL_ENABLED:-false},
  "SMTP_SERVER": "${CFG_SMTP_SERVER:-smtp.exmail.qq.com}",
  "SMTP_PORT": ${CFG_SMTP_PORT:-465},
  "SMTP_USE_SSL": ${CFG_SMTP_USE_SSL:-true},
  "SMTP_USERNAME": "${_smtp_user}",
  "SMTP_PASSWORD": "${_smtp_pass}",
  "EMAIL_RECIPIENTS": ${recipients_json},
  "SENDER_NAME": "${_sender}",
  "WEEKLY_REPORT_DAY": ${CFG_WEEKLY_REPORT_DAY:-6},
  "ROUTE_TARGET": "${_route_target}",
  "ROUTE_INTERVAL": ${CFG_ROUTE_INTERVAL:-300},
  "ROUTE_ALERT_ENABLED": ${CFG_ROUTE_ALERT_ENABLED:-true},
  "LOG_RETENTION_DAYS": ${CFG_LOG_RETENTION_DAYS:-30},
  "DISK_ALERT_MB": ${CFG_DISK_ALERT_MB:-1024},
  "DAILY_REPORT_TIME": "${CFG_DAILY_REPORT_TIME:-23:00}"
}
JSONEOF

    if [ $? -ne 0 ]; then
        step_row "${RED}✗ 配置写入失败${NC}"
        step_bottom
        return 1
    fi

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
        "bandwidth-daily-report.service"
        "bandwidth-daily-report.timer"
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
    systemctl enable bandwidth-daily-report.timer 2>/dev/null && \
        step_row "  ${GREEN}✓${NC} 每日报告 (23:00)" || \
        step_row "  ${YELLOW}⚠${NC} 每日报告启用失败"

    # 日志轮转 + 磁盘告警定时器（每天 04:00 执行）
    step_sep
    step_row "配置日志轮转定时器..."
    cat > /etc/systemd/system/bandwidth-maintenance.service << MSVCEOF
[Unit]
Description=Daily log rotation and disk alert
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 ${CFG_INSTALL_DIR}/notifications.py --maintenance
StandardOutput=journal
StandardError=journal
MSVCEOF

    cat > /etc/systemd/system/bandwidth-maintenance.timer << MTMREOF
[Unit]
Description=Run maintenance daily at 04:00

[Timer]
OnCalendar=*-*-* 04:00:00
Persistent=true

[Install]
WantedBy=timers.target
MTMREOF

    systemctl daemon-reload
    systemctl enable bandwidth-maintenance.timer 2>/dev/null && \
        step_row "  ${GREEN}✓${NC} 日志轮转定时器 (每天 04:00)" || \
        step_row "  ${YELLOW}⚠${NC} 日志轮转定时器启用失败"

    step_bottom
}

SERVICE_FAILURES=0

start_services() {
    step_title "启动服务"
    step_sep

    # 带宽采集
    systemctl start bandwidth-monitor.service 2>/dev/null && \
        step_row "  ${GREEN}●${NC} 带宽采集       ${GREEN}running${NC}" || \
        { step_row "  ${RED}●${NC} 带宽采集       ${RED}failed${NC}"; SERVICE_FAILURES=$((SERVICE_FAILURES+1)); }

    # 每日分析定时器
    systemctl start bandwidth-analyzer.timer 2>/dev/null && \
        step_row "  ${GREEN}●${NC} 每日分析定时器 ${GREEN}running${NC}" || \
        { step_row "  ${RED}●${NC} 每日分析定时器 ${RED}failed${NC}"; SERVICE_FAILURES=$((SERVICE_FAILURES+1)); }

    # 路由监测
    systemctl start route-monitor.service 2>/dev/null && \
        step_row "  ${GREEN}●${NC} 路由监测       ${GREEN}running${NC}" || \
        { step_row "  ${RED}●${NC} 路由监测       ${RED}failed${NC}"; SERVICE_FAILURES=$((SERVICE_FAILURES+1)); }

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
    step_row "菜单 '配置管理' 随时修改。"
    step_row ""
    step_row "${DIM}直接回车跳过可选项，稍后配置。${NC}"
    step_bottom

    # ── 1. 基本信息 ──
    echo ""
    step_title "1/6  基本信息"
    step_sep
    CFG_SERVER_ALIAS=$(ask_input "服务器别名" "${CFG_SERVER_ALIAS:-My-Server}")
    step_bottom

    # ── 2. 推送配置（层级递进） ──
    echo ""
    step_title "2/6  推送配置"
    step_sep
    step_row "${DIM}告警通知、日报、路由变化告警等${NC}"
    step_sep
    if ask_yes_no "是否启用推送通知？" "y"; then
        step_sep
        step_row "  选择推送方式（可多选，输入编号）:"
        step_row ""
        step_row "    ${YELLOW}1${NC}  钉钉机器人"
        step_row "    ${YELLOW}2${NC}  Telegram 机器人"
        step_row "    ${YELLOW}3${NC}  邮件"
        step_row ""
        local push_choices
        push_choices=$(ask_input "推送方式 (如 1,2 或 1,3)" "1")
        step_sep

        # 钉钉
        if echo "$push_choices" | grep -q "1"; then
            step_row ""
            step_row "  ${BOLD}── 钉钉配置 ──${NC}"
            step_row "  ${DIM}从钉钉群机器人设置中获取${NC}"
            CFG_DINGTALK_WEBHOOK=$(ask_input "  Webhook URL" "")
            if [ -n "$CFG_DINGTALK_WEBHOOK" ]; then
                CFG_DINGTALK_SECRET=$(ask_input "  加签密钥 (SEC开头)" "")
            fi
        fi

        # Telegram
        if echo "$push_choices" | grep -q "2"; then
            step_row ""
            step_row "  ${BOLD}── Telegram 配置 ──${NC}"
            step_row "  ${DIM}Token 从 @BotFather 获取，Chat ID 从 @userinfobot 获取${NC}"
            CFG_TG_BOT_TOKEN=$(ask_input "  Bot Token" "")
            if [ -n "$CFG_TG_BOT_TOKEN" ]; then
                CFG_TG_CHAT_ID=$(ask_input "  Chat ID" "")
            fi
        fi

        # 邮件
        if echo "$push_choices" | grep -q "3"; then
            step_row ""
            step_row "  ${BOLD}── 邮件配置 ──${NC}"
            step_row "  ${DIM}用于发送 HTML 格式的流量周报${NC}"
            CFG_EMAIL_ENABLED="true"
            CFG_SMTP_SERVER=$(ask_input "  SMTP 服务器" "smtp.exmail.qq.com")
            CFG_SMTP_PORT=$(ask_input "  SMTP 端口" "465")
            if ask_yes_no "  使用 SSL？" "y"; then
                CFG_SMTP_USE_SSL="true"
            else
                CFG_SMTP_USE_SSL="false"
            fi
            CFG_SMTP_USERNAME=$(ask_input "  SMTP 用户名 (发件人)" "")
            CFG_SMTP_PASSWORD=$(ask_input "  SMTP 密码" "")
            CFG_EMAIL_RECIPIENTS=$(ask_input "  收件人 (多个用逗号分隔)" "")
            CFG_SENDER_NAME=$(ask_input "  发件人名称" "服务器监控")
            step_row ""
            step_row "  周报发送日:"
            step_row "    ${YELLOW}0${NC} 周一  ${YELLOW}1${NC} 周二  ${YELLOW}2${NC} 周三  ${YELLOW}3${NC} 周四"
            step_row "    ${YELLOW}4${NC} 周五  ${YELLOW}5${NC} 周六  ${YELLOW}6${NC} 周日"
            local week_day
            week_day=$(ask_input "  选择 [0-6]" "6")
            if [[ "$week_day" =~ ^[0-6]$ ]]; then
                CFG_WEEKLY_REPORT_DAY="$week_day"
            fi
        fi
    fi
    step_bottom

    # ── 3. AI 分析 ──
    echo ""
    step_title "3/6  AI 深度分析 ${DIM}(可选)${NC}"
    step_sep
    step_row "${DIM}使用讯飞星火大模型对带宽数据进行智能分析${NC}"
    step_sep
    if ask_yes_no "是否启用 AI 分析？" "n"; then
        CFG_XFYUN_API_KEY=$(ask_input "  API Key" "")
        if [ -n "$CFG_XFYUN_API_KEY" ]; then
            CFG_XFYUN_ENABLED="true"
            CFG_XFYUN_MODEL=$(ask_input "  模型名称" "4.0Ultra")
        fi
    fi
    step_bottom

    # ── 4. 监控参数 ──
    echo ""
    step_title "4/6  监控参数"
    step_sep
    CFG_ROUTE_TARGET=$(ask_input "路由监测目标" "${CFG_ROUTE_TARGET}")
    while true; do
        CFG_ROUTE_INTERVAL=$(ask_input "路由检测间隔(秒)" "${CFG_ROUTE_INTERVAL}")
        if [[ "$CFG_ROUTE_INTERVAL" =~ ^[0-9]+$ ]] && [ "$CFG_ROUTE_INTERVAL" -ge 10 ]; then
            break
        fi
        echo -e "  ${RED}请输入 ≥10 的整数${NC}"
    done
    if ask_yes_no "是否开启路由变化告警？" "y"; then
        CFG_ROUTE_ALERT_ENABLED="true"
    else
        CFG_ROUTE_ALERT_ENABLED="false"
    fi
    step_bottom

    # ── 5. 运维参数 ──
    echo ""
    step_title "5/6  运维参数"
    step_sep
    CFG_DAILY_REPORT_TIME=$(ask_input "每日推送时间 (HH:MM)" "23:00")
    CFG_LOG_RETENTION_DAYS=$(ask_input "日志保留天数" "30")
    CFG_DISK_ALERT_MB=$(ask_input "磁盘告警阈值 (MB)" "1024")
    step_bottom

    # ── 6. 快捷命令 ──
    echo ""
    step_title "6/6  快捷命令"
    step_sep
    step_row "${DIM}默认命令: monitor${NC}"
    step_row "${DIM}可自定义更短的快捷键（如 m、mo、mt）${NC}"
    step_sep
    CFG_SHORTCUT=$(ask_input "快捷命令名称" "monitor")
    if [[ ! "$CFG_SHORTCUT" =~ ^[a-zA-Z0-9_-]+$ ]]; then
        CFG_SHORTCUT="monitor"
        echo -e "  ${YELLOW}无效名称，使用默认: monitor${NC}"
    fi
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
    frame_row "  终端菜单:    ${YELLOW}monitor${NC} 或 ${DIM}python3 ${CFG_INSTALL_DIR}/main.py${NC}"
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
    if [ "$SERVICE_FAILURES" -gt 0 ]; then
        frame_sep
        frame_row "  ${RED}⚠  有 ${SERVICE_FAILURES} 个服务启动失败，请检查日志:${NC}"
        frame_row "  ${DIM}journalctl -xe${NC}"
        frame_row ""
    fi
    frame_bottom
    echo ""
}

# ---------------------------------------------------------------------------
# 钉钉通知
# ---------------------------------------------------------------------------

send_dingtalk_test() {
    echo ""
    step_title "钉钉通知"
    step_sep
    step_row "  发送测试消息..."

    # 使用 Python 发送测试消息（复用 notifications.py 的逻辑）
    python3 "${CFG_INSTALL_DIR}/notifications.py" --test 2>/dev/null
    if [ $? -eq 0 ]; then
        step_row "  ${GREEN}✓ 测试消息发送成功${NC}"
    else
        step_row "  ${YELLOW}⚠ 测试消息发送失败（不影响安装）${NC}"
    fi

    # 创建 10 分钟延迟数据消息的 systemd 一次性服务
    step_row "  调度 10 分钟后数据验证消息..."
    cat > /etc/systemd/system/bandwidth-data-check.service << SVCEOF
[Unit]
Description=Bandwidth Monitor Data Check (one-shot)
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 ${CFG_INSTALL_DIR}/notifications.py --data-check
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

    cat > /etc/systemd/system/bandwidth-data-check.timer << TMREOF
[Unit]
Description=Run data check 10 minutes after install

[Timer]
OnActiveSec=10min
Persistent=false

[Install]
WantedBy=timers.target
TMREOF

    systemctl daemon-reload
    systemctl enable bandwidth-data-check.timer 2>/dev/null
    systemctl start bandwidth-data-check.timer 2>/dev/null
    step_row "  ${GREEN}✓ 10 分钟后将发送数据验证消息${NC}"
    step_bottom
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
    local virtual_pattern="^(lo|docker|br-|veth|virbr|vmnet|tun|tap|ppp|wg|CloudflareWARP|warp|zt|tailscale|utun)"
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
    step_sep
    step_row "  钉钉:        $(if [ -n "$CFG_DINGTALK_WEBHOOK" ]; then echo "${GREEN}已配置${NC}"; else echo "${DIM}未配置${NC}"; fi)"
    step_row "  Telegram:    $(if [ -n "${CFG_TG_BOT_TOKEN:-}" ]; then echo "${GREEN}已配置${NC}"; else echo "${DIM}未配置${NC}"; fi)"
    step_row "  讯飞星火:    $(if [ "$CFG_XFYUN_ENABLED" = "true" ]; then echo "${GREEN}已启用${NC}"; else echo "${DIM}未启用${NC}"; fi)"
    step_row "  邮件:        $(if [ "$CFG_EMAIL_ENABLED" = "true" ]; then echo "${GREEN}已启用${NC}"; else echo "${DIM}未启用${NC}"; fi)"
    step_sep
    step_row "  路由目标:    ${GREEN}${CFG_ROUTE_TARGET}${NC}"
    step_row "  路由间隔:    ${CFG_ROUTE_INTERVAL}秒"
    step_row "  快捷命令:    ${YELLOW}monitor${NC}"
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

    # 钉钉通知：测试消息 + 10 分钟后数据消息
    if [ -n "$CFG_DINGTALK_WEBHOOK" ] && [ -n "$CFG_DINGTALK_SECRET" ]; then
        send_dingtalk_test
    fi

    # 完成
    show_completion
}

main "$@"
