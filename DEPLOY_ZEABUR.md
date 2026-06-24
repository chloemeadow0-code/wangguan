# 🚀 Zeabur 部署完整教程（从 Supabase 开始）

本教程手把手教你部署「橘瓣记忆系统」——一个按人设/线路隔离的 OrangeChat 记忆网关。

> ⏱ 预计耗时：20~30 分钟

---

## 📋 目录

- [第 1 步：创建 Supabase 数据库](#第-1-步创建-supabase-数据库)
- [第 2 步：执行建表 SQL](#第-2-步执行建表-sql)
- [第 3 步：获取 service_role Key](#第-3-步获取-service_role-key)
- [第 4 步：开启 RLS 安全策略](#第-4-步开启-rls-安全策略)
- [第 5 步：推送代码到 GitHub](#第-5-步推送代码到-github)
- [第 6 步：在 Zeabur 创建项目](#第-6-步在-zeabur-创建项目)
- [第 7 步：配置环境变量](#第-7-步配置环境变量)
- [第 8 步：绑定域名](#第-8-步绑定域名)
- [第 9 步：验证部署](#第-9-步验证部署)
- [第 10 步：使用记忆面板](#第-10-步使用记忆面板)
- [常见问题排查](#常见问题排查)

---

## 第 1 步：创建 Supabase 数据库

> Supabase 是整个记忆系统的数据底座，必须先配好。

### 1.1 注册 / 登录

1. 打开 [supabase.com](https://supabase.com)
2. 用 GitHub 账号登录（推荐）

### 1.2 新建项目

1. 点击 **New Project**
2. 填写：
   - **Name**：`orangechat`（或你喜欢的名字）
   - **Database Password**：设一个强密码，**记下来**
   - **Region**：选离你近的（如 `Southeast Asia (Singapore)`）
3. 点击 **Create new project**
4. 等待 1~2 分钟，数据库初始化完成

---

## 第 2 步：执行建表 SQL

> 这一步创建橘瓣记忆系统需要的全部表。

### 2.1 执行主建表脚本

1. 在 Supabase 左侧菜单点击 **SQL Editor**
2. 点击 **New query**
3. 把项目仓库里的 `migration_rebuild_all.sql` **全部内容**粘贴进去
4. 点击 **Run**（运行）

这个脚本会创建 5 张表：

| 表名 | 用途 |
|------|------|
| `chat_archive` | 对话原文归档（按人设隔离） |
| `chat_messages` | 精华记忆库（按人设隔离） |
| `personas` | 人设/线路表 |
| `persona_map` | 人设映射表 |
| `chat_message_embeddings` | 向量表（预留向量检索） |

> ⚠️ 这个脚本会**先删表再重建**（drop + create）。如果是全新项目放心执行；如果库里已有数据请先备份。

### 2.2 执行人设增量脚本

再新建一个 query，粘贴 `migration_phase2_personas.sql` 的内容并运行。这会给人设表加上 `system_prompt` 和 `is_active` 字段。

### 2.3 验证表创建成功

在左侧菜单点击 **Table Editor**，你应该能看到这些表：

- ✅ `chat_archive`
- ✅ `chat_messages`
- ✅ `personas`
- ✅ `persona_map`
- ✅ `chat_message_embeddings`

---

## 第 3 步：获取 service_role Key

> ⚠️ 这一步极其重要。必须用 **service_role key**，不能用 anon key。

### 3.1 找到 API 密钥

1. 在 Supabase 左侧菜单点击 **Settings**（齿轮图标）
2. 点击 **API**
3. 你会看到两个 Key：

| Key 类型 | 说明 |
|----------|------|
| `anon` / `publishable` | 公开密钥，受 RLS 约束，**不要用这个** |
| **`service_role`** | 服务端密钥，绕过 RLS，**用这个** ✅ |

### 3.2 记下两个值

| 需要记下的值 | 在哪找 |
|-------------|--------|
| **Project URL** | Settings → API → Project URL，形如 `https://xxxxx.supabase.co` |
| **service_role key** | Settings → API → `service_role` secret，一串很长的 `eyJ...` 开头的字符串 |

> 🔐 **安全警告**：`service_role` key 拥有数据库的完全读写权限，**绝对不能暴露到前端**。它只放在 Zeabur 环境变量里。

---

## 第 4 步：开启 RLS 安全策略

> 这一步防止别人拿到你的 Supabase URL + anon key 后读写你的数据。

### 4.1 执行 RLS 脚本

1. 在 Supabase **SQL Editor** 新建 query
2. 粘贴 `migration_rls.sql` 的全部内容
3. 点击 **Run**

### 4.2 脚本做了什么

- 对所有表（`chat_messages` / `chat_archive` / `personas` / `persona_map` / `chat_message_embeddings` / `memories` / `user_facts` 等）开启 **Row Level Security**
- 设置策略：**anon 角色全部拒绝，service_role 全部放行**

### 4.3 验证 RLS 生效

在 Table Editor 里点开 `chat_messages` 表，如果看到 **"RLS Enabled** 标记，说明已生效。

> ✅ 之后网关后端用 `service_role` key 访问，完全不受影响。前端面板只持有 `API_SECRET`，不直接连 Supabase。

---

## 第 5 步：推送代码到 GitHub

### 5.1 创建 GitHub 仓库

1. 打开 [github.com/new](https://github.com/new)
2. Repository name 填：`mcp-gateway`
3. 选择 **Private**（推荐）
4. 点击 **Create repository**

### 5.2 推送代码

在项目目录打开终端：

```bash
git init
git remote add origin https://github.com/<你的用户名>/mcp-gateway.git
git add -A
git commit -m "橘瓣记忆系统：自动归档 + 精华提炼 + RLS"
git branch -M main
git push -u origin main
```

> ⚠️ 确认 `.env` 文件没有被推送（`.dockerignore` 已排除）。

---

## 第 6 步：在 Zeabur 创建项目

### 6.1 新建项目

1. 登录 [Zeabur Dashboard](https://dashboard.zeabur.com)
2. 点击 **+ New Project**
3. 项目名：`orangechat`，区域选 `Asia - Hong Kong`
4. 点击 **Create**

### 6.2 添加 Service

1. 点击 **+ Add Service** → **Git Repository**
2. 授权 GitHub，选择 `mcp-gateway` 仓库

### 6.3 确认构建

Zeabur 会自动识别 `Dockerfile`，通常无需手动配置。等待 2~5 分钟首次构建完成，状态变绿色 **Running**。

---

## 第 7 步：配置环境变量

> 这是最关键的一步。在 Zeabur 的 Service 详情页 → **Variables** 标签。

### 7.1 🔴 必填项（最小可运行）

```
CHAT_API_KEY=sk-xxxxxxxx
CHAT_MODEL_NAME=abab6.5s-chat
API_SECRET=你自定义的随机密钥
```

如果用第三方 OpenAI 兼容服务（DeepSeek / 通义等），还需加：

```
CHAT_BASE_URL=https://api.deepseek.com/v1
```

### 7.2 🔴 Supabase 数据库（核心）

> 把第 3 步记下的 **service_role key** 填进来，不是 anon key！

```
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_KEY=eyJhbGci...（service_role key）
```

> ⚠️ **再次强调**：必须用 `service_role` key。如果误填了 `anon` key，RLS 开启后所有数据库操作都会被拒绝。

### 7.3 🟡 推荐配置

```
AI_NAME=小橘
USER_NAME=函函
USER_ID=default
AI_PERSONA=你是一个通用智能助手。
```

### 7.4 🟢 批量粘贴（Bulk Edit）

Zeabur Variables 编辑器支持 RAW 批量粘贴，一次粘贴全部：

```
CHAT_API_KEY=sk-xxxxxxxx
CHAT_MODEL_NAME=abab6.5s-chat
CHAT_BASE_URL=https://api.minimaxi.com/v1
API_SECRET=lyy123
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_KEY=eyJhbGci...
AI_NAME=小橘
USER_NAME=函函
USER_ID=default
AI_PERSONA=你是一个通用智能助手。
```

粘贴后点击 **Save**，Zeabur 自动重新部署。

### 7.5 可选增强（按需）

<details>
<summary>📧 邮件 (Resend)</summary>

```
RESEND_API_KEY=re_xxxxxxxx
MY_EMAIL=你的邮箱@gmail.com
```
</details>

<details>
<summary>🧠 向量记忆 (Mem0)</summary>

```
MEM0_API_KEY=mem0_xxxxxxxx
MEM0_USER_ID=default
```
</details>

<details>
<summary>🔍 网页搜索 (Tavily)</summary>

```
TAVILY_API_KEY=tvly_xxxxxxxx
```

不配置则自动用 DuckDuckGo 免费兜底。
</details>

<details>
<summary>🔮 向量嵌入 (用于未来向量检索)</summary>

```
DOUBAO_API_KEY=sk-xxxxxxxx
DOUBAO_EMBEDDING_EP=BAAI/bge-m3
```

配置后可用于精华记忆的语义检索（第五阶段功能）。
</details>

---

## 第 8 步：绑定域名

### 8.1 生成域名

1. Service 详情页 → **Networking** 标签
2. 点击 **Generate Domain**
3. 获得形如 `orangechat-xxx.zeabur.app` 的免费 HTTPS 域名

### 8.2 确认端口

| 设置 | 值 |
|------|-----|
| Port | `10000` |

### 8.3 入口路径

| 路径 | 用途 |
|------|------|
| `/health` | 健康检查 |
| `/sse` | MCP 客户端接入 |
| `/v1/chat/completions` | 橘瓣聊天接入（OpenAI 兼容） |
| `/panel` | **记忆管理面板** |

---

## 第 9 步：验证部署

### 9.1 健康检查

```bash
curl https://orangechat-xxx.zeabur.app/health
```

预期返回：

```json
{"status": "ok", "service": "generic-mcp-gateway"}
```

### 9.2 查看配置体检报告

在 Zeabur 的 **Runtime → Logs** 里看启动日志，应该显示：

```
✅ 主对话 (CHAT)     → 已连接
✅ 数据库 (Supabase) → 已连接
✅ 接口安全密钥      → 已配置
```

### 9.3 测试橘瓣聊天

用 OpenAI 兼容客户端（如 ChatBox）接入：

| 字段 | 值 |
|------|-----|
| API Base URL | `https://orangechat-xxx.zeabur.app/v1` |
| API Key | 你的 `API_SECRET`（如 `lyy123`） |
| Model | 你的 `CHAT_MODEL_NAME` |

发一条消息，然后在 Zeabur Logs 里应该看到：

```
➡️ [转发] POST ...
🍊 [归档] 已写入 chat_archive [默认助手_技术线]: user(20字) + assistant(50字)
🧠 [提炼] [默认助手_技术线] 新增2 更新0 冲突0 跳过0
```

说明对话已自动归档并提炼了精华记忆。

---

## 第 10 步：使用记忆面板

### 10.1 打开面板

浏览器访问：

```
https://orangechat-xxx.zeabur.app/panel
```

### 10.2 连接

在面板里只填 **API_SECRET**（如 `lyy123`），不需要填 Supabase URL/Key。

### 10.3 功能

| 页签 | 功能 |
|------|------|
| 💬 精华记忆 | 查看/编辑/删除 `chat_messages`（按人设筛选 + 分类筛选 + 搜索） |
| 📜 对话归档 | 查看 `chat_archive` 原文 |
| 🎭 人设管理 | 新增/编辑/删除人设，设置激活线路 |

### 10.4 手动触发提炼

在精华记忆页或用 API 手动对某段文本触发提炼：

```bash
curl -X POST https://orangechat-xxx.zeabur.app/api/panel/distill \
  -H "x-api-key: lyy123" \
  -H "Content-Type: application/json" \
  -d '{"assistant_id":"骆云影_联姻线","from_archive":true,"limit":10}'
```

---

## 数据流架构

```
橘瓣聊天 (/v1/chat/completions)
  → 原文写入 chat_archive（按 assistant_id 隔离）
  → 自动异步提炼精华（distill.py）
    → LLM 提取 [标签] 原子事实（强制真实角色名）
    → 与已有 chat_messages 去重比对
      → 新事实 → 写入 chat_messages
      → 更具体 → 更新旧记录
      → 冲突（喜欢→讨厌）→ 标记 [档案] 待确认
  → 阶段总结写入 memories（Core_Cognition）

注入时读取：
  → chat_archive（对话历史）
  → chat_messages（精华记忆）
  → memories（Core_Cognition 总结）

安全：
  → 前端只持有 API_SECRET
  → 后端持有 service_role key
  → Supabase RLS 全表开启，anon 全拒
```

---

## 常见问题排查

### ❓ Supabase 报权限错误 / 数据读不出来

**原因**：用了 `anon` key 而不是 `service_role` key。

**解决**：回到第 3 步，确认 `SUPABASE_KEY` 填的是 `service_role` key。

### ❓ 精华提炼没有产出（Logs 里显示 "无可提取的长期事实"）

**原因**：正常现象。如果对话只是寒暄（如"你好""再见"），提炼引擎会判定没有长期价值的事实。

**解决**：进行有实质内容的对话（如讨论关系、剧情、喜好），再观察日志。

### ❓ 面板打开是空白的

**原因**：`API_SECRET` 没配或填错。

**解决**：确认 Zeabur 环境变量里 `API_SECRET` 已设置，面板连接时填入同一个值。

### ❓ MCP 客户端连接后卡住

**原因**：通常是 SSE 流被缓冲。

**解决**：确认域名是 `https://` 开头。Zeabur 原生支持 SSE，一般不会出现这个问题。

### ❓ 修改环境变量后没生效

Zeabur 修改变量后会自动重新部署。如果没有，手动点击 **Redeploy**。

---

## 数据库表说明

### 橘瓣记忆系统表（核心）

| 表 | 用途 | 隔离方式 |
|----|------|---------|
| `chat_archive` | 对话原文归档 | 按 `assistant_id`（人设/线路） |
| `chat_messages` | 精华事实库 | 按 `assistant_id` |
| `personas` | 人设/线路列表 | — |
| `persona_map` | assistant_id → 展示名映射 | — |
| `chat_message_embeddings` | 向量索引（预留） | 按 `assistant_id` |

### 旧版通用表（保留）

| 表 | 用途 |
|----|------|
| `memories` | 日记 / Core_Cognition 总结 / 心跳记录（不再存对话流水） |
| `user_facts` | 用户画像 / 系统配置 |
| `memory_house` | AI 虚拟生活小屋 |
| `expenses` | 记账 |
| `device_data` | 设备定位 |

> 建表 SQL 见仓库中的 `migration_rebuild_all.sql`，RLS 策略见 `migration_rls.sql`。

---

## 🎉 完成！

部署成功后，你拥有了一个：

- ✅ 按人设/线路隔离的橘瓣记忆系统
- ✅ 对话自动归档到 `chat_archive`
- ✅ 精华事实自动提炼到 `chat_messages`（带去重/冲突检测）
- ✅ 手机端可用的 HTML 管理面板
- ✅ RLS 安全策略 + service_role 收口
- ✅ 24 小时在线 + 自动 HTTPS

---

> 📚 更多信息参考 [README.md](README.md) 和 [VARIABLES.md](VARIABLES.md)