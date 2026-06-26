<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:0f0f0f,50:fce300,100:00f0ff&height=220&section=header&text=AWS-Server-Monitoring&fontSize=50&fontColor=fce300&fontAlignY=35&desc=Advanced%20Cloud%20State%20Guardian&descSize=15&descColor=00f0ff&descAlignY=55&animation=twinkling" width="100%"/>

<br/>

<a href="https://github.com/linjunhao024-byte/AWS-server-monitoring/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-fce300?style=flat-square&labelColor=0f0f0f" alt="License MIT"/></a>
<a href="https://www.python.org/"><img src="https://img.shields.io/badge/Language-Python%203.10+-3776AB?style=flat-square&logo=python&logoColor=fce300&labelColor=0f0f0f" alt="Python 3.10+"/></a>
<a href="https://aws.amazon.com/lightsail/"><img src="https://img.shields.io/badge/Platform-AWS%20Lightsail-FF9900?style=flat-square&logo=amazon-aws&logoColor=fff&labelColor=0f0f0f" alt="AWS Lightsail"/></a>
<a href="https://www.linux.org/"><img src="https://img.shields.io/badge/Deploy-Linux%20%7C%20systemd-FCC624?style=flat-square&logo=linux&logoColor=fff&labelColor=0f0f0f" alt="Linux"/></a>
<a href="https://github.com/linjunhao024-byte/AWS-server-monitoring"><img src="https://img.shields.io/badge/Version-v3.0-00f0ff?style=flat-square&labelColor=0f0f0f" alt="Version 3.0"/></a>

<br/>

**⚡ 高性能 AWS 云服务器态势感知与路由状态守护中枢 ⚡**

*1Hz 带宽采样 · 积分机制逆向 · 路由拓扑感知 · 智能告警 · LLM 深度分析*

</div>

---

## 📐 系统架构 | System Architecture

```
                        ┌─────────────────────────────────────────────┐
                        │           main.py  (Terminal TUI)           │
                        │   ┌─────┬─────┬─────┬─────┬─────┬─────┐   │
                        │   │状态 │分析 │报表 │路由 │配置 │服务 │   │
                        │   └──┬──┴──┬──┴──┬──┴──┬──┴──┬──┴──┬──┘   │
                        └──────┼─────┼─────┼─────┼─────┼─────┼──────┘
                               │     │     │     │     │     │
              ┌────────────────┼─────┼─────┼─────┼─────┼─────┼────────────────┐
              │                ▼     ▼     ▼     ▼     ▼     ▼                │
              │  ┌──────────────────────────────────────────────────────────┐  │
              │  │              notifications.py  (告警中枢)                │  │
              │  │      DingTalk HMAC-SHA256 │ SMTP │ iFlytek Spark        │  │
              │  └──────────────────────────────────────────────────────────┘  │
              │  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐   │
              │  │ analyzer.py  │  │ reporter.py   │  │  data_sources.py  │   │
              │  │ 积分逆向引擎 │  │ 流量战报生成  │  │  底层数据采集层   │   │
              │  └──────┬───────┘  └──────┬────────┘  └────────┬──────────┘   │
              │         │                 │                     │              │
              │         ▼                 ▼                     ▼              │
              │  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐   │
              │  │  stats.py    │  │  utils.py     │  │   config.py       │   │
              │  │  统计内核    │  │  工具函数集   │  │   声明式配置中心  │   │
              │  └──────────────┘  └──────────────┘  └───────────────────┘   │
              │                           │                     │              │
              │  ┌────────────────────────┼─────────────────────┼──────────┐  │
              │  │        Python Standard Library (零依赖核心)             │  │
              │  │   sysfs · /proc · ping · traceroute · vnstat · SMTP    │  │
              │  └────────────────────────────────────────────────────────┘  │
              └──────────────────────────────────────────────────────────────┘
                    ▲                                      ▲
                    │                                      │
        ┌───────────┴───────────┐              ┌───────────┴───────────┐
        │  monitor_daemon.py    │              │  route_daemon.py      │
        │  1Hz 带宽采集守护进程 │              │  路由拓扑监测守护进程 │
        │  systemd managed      │              │  systemd managed      │
        └───────────────────────┘              └───────────────────────┘
```

---

## ✨ 核心特性 | Core Features

### 🔬 1Hz 高频带宽采样引擎

纯 Python 实现的 `/sys/class/net/{iface}/statistics/` 直读式采样，严格 1Hz 无降噪。ICMP 探测下沉至独立后台线程，通过 `threading.Lock` 保护的缓存字典实现零阻塞数据交换，主循环永远不等待网络 I/O。

```
╔═══════════════════════════════════════════════════════════════════════════╗
║  monitor_daemon.py  ·  1Hz Bandwidth Sampler                             ║
╠═══════════════════════════════════════════════════════════════════════════╣
║                                                                           ║
║  主循环 (1Hz)                   后台线程 (5s)                             ║
║  ┌─────────────────────┐       ┌─────────────────────┐                   ║
║  │ time.sleep(1.0)     │       │ ping(169.254.169.254)│                   ║
║  │ read /sys/net/...   │       │ ping(114.114.114.114)│                   ║
║  │ read /proc/loadavg  │◄──────┤ write _ping_cache    │                   ║
║  │ write CSV row       │ Lock  └─────────────────────┘                   ║
║  └─────────────────────┘                                                 ║
║                                                                           ║
║  输出: timestamp, rx_mbps, tx_mbps, cpu_load_1m,                         ║
║        rtt_gw_ms, rtt_ext_ms, loss_gw_pct, loss_ext_pct                 ║
║                                                                           ║
╚═══════════════════════════════════════════════════════════════════════════╝
```

### 🧠 带宽积分机制逆向算法

五步级联分析引擎，从原始 1Hz 采样数据中反推云厂商未公开的带宽积分机制：

| 阶段 | 算法 | 输出 |
|:---:|------|------|
| **A** | P25 以下均值 → 可持续基线 | `sustainable_rx / sustainable_tx` |
| **B** | P95 百分位 → 可突上限 | `burst_ceiling_rx / burst_ceiling_tx` |
| **C** | 连续 ≥60s 低于 `baseline×1.2` → 钳位检测 | `throttle_floor` |
| **D** | 连续 ≥5s 高于 P50 → 突发检测 | `burst_events[]` |
| **E** | 突发/钳位交叉分析 → 积分周期推断 | `credit_inference` |

> 事件区间检索采用 `bisect` 二分查找，O(log n) 定位，拒绝 O(n²) 暴力遍历。

### 📡 路由拓扑感知

基于 `traceroute` 的路径指纹比对引擎。每次检测生成路径快照，与上一帧逐跳比对，精确定位拓扑变化发生在第几跳。

```
╔═══════════════════════════════════════════════════════════════════════════╗
║  [2026-06-26 14:32:01] target=114.114.114.114                           ║
║  ✅ 路径无变化 (累计变化 3 次)                                           ║
║  路径: 1 10.0.0.1 → 2 172.16.0.1 → 3 * → 4 202.97.12.1 → 5 114.114.114 ║
╠═══════════════════════════════════════════════════════════════════════════╣
║  [2026-06-26 14:37:01] target=114.114.114.114                           ║
║  ⚠️  路由变化! 变化位置: hop 4                                           ║
║  新增节点: 4 202.97.56.1                                                 ║
║  消失节点: 4 202.97.12.1                                                 ║
║  跳数: 5 → 6                                                            ║
╚═══════════════════════════════════════════════════════════════════════════╝
```

### 🤖 LLM 深度分析

集成讯飞星火 4.0 Ultra 大模型，流式 SSE 响应。自动构建包含完整统计分布、逐小时带宽、延迟-带宽关联、突发/钳位事件时间线的结构化 Prompt，输出带宽积分机制的专家级研判。

### 📊 多维告警与战报

- **钉钉推送**：HMAC-SHA256 签名，`urllib` 零依赖实现
- **邮件周报**：HTML 渲染 + matplotlib 趋势图，优雅降级至 ASCII 图表
- **流量日报**：日同比 / 周同比 / 进度条 / 数据完整性校验
- **路由告警**：拓扑变化毫秒级钉钉推送

---

## 📦 项目结构 | Project Structure

```
monitor/
├── config.py              # 声明式配置中心，持久化至 settings.json
├── utils.py               # 纯函数工具集（时间、格式化、信号处理）
├── stats.py               # 统计内核（百分位、基础统计、分桶分布）
├── data_sources.py        # 底层数据采集层（sysfs / proc / ping / traceroute / vnstat）
├── notifications.py       # 统一告警中枢（钉钉 / 邮件 / 讯飞星火）
├── analyzer.py            # 带宽积分逆向引擎 + 报告生成 + LLM Prompt 构建
├── reporter.py            # 流量战报生成器（日报 / 周报 / 趋势图）
├── monitor_daemon.py      # 1Hz 带宽采集守护进程（systemd managed）
├── route_daemon.py        # 路由拓扑监测守护进程（systemd managed）
├── main.py                # 终端 TUI 菜单入口
├── install.sh             # 交互式一键部署向导
└── systemd/
    ├── bandwidth-monitor.service
    ├── bandwidth-analyzer.service
    ├── bandwidth-analyzer.timer
    └── route-monitor.service
```

---

## 🚀 快速部署 | Quick Start

### 一键安装

```bash
git clone https://github.com/linjunhao024-byte/AWS-server-monitoring.git
cd AWS-server-monitoring/monitor
sudo bash install.sh
```

安装向导将引导你完成：

```
╔═══════════════════════════════════════════════════════════════════════════╗
║  AWS Lightsail 服务器监控系统 v3.0 — 安装向导                             ║
╠═══════════════════════════════════════════════════════════════════════════╣
║                                                                           ║
║  1/5  基本信息        → 服务器别名                                       ║
║  2/5  钉钉机器人      → Webhook + Secret (可选)                          ║
║  3/5  讯飞星火 LLM    → API Key (可选)                                   ║
║  4/5  邮件周报        → SMTP + 收件人 (可选)                             ║
║  5/5  路由监测        → 目标 IP + 检测间隔                               ║
║                                                                           ║
╚═══════════════════════════════════════════════════════════════════════════╝
```

### 启动终端菜单

```bash
python3 /opt/bandwidth_monitor/main.py
```

```
╔═══════════════════════════════════════════════════════════════════════════╗
║  AWS Lightsail 服务器监控系统 v3.0                                        ║
╠═══════════════════════════════════════════════════════════════════════════╣
║  服务器: AWS-Lightsail-Tokyo                                              ║
║  IP:     13.112.xx.xx                                                     ║
╠═══════════════════════════════════════════════════════════════════════════╣
║    1. 📊  查看监控状态                                                    ║
║    2. 🔍  带宽积分分析                                                    ║
║    3. 📈  流量报表                                                        ║
║    4. 📡  路由状态                                                        ║
║    5. ⚙️   配置管理                                                       ║
║    6. 🔧  服务管理                                                        ║
║                                                                           ║
║    0. 退出                                                                ║
╚═══════════════════════════════════════════════════════════════════════════╝
```

---

## 🔧 服务管理 | Service Management

所有守护进程由 systemd 托管，支持开机自启与故障自动重启：

```bash
# 带宽采集
systemctl status bandwidth-monitor
systemctl restart bandwidth-monitor
journalctl -u bandwidth-monitor -f

# 路由监测
systemctl status route-monitor
journalctl -u route-monitor -f

# 每日分析定时器 (23:59 触发)
systemctl status bandwidth-analyzer.timer
systemctl start bandwidth-analyzer.service   # 手动触发
```

---

## 📋 CLI 参数 | CLI Arguments

### monitor_daemon.py

```bash
sudo python3 monitor_daemon.py -i ens5 -o /var/log/bandwidth
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-i, --interface` | 监控网卡名称 | `ens5` |
| `-o, --output` | CSV 日志输出目录 | `/var/log/bandwidth` |

### route_daemon.py

```bash
sudo python3 route_daemon.py -t 114.114.114.114 -n 300
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-t, --target` | traceroute 目标 | `114.114.114.114` |
| `-n, --interval` | 检测间隔（秒） | `300` |
| `-o, --output` | 日志输出目录 | `/var/log/bandwidth` |

---

## ⚙️ 配置参考 | Configuration Reference

所有配置集中于 `config.py`，持久化存储在 `settings.json`（权限 600）。

### 钉钉机器人

| 键 | 说明 | 默认值 |
|----|------|--------|
| `DINGTALK_WEBHOOK` | Webhook URL | `""` |
| `DINGTALK_SECRET` | 加签密钥 (SEC) | `""` |

### 讯飞星火 LLM

| 键 | 说明 | 默认值 |
|----|------|--------|
| `XFYUN_API_URL` | API 端点 | `https://spark-api-open.xf-yun.com/v1/chat/completions` |
| `XFYUN_API_KEY` | APIpassword | `""` |
| `XFYUN_MODEL` | 模型名称 | `4.0Ultra` |
| `XFYUN_ENABLED` | 启用开关 | `False` |

### 邮件

| 键 | 说明 | 默认值 |
|----|------|--------|
| `EMAIL_ENABLED` | 启用开关 | `False` |
| `SMTP_SERVER` | SMTP 服务器 | `smtp.exmail.qq.com` |
| `SMTP_PORT` | 端口 | `465` |
| `SMTP_USERNAME` | 发件人 | `""` |
| `SMTP_PASSWORD` | 密码 | `""` |
| `EMAIL_RECIPIENTS` | 收件人列表 | `[]` |
| `WEEKLY_REPORT_DAY` | 周报发送日 | `6` (周日) |

### 监控参数

| 键 | 说明 | 默认值 |
|----|------|--------|
| `INTERFACE` | 网卡名称 | `ens5` |
| `PING_GW` | AWS 网关探测目标 | `169.254.169.254` |
| `PING_EXT` | 公网探测目标 | `114.114.114.114` |
| `PING_INTERVAL` | 后台 ping 间隔（秒） | `5` |
| `ROUTE_TARGET` | 路由监测目标 | `114.114.114.114` |
| `ROUTE_INTERVAL` | 路由检测间隔（秒） | `300` |

---

## 📊 数据格式 | Data Format

### CSV 输出

每日自动切割，文件命名：`traffic_log_YYYYMMDD_{interface}.csv`

```csv
timestamp,rx_mbps,tx_mbps,cpu_load_1m,rtt_gw_ms,rtt_ext_ms,loss_gw_pct,loss_ext_pct
2026-06-26 00:00:01,0.0234,0.0156,0.08,0.45,12.30,0.0,0.0
2026-06-26 00:00:02,0.0198,0.0142,0.07,0.52,11.85,0.0,0.0
...
# ===== DAILY SUMMARY =====
# date: 20260626
# interface: ens5
# total_records: 86400
# peak_tx_mbps: 45.2300
# peak_tx_time: 2026-06-26 14:32:17
# peak_rx_mbps: 98.7600
# peak_rx_time: 2026-06-26 14:32:18
# ===== END SUMMARY =====
```

---

## 🛡️ 虚拟网卡过滤 | Virtual NIC Blacklist

自动排除以下虚拟 / 容器 / VPN 网卡，确保采样目标为物理接口：

```
lo · docker* · br-* · veth* · virbr* · vmnet* · tun* · tap*
ppp* · wg* · CloudflareWARP · warp* · zt* · tailscale* · utun*
```

---

## 🧬 技术亮点 | Technical Highlights

| 技术 | 实现 |
|------|------|
| **零阻塞采样** | ICMP 探测下沉至 `threading.Thread(daemon=True)`，`Lock` 保护缓存，主循环永不阻塞 |
| **O(log n) 事件检索** | `bisect.bisect_left/right` 预构建时间索引，拒绝 O(n²) 暴力扫描 |
| **零依赖核心** | 钉钉 HMAC-SHA256 签名使用 `urllib.request`，无需 `requests` |
| **优雅降级** | matplotlib 缺失 → ASCII 图表；requests 缺失 → socket 获取 IP |
| **声明式配置** | `config.py` 单一数据源，`settings.json` 持久化，`chmod 600` 权限隔离 |
| **LLM 流式分析** | 讯飞星火 SSE 流式响应，逐帧解析 `data: ` 前缀，实时拼接 |
| **日切防抖** | 23:59 切割后 `sleep(61)` 防止重复触发 |
| **网卡黑名单** | Python + Bash 双端维护虚拟接口前缀列表，自动过滤 WARP/Docker/VPN |

---

## 📁 依赖 | Dependencies

### 系统依赖

| 组件 | 用途 | 必需 |
|------|------|:---:|
| Python 3.10+ | 运行时 | ✅ |
| ping | RTT / 丢包探测 | ✅ |
| traceroute | 路由拓扑监测 | ✅ |
| vnstat | 流量统计（周报） | ⚪ |
| systemd | 服务管理 | ✅ |
| matplotlib | 趋势图生成 | ⚪ |

### Python 依赖

**核心功能零外部依赖**，仅使用 Python 标准库。

可选：
- `matplotlib` — PNG 趋势图（缺失时降级为 ASCII）
- `requests` — 讯飞星火 LLM 调用（缺失时跳过 LLM 分析）

---

## 📜 许可证 | License

[MIT License](LICENSE) © 2026 [linjunhao024-byte](https://github.com/linjunhao024-byte)

---

<div align="center">

**`AWS-Server-Monitoring`** — 对抗黑盒，从 1Hz 开始。

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:0f0f0f,50:fce300,100:00f0ff&height=120&section=footer&fontColor=fce300&animation=twinkling" width="100%"/>

</div>
