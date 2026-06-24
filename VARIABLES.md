# 📋 环境变量完整清单

本文档列出「橘瓣记忆系统」**代码中实际使用的**所有环境变量。

> 已删除废弃变量（`CHAT_TAG` / `GATEWAY_HOST` / `RESTART_WEBHOOK_URL` / `LOG_FILE` / `MUTE_*` / `ZEABUR_API_KEY` 等，代码中已不存在）。

---

## 目录
- [1. 基础部署](#1-基础部署)
- [2. 数据库 (Supabase)](#2-数据库-supabase)
- [3. 橘瓣记忆系统](#3-橘瓣记忆系统)
- [4. 多模型 LLM](#4-多模型-llm)
- [5. 向量记忆 (Mem0 + Pinecone)](#5-向量记忆-mem0--pinecone)
- [6. 通讯 / 地图 / 搜索](#6-通讯--地图--搜索)
- [7. 后台心跳调度](#7-后台心跳调度)
- [最小可运行配置示例](#最小可运行配置示例)

---

## 1. 基础部署

| 变量名 | 必填 | 默认值 | 说明 |
|--------|:---:|--------|------|
| `PORT` | ✅ | `10000` | 网关监听端口 |
| `API_SECRET` | ✅ | 空 | `/api/*` 接口和面板的鉴权密钥。**面板连接时填这个值**（如 `lyy123`），不填 Supabase key。 |

### 1.1 智能体身份

仅当配置了 `SUPABASE_URL` 时生效（启用归档 + 提炼 + 注入）。

| 变量名 | 必填 | 默认值 | 说明 |
|--------|:---:|--------|------|
| `USER_NAME` | ❌ | `用户` | 用户称呼（如 `函函`） |
| `AI_NAME` | ❌ | `助手` | AI 角色称呼（如 `小橘`） |
| `USER_ID` | ❌ | `default` | Mem0 向量记忆隔离 ID |
| `AI_PERSONA` | ❌ | 空 | AI 人设文本。**被激活人设的 `system_prompt` 覆盖。** |
| `SUMMARY_THRESHOLD` | ❌ | `30` | chat_archive 归档累计达到该条数时自动生成阶段总结 |

---

## 2. 数据库 (Supabase)

> ⚠️ **必须用 service_role key**，不能用 anon key。
>
> 这一点在开启 RLS（第四阶段）后尤其关键：service_role 绕过 RLS，
> anon/publishable key 会被 RLS 拦截导致网关全部报错。

| 变量名 | 必填 | 默认值 | 说明 |
|--------|:---:|--------|------|
| `SUPABASE_URL` | ✅ | 空 | Supabase 项目 URL |
| `SUPABASE_KEY` | ✅ | 空 | **service_role key**。Settings → API → `service_role`。⚠️ 绝不能暴露到前端。 |

### 🔒 RLS 安全收口（第四阶段）

消除 Supabase 邮件"RLS 未开启"告警，执行 [`migration_rls.sql`](migration_rls.sql)：
- 对橘瓣全部表 `enable + force` RLS，**不创建任何 policy**（默认拒绝 anon/普通用户）
- 网关因持有 service_role 自动绕过，前端只持 API_SECRET 经网关访问
- 执行前置条件：确认 `SUPABASE_KEY` 已填 service_role（否则开启后网关全挂）
- 回退脚本已内嵌在 SQL 文件注释中

---

## 3. 橘瓣记忆系统

> 对话自动归档 + 精华自动提炼，按人设/线路隔离。

| 变量名 | 必填 | 默认值 | 说明 |
|--------|:---:|--------|------|
| `ORANGECHAT_ASSISTANT_ID` | ❌ | 空 | 归档兜底人设 ID。无激活人设时用此值，留空默认 `默认助手_技术线` |

### 自动归档
配了 Supabase + LLM 即自动生效，无需手动操作。

### 自动精华提炼
每轮对话归档后，异步调用 `distill.py`：提取 `[标签]` 原子事实 → 去重 → 写入 `chat_messages`。

### 相关工具 / API

| 入口 | 说明 |
|------|------|
| `memory_write` (MCP) | 手动写入精华记忆 |
| `archive_write` (MCP) | 手动归档对话 |
| `memory_search_v2` (MCP) | 按人设关键词检索 |
| `memory_distill` (MCP) | 🆕 手动触发提炼 |
| `POST /api/panel/distill` | 🆕 面板/API 触发提炼 |

---

## 4. 多模型 LLM

### 4.1 主对话模型 CHAT（聊天 + 提炼 + 总结）

| 变量名 | 必填 | 默认值 |
|--------|:---:|--------|
| `CHAT_API_KEY` | ✅ | 空 |
| `CHAT_BASE_URL` | ❌ | `https://api.minimaxi.com/v1` |
| `CHAT_MODEL_NAME` | ❌ | `abab6.5s-chat` |

### 4.2 兼容回退（OPENAI / DEFAULT）

当 `CHAT_*` 未配置时的回退链：

| 变量名 | 兼容别名 |
|--------|---------|
| `OPENAI_API_KEY` | `DEFAULT_API_KEY` |
| `OPENAI_BASE_URL` | `DEFAULT_BASE_URL` |
| `OPENAI_MODEL_NAME` | `DEFAULT_MODEL_NAME` |

### 4.3 硅基流动 SILICON1

| 变量名 | 默认值 |
|--------|--------|
| `SILICON1_API_KEY` | 空 |
| `SILICON1_BASE_URL` | `https://api.siliconflow.cn/v1` |
| `SILICON1_MODEL_NAME` | `Qwen/Qwen2.5-7B-Instruct` |

### 4.4 视觉 VISION

| 变量名 | 默认值 |
|--------|--------|
| `VISION_API_KEY` | 空 |
| `VISION_BASE_URL` | 空 |
| `VISION_MODEL_NAME` | `gpt-4o-mini` |

### 4.5 向量嵌入（第五阶段向量检索用）

| 变量名 | 默认值 |
|--------|--------|
| `DOUBAO_API_KEY` | 空 |
| `DOUBAO_EMBEDDING_EP` | 空（如 `BAAI/bge-m3`） |

---

## 5. 向量记忆 (Mem0 + Pinecone)

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `MEM0_API_KEY` | 空 | mem0.ai API Key |
| `MEM0_USER_ID` | `default` | 用户隔离 ID |
| `PINECONE_API_KEY` | 空 | Pinecone 兜底向量库 |
| `PINECONE_INDEX_NAME` | `notion-brain-v2` | 索引名 |

---

## 6. 通讯 / 地图 / 搜索

| 变量名 | 用途 | 默认值 |
|--------|------|--------|
| `RESEND_API_KEY` | 邮件发送 | 空 |
| `MY_EMAIL` | 收件邮箱（兼容 `ADMIN_EMAIL`） | 空 |
| `AMAP_API_KEY` | 高德地图/GPS/天气 | 空 |
| `TAVILY_API_KEY` | 高质量搜索（不配用 DDG 兜底） | 空 |
| `HCTI_API_ID` | HTML 转图片 | 空 |
| `HCTI_API_KEY` | HTML 转图片 | 空 |

---

## 7. 后台心跳调度

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `HEARTBEAT_INTERVAL` | `7200` | 主动问候间隔（秒） |
| `SUMMARIZE_INTERVAL` | `1800` | 消息总结间隔（秒） |
| `DIARY_TIME` | `03:00` | 每日日记生成时间 |
| `SYNC_KEYS` | 空 | 额外热同步的变量键，逗号分隔 |

---

## 最小可运行配置示例

```env
# 必填
CHAT_API_KEY=sk-xxxxxxxx
CHAT_MODEL_NAME=abab6.5s-chat
API_SECRET=lyy123

# 必填（⚠️ service_role key！）
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_KEY=eyJhbGci...

# 推荐
AI_NAME=小橘
USER_NAME=函函
USER_ID=default
AI_PERSONA=你是一个通用智能助手。
```

> 📚 部署细节见 [DEPLOY_ZEABUR.md](DEPLOY_ZEABUR.md)