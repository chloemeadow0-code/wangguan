import os
import json
import asyncio
import time
import hmac
import datetime
import requests

# ==========================================
# 全局连接（延迟初始化，避免启动时无 Supabase 就崩）
# ==========================================
_supabase_client = None
_mem0_client = None
_system_logs_buffer = []   # 简易日志缓存（用于 /api/logs）
_MAX_LOGS = 200


def _log(msg: str):
    """统一的日志打印 + 内存缓存（供 /api/logs 查询）"""
    line = f"[{datetime.datetime.utcnow().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    _system_logs_buffer.append(line)
    if len(_system_logs_buffer) > _MAX_LOGS:
        del _system_logs_buffer[: len(_system_logs_buffer) - _MAX_LOGS]


def _get_supabase():
    """惰性初始化 Supabase 客户端，没配 URL/KEY 就返回 None"""
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_KEY", "").strip()
    if not url or not key:
        return None
    try:
        from supabase import create_client
        _supabase_client = create_client(url, key)
        _log(f"✅ Supabase 已连接: {url[:30]}...")
    except Exception as e:
        _log(f"❌ Supabase 连接失败: {e}")
        _supabase_client = None
    return _supabase_client


def _get_mem0():
    """惰性初始化 Mem0 客户端，没配 API_KEY 就返回 None"""
    global _mem0_client
    if _mem0_client is not None:
        return _mem0_client
    api_key = os.environ.get("MEM0_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from mem0 import Memory
        _mem0_client = Memory.from_config({"vector_store": {"provider": "mem0", "config": {"api_key": api_key}}}) \
            if False else Memory()   # 兼容 mem0ai 新旧版本
        _log("✅ Mem0 已初始化")
    except Exception as e:
        _log(f"❌ Mem0 初始化失败（将跳过向量记忆）: {e}")
        _mem0_client = None
    return _mem0_client


def _get_active_persona(sb):
    """🌟 读取当前激活的人设（personas 表 is_active=true）。
    返回 {id, display_name, system_prompt} 或 None。
    优雅降级：表/字段不存在时返回 None（不影响主流程）。"""
    if not sb:
        return None
    try:
        res = sb.table("personas").select("id, display_name, system_prompt") \
            .eq("is_active", True).limit(1).execute()
        if res and res.data:
            return res.data[0]
    except Exception:
        pass
    return None


class HostFixMiddleware:
    """ASGI 中间件：路由分发 + OpenAI 兼容代理 + MCP 下游转发"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # 非 HTTP 类型直接透传给下游
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # ---------- 根路径：返回占位（或前端 index.html）----------
        if scope["path"] == "/":
            html = "<h1>🚪 MCP Gateway</h1><p>Endpoints: <code>/health</code> <code>/sse</code> <code>/v1/chat/completions</code> <code>/panel</code></p>"
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/html; charset=utf-8")]})
            await send({"type": "http.response.body", "body": html.encode("utf-8")})
            return

        # ---------- 健康检查 ----------
        if scope["path"] == "/health":
            await _send_json_resp(send, 200, {"status": "ok", "service": "generic-mcp-gateway"})
            return

        # ---------- 🆕 橘瓣记忆库 HTML 面板 (/panel) ----------
        # 仅返回 HTML 外壳，不含敏感信息；数据通过 /api/panel/*（需鉴权）获取
        if scope["path"] == "/panel":
            try:
                import panel
                await panel.handle_panel_html(send)
            except Exception as e:
                _log(f"❌ 面板加载失败: {e}")
                await _send_json_resp(send, 500, {"error": str(e)})
            return

        # ---------- 🆕 OpenAI 兼容代理 (/v1/*) ----------
        if scope["path"].startswith("/v1/"):
            if scope["method"] == "OPTIONS":
                await _send_cors_preflight(send)
                return
            await self._handle_openai_proxy(scope, receive, send)
            return

        # 🛡️ 全局 API 安全拦截 (涵盖 /api/* /sse /messages)
        if (scope["path"].startswith("/api/") or scope["path"].startswith("/sse") or scope["path"].startswith("/messages")) and scope["method"] != "OPTIONS":
            if not await _check_api_secret(scope, send):
                return

        # ---------- CORS 预检 ----------
        if scope["method"] == "OPTIONS":
            await _send_cors_preflight(send)
            return

        # ---------- 运行日志接口 ----------
        if scope["path"] == "/api/logs":
            await self._handle_logs(send)
            return

        # ---------- 🆕 橘瓣记忆库 Panel API (/api/panel/*) ----------
        if scope["path"].startswith("/api/panel/"):
            try:
                import panel
                await panel.handle_panel_request(scope, receive, send)
            except Exception as e:
                _log(f"❌ Panel API 异常: {e}")
                await _send_json_resp(send, 500, {"error": str(e)})
            return

        # ---------- 兜底其余请求 (Host Fix → 下游 MCP) ----------
        headers = dict(scope.get("headers", []))
        headers[b"host"] = b"localhost:8000"
        scope["headers"] = list(headers.items())
        await self.app(scope, receive, send)

    # ------------------------------------------
    # 🧠 OpenAI 兼容代理（核心）
    # ------------------------------------------

    async def _handle_openai_proxy(self, scope, receive, send):
        """把 /v1/* 请求转发到上游模型。配了 Supabase 时自动开启智能体模式。"""
        path = scope["path"]
        method = scope["method"]

        # 🛡️ 强制鉴权（防止 LLM 额度被白嫖 / 账号被滥用）
        if not await _check_api_secret(scope, send):
            return

        # ---- /v1/models ----
        if path == "/v1/models" and method == "GET":
            default_model = os.environ.get("OPENAI_MODEL_NAME", "gpt-3.5-turbo")
            models = [{"id": default_model, "object": "model", "created": int(time.time()), "owned_by": "mcp-gateway"}]
            for prefix in ("CHAT_", "SILICON1_", "VISION_"):
                mn = os.environ.get(f"{prefix}MODEL_NAME", "").strip()
                if mn and mn != default_model:
                    models.append({"id": mn, "object": "model", "created": int(time.time()), "owned_by": "mcp-gateway"})
            await _send_json_resp(send, 200, {"object": "list", "data": models})
            return

        # ---- /v1/chat/completions ----
        if path == "/v1/chat/completions" and method == "POST":
            await self._handle_chat(scope, receive, send)
            return

        await _send_json_resp(send, 404, {"error": {"message": f"Unknown endpoint: {path}"}})

    async def _handle_chat(self, scope, receive, send):
        """聊天核心：透传 + 可选上文注入 + 流式收集双写"""
        # 读请求体
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body", False):
                break

        try:
            req_data = json.loads(body.decode("utf-8"))
        except Exception:
            await _send_json_resp(send, 400, {"error": {"message": "Invalid JSON body"}})
            return

        # 解析上游配置（统一用 OPENAI_*，兼容旧 CHAT_*）
        upstream_base = os.environ.get("OPENAI_BASE_URL", os.environ.get("CHAT_BASE_URL", os.environ.get("DEFAULT_BASE_URL", ""))).strip()
        upstream_key = os.environ.get("OPENAI_API_KEY", os.environ.get("CHAT_API_KEY", os.environ.get("DEFAULT_API_KEY", ""))).strip()
        default_model = os.environ.get("OPENAI_MODEL_NAME", os.environ.get("CHAT_MODEL_NAME", os.environ.get("DEFAULT_MODEL_NAME", "gpt-3.5-turbo")))

        if not upstream_key:
            await _send_json_resp(send, 500, {"error": {"message": "Server 未配置 OPENAI_API_KEY"}})
            return

        if not req_data.get("model"):
            req_data["model"] = default_model

        # 构造上游 URL（兼容用户填或不填 /v1 后缀）
        base = upstream_base.rstrip("/") or "https://api.openai.com/v1"
        if not base.endswith("/v1"):
            upstream_url = f"{base}/v1/chat/completions"
        else:
            upstream_url = f"{base}/chat/completions"

        # ==========================================
        # 🧠 智能体模式：注入上文/人设/记忆（仅当配了 Supabase 时启用）
        # ==========================================
        sb = _get_supabase()
        user_msg = ""
        for m in reversed(req_data.get("messages", [])):
            if m.get("role") == "user":
                user_msg = str(m.get("content", ""))
                break

        if sb and user_msg:
            try:
                await self._inject_context(req_data, sb, user_msg)
            except Exception as e:
                _log(f"⚠️ 上文注入失败（已降级为透传）: {e}")
        else:
            if sb:
                _log("➡️ [透传] 无 user 消息或无 Supabase，直接转发")

        # 强制流式（便于边透传边收集）
        req_data["stream"] = True
        if req_data.get("tools"):
            req_data["tool_choice"] = "auto"

        # 构造请求头（修复 python-requests UA 被拦截 + 透传客户端头）
        client_headers = {k.decode("utf-8", "ignore").lower(): v.decode("utf-8", "ignore") for k, v in scope.get("headers", [])}
        client_ua = client_headers.get("user-agent", "")
        fwd_headers = {
            "Authorization": f"Bearer {upstream_key}",
            "Content-Type": "application/json",
            "User-Agent": client_ua or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": client_headers.get("accept", "application/json"),
        }
        for h in ("accept-language", "x-requested-with"):
            if h in client_headers:
                fwd_headers[h] = client_headers[h]

        _log(f"➡️ [转发] POST {upstream_url} | model={req_data.get('model')}")

        # 启动响应流（通知客户端开始接收 SSE）
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/event-stream; charset=utf-8"),
                (b"cache-control", b"no-cache"),
                (b"connection", b"keep-alive"),
                (b"access-control-allow-origin", _allowed_cors_origin()),
            ],
        })

        # 后台线程：读取上游流，喂给队列
        import queue
        import threading
        q = queue.Queue()

        def _stream_forward():
            try:
                fwd_headers["Connection"] = "keep-alive"
                with requests.post(upstream_url, headers=fwd_headers, json=req_data, stream=True, timeout=300) as resp:
                    if resp.status_code != 200:
                        _log(f"❌ 上游返回 HTTP {resp.status_code}: {resp.text[:300]}")
                        q.put({"error": f"上游服务返回 HTTP {resp.status_code}（详情见服务端日志）"})
                        q.put(None)
                        return
                    for line in resp.iter_lines():
                        if line:
                            q.put(line.decode("utf-8"))
                q.put(None)
            except Exception as e:
                q.put({"error": str(e)})
                q.put(None)

        threading.Thread(target=_stream_forward, daemon=True).start()

        collected_content = ""
        collected_reasoning = ""
        tool_calls_dict = {}

        # 主循环：透传 + 收集
        while True:
            chunk = await asyncio.to_thread(q.get)
            if chunk is None:
                break

            if isinstance(chunk, dict) and "error" in chunk:
                _log(f"❌ 上游流式报错: {chunk['error']}")
                err_chunk = {
                    "id": "chatcmpl-error",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": req_data.get("model"),
                    "choices": [{"index": 0, "delta": {"content": f"\n\n[上游错误] {chunk['error']}"}, "finish_reason": "stop"}],
                }
                await send({"type": "http.response.body", "body": f"data: {json.dumps(err_chunk, ensure_ascii=False)}\n\n".encode("utf-8"), "more_body": True})
                continue

            await send({"type": "http.response.body", "body": (chunk + "\n\n").encode("utf-8"), "more_body": True})

            if chunk.startswith("data: ") and chunk != "data: [DONE]":
                try:
                    dj = json.loads(chunk[6:])
                    if dj.get("choices"):
                        delta = dj["choices"][0].get("delta", {})
                        if delta.get("content"):
                            collected_content += delta["content"]
                        if delta.get("reasoning_content"):
                            collected_reasoning += delta["reasoning_content"]
                        if delta.get("tool_calls"):
                            for tc in delta["tool_calls"]:
                                idx = tc.get("index", 0)
                                if idx not in tool_calls_dict:
                                    tool_calls_dict[idx] = tc
                                else:
                                    if tc.get("function", {}).get("arguments"):
                                        tool_calls_dict[idx]["function"].setdefault("arguments", "")
                                        tool_calls_dict[idx]["function"]["arguments"] += tc["function"]["arguments"]
                except Exception:
                    pass

        # 结束响应
        await send({"type": "http.response.body", "body": b"", "more_body": False})

        # ==========================================
        # 💾 异步双写：把本轮对话存到 Supabase + Mem0（不阻塞响应）
        # ==========================================
        if sb and user_msg and (collected_content or tool_calls_dict):
            asyncio.create_task(self._save_conversation(sb, user_msg, collected_content, collected_reasoning, tool_calls_dict))

    async def _inject_context(self, req_data, sb, current_query):
        """
        智能体上下文注入（全部变量化，无硬编码）：
        - 🌟 当前激活人设（personas 表，优先级最高）
        - 系统当前状态（北京时间 / 沉默时长）
        - 用户画像（user_facts 表）
        - 阶段总结（memories 表 tags=Core_Cognition）
        - Mem0 向量记忆（可选）
        - 🌟 当前线路精华记忆（chat_messages 表，按 assistant_id 隔离）
        - 最近 N 条对话历史（从 chat_archive 读取，按 assistant_id 隔离）
        """
        ai_name = os.environ.get("AI_NAME", "助手")
        user_name = os.environ.get("USER_NAME", "用户")
        user_id = os.environ.get("USER_ID", "default")
        persona = os.environ.get("AI_PERSONA", "").strip()

        # 🌟 读取当前激活人设（优先于环境变量 AI_PERSONA）
        active_persona = await asyncio.to_thread(lambda: _get_active_persona(sb))
        oc_assistant_id = "默认助手_技术线"   # 默认兜底
        if active_persona:
            oc_assistant_id = active_persona.get("id", oc_assistant_id)
            sp = (active_persona.get("system_prompt") or "").strip()
            if sp:
                persona = sp   # 激活人设的 system_prompt 覆盖环境变量
            _log(f"🌟 [人设] 当前激活: {active_persona.get('display_name', oc_assistant_id)}")
        now_bj = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
        time_str = now_bj.strftime("%Y-%m-%d %H:%M")

        # 沉默时长（从 chat_archive 最近一条记录到现在的小时差，优雅降级）
        silence_hours = 0
        try:
            res = await asyncio.to_thread(lambda: sb.table("chat_archive").select("created_at").eq("assistant_id", oc_assistant_id).order("created_at", desc=True).limit(1).execute())
            if res and res.data:
                last = res.data[0].get("created_at", "")
                if last:
                    try:
                        last_dt = datetime.datetime.strptime(last[:19], "%Y-%m-%dT%H:%M:%S")
                        silence_hours = max(0, round((now_bj - (last_dt + datetime.timedelta(hours=8))).total_seconds() / 3600, 1))
                    except Exception:
                        pass
        except Exception:
            pass

        # 阶段总结
        core_summaries = "无长期记忆"
        try:
            sr = await asyncio.to_thread(lambda: sb.table("memories").select("content").eq("tags", "Core_Cognition").order("created_at", desc=True).limit(3).execute())
            if sr and sr.data:
                core_summaries = "\n".join([f"- {s['content']}" for s in sr.data])
        except Exception:
            pass

        # 用户画像
        user_prof = "暂无"
        try:
            pr = await asyncio.to_thread(lambda: sb.table("user_facts").select("key, value").neq("key", "sys_config").neq("key", "llm_settings").execute())
            if pr and pr.data:
                user_prof = "\n".join([f"- {r['key']}: {str(r['value'])[:200]}" for r in pr.data[:30]])
        except Exception:
            pass

        # 🌟 当前线路精华记忆（chat_messages 表，按 assistant_id 隔离）
        # 类别权重：关系/设定/雷点 > 喜好 > 剧情/档案
        chat_facts = ""
        try:
            # 关键词检索当前查询在当前线路的记忆（top 5）
            safe_kw = current_query.strip()[:50].replace("%", "\\%").replace("_", "\\_")
            def _fetch_facts():
                # 关键词命中
                kw_q = sb.table("chat_messages").select("content, category").eq("assistant_id", oc_assistant_id)
                if safe_kw:
                    kw_q = kw_q.ilike("content", f"%{safe_kw}%")
                kw_q = kw_q.order("created_at", desc=True).limit(5)
                kw_res = kw_q.execute()
                # 兜底：如果关键词没命中，拉该线路最新的核心记忆
                if not (kw_res and kw_res.data):
                    return sb.table("chat_messages").select("content, category") \
                        .eq("assistant_id", oc_assistant_id) \
                        .in_("category", ["关系", "设定", "雷点", "喜好"]) \
                        .order("created_at", desc=True).limit(15).execute()
                return kw_res
            fr = await asyncio.to_thread(_fetch_facts)
            if fr and fr.data:
                chat_facts = "\n".join([f"- {r['content']}" for r in fr.data])
        except Exception as e:
            _log(f"⚠️ 拉取精华记忆失败（跳过）: {e}")

        # Mem0 向量记忆（可选）
        mem0_context = "无相关深层记忆"
        mc = _get_mem0()
        if mc and current_query.strip():
            try:
                def _s():
                    return mc.search(query=str(current_query), user_id=user_id, filters={"user_id": user_id}, limit=5)
                mr = await asyncio.to_thread(_s)
                if mr:
                    rl = mr.get("results", mr) if isinstance(mr, dict) else mr
                    if isinstance(rl, list) and rl:
                        mem0_context = "\n".join([f"- {m.get('memory', str(m))}" if isinstance(m, dict) else f"- {str(m)}" for m in rl])
            except Exception as e:
                _log(f"Mem0 检索失败（跳过）: {e}")

        # 📝 方案A：对话历史从 chat_archive 读取（按 assistant_id 隔离）
        history_msgs = []
        try:
            hr = await asyncio.to_thread(lambda: sb.table("chat_archive").select("role, content, created_at").eq("assistant_id", oc_assistant_id).order("created_at", desc=True).limit(20).execute())
            if hr and hr.data:
                rows = list(reversed(hr.data))[-10:]
                for row in rows:
                    role = row.get("role", "user")
                    content = str(row.get("content", "")).strip()
                    if not content:
                        continue
                    # 跳过工具调用系统记录
                    if content.startswith("[系统记录："):
                        continue
                    history_role = "user" if role == "user" else "assistant"
                    history_msgs.append({"role": history_role, "content": content[:500]})
                # 合并相邻同 role
                merged = []
                for m in history_msgs:
                    if merged and merged[-1]["role"] == m["role"]:
                        merged[-1]["content"] += "\n" + m["content"]
                    else:
                        merged.append(m)
                history_msgs = merged
                while history_msgs and history_msgs[0]["role"] != "user":
                    history_msgs.pop(0)
        except Exception as e:
            _log(f"拉取上文失败（跳过）: {e}")

        # 拼装 system prompt
        facts_block = chat_facts if chat_facts else "暂无"
        status_inject = (
            f"\n\n[系统当前状态]\n当前时间:{time_str}(北京时间),距离上次聊天:{silence_hours}h。\n"
            f"【{user_name}的核心画像】:\n{user_prof}\n\n"
            f"--- 以下为调取的历史背景记忆（请注意这是过去的事，不是现在正在聊的内容） ---\n"
            f"【当前线路({oc_assistant_id})核心记忆】:\n{facts_block}\n"
            f"【深层关联记忆】:\n{mem0_context}\n"
            f"【近3次阶段总结】:\n{core_summaries}\n"
            f"------------------------------------------------\n"
        )
        if persona:
            status_inject = f"{persona}\n{status_inject}"

        # 注入到 messages：已有 system 就追加，没有就插入
        has_system = False
        for m in req_data.get("messages", []):
            if m.get("role") == "system":
                m["content"] = str(m.get("content", "")) + status_inject
                has_system = True
                break
        if not has_system and req_data.get("messages"):
            req_data["messages"].insert(0, {"role": "system", "content": status_inject.strip()})

        # 清理：去掉末尾的 assistant 尾巴（防止前端误带）
        while req_data.get("messages") and req_data["messages"][-1].get("role") == "assistant":
            req_data["messages"].pop()

        # 把上文历史插到 system 之后、user 之前
        if history_msgs:
            sys_idx = 0
            for i, m in enumerate(req_data["messages"]):
                if m.get("role") == "system":
                    sys_idx = i + 1
                    break
            for j, hm in enumerate(history_msgs):
                req_data["messages"].insert(sys_idx + j, hm)

        _log(f"🧠 [智能体] 注入完成：画像{len(user_prof)}字 + 总结{len(core_summaries)}字 + Mem0{len(mem0_context)}字 + 上文{len(history_msgs)}条")

    async def _save_conversation(self, sb, user_msg, ai_msg, reasoning, tool_calls):
        """异步把本轮对话存到橘瓣 chat_archive + Mem0 + 自动提炼（方案A：不再写 memories 流水）"""
        user_id = os.environ.get("USER_ID", "default")

        final_save_text = ai_msg
        if reasoning:
            final_save_text = f"<think>\n{reasoning}\n</think>\n\n{final_save_text}"
        if not final_save_text and tool_calls:
            tc_names = [tc.get("function", {}).get("name", "unknown") for tc in tool_calls.values()]
            final_save_text = f"[系统记录：调用了工具 {', '.join(tc_names)}]"

        # 📝 方案A：对话原文不再写入 memories 表，统一由 chat_archive 归档。
        #    memories 表仅保留日记 / Core_Cognition 总结 / 心跳记录。

        # 1. 写入 Mem0（可选）
        mc = _get_mem0()
        if mc and user_msg:
            try:
                def _add_m():
                    mc.add([
                        {"role": "user", "content": user_msg},
                        {"role": "assistant", "content": final_save_text},
                    ], user_id=user_id)
                await asyncio.to_thread(_add_m)
                _log("🧠 Mem0 已写入")
            except Exception as e:
                _log(f"Mem0 写入失败: {e}")

        # 2. 🍊 橘瓣记忆库自动归档：把本轮对话原文写入 chat_archive
        #    🌟 按当前激活人设/线路隔离（优先于环境变量）
        try:
            active_persona = _get_active_persona(sb)
            oc_assistant_id = os.environ.get("ORANGECHAT_ASSISTANT_ID", "").strip()
            if active_persona:
                oc_assistant_id = active_persona.get("id", oc_assistant_id)
            if not oc_assistant_id:
                oc_assistant_id = "默认助手_技术线"
            now_iso = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).isoformat()

            def _archive_user():
                sb.table("chat_archive").insert({
                    "assistant_id": oc_assistant_id,
                    "conversation_id": "",
                    "role": "user",
                    "content": user_msg[:4000],
                    "category": "archive",
                    "created_at": now_iso,
                }).execute()

            def _archive_ai():
                sb.table("chat_archive").insert({
                    "assistant_id": oc_assistant_id,
                    "conversation_id": "",
                    "role": "assistant",
                    "content": final_save_text[:4000],
                    "category": "archive",
                    "created_at": now_iso,
                }).execute()

            await asyncio.to_thread(_archive_user)
            if final_save_text:
                await asyncio.to_thread(_archive_ai)
            _log(f"🍊 [归档] 已写入 chat_archive [{oc_assistant_id}]: user({len(user_msg)}字) + assistant({len(final_save_text)}字)")

            # 🆕 自动精华提炼：从本轮对话提取长期事实写入 chat_messages
            #    方案A：归档是橘瓣系统的唯一原文来源，提炼只依赖 chat_archive
            #    全程异步，失败只记日志，不影响对话
            try:
                from distill import run_distill
                conv_text = f"用户：{user_msg[:2000]}"
                if final_save_text:
                    conv_text += f"\n助手：{final_save_text[:2000]}"
                distill_result = await run_distill(oc_assistant_id, conv_text)
                if distill_result.get("ok"):
                    s = distill_result.get("stats", {})
                    _log(f"🧠 [提炼] [{oc_assistant_id}] 新增{s.get('new',0)} 更新{s.get('updated',0)} 冲突{s.get('conflict',0)} 跳过{s.get('skipped',0)}")
            except Exception as e:
                _log(f"⚠️ 自动精华提炼失败（不影响主流程）: {e}")
        except Exception as e:
            _log(f"⚠️ 橘瓣自动归档失败（不影响主流程）: {e}")

        # 3. 🧠 异步触发统一对话总结（不阻塞响应）
        try:
            await _check_and_summarize_all(sb)
        except Exception as e:
            _log(f"⚠️ 触发对话总结失败（不影响主流程）: {e}")

    async def _check_and_summarize_all(self, sb):
        """🧠 统一对话总结机制（方案A：从 chat_archive 读取，按当前激活人设隔离）
        当累计达到阈值时，自动触发大模型总结并归档，生成阶段总结存入 Core_Cognition。
        """
        try:
            threshold = int(os.environ.get("SUMMARY_THRESHOLD", "30"))
            ai_name = os.environ.get("AI_NAME", "助手")
            user_name = os.environ.get("USER_NAME", "用户")

            _MAX_MSG_CHARS = 500
            _MAX_PROMPT_CHARS = 80000

            def _check():
                if not sb:
                    return
                # 📝 方案A：从 chat_archive 读对话原文（按当前激活人设隔离）
                active = _get_active_persona(sb)
                summarizer_id = active.get("id", "默认助手_技术线") if active else os.environ.get("ORANGECHAT_ASSISTANT_ID", "默认助手_技术线")
                all_chats = sb.table("chat_archive").select("id, role, content, created_at").eq("assistant_id", summarizer_id).order("created_at").execute()
                if all_chats and all_chats.data and len(all_chats.data) >= threshold:
                    items_to_summarize = all_chats.data[-threshold:]
                    all_ids_to_archive = [item['id'] for item in all_chats.data]

                    _log(f"📦 [{summarizer_id}] 归档累计满 {len(all_chats.data)} 条，正在触发总结（取最新{threshold}条，归档全部）...")

                    chat_parts = []
                    total_chars = 0
                    for item in items_to_summarize:
                        truncated_content = item['content'][:_MAX_MSG_CHARS]
                        part = f"[{item.get('role', 'user')}] {truncated_content}"
                        if total_chars + len(part) > _MAX_PROMPT_CHARS:
                            _log(f"⚠️ 总结prompt已达 {_MAX_PROMPT_CHARS} 字符上限，截断剩余记录")
                            break
                        chat_parts.append(part)
                        total_chars += len(part)

                    chat_text = "\n".join(chat_parts)
                    prompt = (
                        f"以下是我们最近在网页的{len(chat_parts)}条对话记录：\n{chat_text}\n\n"
                        f"请你以{ai_name}(我)的第一人称视角，提取核心要点，精炼地总结一下我们最近聊了什么、发生了什么。"
                        f"⚠️严重警告：1. 必须严格区分清楚'{ai_name}(我)'做了什么，以及'{user_name}'做了什么，绝对不能把两人的话搞混！"
                        f"2. 绝对禁止以'今天'开头！请扔掉日记格式，直接开门见山地叙述事情"
                        f"（例如直接说：'{user_name}最近在忙...' 或 '我们刚才聊了...'）。"
                    )
                    client = _get_openai_client()
                    if client:
                        try:
                            model_name = os.environ.get("CHAT_MODEL_NAME", os.environ.get("OPENAI_MODEL_NAME", "gpt-3.5-turbo"))
                            summary = client.chat.completions.create(
                                model=model_name,
                                messages=[{"role": "user", "content": prompt}],
                                temperature=0.7
                            ).choices[0].message.content.strip()
                            if summary:
                                sb.table("memories").insert({
                                    "title": "📚 全渠道阶段总结", "content": summary,
                                    "category": "记事", "mood": "温情", "tags": "Core_Cognition"
                                }).execute()
                            _log(f"✅ 对话总结完成")
                        except Exception as llm_err:
                            _log(f"❌ 总结LLM调用失败: {llm_err}")
                    else:
                        _log("⚠️ 未配置 CHAT_API_KEY，跳过总结")
            await asyncio.to_thread(_check)
        except Exception as e:
            _log(f"❌ 统一对话总结失败: {e}")

    # ------------------------------------------
    # 管理接口
    # ------------------------------------------

    async def _handle_logs(self, send):
        try:
            await _send_json_resp(send, 200, {"logs": "\n".join(_system_logs_buffer[-100:])})
        except Exception as e:
            await _send_json_resp(send, 500, {"error": str(e)})


# ==========================================
# 辅助函数
# ==========================================

def _allowed_cors_origin() -> bytes:
    """读取 CORS_ALLOWED_ORIGINS 返回允许的 Origin（逗号分隔，取首个）。
    未配置则返回 * 兼容旧部署；生产环境建议配置为前端域名以收紧跨域策略。"""
    allowed = os.environ.get("CORS_ALLOWED_ORIGINS", "").strip()
    if not allowed:
        return b"*"
    first = allowed.split(",")[0].strip()
    return first.encode("utf-8") if first else b"*"


def _get_openai_client():
    """惰性创建 OpenAI 兼容客户端（用 CHAT_* 优先，回退 OPENAI_*）。"""
    key = os.environ.get("CHAT_API_KEY", os.environ.get("OPENAI_API_KEY", "")).strip()
    if not key:
        return None
    try:
        from openai import OpenAI
        base_url = os.environ.get("CHAT_BASE_URL", os.environ.get("OPENAI_BASE_URL", "")).strip() or None
        return OpenAI(api_key=key, base_url=base_url)
    except Exception as e:
        _log(f"❌ 创建 OpenAI 客户端失败: {e}")
        return None


async def _check_api_secret(scope, send):
    """校验 API_SECRET（🛡️ 强制鉴权）。返回 True=通过，False=已拒绝（已发送 401/403）。
    安全策略：未配置 API_SECRET 时【拒绝】所有受保护接口，避免误开导致裸奔。"""
    api_secret = os.environ.get("API_SECRET", "").strip()
    if not api_secret:
        _log("🚫 [安全] API_SECRET 未配置，拒绝访问受保护接口（请在环境变量中设置 API_SECRET）")
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json"),
                                (b"access-control-allow-origin", _allowed_cors_origin())]})
        await send({"type": "http.response.body",
                    "body": b'{"error":"Forbidden: API_SECRET is not configured on the server"}'})
        return False
    headers_dict = {k.decode("utf-8").lower(): v.decode("utf-8") for k, v in scope.get("headers", [])}
    auth_token = headers_dict.get("authorization", "").replace("Bearer ", "").replace("bearer ", "").strip()
    x_api_key = headers_dict.get("x-api-key", "").strip()
    # 常量时间比较，防止时序攻击逐字节爆破密钥
    ok = (auth_token and hmac.compare_digest(auth_token, api_secret)) or \
         (x_api_key and hmac.compare_digest(x_api_key, api_secret))
    if not ok:
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"),
                                (b"access-control-allow-origin", _allowed_cors_origin())]})
        await send({"type": "http.response.body", "body": b'{"error":"Unauthorized: Missing or invalid API key"}'})
        return False
    return True


async def _send_json_resp(send, status: int, data: dict):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"access-control-allow-origin", _allowed_cors_origin()),
            (b"access-control-allow-methods", b"GET, POST, OPTIONS"),
            (b"access-control-allow-headers", b"Content-Type, Authorization, x-api-key"),
        ]
    })
    await send({"type": "http.response.body", "body": body})


async def _send_cors_preflight(send):
    await send({
        "type": "http.response.start",
        "status": 204,
        "headers": [
            (b"access-control-allow-origin", _allowed_cors_origin()),
            (b"access-control-allow-methods", b"GET, POST, PATCH, DELETE, OPTIONS"),
            (b"access-control-allow-headers", b"Content-Type, Authorization, x-api-key"),
            (b"access-control-max-age", b"86400"),
        ]
    })
    await send({"type": "http.response.body", "body": b""})