# 📋 环境变量清单

橘瓣记忆系统**代码中实际使用的**所有环境变量。

> 已删除代码中不存在的废弃变量（`CHAT_TAG` / `GATEWAY_HOST` / `RESTART_WEBHOOK_URL` / `LOG_FILE` / `MUTE_*` / `ZEABUR_API_KEY`）。
> 已删除橘瓣记忆系统不用的旧模板变量（`SILICON1_*` 硅基流动 / `VISION_*` 视觉模型）。

---

## 🔴 必填（缺一不可）

| 变量名 | 说明 |
|--------|------|
| `CHAT_API_KEY` | 主对话 LLM 的 API Key（聊天 + 提炼 + 总结都用它） |
| `CHAT_MODEL_NAME` | 模型名称（如 `abab6.5s-chat`、`deepseek-chat` 等） |
| `CHAT_BASE_URL` | 第三方服务地址（OpenAI 官方可不填，第三方如 `https://api.deepseek.com/v1`） |
| `API_SECRET` | 面板和 `/api/*` 接口的鉴权密钥（如 `lyy123`） |
| `SUPABASE_URL` | Supabase 项目 URL |
| `SUPABASE_KEY` | **service_role key**（不是 anon！）。Settings → API → `service_role` |

---

## 🟡 推荐

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `USER_NAME` | `用户` | 用户称呼（如 `函函`） |
| `AI_NAME` | `助手` | AI 角色称呼（如 `小橘`） |
| `AI_PERSONA` | 空 | 人设文本。**被面板激活人设的 `system_prompt` 覆盖。** |

---

## 🟢 橘瓣记忆系统

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `ORANGECHAT_ASSISTANT_ID` | 空 | 归档兜底人设 ID，无激活人设时用，留空默认 `默认助手_技术线` |
| `SUMMARY_THRESHOLD` | `30` | 归档累计条数达此值时触发阶段总结 |
| `USER_ID` | `default` | Mem0 向量记忆隔离 ID |

---

## 🟢 MCP 工具（按需开启）

| 变量名 | 默认值 | 用途 |
|--------|--------|------|
| `MEM0_API_KEY` | 空 | Mem0 长期语义记忆 |
| `PINECONE_API_KEY` | 空 | Pinecone 向量库（Mem0 兜底双写） |
| `PINECONE_INDEX_NAME` | `notion-brain-v2` | Pinecone 索引名 |
| `DOUBAO_API_KEY` | 空 | 向量嵌入（第五阶段 bge-m3 检索用） |
| `DOUBAO_EMBEDDING_EP` | 空 | 嵌入模型名（如 `BAAI/bge-m3`） |
| `TAVILY_API_KEY` | 空 | 高质量网页搜索（不配用 DDG 兜底） |
| `AMAP_API_KEY` | 空 | 高德地图 / GPS 定位 / 天气 |
| `HCTI_API_ID` | 空 | HTML 转图片 |
| `HCTI_API_KEY` | 空 | HTML 转图片 |
| `RESEND_API_KEY` | 空 | 邮件发送 |
| `MY_EMAIL` | 空 | 收件邮箱（兼容 `ADMIN_EMAIL`） |

---

## 🟢 后台心跳

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `HEARTBEAT_INTERVAL` | `7200` | 主动问候间隔（秒） |
| `SUMMARIZE_INTERVAL` | `1800` | 消息总结间隔（秒） |
| `DIARY_TIME` | `03:00` | 每日日记生成时间 |
| `SYNC_KEYS` | 空 | 额外热同步的变量键，逗号分隔 |

---

## 最小配置示例

```env
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

> 📚 部署见 [DEPLOY_ZEABUR.md](DEPLOY_ZEABUR.md)