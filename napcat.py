"""
通用 NapCat QQ 机器人模块 (Generic NapCat QQ Bot Module)
=========================================================
负责：
- 接收本地 NapCat 通过反向 WebSocket 推送的 QQ 消息
- 转发给 LLM 处理后将回复发送回 QQ
- 维护连接状态 / 登录二维码
- 自动重连与掉线通知

所有敏感配置 (QQ 号 / WS 地址 / 通知列表) 均从环境变量读取。
"""

import os
import re
import json
import time
import asyncio
import datetime

# 可选依赖：websockets
try:
    import websockets
except ImportError:
    websockets = None

# 可选依赖：requests (用于 HTTP 回调)
try:
    import requests as _requests
except ImportError:
    _requests = None


# ==========================================
# 1. 全局配置 (从环境变量读取)
# ==========================================

NAPCAT_WS_URL = os.environ.get("NAPCAT_WS_URL", "").strip()
NAPCAT_HTTP_URL = os.environ.get("NAPCAT_HTTP_URL", "").strip()
NAPCAT_BOT_QQ = os.environ.get("NAPCAT_BOT_QQ", "").strip()
NAPCAT_TARGET_USER = os.environ.get("NAPCAT_TARGET_USER", "").strip()
NAPCAT_NOTIFY_QQ = os.environ.get("NAPCAT_NOTIFY_QQ", "").strip()
NAPCAT_ALLOWED_GROUPS = os.environ.get("NAPCAT_ALLOWED_GROUPS", "").strip()

# 通知 QQ 列表
NAPCAT_NOTIFY_QQ_LIST = [x.strip() for x in NAPCAT_NOTIFY_QQ.split(",") if x.strip()]
# Telegram 通知列表 (可选，逗号分隔)
NAPCAT_NOTIFY_TG_LIST = [x.strip() for x in os.environ.get("NAPCAT_NOTIFY_TG", "").split(",") if x.strip()]

# 允许响应的群列表
NAPCAT_ALLOWED_GROUPS_LIST = [x.strip() for x in NAPCAT_ALLOWED_GROUPS.split(",") if x.strip()]

# 重连参数
RECONNECT_INITIAL_DELAY = int(os.environ.get("NAPCAT_RECONNECT_DELAY", 5))
RECONNECT_BACKOFF_FACTOR = float(os.environ.get("NAPCAT_BACKOFF_FACTOR", 1.5))
RECONNECT_MAX_DELAY = int(os.environ.get("NAPCAT_MAX_DELAY", 60))


# ==========================================
# 2. 全局状态
# ==========================================

_napcat_connected = False
_napcat_ws_send = None  # 反向 WS 的 send 回调
_napcat_status_message = "未连接"
_napcat_last_connected_at = 0.0
_napcat_disconnect_count = 0
_napcat_qr_code = None
_napcat_qr_expire = 0.0
_napcat_logs = []  # 最近 200 条日志
_napcat_ws_pending = {}  # 等待响应的 WS API 请求 {echo: future}


def _naplog(msg: str):
    """记录 NapCat 模块日志。"""
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    _napcat_logs.append(line)
    if len(_napcat_logs) > 200:
        _napcat_logs.pop(0)
    print(line)


def _get_deps():
    """延迟获取 server 模块的依赖 (避免循环导入)。"""
    try:
        import server
        return server
    except Exception:
        return None


# ==========================================
# 3. 状态查询接口
# ==========================================

def get_napcat_status() -> dict:
    """返回当前 NapCat 连接状态汇总。"""
    return {
        "connected": _napcat_connected,
        "status_message": _napcat_status_message,
        "last_connected_at": _napcat_last_connected_at,
        "disconnect_count": _napcat_disconnect_count,
        "ws_url": NAPCAT_WS_URL or "未配置",
        "http_url": NAPCAT_HTTP_URL or "未配置",
        "bot_qq": NAPCAT_BOT_QQ,
        "target_user": NAPCAT_TARGET_USER,
        "notify_qq": NAPCAT_NOTIFY_QQ,
        "allowed_groups": NAPCAT_ALLOWED_GROUPS,
    }


def get_napcat_logs() -> list:
    """返回最近的日志列表。"""
    return _napcat_logs[-100:]


def get_napcat_qr_code() -> dict:
    """返回当前登录二维码信息 (如有效)。"""
    now = time.time()
    if _napcat_qr_code and now < _napcat_qr_expire:
        return {
            "qr_code": _napcat_qr_code,
            "remaining_seconds": int(_napcat_qr_expire - now),
        }
    return None


def _set_napcat_qr_code(qr_url):
    """设置或清除二维码缓存。"""
    global _napcat_qr_code, _napcat_qr_expire
    if qr_url:
        _napcat_qr_code = qr_url
        _napcat_qr_expire = time.time() + 300  # 默认 5 分钟有效
    else:
        _napcat_qr_code = None
        _napcat_qr_expire = 0.0


def update_napcat_config(config: dict):
    """热更新 NapCat 配置 (同时写入模块级全局变量)。"""
    global NAPCAT_WS_URL, NAPCAT_HTTP_URL, NAPCAT_BOT_QQ
    global NAPCAT_TARGET_USER, NAPCAT_NOTIFY_QQ, NAPCAT_ALLOWED_GROUPS
    global NAPCAT_NOTIFY_QQ_LIST, NAPCAT_ALLOWED_GROUPS_LIST

    if "ws_url" in config and config["ws_url"]:
        NAPCAT_WS_URL = str(config["ws_url"]).strip()
        os.environ["NAPCAT_WS_URL"] = NAPCAT_WS_URL
    if "http_url" in config and config["http_url"]:
        NAPCAT_HTTP_URL = str(config["http_url"]).strip()
        os.environ["NAPCAT_HTTP_URL"] = NAPCAT_HTTP_URL
    if "bot_qq" in config and config["bot_qq"]:
        NAPCAT_BOT_QQ = str(config["bot_qq"]).strip()
        os.environ["NAPCAT_BOT_QQ"] = NAPCAT_BOT_QQ
    if "target_user" in config and config["target_user"]:
        NAPCAT_TARGET_USER = str(config["target_user"]).strip()
        os.environ["NAPCAT_TARGET_USER"] = NAPCAT_TARGET_USER
    if "notify_qq" in config and config["notify_qq"]:
        NAPCAT_NOTIFY_QQ = str(config["notify_qq"]).strip()
        os.environ["NAPCAT_NOTIFY_QQ"] = NAPCAT_NOTIFY_QQ
        NAPCAT_NOTIFY_QQ_LIST = [x.strip() for x in NAPCAT_NOTIFY_QQ.split(",") if x.strip()]
    if "allowed_groups" in config and config["allowed_groups"]:
        NAPCAT_ALLOWED_GROUPS = str(config["allowed_groups"]).strip()
        os.environ["NAPCAT_ALLOWED_GROUPS"] = NAPCAT_ALLOWED_GROUPS
        NAPCAT_ALLOWED_GROUPS_LIST = [x.strip() for x in NAPCAT_ALLOWED_GROUPS.split(",") if x.strip()]


# ==========================================
# 4. WS API 调用 (向 NapCat 发指令)
# ==========================================

async def _call_napcat_api(action: str, params: dict = None, timeout: float = 10.0) -> dict:
    """
    通过反向 WS 向 NapCat 发送 OneBot API 请求，并等待响应。
    使用 echo 字段做请求-响应匹配。
    """
    if not _napcat_ws_send:
        return None
    echo = f"req_{int(time.time() * 1000)}_{id(params)}"
    payload = {"action": action, "params": params or {}, "echo": echo}

    fut = asyncio.get_event_loop().create_future()
    _napcat_ws_pending[echo] = fut

    try:
        await _napcat_ws_send(json.dumps(payload))
        return await asyncio.wait_for(fut, timeout=timeout)
    except Exception as e:
        _naplog(f"❌ WS API 调用失败 [{action}]: {e}")
        return None
    finally:
        _napcat_ws_pending.pop(echo, None)


async def get_qr_via_ws() -> dict:
    """通过反向 WS 获取登录二维码 / 登录状态。"""
    res = await _call_napcat_api("get_login_info")
    if res and res.get("status") == "ok":
        data = res.get("data", {})
        if data.get("user_id"):
            return {"status": "logged_in", "user_id": data["user_id"], "nickname": data.get("nickname", "")}
    # 尝试获取二维码
    res = await _call_napcat_api("get_qr_code")
    if res and res.get("status") == "ok":
        qr = res.get("data", {}).get("url") or res.get("data", {}).get("qr_code")
        if qr:
            _set_napcat_qr_code(qr)
            return {"status": "need_login", "qr_code": qr}
    return None


async def send_qq_message(user_id: int, message: str, is_group: bool = False):
    """通过 WS 发送 QQ 私聊 / 群消息。"""
    action = "send_group_msg" if is_group else "send_private_msg"
    params = {"message": message}
    if is_group:
        params["group_id"] = user_id
    else:
        params["user_id"] = user_id
    return await _call_napcat_api(action, params)


async def _send_disconnect_notification():
    """发送掉线通知到 QQ 和 Telegram。"""
    global _napcat_disconnect_count
    _napcat_disconnect_count += 1

    disconnect_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    message = f"⚠️ NapCat 掉线通知\n\n时间: {disconnect_time}\n断开次数: {_napcat_disconnect_count}\n请检查 NapCat 状态。"

    # QQ 通知
    if NAPCAT_NOTIFY_QQ_LIST and _requests and NAPCAT_HTTP_URL:
        for qq in NAPCAT_NOTIFY_QQ_LIST:
            try:
                url = f"{NAPCAT_HTTP_URL}/send_private_msg"
                payload = {"user_id": int(qq), "message": message}
                resp = _requests.post(url, json=payload, timeout=10)
                if resp.status_code == 200:
                    _naplog(f"✅ 掉线通知已发送到 QQ: {qq}")
            except Exception as e:
                _naplog(f"❌ 发送 QQ 掉线通知失败: {e}")

    # Telegram 通知 (可选)
    if NAPCAT_NOTIFY_TG_LIST:
        try:
            dep = _get_deps()
            if hasattr(dep, "send_telegram_message"):
                for tg_chat_id in NAPCAT_NOTIFY_TG_LIST:
                    await asyncio.to_thread(dep.send_telegram_message, tg_chat_id, message)
        except Exception as e:
            _naplog(f"❌ 发送 Telegram 掉线通知失败: {e}")


# ==========================================
# 5. 消息处理
# ==========================================

async def _process_napcat_message(data: dict, send):
    """处理一条收到的 QQ 消息。"""
    try:
        post_type = data.get("post_type")
        if post_type != "message":
            return

        message_type = data.get("message_type")
        raw_message = data.get("raw_message", "")
        sender = data.get("sender", {})
        sender_id = data.get("user_id")

        # 群消息：只处理白名单内的群
        if message_type == "group":
            group_id = data.get("group_id")
            if NAPCAT_ALLOWED_GROUPS_LIST and str(group_id) not in NAPCAT_ALLOWED_GROUPS_LIST:
                return
            # 群消息需要 @ 机器人才响应 (简化判断)
            if f"[CQ:at,qq={NAPCAT_BOT_QQ}]" not in raw_message and NAPCAT_BOT_QQ:
                return
            clean_text = raw_message.replace(f"[CQ:at,qq={NAPCAT_BOT_QQ}]", "").strip()
        else:
            # 私聊：可选限制只响应目标用户
            if NAPCAT_TARGET_USER and str(sender_id) != NAPCAT_TARGET_USER:
                return
            clean_text = raw_message.strip()

        if not clean_text:
            return

        # 调用 LLM 生成回复
        dep = _get_deps()
        if not dep:
            return

        client = dep._get_llm_client("default")
        if not client:
            await send_qq_message(
                group_id if message_type == "group" else sender_id,
                "（AI 服务暂未配置，无法回复）",
                is_group=(message_type == "group")
            )
            return

        # 构造 prompt
        curr_persona = dep._get_current_persona()
        prompt = f"""
        收到一条 QQ 消息: {clean_text}
        发送者: {sender.get('nickname', '未知')}
        当前人设: {curr_persona}

        请用符合人设的口吻回复。纯文本，简洁自然。
        """
        reply = await dep._ask_llm_async(client, prompt, temperature=0.8)

        if reply:
            target_id = group_id if message_type == "group" else sender_id
            await send_qq_message(target_id, reply, is_group=(message_type == "group"))

            # 记忆入库
            if hasattr(dep, "_save_memory_to_db"):
                await asyncio.to_thread(
                    dep._save_memory_to_db,
                    "🤖 QQ 互动",
                    f"{sender.get('nickname', '未知')}: {clean_text}\n回复: {reply}",
                    "流水", "温柔", "QQ_MSG"
                )
    except Exception as e:
        _naplog(f"❌ 处理 QQ 消息失败: {e}")


async def _handle_poke_event(send, data, allowed_groups):
    """处理戳一戳事件 (简化版：仅记录日志)。"""
    _naplog(f"👉 收到戳一戳事件: {json.dumps(data, ensure_ascii=False)[:100]}")


# ==========================================
# 6. 反向 WS 服务端处理 (供 server.py 挂载)
# ==========================================

async def handle_napcat_ws(scope, receive, send):
    """
    反向 WebSocket 处理函数。
    本地 NapCat 作为客户端连接到本网关的 /qq-ws 路径。
    """
    global _napcat_connected, _napcat_ws_send, _napcat_last_connected_at, _napcat_status_message

    # 握手
    await send({"type": "websocket.accept"})
    _napcat_connected = True
    _napcat_ws_send = send
    _napcat_last_connected_at = time.time()
    _napcat_status_message = "已连接"
    _naplog("✅ NapCat 反向 WS 已连接")

    try:
        while True:
            try:
                msg = await receive()
            except Exception:
                break
            if msg["type"] == "websocket.disconnect":
                break
            if msg["type"] != "websocket.receive":
                continue
            raw_text = msg.get("text", "")
            if not raw_text:
                continue
            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError:
                continue

            # 匹配 API 响应
            echo_val = data.get("echo", "")
            if echo_val and echo_val in _napcat_ws_pending:
                future = _napcat_ws_pending[echo_val]
                if not future.done():
                    future.set_result(data)
                continue

            # 心跳
            if data.get("post_type") == "meta_event" and data.get("meta_event_type") == "heartbeat":
                _napcat_last_connected_at = time.time()
                continue

            # 登录事件
            if data.get("post_type") == "meta_event" and data.get("meta_event_type") == "login":
                sub_type = data.get("sub_type", "")
                if "offline" in str(data).lower() or "kick" in str(data).lower():
                    _napcat_status_message = "🔴 QQ 已掉线"
                    _naplog("🚨 QQ 登录失效，需要重新扫码")
                elif sub_type == "login_success":
                    _napcat_status_message = "🟢 QQ 已登录"
                    _naplog("✅ QQ 已重新登录")
                continue

            # 通知事件
            if data.get("post_type") == "notice":
                if "offline" in str(data).lower():
                    _napcat_status_message = "🔴 QQ 已掉线"
                elif data.get("notice_type") == "notify" and data.get("sub_type") == "poke":
                    try:
                        await _handle_poke_event(send, data, NAPCAT_ALLOWED_GROUPS_LIST)
                    except Exception:
                        pass
                continue

            # 消息事件
            if data.get("post_type") != "message":
                continue
            try:
                await _process_napcat_message(data, send)
            except Exception:
                pass
    except Exception:
        pass
    finally:
        _napcat_ws_send = None
        _napcat_connected = False
        _napcat_status_message = "反向 WS 已断开"
        for eid, fut in _napcat_ws_pending.items():
            if not fut.done():
                fut.set_result(None)
        _napcat_ws_pending.clear()
        _naplog("❌ NapCat 反向 WS 连接已关闭")


# ==========================================
# 7. 主动客户端模式 (可选)
# ==========================================

async def napcat_client_loop():
    """
    主动连接 NapCat 的正向 WS (客户端模式)。
    当无法使用反向 WS 时，可启动此循环。
    """
    if not websockets:
        _naplog("缺少 websockets 库，客户端模式无法启动")
        return

    global _napcat_connected, _napcat_last_connected_at, _napcat_status_message

    if not NAPCAT_WS_URL:
        _naplog("未配置 NAPCAT_WS_URL，客户端模式休眠")
        return

    _naplog(f"客户端模式启动，目标: {NAPCAT_WS_URL}")
    delay = RECONNECT_INITIAL_DELAY

    while True:
        try:
            _napcat_status_message = "正在连接..."
            async with websockets.connect(NAPCAT_WS_URL, ping_interval=30, ping_timeout=10, close_timeout=5) as ws:
                _naplog("已连接")
                _napcat_connected = True
                _napcat_last_connected_at = time.time()
                _napcat_status_message = "已连接"
                delay = RECONNECT_INITIAL_DELAY

                async for raw_text in ws:
                    try:
                        data = json.loads(raw_text)
                    except json.JSONDecodeError:
                        continue
                    if data.get("post_type") == "meta_event":
                        _napcat_last_connected_at = time.time()
                        continue
                    if data.get("post_type") == "notice":
                        continue
                    if data.get("post_type") != "message":
                        continue
                    try:
                        await _process_napcat_message(data, ws.send)
                    except Exception:
                        pass

        except (websockets.exceptions.ConnectionClosed, ConnectionRefusedError, OSError) as e:
            _naplog(f"连接断开: {e}")
            _napcat_connected = False
            _napcat_status_message = f"连接断开: {str(e)[:50]}"
            await _send_disconnect_notification()
        except Exception as e:
            _naplog(f"意外错误: {e}")
            _napcat_connected = False
            _napcat_status_message = f"错误: {str(e)[:50]}"
            await _send_disconnect_notification()

        _naplog(f"{delay}秒后重连...")
        await asyncio.sleep(delay)
        delay = min(delay * RECONNECT_BACKOFF_FACTOR, RECONNECT_MAX_DELAY)


async def _check_napcat_login_status():
    """通过 HTTP 接口检查登录状态并获取二维码 (如可用)。"""
    if not _requests or not NAPCAT_HTTP_URL:
        return None
    try:
        url = f"{NAPCAT_HTTP_URL}/get_login_info"
        resp = _requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "ok":
                login_info = data.get("data", {})
                if login_info.get("user_id"):
                    _set_napcat_qr_code(None)
                    return {"status": "logged_in", "user_id": login_info["user_id"], "nickname": login_info.get("nickname", "")}
    except Exception as e:
        _naplog(f"⚠️ 检查登录状态失败: {e}")

    try:
        url = f"{NAPCAT_HTTP_URL}/get_qr_code"
        resp = _requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "ok":
                qr_url = data.get("data", {}).get("url") or data.get("data", {}).get("qr_code")
                if qr_url:
                    _set_napcat_qr_code(qr_url)
                    return {"status": "need_login", "qr_code": qr_url}
    except Exception as e:
        _naplog(f"⚠️ 获取二维码失败: {e}")

    return None