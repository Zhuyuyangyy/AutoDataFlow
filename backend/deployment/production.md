# AutoDataFlow v2.3 — 生产部署文档

## 1. 概述

AutoDataFlow v2.3 是工业级数据健康度治理 API 服务，提供：

- **REST API** — 数据表健康度查询、趋势分析、Schema 变更检测
- **Prometheus Metrics** — `/metrics` 端点暴露请求计数、延迟直方图、活跃请求数
- **滑动窗口限流** — 全局 + 单 IP 两层，60s 窗口
- **结构化日志** — loguru + request_id 贯穿每个请求
- **安全 Headers** — HSTS / X-Content-Type / X-Frame / CSP
- **Webhook 告警** — 飞书/钉钉/企业微信

## 2. 架构

```
                    ┌─────────────────────────┐
  Client            │   Nginx / API Gateway   │
  (Browser/Dashboard)│   (SSL termination)     │
                    └────────────┬────────────┘
                                 │ :8080
                    ┌────────────▼────────────┐
                    │   Gunicorn (4 workers)   │
                    │  UvicornWorker (async)   │
                    │   FastAPI application    │
                    └────────────┬────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                  │
    ┌─────────▼─────┐  ┌────────▼────┐  ┌─────────▼─────┐
    │  Prometheus    │  │  SQLite      │  │  auto_data_   │
    │  /metrics      │  │  WAL + pool  │  │  flow.py      │
    │  (scraped)     │  │  (5 conns)   │  │  (subprocess) │
    └────────────────┘  └─────────────┘  └───────────────┘
```

## 3. 环境要求

- **OS**: Linux (Ubuntu 20.04+ / Debian 11+)
- **Python**: 3.10+
- **CPU**: 4 核+（推荐 8 核）
- **RAM**: 4 GB+
- **Disk**: 10 GB+（报告文件 + SQLite WAL）

## 4. 依赖安装

```bash
# 进入 backend 目录
cd backend

# 创建虚拟环境（推荐）
python3.11 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 额外工业级依赖
pip install \
    loguru \
    prometheus-client \
    tenacity \
    gunicorn
```

## 5. 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AUTODATAFLOW_API_KEY` | `dev-key-change-me` | API 认证密钥（**生产必须修改**） |
| `AUTODATAFLOW_DATA_DIR` | `./data` | 数据目录路径 |
| `GUNICORN_WORKERS` | `4` | Gunicorn worker 数量 |
| `GUNICORN_THREADS` | `2` | 每个 worker 的线程数 |
| `GUNICORN_TIMEOUT` | `60` | 请求超时（秒） |
| `GUNICORN_BIND` | `0.0.0.0:8080` | 监听地址 |

## 6. 启动方式

### 6.1 开发模式（单进程）

```bash
cd backend
python app.py
# 访问 http://localhost:8080/docs
```

### 6.2 生产模式（Gunicorn）

```bash
cd backend
gunicorn app:app -c gunicorn_conf.py
```

### 6.3 systemd 服务

```ini
# /etc/systemd/system/autodataflow.service
[Unit]
Description=AutoDataFlow v2.3 API Service
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/autodataflow/backend
Environment="PATH=/opt/autodataflow/backend/.venv/bin"
Environment="AUTODATAFLOW_API_KEY=your-secure-key-here"
Environment="GUNICORN_WORKERS=4"
ExecStart=/opt/autodataflow/backend/.venv/bin/gunicorn \
    app:app -c gunicorn_conf.py
Restart=always
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable autodataflow
sudo systemctl start autodataflow
sudo systemctl status autodataflow
```

### 6.4 Docker 部署

```dockerfile
# backend/Dockerfile
FROM python:3.11-slim

WORKDIR /app/backend

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir loguru prometheus-client tenacity gunicorn

COPY . .

# 非 root 运行
USER nobody

EXPOSE 8080

CMD ["gunicorn", "app:app", "-c", "gunicorn_conf.py"]
```

```yaml
# docker-compose.yml
version: "3.9"
services:
  autodataflow:
    build: ./backend
    restart: unless-stopped
    ports:
      - "8080:8080"
    environment:
      - AUTODATAFLOW_API_KEY=${API_KEY}
    volumes:
      - ./data:/app/backend/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

## 7. Nginx 反向代理（可选）

```nginx
# /etc/nginx/sites-available/autodataflow
server {
    listen 443 ssl http2;
    server_name dataplatform.yourcompany.com;

    ssl_certificate     /etc/ssl/certs/yourcompany.crt;
    ssl_certificate_key  /etc/ssl/private/yourcompany.key;
    ssl_protocols        TLSv1.2 TLSv1.3;
    ssl_ciphers          HIGH:!aNULL:!MD5;

    client_max_body_size 10M;

    location / {
        proxy_pass         http://127.0.0.1:8080;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   X-Request-ID      $request_id;
        proxy_read_timeout 90s;
        proxy_connect_timeout 30s;
    }

    # Prometheus metrics（内网访问）
    location /metrics {
        proxy_pass         http://127.0.0.1:8080/metrics;
        allow             10.0.0.0/8;
        deny              all;
    }

    # 强制 HTTPS
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
}
```

## 8. Prometheus + Grafana 监控

### 8.1 Prometheus 抓取配置

```yaml
# /etc/prometheus/prometheus.yml
scrape_configs:
  - job_name: 'autodataflow'
    static_configs:
      - targets: ['localhost:8080']
    metrics_path: /metrics
    scrape_interval: 15s
    scrape_timeout: 10s
```

### 8.2 关键指标

| 指标名 | 类型 | 说明 |
|--------|------|------|
| `autodataflow_http_requests_total` | Counter | 请求总数（method/endpoint/status） |
| `autodataflow_http_request_duration_seconds` | Histogram | 请求延迟分布 |
| `autodataflow_active_requests` | Gauge | 当前活跃请求数 |
| `autodataflow_analysis_total` | Counter | 分析任务总数（status: success/timeout/error） |

### 8.3 Grafana Dashboard（JSON 导入）

```json
{
  "title": "AutoDataFlow API",
  "panels": [
    {
      "title": "QPS",
      "targets": [
        {
          "expr": "rate(autodataflow_http_requests_total[1m])",
          "legendFormat": "{{method}} {{endpoint}}"
        }
      ]
    },
    {
      "title": "P99 Latency",
      "targets": [
        {
          "expr": "histogram_quantile(0.99, rate(autodataflow_http_request_duration_seconds_bucket[5m]))"
        }
      ]
    },
    {
      "title": "Active Requests",
      "targets": [
        {
          "expr": "autodataflow_active_requests"
        }
      ]
    },
    {
      "title": "Analysis Success Rate",
      "targets": [
        {
          "expr": "sum(rate(autodataflow_analysis_total{status='success'}[5m])) / sum(rate(autodataflow_analysis_total[5m]))"
        }
      ]
    }
  ]
}
```

## 9. 日志管理

### 9.1 日志文件位置

```
backend/data/logs/
├── autodataflow.log        # 主日志（DEBUG+，自动轮转 100MB，保留 7 天）
└── audit.log               # 审计日志（/run/analysis 调用）
```

### 9.2 日志格式

```
2026-05-02 16:51:00.123 | INFO     | app:health:42     | Fetching summary request_id=abc123
```

### 9.3 logrotate

```bash
# /etc/logrotate.d/autodataflow
/data/logs/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0640 www-data www-data
    sharedscripts
    postrotate
        # 通知 gunicorn 重新打开日志
        kill -USR1 $(cat /run/gunicorn.pid)
    endscript
}
```

## 10. 备份策略

```bash
# 每日备份 SQLite（crontab -e）
# 凌晨 3 点备份
0 3 * * * /opt/autodataflow/backend/.venv/bin/python -c "
import shutil, datetime
from pathlib import Path
src = Path('/opt/autodataflow/backend/data/warehouse.db')
dst = Path(f'/backup/warehouse_{datetime.date.today()}.db')
shutil.copy(src, dst)
# 只保留最近 30 天
for f in Path('/backup').glob('warehouse_*.db'):
    if f.stat().st_mtime < datetime.datetime.now().timestamp() - 30*86400:
        f.unlink()
"
```

## 11. 安全清单

- [ ] 修改 `AUTODATAFLOW_API_KEY` 为强随机密钥（32+ 字符）
- [ ] 限制 CORS 白名单为具体域名（禁止 `*`）
- [ ] Nginx 配置 SSL/TLS（禁止 HTTP 明文）
- [ ] `/metrics` 端点用 Nginx IP 白名单保护
- [ ] 定期轮转 SSL 证书
- [ ] 禁止数据库文件对外暴露
- [ ] 定期更新依赖：`pip install -U -r requirements.txt`
- [ ] 配置 Linux 文件描述符上限：`ulimit -n 65535`

## 12. 故障排查

| 问题 | 可能原因 | 解决方案 |
|------|----------|----------|
| 429 Rate Limit | 频繁请求 | 降低请求频率或调大 `max_requests` |
| 500 分析超时 | subprocess 超过 300s | 检查 `auto_data_flow.py` 执行时间 |
| SQLite Locked | 多 worker 并发写同一 DB | WAL 模式已启用，检查连接池大小 |
| 日志无 request_id | 中间件执行顺序问题 | 检查 `app.middleware("http")` 注册顺序 |
| Prometheus 无 metrics | 未安装 prometheus_client | `pip install prometheus-client` |

## 13. 版本升级

```bash
cd /opt/autodataflow
git pull

# 重启服务（systemd）
sudo systemctl restart autodataflow

# 观察日志
sudo journalctl -u autodataflow -f
```
