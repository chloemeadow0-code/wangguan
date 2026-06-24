# 通用 MCP 网关 (Generic MCP Gateway)

一个基于 **FastMCP + ASGI** 的通用智能体网关架构模板。它将"工具能力 (MCP)"、"记忆 / 画像系统"与"自主生命心跳"整合为一个可独立部署的服务。

> 本仓库为**通用化版本**：已移除全部个人化内容 (硬编码密钥、私人域名、人设、ID 等)，所有配置均通过环境变量注入，方便直接复用与二次开发。

---

## 📐 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                        客户端 / 外部平台                        │
│   (Claude / Cursor 等 MCP 客户端、网页对话、邮件)               │
└───────────────┬─────────────────────────────────────────────┘
                 │ HTTP / SSE
                 ▼
┌──────────────────────────────┐   ┌──────────────────────────┐
│      gateway.py              │   │     heartbeat.py         │
│  ┌────────────────────────┐  │   │  (后台 daemon 线程池)      │
│  │ HostFixMiddleware      │──┼───┼─► 自主生命循环            │
│  │  • /health 健康检查     │  │   │  • 每日日记生成           │
│  │  • /api/config 热更新   │  │   │  • 消息总结器            │
│  │  • /api/logs  日志      │  │   │  • 环境变量热同步          │
│  │  • /api/restart 重启    │  │   │                          │
│  └────────────────────────┘  │   └──────────────────────────┘
└──────────┬───────────────────┘
           ▼
┌──────────────────────────────┐   ┌──────────────────────────┐
│      server.py               │   │     外部依赖 (可选)        │
│  FastMCP("GenericGateway")   │   │  • Supabase (数据库)      │
│  • echo / save_memory        │   │  • Mem0 (长期记忆)        │
│  • search_memory             │   │  • Pinecone (向量兜底)    │
│  • manage_user_fact          │   │  • Resend (邮件)          │
│  • organize_knowledge_base   │   │  • 高德地图 (GPS/天气)     │
│  • send_email_via_api        │   │  • Tavily (网页搜索)      │
│  • web_search                │   └──────────────────────────┘
│  • manage_memory_house       │
│  • save_expense / piggy_bank │
│  • tarot_reading             │
│  • render_html_to_image     │
└──────────────────────────────┘
```

### 文件职责

| 文件 | 角色 | 说明 |
|------|------|------|
| `server.py` | **MCP 工具层** | 注册所有 `@mcp.tool` 工具，是 LLM 调用的入口 |
| `gateway.py` | **ASGI 中间件层** | Host 修正、CORS、管理接口、对话总结路由 |
| `heartbeat.py` | **后台心跳层** | daemon 线程池，驱动"自主生命感" |
| `panel.py` | **管理面板** | 轻量 Web 管理界面服务 |
| `.env.example` | **配置模板** | 所有可配置项的文档化示例 |

---

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入你的真实配置
```

**最小化配置**（只跑通 MCP 工具）只需设置：
- `CHAT_API_KEY` — 主对话模型 API Key
- `CHAT_MODEL_NAME` — 模型名

### 3. 启动

```bash
python server.py
```

服务默认监听 `0.0.0.0:10000`。健康检查：`GET /health`。

### 4. 接入 MCP 客户端

将 MCP 客户端 (如 Claude Desktop、Cursor) 指向：

```
http://<你的域名或IP>:10000/sse
```

---

## 🧩 MCP 工具清单

> 网关注册了丰富的 MCP 工具，按子系统分组（按需配置即可启用）：

| 分类 | 工具 | 功能 |
|------|------|------|
| 基础 | `echo` | 回声测试 |
| 记忆 | `save_memory` / `search_memory` | 记忆存取（数据库 + Mem0/Pinecone 向量双写双搜）|
| 记忆 | `get_latest_diary` | 加载最新记忆流（长期总结 + 短期对话 + 小屋动态）|
| 画像 | `manage_user_fact` / `get_user_profile` | 用户画像 CRUD |
| 知识库 | `organize_knowledge_base` | 通用知识库 CRUD |
| 邮件 | `send_email_via_api` | Resend 发邮件 |
| 搜索 | `web_search` | 网页搜索 (Tavily 优先 + DDG 兜底) |
| 生活 | `manage_memory_house` | AI 虚拟生活小屋 (陪伴感) |
| 生活 | `save_expense` / `check_expense_report` / `manage_piggy_bank` | 记账 + 账单 + 储蓄罐 |
| 生活 | `where_is_user` | GPS 定位 + 天气 (高德) |
| 娱乐 | `tarot_reading` | AI 塔罗占卜 |
| 多媒体 | `render_html_to_image` | HTML/CSS 转图片 (HCTI) |
| 橘瓣 | `memory_write` / `archive_write` / `memory_search_v2` | 按人设隔离的精华记忆库 |

---

## 📡 管理 API

| 路径 | 方法 | 功能 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/api/config` | POST | 热更新环境变量 (JSON body) |
| `/api/logs` | GET | 读取最近日志 |
| `/api/restart` | POST | 触发云平台重启 (需配 `RESTART_WEBHOOK_URL`) |

---

## 💓 后台心跳说明

`heartbeat.py` 中的协程**全部是可选的**，按需开启：

| 协程 | 启用条件 | 默认间隔 |
|------|---------|---------|
| 自主生命循环 | 配置了 LLM | 2 小时 |
| 每日日记生成 | 配置了 LLM + Supabase | 每天 `DIARY_TIME`（默认 03:00） |
| 消息总结器 | 配置了 LLM + Supabase | 30 分钟 |
| 环境变量热同步 | 配置了 Supabase | 10 秒 |

---

## 🗄️ 数据库表结构 (Supabase)

通用版需要的核心表，字段尽量精简：

```sql
-- 记忆表
create table memories (
  id bigint generated always as identity primary key,
  title text,
  content text,
  category text default '流水',          -- 流水/记事/灵感/情感/画像
  mood text default '平静',
  tags text default 'System',
  importance int default 1,              -- 权重 1~10，按 category 自动计算
  created_at text
);

-- 用户画像表
create table user_facts (
  key text primary key,
  value text,
  confidence float default 1.0
);

-- 记忆小屋 (AI 虚拟生活系统，可选)
create table memory_house (
  id bigint generated always as identity primary key,
  room text,                        -- 卧室/厨房/客厅/书房/阳台
  action_type text,                 -- 看书/做饭/听音乐/发呆
  content text,
  is_locked boolean default false,
  created_at text
);

-- 记账 (可选)
create table expenses (
  id bigint generated always as identity primary key,
  item text,
  amount float,
  type text,                        -- 餐饮/购物/交通/娱乐/日常/其他
  date date
);

-- 设备定位数据 (可选，供 where_is_user 使用)
create table device_data (
  id bigint generated always as identity primary key,
  timestamp text,
  location_latitude float,
  location_longitude float,
  location_address text,
  foreground_app text,
  app_usage jsonb
);

-- 橘瓣记忆库 (可选，按人设隔离的精华记忆)
create table chat_messages (
  id bigint generated always as identity primary key,
  assistant_id text,
  conversation_id text,
  content text,
  category text,
  role text default 'assistant',
  created_at text default now()
);

create table chat_archive (
  id bigint generated always as identity primary key,
  assistant_id text,
  conversation_id text,
  role text,
  content text,
  category text default 'archive',
  created_at text default now()
);
```

> 可在 `user_facts` 中插入 `key='sys_config'`，value 为 JSON 字符串，实现配置的数据库热同步。

---

## 📦 部署指南

本网关需要**长驻进程**（后台心跳线程），因此**不支持 Serverless 平台**（如 Vercel、Cloudflare Workers）。下面提供 4 种主流部署方案。

### 方案一：Docker 部署（推荐）

最省心的方式，已内置 Dockerfile 和 Compose 配置。

```bash
# 1. 准备配置文件
cp .env.example .env
# 编辑 .env 填入真实配置

# 2. 构建并启动
docker compose up -d

# 3. 查看日志
docker compose logs -f

# 4. 停止 / 重启
docker compose down
docker compose restart
```

**手动 `docker` 命令（不用 compose）：**

```bash
# 构建镜像
docker build -t mcp-gateway .

# 运行容器
docker run -d --name mcp-gateway --restart unless-stopped \
  -p 10000:10000 --env-file .env mcp-gateway
```

> 💡 `Dockerfile` 内置了 `HEALTHCHECK`，K8s、Portainer 等编排平台可直接识别容器健康状态。

---

### 方案二：云平台 PaaS（Zeabur / Render / Railway / Fly.io）

这类平台支持长驻进程 + 自动 HTTPS，最适合本项目。通用流程：

1. **推送代码到 GitHub**
2. **在平台新建项目** → 选择本仓库
3. **配置构建命令**：平台一般会自动识别，或手动填写
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python server.py`
4. **注入环境变量**：在平台后台把 `.env` 里的变量逐条填入
   - 必填：`CHAT_API_KEY`、`CHAT_MODEL_NAME`
   - 端口：平台会自动注入 `PORT`，无需手动设
5. **暴露端口**：设为 `10000`（或代码读取的 `PORT`）
6. **绑定域名**：平台自动分配 HTTPS 域名，直接用

部署成功后，MCP 客户端接入地址为：`https://<平台分配的域名>/sse`

---

### 方案三：VPS + Nginx 反向代理（阿里云 / 腾讯云 / AWS EC2）

适合需要完全掌控的场景。需要自行处理 HTTPS 和反代。

**第 1 步：用 systemd 把网关注册为系统服务**

创建 `/etc/systemd/system/mcp-gateway.service`：

```ini
[Unit]
Description=Generic MCP Gateway
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/mcp-gateway
EnvironmentFile=/opt/mcp-gateway/.env
ExecStart=/usr/bin/python3 /opt/mcp-gateway/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
# 启用并启动
systemctl daemon-reload
systemctl enable mcp-gateway
systemctl start mcp-gateway
systemctl status mcp-gateway   # 检查状态
journalctl -u mcp-gateway -f   # 查看实时日志
```

**第 2 步：配置 Nginx 反向代理**

> ⚠️ **关键**：MCP 的 `/sse` 是流式响应，**必须关闭 buffering**，否则客户端会一直卡住收不到数据。

创建 `/etc/nginx/conf.d/mcp-gateway.conf`：

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:10000;

        # ====== SSE 流式响应关键配置 ======
        proxy_buffering off;              # 关闭缓冲，SSE 必需
        proxy_cache off;                  # 关闭缓存
        proxy_read_timeout 86400s;        # 长连接超时调大
        chunked_transfer_encoding on;

        # ====== 通用反代头 ======
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
nginx -t              # 测试配置
systemctl reload nginx
```

**第 3 步：配置 HTTPS（推荐 certbot）**

```bash
# Ubuntu/Debian
apt install certbot python3-certbot-nginx
certbot --nginx -d your-domain.com
```

配置完成后，接入地址：`https://your-domain.com/sse`

---

### 方案四：本地开发调试

```bash
# 1. 创建虚拟环境
python -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate         # Windows

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env，至少填入 CHAT_API_KEY 和 CHAT_MODEL_NAME

# 4. 运行
python server.py

# 5. 验证
curl http://localhost:10000/health
```

---

### ⚠️ 部署常见坑点

| 问题 | 原因 | 解决 |
|------|------|------|
| MCP 客户端连接后卡住 | Nginx 没关 `proxy_buffering` | 加 `proxy_buffering off;` |
| 部署到 Vercel 失败 | Serverless 不支持长驻进程 | 换用 Render/Railway/VPS |
| Supabase 报错 | RLS 权限策略 | 确认表关闭了 RLS 或配置了 service_role key |

---

## 🔒 安全说明

本通用版本已做到：
- ✅ **零硬编码密钥**：所有 API Key、Token、URL 均从环境变量读取
- ✅ **无个人化数据**：移除了人设、私人域名、用户 ID 等
- ✅ **统一错误兜底**：`mcp_error_handler` 装饰器防止单个工具崩溃影响整体
- ✅ **可选依赖**：Supabase / Mem0 / Pinecone 等缺失时优雅降级而非报错
- ✅ **管理接口鉴权**：所有 `/api/*` 接口强制校验 `API_SECRET` 密钥

---

## 🛠️ 自定义扩展

- **新增 MCP 工具**：在 `server.py` 中仿照现有工具添加 `@mcp.tool()` 函数即可
- **新增后台协程**：在 `heartbeat.py` 中添加新的异步协程，在 `start_autonomous_life()` 中启动
- **替换 LLM**：只需修改 `CHAT_BASE_URL` 和 `CHAT_MODEL_NAME`（或配置多角色 `SILICON1_*` / `VISION_*`）
- **替换数据库**：将 `server.py` 顶部的 `supabase = ...` 改为你的客户端即可

---

## 📄 License

MIT — 自由使用、修改、分发。