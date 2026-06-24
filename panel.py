"""
OrangeChat / 橘瓣记忆库 面板后端 (Panel API)
=================================================
提供 6 个 REST 接口，供 HTML 面板访问 Supabase 数据：
- GET    /api/panel/personas   可见人设列表
- GET    /api/panel/records    分页查询 chat_messages / chat_archive
- POST   /api/panel/record     新增
- PATCH  /api/panel/record     编辑
- DELETE /api/panel/record     删除
- GET    /panel                返回 HTML 面板

设计原则：
- 走 x-api-key 鉴权（由 gateway.py 在调用前完成）
- 后端持有 SUPABASE_KEY，前端不暴露
- 按 assistant_id 隔离记忆
- content 必须以 [标签] 开头（chat_messages）
"""

import os
import json
import asyncio
import datetime

# 复用 gateway 的 supabase 客户端与日志
from gateway import _get_supabase, _log, _send_json_resp, _send_cors_preflight


# ==========================================
# 常量
# ==========================================
# chat_messages 允许的分类
ALLOWED_CATEGORIES = ["关系", "剧情", "喜好", "雷点", "设定", "档案"]

# chat_messages content 必须以这些标签之一开头
ALLOWED_TAGS = [f"[{c}]" for c in ALLOWED_CATEGORIES]

# chat_archive 允许的 role
ALLOWED_ARCHIVE_ROLES = ["user", "assistant", "system"]

# HTML 面板文件路径（同目录）
PANEL_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel.html")


# ==========================================
# 辅助函数
# ==========================================
async def _read_body(receive) -> bytes:
    """读取完整 HTTP 请求体"""
    body = b""
    while True:
        msg = await receive()
        body += msg.get("body", b"")
        if not msg.get("more_body", False):
            break
    return body


def _parse_query(scope) -> dict:
    """解析 query string 成字典"""
    qs = scope.get("query_string", b"").decode("utf-8", "ignore")
    result = {}
    if not qs:
        return result
    for pair in qs.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            result[k] = v
        elif pair:
            result[pair] = ""
    return result


def _validate_chat_message_content(content: str, category: str = "") -> str:
    """
    校验 chat_messages 的 content：
    - 必须以 [标签] 开头
    - 如果没带标签但传了 category，自动补 [category]
    返回校验后的 content，或抛 ValueError。
    """
    if not content or not content.strip():
        raise ValueError("content 不能为空")

    content = content.strip()
    has_tag = any(content.startswith(tag) for tag in ALLOWED_TAGS)

    if not has_tag:
        # 尝试用传入的 category 补标签
        if category and category in ALLOWED_CATEGORIES:
            content = f"[{category}] {content}"
        else:
            raise ValueError(
                f"content 必须以标签开头，如 [关系]/[剧情]/[喜好]/[雷点]/[设定]/[档案]"
            )
    return content


def _resolve_display_name(assistant_id: str, sb) -> str:
    """把 assistant_id 映射成中文展示名（查 persona_map / personas）"""
    try:
        # 优先 persona_map
        r = sb.table("persona_map").select("display_name").eq("assistant_id", assistant_id).limit(1).execute()
        if r and r.data:
            return r.data[0].get("display_name", assistant_id)
        # 再查 personas
        r2 = sb.table("personas").select("display_name").eq("id", assistant_id).limit(1).execute()
        if r2 and r2.data:
            return r2.data[0].get("display_name", assistant_id)
    except Exception:
        pass
    return assistant_id


# ==========================================
# API 处理函数
# ==========================================

async def handle_get_personas(scope, send):
    """GET /api/panel/personas — 返回可见人设列表"""
    sb = _get_supabase()
    if not sb:
        await _send_json_resp(send, 500, {"error": "数据库未连接"})
        return
    # 表不存在时返回空列表（人设由用户在面板自行添加）
    DEFAULT_PERSONAS = []
    try:
        show_all = (_parse_query(scope).get("all", "") == "1")
        def _q():
            q = sb.table("personas").select("id, display_name, is_visible, sort_order, system_prompt, is_active")
            if not show_all:
                q = q.eq("is_visible", True)
            return q.order("sort_order").execute()
        res = await asyncio.to_thread(_q)
        personas = res.data if res and res.data else []
        # 如果表里没数据，用默认人设兜底
        if not personas:
            personas = DEFAULT_PERSONAS
        await _send_json_resp(send, 200, {"personas": personas})
    except Exception as e:
        err_str = str(e).lower()
        if "does not exist" in err_str or "42p01" in err_str:
            _log(f"⚠️ [panel] personas 表不存在，返回内置默认人设（请执行 migration_rebuild_all.sql 建表）")
            await _send_json_resp(send, 200, {
                "personas": DEFAULT_PERSONAS,
                "warning": "personas 表尚未创建，已返回默认人设。请执行 migration_rebuild_all.sql",
            })
        else:
            _log(f"❌ [panel] 查询人设失败: {e}")
            await _send_json_resp(send, 500, {"error": "服务器内部错误"})


async def handle_get_records(scope, send):
    """
    GET /api/panel/records
    参数: table=chat_messages|chat_archive, assistant_id, category, role,
          keyword, page=1, page_size=20
    """
    sb = _get_supabase()
    if not sb:
        await _send_json_resp(send, 500, {"error": "数据库未连接"})
        return

    q = _parse_query(scope)
    table = q.get("table", "chat_messages")
    if table not in ("chat_messages", "chat_archive"):
        await _send_json_resp(send, 400, {"error": "table 必须是 chat_messages 或 chat_archive"})
        return

    assistant_id = q.get("assistant_id", "").strip()
    category = q.get("category", "").strip()
    role = q.get("role", "").strip()
    keyword = q.get("keyword", "").strip()

    try:
        page = max(1, int(q.get("page", "1")))
    except ValueError:
        page = 1

    # 默认每页：精华 20，归档 5
    default_size = 5 if table == "chat_archive" else 20
    try:
        page_size = max(1, min(200, int(q.get("page_size", default_size))))
    except ValueError:
        page_size = default_size

    try:
        offset = (page - 1) * page_size

        def _query():
            # 先查总数
            count_q = sb.table(table).select("id", count="exact")
            data_q = sb.table(table).select("*")

            if assistant_id:
                count_q = count_q.eq("assistant_id", assistant_id)
                data_q = data_q.eq("assistant_id", assistant_id)
            if category and table == "chat_messages":
                count_q = count_q.eq("category", category)
                data_q = data_q.eq("category", category)
            if role and table == "chat_archive":
                count_q = count_q.eq("role", role)
                data_q = data_q.eq("role", role)
            if keyword:
                # ilike 模糊匹配（PostgreSQL，% 转义）
                safe_kw = keyword.replace("%", "\\%").replace("_", "\\_")
                count_q = count_q.ilike("content", f"%{safe_kw}%")
                data_q = data_q.ilike("content", f"%{safe_kw}%")

            count_res = count_q.execute()
            total = count_res.count if count_res and count_res.count is not None else 0

            data_res = data_q.order("created_at", desc=True) \
                .range(offset, offset + page_size - 1).execute()
            return total, (data_res.data if data_res and data_res.data else [])

        total, rows = await asyncio.to_thread(_query)
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0

        await _send_json_resp(send, 200, {
            "table": table,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "records": rows,
        })
    except Exception as e:
        err_str = str(e).lower()
        # 表不存在时优雅降级：返回空列表而不是报错
        if "does not exist" in err_str or "42p01" in err_str:
            _log(f"⚠️ [panel] 表 {table} 不存在，返回空列表（请先执行 migration_rebuild_all.sql 建表）")
            await _send_json_resp(send, 200, {
                "table": table,
                "page": page,
                "page_size": page_size,
                "total": 0,
                "total_pages": 0,
                "records": [],
                "warning": f"表 {table} 尚未创建，请在 Supabase SQL Editor 中执行 migration_rebuild_all.sql",
            })
        else:
            _log(f"❌ [panel] 查询记录失败: {e}")
            await _send_json_resp(send, 500, {"error": "服务器内部错误"})


def _ensure_persona_exists(sb, assistant_id):
    """如果人设不存在于 personas 表，自动创建一条可见人设"""
    if not assistant_id:
        return
    try:
        existing = sb.table("personas").select("id").eq("id", assistant_id).limit(1).execute()
        if existing and existing.data:
            return  # 已存在
        # 自动创建
        sb.table("personas").insert({
            "id": assistant_id,
            "display_name": assistant_id,
            "is_visible": True,
            "sort_order": 100,
        }).execute()
        _log(f"✨ [panel] 自动创建人设: {assistant_id}")
    except Exception as e:
        _log(f"⚠️ [panel] 自动创建人设失败({assistant_id}): {e}")


async def handle_post_record(scope, receive, send):
    """POST /api/panel/record — 新增一条记录"""
    sb = _get_supabase()
    if not sb:
        await _send_json_resp(send, 500, {"error": "数据库未连接"})
        return

    body = await _read_body(receive)
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        await _send_json_resp(send, 400, {"error": "无效的 JSON"})
        return

    table = data.get("table", "chat_messages")
    if table not in ("chat_messages", "chat_archive"):
        await _send_json_resp(send, 400, {"error": "table 必须是 chat_messages 或 chat_archive"})
        return

    assistant_id = str(data.get("assistant_id", "")).strip()
    content = str(data.get("content", "")).strip()
    conversation_id = str(data.get("conversation_id", "")).strip()
    category = str(data.get("category", "")).strip()

    if not assistant_id:
        await _send_json_resp(send, 400, {"error": "assistant_id 不能为空"})
        return
    if not content:
        await _send_json_resp(send, 400, {"error": "content 不能为空"})
        return

    row = {
        "assistant_id": assistant_id,
        "conversation_id": conversation_id,
        "content": content,
    }

    if table == "chat_messages":
        # 校验标签，自动补全 category
        try:
            content = _validate_chat_message_content(content, category)
        except ValueError as e:
            await _send_json_resp(send, 400, {"error": "服务器内部错误"})
            return
        row["content"] = content
        # 从标签提取 category（若未显式给）
        if not category:
            for tag_name in ALLOWED_CATEGORIES:
                if content.startswith(f"[{tag_name}]"):
                    category = tag_name
                    break
        row["category"] = category
        row["role"] = data.get("role", "assistant")
    else:
        # chat_archive
        role = str(data.get("role", "user")).strip()
        if role not in ALLOWED_ARCHIVE_ROLES:
            role = "user"
        row["role"] = role
        row["category"] = data.get("category", "archive")

    try:
        await asyncio.to_thread(lambda: _ensure_persona_exists(sb, assistant_id))
        def _insert():
            return sb.table(table).insert(row).execute()
        res = await asyncio.to_thread(_insert)
        created = res.data[0] if res and res.data else row
        _log(f"✅ [panel] 新增 {table}: {assistant_id} / {content[:40]}")
        await _send_json_resp(send, 200, {"ok": True, "record": created})
    except Exception as e:
        _log(f"❌ [panel] 新增失败: {e}")
        await _send_json_resp(send, 500, {"error": "服务器内部错误"})


async def handle_patch_record(scope, receive, send):
    """PATCH /api/panel/record — 编辑一条记录"""
    sb = _get_supabase()
    if not sb:
        await _send_json_resp(send, 500, {"error": "数据库未连接"})
        return

    body = await _read_body(receive)
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        await _send_json_resp(send, 400, {"error": "无效的 JSON"})
        return

    table = data.get("table", "chat_messages")
    if table not in ("chat_messages", "chat_archive"):
        await _send_json_resp(send, 400, {"error": "table 必须是 chat_messages 或 chat_archive"})
        return

    record_id = str(data.get("id", "")).strip()
    if not record_id:
        await _send_json_resp(send, 400, {"error": "id 不能为空"})
        return

    update_fields = {}
    if "content" in data:
        content = str(data["content"]).strip()
        if not content:
            await _send_json_resp(send, 400, {"error": "content 不能为空"})
            return
        if table == "chat_messages":
            category = str(data.get("category", "")).strip()
            try:
                content = _validate_chat_message_content(content, category)
            except ValueError as e:
                await _send_json_resp(send, 400, {"error": "服务器内部错误"})
                return
            update_fields["content"] = content
            if "category" in data:
                update_fields["category"] = category
        else:
            update_fields["content"] = content
    if "category" in data and table == "chat_messages":
        update_fields["category"] = str(data["category"]).strip()
    if "role" in data and table == "chat_archive":
        role = str(data["role"]).strip()
        if role in ALLOWED_ARCHIVE_ROLES:
            update_fields["role"] = role
    if "assistant_id" in data:
        update_fields["assistant_id"] = str(data["assistant_id"]).strip()
    if "conversation_id" in data:
        update_fields["conversation_id"] = str(data["conversation_id"]).strip()

    if not update_fields:
        await _send_json_resp(send, 400, {"error": "没有可更新的字段"})
        return

    try:
        if "assistant_id" in update_fields and update_fields["assistant_id"]:
            await asyncio.to_thread(lambda: _ensure_persona_exists(sb, update_fields["assistant_id"]))
        def _update():
            return sb.table(table).update(update_fields).eq("id", record_id).execute()
        await asyncio.to_thread(_update)
        _log(f"✏️ [panel] 编辑 {table} id={record_id}: {list(update_fields.keys())}")
        await _send_json_resp(send, 200, {"ok": True})
    except Exception as e:
        _log(f"❌ [panel] 编辑失败: {e}")
        await _send_json_resp(send, 500, {"error": "服务器内部错误"})


async def handle_delete_record(scope, receive, send):
    """DELETE /api/panel/record — 删除一条记录"""
    sb = _get_supabase()
    if not sb:
        await _send_json_resp(send, 500, {"error": "数据库未连接"})
        return

    body = await _read_body(receive)
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        # 允许 query 参数传递
        data = _parse_query(scope)

    table = data.get("table", "chat_messages")
    if table not in ("chat_messages", "chat_archive"):
        await _send_json_resp(send, 400, {"error": "table 必须是 chat_messages 或 chat_archive"})
        return

    record_id = str(data.get("id", "")).strip()
    if not record_id:
        await _send_json_resp(send, 400, {"error": "id 不能为空"})
        return

    try:
        def _delete():
            return sb.table(table).delete().eq("id", record_id).execute()
        await asyncio.to_thread(_delete)
        _log(f"🗑️ [panel] 删除 {table} id={record_id}")
        await _send_json_resp(send, 200, {"ok": True})
    except Exception as e:
        _log(f"❌ [panel] 删除失败: {e}")
        await _send_json_resp(send, 500, {"error": "服务器内部错误"})


# ==========================================
# 人设/线路管理 CRUD
# ==========================================

# ==========================================
# 精华提炼 API
# ==========================================

async def handle_post_distill(scope, receive, send):
    """POST /api/panel/distill — 手动触发精华提炼
    Body: { "assistant_id": "骆云影_联姻线", "text": "对话文本..." }
    或:   { "assistant_id": "骆云影_联姻线", "from_archive": true, "limit": 10 }
    """
    try:
        from distill import run_distill, distill_from_archive
    except ImportError as e:
        await _send_json_resp(send, 500, {"error": f"distill 模块导入失败: {e}"})
        return

    body = await _read_body(receive)
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        await _send_json_resp(send, 400, {"error": "无效的 JSON"})
        return

    assistant_id = str(data.get("assistant_id", "")).strip()
    if not assistant_id:
        await _send_json_resp(send, 400, {"error": "assistant_id 不能为空"})
        return

    # 两种模式：直接给文本 / 从归档拉取
    if data.get("from_archive"):
        limit = int(data.get("limit", 10))
        result = await distill_from_archive(assistant_id, limit)
    else:
        text = str(data.get("text", "")).strip()
        if not text:
            await _send_json_resp(send, 400, {"error": "text 不能为空（或设 from_archive=true 从归档提炼）"})
            return
        result = await run_distill(assistant_id, text)

    await _send_json_resp(send, 200, result)


# ==========================================
# 人设/线路管理 CRUD
# ==========================================

async def handle_post_persona(scope, receive, send):
    """POST /api/panel/persona — 新增人设/线路"""
    sb = _get_supabase()
    if not sb:
        await _send_json_resp(send, 500, {"error": "数据库未连接"})
        return
    try:
        body = json.loads((await _read_body(receive)).decode("utf-8"))
        pid = (body.get("id") or "").strip()
        display_name = (body.get("display_name") or "").strip()
        is_visible = body.get("is_visible", True)
        sort_order = body.get("sort_order", 100)

        if not pid or not display_name:
            await _send_json_resp(send, 400, {"error": "id 和 display_name 不能为空"})
            return
        if len(pid) > 100:
            await _send_json_resp(send, 400, {"error": "id 过长（最多100字符）"})
            return

        row = {
            "id": pid,
            "display_name": display_name,
            "is_visible": bool(is_visible),
            "sort_order": int(sort_order),
            "system_prompt": str(body.get("system_prompt", "") or ""),
        }
        def _insert():
            return sb.table("personas").insert(row).execute()
        await asyncio.to_thread(_insert)
        _log(f"✨ [panel] 新增人设: {pid} ({display_name})")
        await _send_json_resp(send, 200, {"ok": True, "persona": row})
    except Exception as e:
        err_str = str(e).lower()
        if "duplicate" in err_str or "23505" in err_str:
            await _send_json_resp(send, 409, {"error": f"人设ID「{body.get('id','')}」已存在"})
        else:
            _log(f"❌ [panel] 新增人设失败: {e}")
            await _send_json_resp(send, 500, {"error": "服务器内部错误"})


async def handle_patch_persona(scope, receive, send):
    """PATCH /api/panel/persona — 编辑人设/线路"""
    sb = _get_supabase()
    if not sb:
        await _send_json_resp(send, 500, {"error": "数据库未连接"})
        return
    try:
        body = json.loads((await _read_body(receive)).decode("utf-8"))
        pid = (body.get("id") or "").strip()
        if not pid:
            await _send_json_resp(send, 400, {"error": "id 不能为空"})
            return

        update_fields = {}
        if "display_name" in body:
            dn = (body.get("display_name") or "").strip()
            if dn:
                update_fields["display_name"] = dn
        if "is_visible" in body:
            update_fields["is_visible"] = bool(body["is_visible"])
        if "sort_order" in body:
            update_fields["sort_order"] = int(body["sort_order"])
        if "system_prompt" in body:
            update_fields["system_prompt"] = str(body.get("system_prompt") or "")

        # 特殊处理：激活/取消激活（保证全局唯一）
        activate = body.get("activate")
        if activate is not None:
            update_fields["is_active"] = bool(activate)
            if activate:
                # 先把所有人设的 is_active 置 false（利用 partial unique index）
                def _deactivate_all():
                    sb.table("personas").update({"is_active": False}).eq("is_active", True).execute()
                await asyncio.to_thread(_deactivate_all)

        if not update_fields:
            await _send_json_resp(send, 400, {"error": "没有需要更新的字段"})
            return

        def _update():
            return sb.table("personas").update(update_fields).eq("id", pid).execute()
        await asyncio.to_thread(_update)
        _log(f"📝 [panel] 编辑人设: {pid} → {update_fields}")
        await _send_json_resp(send, 200, {"ok": True})
    except Exception as e:
        err_str = str(e).lower()
        if "uniq_personas_active" in err_str or "23505" in err_str:
            await _send_json_resp(send, 409, {"error": "激活冲突：请重试（系统已自动取消旧激活）"})
        else:
            _log(f"❌ [panel] 编辑人设失败: {e}")
            await _send_json_resp(send, 500, {"error": "服务器内部错误"})


async def handle_get_active_persona(scope, send):
    """GET /api/panel/active_persona — 返回当前激活的人设（供网关/面板查询）"""
    sb = _get_supabase()
    if not sb:
        await _send_json_resp(send, 500, {"error": "数据库未连接"})
        return
    try:
        def _q():
            return sb.table("personas").select("id, display_name, system_prompt, is_active") \
                .eq("is_active", True).limit(1).execute()
        res = await asyncio.to_thread(_q)
        persona = res.data[0] if res and res.data else None
        await _send_json_resp(send, 200, {"persona": persona})
    except Exception as e:
        err_str = str(e).lower()
        if "does not exist" in err_str or "42p01" in err_str or "42703" in err_str:
            await _send_json_resp(send, 200, {"persona": None, "warning": "请先执行 migration_phase2_personas.sql"})
        else:
            _log(f"❌ [panel] 查询激活人设失败: {e}")
            await _send_json_resp(send, 500, {"error": "服务器内部错误"})


async def handle_delete_persona(scope, receive, send):
    """DELETE /api/panel/persona — 删除人设/线路"""
    sb = _get_supabase()
    if not sb:
        await _send_json_resp(send, 500, {"error": "数据库未连接"})
        return
    try:
        body = json.loads((await _read_body(receive)).decode("utf-8"))
        pid = (body.get("id") or "").strip()
        if not pid:
            await _send_json_resp(send, 400, {"error": "id 不能为空"})
            return

        # 防止误删系统保留人设
        _PROTECTED = {"diagnose", "debug", "manual", "unknown", "未映射"}
        if pid in _PROTECTED:
            await _send_json_resp(send, 403, {"error": f"系统保留人设「{pid}」不可删除"})
            return

        def _delete():
            return sb.table("personas").delete().eq("id", pid).execute()
        await asyncio.to_thread(_delete)
        _log(f"🗑️ [panel] 删除人设: {pid}")
        await _send_json_resp(send, 200, {"ok": True})
    except Exception as e:
        _log(f"❌ [panel] 删除人设失败: {e}")
        await _send_json_resp(send, 500, {"error": "服务器内部错误"})


async def handle_panel_html(send):
    """GET /panel — 返回 HTML 面板"""
    try:
        if not os.path.exists(PANEL_HTML_PATH):
            await _send_json_resp(send, 404, {"error": "panel.html 未找到"})
            return
        with open(PANEL_HTML_PATH, "r", encoding="utf-8") as f:
            html = f.read()
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/html; charset=utf-8"),
                (b"access-control-allow-origin", b"*"),
            ],
        })
        await send({"type": "http.response.body", "body": html.encode("utf-8")})
    except Exception as e:
        await _send_json_resp(send, 500, {"error": "服务器内部错误"})


# ==========================================
# 主路由分发（由 gateway.py 调用）
# ==========================================
async def handle_panel_request(scope, receive, send):
    """
    处理所有 /api/panel/* 请求。鉴权已由 gateway.py 完成。
    """
    path = scope["path"]
    method = scope["method"]

    # CORS 预检
    if method == "OPTIONS":
        await _send_cors_preflight(send)
        return

    try:
        if path == "/api/panel/personas" and method == "GET":
            await handle_get_personas(scope, send)
        elif path == "/api/panel/active_persona" and method == "GET":
            await handle_get_active_persona(scope, send)
        elif path == "/api/panel/persona" and method == "POST":
            await handle_post_persona(scope, receive, send)
        elif path == "/api/panel/persona" and method == "PATCH":
            await handle_patch_persona(scope, receive, send)
        elif path == "/api/panel/persona" and method == "DELETE":
            await handle_delete_persona(scope, receive, send)
        elif path == "/api/panel/distill" and method == "POST":
            await handle_post_distill(scope, receive, send)
        elif path == "/api/panel/records" and method == "GET":
            await handle_get_records(scope, send)
        elif path == "/api/panel/record" and method == "POST":
            await handle_post_record(scope, receive, send)
        elif path == "/api/panel/record" and method == "PATCH":
            await handle_patch_record(scope, receive, send)
        elif path == "/api/panel/record" and method == "DELETE":
            await handle_delete_record(scope, receive, send)
        else:
            await _send_json_resp(send, 404, {"error": f"未知的 panel 端点: {method} {path}"})
    except Exception as e:
        _log(f"❌ [panel] 未捕获异常: {e}")
        await _send_json_resp(send, 500, {"error": "服务器内部错误"})