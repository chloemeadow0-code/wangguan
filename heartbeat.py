"""
通用后台心跳模块 (Generic Background Heartbeat)
===============================================
负责启动一系列后台异步协程 (运行在独立 daemon 线程中)：
- 自主生命循环：定时主动思考/问候
- Telegram 轮询：接收并处理用户消息
- 消息总结器：定期汇总未处理消息
- 提醒巡视器：检查数据库闹钟并触发
- 日程小秘书：每日早晚播报日历
- 信箱巡视器：检查新邮件
- 环境变量同步：从数据库热更新配置

所有协程均通过延迟导入 (函数内 import) 避免 server.py 的循环依赖。
所有个性化内容 (人设 / 用户名 / 时区) 均从环境变量读取。
"""

import os
import re
import json
import time
import random
import asyncio
import datetime
import threading

# 全局：下一次主动唤醒的时间戳，可供前端展示
global_next_wake_time = 0.0


# ==========================================
# 1. 自主生命循环 (主动问候)
# ==========================================

async def async_autonomous_life():
    """定时主动思考并发送问候，让 AI 拥有"自主生命感"。"""
    # 延迟导入，避免循环依赖
    from server import (
        _get_llm_client, _ask_llm_async, _push_wechat,
        _save_memory_to_db, _get_now_bj, _get_current_persona,
        get_latest_diary, where_is_user, supabase
    )

    global global_next_wake_time
    print("💓 自主生命循环已上线...")

    # 触发间隔（秒），默认 2 小时，可通过环境变量调整
    interval = int(os.environ.get("HEARTBEAT_INTERVAL", 7200))

    while True:
        # 随机化下一次唤醒时间，避免过于机械
        wake_jitter = random.randint(-600, 600)
        global_next_wake_time = time.time() + interval + wake_jitter

        await asyncio.sleep(interval + wake_jitter)

        try:
            client = _get_llm_client("default")
            if not client:
                continue

            recent_mem = await get_latest_diary()
            curr_loc = await where_is_user()
            curr_persona = _get_current_persona()
            now_bj = _get_now_bj()

            prompt = f"""
            当前时间: {now_bj.strftime('%Y-%m-%d %H:%M')} (星期{now_bj.isoweekday()})
            当前人设: {curr_persona}
            近期互动记录: {recent_mem}
            用户大概状态: {curr_loc}

            请基于以上信息，用符合人设的口吻主动发一条简短问候 (50 字内)。
            要求自然、有温度，不要提"系统/闹钟/定时"，直接像真人突然想起对方那样说话。
            纯文本输出，禁止使用表情代码或 URL。
            """

            ai_msg = await _ask_llm_async(client, prompt, temperature=0.85)
            if ai_msg:
                await asyncio.to_thread(_push_wechat, ai_msg, "💌 主动问候")
                await asyncio.to_thread(
                    _save_memory_to_db, "🤖 主动问候",
                    f"主动发送: {ai_msg}", "流水", "主动", "Heartbeat"
                )
                print(f"💓 [自主生命] 已发送主动问候: {ai_msg[:30]}...")
        except Exception as e:
            print(f"❌ 自主生命循环出错: {e}")


# ==========================================
# 2. Telegram 消息轮询
# ==========================================

async def async_telegram_polling():
    """轮询 Telegram Bot 的 getUpdates 接口，接收并处理用户消息。"""
    from server import (
        _get_llm_client, _ask_llm_async, _push_wechat,
        _save_memory_to_db, _get_current_persona,
        get_latest_diary, where_is_user, mem0_client
    )

    import requests

    print("📨 Telegram 轮询神经已上线...")
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    if not token:
        print("⚠️ 未配置 TG_BOT_TOKEN，Telegram 轮询休眠。")
        return

    base_url = f"https://api.telegram.org/bot{token}"
    offset = 0

    while True:
        try:
            def _get_updates():
                return requests.get(
                    f"{base_url}/getUpdates",
                    params={"timeout": 30, "offset": offset},
                    timeout=35
                ).json()
            data = await asyncio.to_thread(_get_updates)

            if not data.get("ok"):
                await asyncio.sleep(5)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message")
                if not message:
                    continue

                chat_id = message.get("chat", {}).get("id")
                text = message.get("text", "").strip()
                if not chat_id or not text:
                    continue

                # 简单的指令拦截
                if text.startswith("/"):
                    await asyncio.to_thread(
                        lambda: requests.post(
                            f"{base_url}/sendMessage",
                            json={"chat_id": chat_id, "text": "收到指令，正在处理..."},
                            timeout=10
                        )
                    )
                    continue

                # 调用 LLM 生成回复
                client = _get_llm_client("default")
                if client:
                    try:
                        recent_mem = await get_latest_diary()
                        curr_loc = await where_is_user()
                        curr_persona = _get_current_persona()

                        prompt = f"""
                        用户发来消息: {text}
                        当前人设: {curr_persona}
                        近期记录: {recent_mem}

                        请用符合人设的口吻回复用户。纯文本，自然真诚。
                        """
                        reply = await _ask_llm_async(client, prompt, temperature=0.8)

                        if reply:
                            await asyncio.to_thread(
                                lambda: requests.post(
                                    f"{base_url}/sendMessage",
                                    json={"chat_id": chat_id, "text": reply},
                                    timeout=15
                                )
                            )
                            await asyncio.to_thread(
                                _save_memory_to_db, "🤖 互动记录",
                                f"用户: {text}\n回复: {reply}", "流水", "温柔", "TG_MSG"
                            )

                            # 写入 Mem0 长期记忆
                            if mem0_client:
                                try:
                                    def _add_mem0():
                                        mem0_client.add([
                                            {"role": "user", "content": text},
                                            {"role": "assistant", "content": reply}
                                        ], user_id=os.environ.get("MEM0_USER_ID", "default"))
                                    await asyncio.to_thread(_add_mem0)
                                except Exception as e:
                                    print(f"Mem0 写入报错: {e}")
                    except Exception as e:
                        print(f"❌ TG 回复生成失败: {e}")
        except Exception as e:
            print(f"❌ TG 轮询错误: {e}")
            await asyncio.sleep(5)

        await asyncio.sleep(0.5)


# ==========================================
# 3. 消息总结器
# ==========================================

async def async_message_summarizer():
    """定期汇总数据库中未处理的消息，避免打扰用户。"""
    from server import _get_llm_client, _ask_llm_async, _push_wechat, _save_memory_to_db, supabase

    print("📋 消息总结器已上线...")
    # 总结间隔（秒），默认半小时
    interval = int(os.environ.get("SUMMARIZE_INTERVAL", 1800))

    while True:
        await asyncio.sleep(interval)
        if not supabase:
            continue
        client = _get_llm_client("default")
        if not client:
            continue
        try:
            # 查出所有未总结的消息
            res = await asyncio.to_thread(
                lambda: supabase.table("memories").select("id, title, content")
                .eq("tags", "Pending").execute()
            )

            if res.data and len(res.data) > 0:
                msgs = "\n".join([f"{item['title']}: {item['content']}" for item in res.data])

                # 如果消息极少，直接标记已处理跳过
                if len(msgs) < 30:
                    ids = [item['id'] for item in res.data]
                    await asyncio.to_thread(
                        lambda: supabase.table("memories").update({"tags": "Done"}).in_("id", ids).execute()
                    )
                    continue

                prompt = f"""
                以下是过去一段时间收到的消息：
                {msgs}

                请用简洁的口吻总结重点 (150 字以内)。如果没有重要的事，告诉用户一切正常。
                """
                summary = await _ask_llm_async(client, prompt, temperature=0.7)

                if summary:
                    await asyncio.to_thread(_push_wechat, summary, "📋 消息总结")
                    await asyncio.to_thread(
                        _save_memory_to_db, "🤖 互动记录",
                        f"发送了消息总结: {summary}", "流水", "尽责", "Summary"
                    )
                    ids = [item['id'] for item in res.data]
                    await asyncio.to_thread(
                        lambda: supabase.table("memories").update({"tags": "Done"}).in_("id", ids).execute()
                    )
        except Exception as e:
            print(f"❌ 消息总结器报错: {e}")


# ==========================================
# 4. 提醒巡视器
# ==========================================

async def async_reminder_worker():
    """每分钟巡视数据库 reminders 表，到点就触发。"""
    from server import (
        _get_llm_client, _ask_llm_async, _push_wechat, _save_memory_to_db,
        _get_now_bj, _get_current_persona, get_latest_diary, where_is_user, supabase
    )

    print("⏰ 提醒巡视神经已上线...")
    while True:
        try:
            if supabase:
                now_bj = _get_now_bj()
                current_hm = now_bj.strftime("%H:%M")
                current_date = now_bj.strftime("%Y-%m-%d")

                res = await asyncio.to_thread(
                    lambda: supabase.table("reminders").select("*").eq("is_paused", False).execute()
                )

                if res and res.data:
                    for r in res.data:
                        r_id = r.get("id")
                        t_str = r.get("time_str")
                        raw_msg = r.get("content", "")
                        repeat = r.get("is_repeat", False)
                        last_fired = r.get("last_fired", "")

                        if current_hm == t_str and last_fired != current_date:
                            final_push_text = raw_msg

                            # 尝试用 LLM 生成更自然的提醒文案
                            client = _get_llm_client("default")
                            if client:
                                try:
                                    curr_persona = _get_current_persona()
                                    prompt = f"""
                                    时间: {t_str}
                                    需提醒内容: 【{raw_msg}】
                                    当前人设: {curr_persona}

                                    请用符合人设的口吻发一条提醒。自然真诚，不要提"闹钟/定时"。
                                    纯文本输出。
                                    """
                                    ai_msg = await _ask_llm_async(client, prompt, temperature=0.85)
                                    if ai_msg:
                                        final_push_text = ai_msg
                                except Exception as ai_e:
                                    print(f"❌ 提醒 AI 生成失败，使用兜底文案: {ai_e}")

                            await asyncio.to_thread(_push_wechat, final_push_text, "🔔 提醒")
                            await asyncio.to_thread(
                                _save_memory_to_db, "🤖 互动记录",
                                f"发送提醒: {final_push_text}", "流水", "尽责", "Reminder"
                            )

                            # 更新触发记录
                            if repeat:
                                await asyncio.to_thread(
                                    lambda: supabase.table("reminders")
                                    .update({"last_fired": current_date}).eq("id", r_id).execute()
                                )
                            else:
                                await asyncio.to_thread(
                                    lambda: supabase.table("reminders").delete().eq("id", r_id).execute()
                                )
        except Exception:
            pass

        # 对齐到下一分钟
        now = datetime.datetime.utcnow()
        sleep_sec = 60 - now.second + 1
        await asyncio.sleep(sleep_sec)


# ==========================================
# 5. 日程小秘书
# ==========================================

async def async_schedule_secretary():
    """每日早晚播报 Google 日历日程。"""
    from server import _get_calendar_service, _push_wechat, TARGET_CALENDAR_ID

    print("📅 日程小秘书已上线...")
    if not os.environ.get("GOOGLE_USER_TOKEN_JSON"):
        print("⚠️ 未配置 GOOGLE_USER_TOKEN_JSON，日程播报无法启动。")
        return

    # 播报时间（本地时区），可通过环境变量调整
    morning_time = os.environ.get("SCHEDULE_MORNING_TIME", "07:30")
    evening_time = os.environ.get("SCHEDULE_EVENING_TIME", "22:00")

    while True:
        try:
            now_bj = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
            current_hm = now_bj.strftime("%H:%M")

            if current_hm == morning_time:
                await _broadcast_schedule(now_bj, "今日", _get_calendar_service, _push_wechat, TARGET_CALENDAR_ID, is_morning=True)
            elif current_hm == evening_time:
                tomorrow = now_bj + datetime.timedelta(days=1)
                await _broadcast_schedule(tomorrow, "明日", _get_calendar_service, _push_wechat, TARGET_CALENDAR_ID, is_morning=False)
        except Exception as e:
            print(f"❌ 日程小秘书报错: {e}")

        now = datetime.datetime.utcnow()
        sleep_sec = 60 - now.second + 1
        await asyncio.sleep(sleep_sec)


async def _broadcast_schedule(target_date, label, _get_calendar_service, _push_wechat, calendar_id, is_morning=True):
    """内部辅助：拉取指定日期的日历并推送。"""
    day_start = target_date.replace(hour=0, minute=0, second=0).isoformat() + "+08:00"
    day_end = target_date.replace(hour=23, minute=59, second=59).isoformat() + "+08:00"

    def _get_events():
        service = _get_calendar_service()
        return service.events().list(
            calendarId=calendar_id, timeMin=day_start, timeMax=day_end,
            singleEvents=True, orderBy='startTime', timeZone='Asia/Shanghai'
        ).execute().get('items', [])

    events = await asyncio.to_thread(_get_events)
    greeting = "早安！今天的日程：" if is_morning else f"{label}的日程，提前准备："
    if events:
        msg = f"📅 {label}{greeting}\n"
        for e in events:
            raw_dt = e['start'].get('dateTime')
            if not raw_dt:
                continue
            dt_start = datetime.datetime.fromisoformat(raw_dt.replace('Z', '+00:00'))
            if dt_start.tzinfo is None:
                dt_start = dt_start.replace(tzinfo=datetime.timezone.utc)
            dt_bj = dt_start.astimezone(datetime.timezone(datetime.timedelta(hours=8)))
            msg += f"🔹 {dt_bj.strftime('%H:%M')} - {e.get('summary', '未知')}\n"
        await asyncio.to_thread(_push_wechat, msg, f"📅 {label}日程播报")
    else:
        await asyncio.to_thread(_push_wechat, f"📅 {label}没有日程安排，好好休息～", f"📅 {label}日程播报")


# ==========================================
# 6. 信箱巡视器 (邮件)
# ==========================================

async def async_email_secretary():
    """定期检查新邮件并通知 (可通过 GMAIL_BRIDGE_URL 配置桥接地址)。"""
    from server import (
        _get_llm_client, _push_wechat, _save_memory_to_db,
        _clean_email_body, MY_EMAIL, http_session
    )

    print("📭 信箱巡视神经已接入...")
    BRIDGE_URL = os.environ.get("GMAIL_BRIDGE_URL", "").strip()
    if not BRIDGE_URL:
        print("⚠️ 未配置 GMAIL_BRIDGE_URL，信箱巡视暂时休眠。")
        return

    processed_email_ids = set()

    while True:
        try:
            def _fetch():
                resp = http_session.get(BRIDGE_URL, timeout=20)
                return resp.json() if resp.status_code == 200 else []
            raw_new_emails = await asyncio.to_thread(_fetch)

            if raw_new_emails:
                for mail in raw_new_emails:
                    mail_id = mail.get('id', '')
                    if mail_id in processed_email_ids:
                        continue
                    # 过滤掉自己发的和系统邮件
                    sender = mail.get('from', '').lower()
                    my_email_lower = MY_EMAIL.lower() if MY_EMAIL else ""
                    if "onboarding@resend.dev" in sender or (my_email_lower and my_email_lower in sender):
                        processed_email_ids.add(mail_id)
                        continue

                    # 通知用户收到新邮件
                    subject = mail.get('subject', '无标题')
                    tg_msg = f"📧 收到新邮件: {subject} (来自 {mail.get('from', '未知')})"
                    await asyncio.to_thread(_push_wechat, tg_msg, "📧 信箱提醒")
                    await asyncio.to_thread(
                        _save_memory_to_db, "📧 信箱处理",
                        f"收到邮件: {subject}", "流水", "尽责", "Email_Process"
                    )
                    processed_email_ids.add(mail_id)
        except Exception:
            pass

        await asyncio.sleep(300)


# ==========================================
# 7. 环境变量热同步
# ==========================================

async def async_env_sync():
    """定时从数据库 user_facts.sys_config 读取配置，热更新到环境变量。"""
    from server import supabase, ORIGINAL_ENV

    print("⚙️ 环境变量热同步神经已上线...")
    # 支持热同步的键列表 (可通过环境变量扩展)
    default_sync_keys = [
        "DEFAULT_API_KEY", "DEFAULT_BASE_URL", "DEFAULT_MODEL_NAME",
        "TG_BOT_TOKEN", "TG_CHAT_ID",
        "EMAIL_API_KEY", "EMAIL_FROM", "ADMIN_EMAIL",
        "AI_PERSONA", "MEM0_USER_ID",
    ]
    extra_keys = [k.strip() for k in os.environ.get("SYNC_KEYS", "").split(",") if k.strip()]
    sync_keys = list(set(default_sync_keys + extra_keys))

    while True:
        try:
            if supabase:
                def _sync():
                    res = supabase.table("user_facts").select("value").eq("key", "sys_config").execute()
                    if res.data:
                        conf = json.loads(res.data[0]['value'])
                        for k in sync_keys:
                            val = str(conf.get(k, "")).strip()
                            if val:
                                os.environ[k] = val
                            else:
                                if k in ORIGINAL_ENV:
                                    os.environ[k] = ORIGINAL_ENV[k]
                                elif k in os.environ:
                                    del os.environ[k]
                await asyncio.to_thread(_sync)
        except Exception:
            pass
        await asyncio.sleep(10)


# ==========================================
# 8. 启动入口
# ==========================================

def start_autonomous_life():
    """启动所有后台心跳线程 (daemon 模式，主进程退出时自动结束)。"""
    def _run_heartbeat(): asyncio.run(async_autonomous_life())
    def _run_tg_polling(): asyncio.run(async_telegram_polling())
    def _run_msg_sum(): asyncio.run(async_message_summarizer())
    def _run_reminders(): asyncio.run(async_reminder_worker())
    def _run_email(): asyncio.run(async_email_secretary())
    def _run_env_sync(): asyncio.run(async_env_sync())
    def _run_schedule(): asyncio.run(async_schedule_secretary())

    threading.Thread(target=_run_env_sync, daemon=True).start()
    threading.Thread(target=_run_heartbeat, daemon=True).start()
    threading.Thread(target=_run_tg_polling, daemon=True).start()
    threading.Thread(target=_run_msg_sum, daemon=True).start()
    threading.Thread(target=_run_reminders, daemon=True).start()
    threading.Thread(target=_run_schedule, daemon=True).start()

    # 信箱巡视默认关闭 (需配置 GMAIL_BRIDGE_URL 才有意义)
    # 如需启用，取消下一行注释
    # threading.Thread(target=_run_email, daemon=True).start()

    print("🐱 NapCat QQ 端点已就绪 (被动模式)，等待本地 NapCat 通过反向 WS 连接...")
    print("🌾 所有后台心跳线程已启动。")