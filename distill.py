"""
橘瓣精华记忆 · 自动提炼引擎 (Distill Engine)
=================================================
从 chat_archive 对话原文中，用 LLM 提取长期有价值的原子事实，
写入 chat_messages 精华记忆库（按 assistant_id 隔离）。

核心流程：
    chat_archive 原文
      → LLM 提取原子事实（强制 [标签] 格式 + 真实角色名）
      → 事实去重检查（对同 assistant_id 下已有 chat_messages 比对）
        → 相同事实不同说法 → 更新旧记录
        → 更具体的新事实 → 替换旧事实
        → 冲突事实（如喜欢→讨厌）→ 标记待确认
      → 新事实 → 写入 chat_messages

设计原则：
- 全程异步，失败只记日志，不阻塞对话
- 强约束 prompt：人名规则、分类规则、只提取有价值事实
- 按 assistant_id（人设/线路）严格隔离
"""

import os
import re
import json
import asyncio
import datetime

# 复用 gateway 的 supabase 客户端和日志
from gateway import _get_supabase, _log

# 允许的精华记忆分类
ALLOWED_CATEGORIES = ["关系", "剧情", "喜好", "雷点", "设定", "档案"]
ALLOWED_TAGS = [f"[{c}]" for c in ALLOWED_CATEGORIES]

# 单次提炼最多提取的事实条数（防止 LLM 失控输出）
MAX_FACTS_PER_DISTILL = 20

# 去重时，拉取已有记忆的上限（按 assistant_id）
DEDUP_SCAN_LIMIT = 200


# ==========================================
# 核心：提取原子事实
# ==========================================

# 提炼用的 system prompt —— 强约束
_DISTILL_SYSTEM_PROMPT = """你是一个记忆提炼专家。你的任务是从对话中提取长期有价值的原子事实。

## 提取规则

### 只提取这些类型的信息：
- [关系] 人与人之间的连接（谁和谁是什么关系）
- [剧情] 发生过的事件、进展、转折
- [喜好] 人物偏好
- [雷点] 人物反感、禁忌、容易触发不适的点
- [设定] 世界观、角色背景、固定设定
- [档案] 资料卡、长期背景、规则性说明

### 不要提取这些：
- 寒暄、日常问候
- 无长期价值的抱怨
- 单次技术报错流水
- 没有信息增量的重复解释
- 纯情绪宣泄无具体事实

### 人名规则（极其重要）：
- 角色线（如 骆云影_联姻线）必须写真实角色名（如 函函/骆云影/秦梧）
- 绝对不能写 "用户"、"助手"、"他"、"她"、"你"、"我" 等模糊代词
- 如果是对皮下/模型/助手的讨论（不是角色互动），要写明对象，如 "[档案] 函函与模型讨论了..."
- 无设定助手线（如 默认助手_技术线）可以用 "用户"/"助手"
- 如果能确定用户昵称，优先用昵称

### 输出格式（严格遵守）：
每行一条事实，必须以 [标签] 开头，格式如：
[关系] 骆云影和函函是联姻关系
[喜好] 函函喜欢微辣味的烧烤
[雷点] 函函讨厌被敷衍

不要输出任何解释、编号、markdown 格式，每行一条纯事实。
如果没有值得提取的长期事实，输出空内容。
"""


def _build_distill_prompt(assistant_id: str, conversation_text: str) -> str:
    """构造提炼用的 user prompt"""
    return f"""当前人设/线路：{assistant_id}

以下是一段对话记录，请从中提取长期有价值的原子事实：

---对话开始---
{conversation_text[:8000]}
---对话结束---

请提取事实，每行一条，以 [标签] 开头。最多提取 {MAX_FACTS_PER_DISTILL} 条。"""


async def distill_facts(assistant_id: str, conversation_text: str) -> list:
    """
    【核心提炼函数】从对话文本中提取原子事实。

    参数：
    - assistant_id: 人设/线路名
    - conversation_text: 对话原文（可以是单轮，也可以是多轮拼接）

    返回：
    - 事实列表，每条形如 "[关系] 骆云影和函函是联姻关系"
    """
    if not conversation_text or not conversation_text.strip():
        return []

    try:
        from server import _get_llm_client, _ask_llm_async
    except ImportError:
        _log("❌ [distill] 无法导入 server 模块")
        return []

    client = _get_llm_client("main_chat")
    if not client:
        _log("⚠️ [distill] 未配置 CHAT_API_KEY，跳过提炼")
        return []

    prompt = _build_distill_prompt(assistant_id, conversation_text)

    try:
        raw = await _ask_llm_async(
            client, prompt,
            system_prompt=_DISTILL_SYSTEM_PROMPT,
            temperature=0.3  # 低温度，保证提取稳定
        )
    except Exception as e:
        _log(f"❌ [distill] LLM 调用失败: {e}")
        return []

    if not raw or not raw.strip():
        _log(f"📝 [distill] [{assistant_id}] 本轮无可提取的长期事实")
        return []

    # 解析输出：每行一条，必须以 [标签] 开头
    facts = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        # 去掉可能的编号前缀 "1. " "2. "
        line = re.sub(r'^\d+[\.\)]\s*', '', line)
        # 去掉可能的 markdown 前缀 "- " "* "
        line = re.sub(r'^[-\*]\s*', '', line)
        line = line.strip()

        if not line:
            continue

        # 校验标签
        has_tag = any(line.startswith(tag) for tag in ALLOWED_TAGS)
        if not has_tag:
            continue  # 不符合格式的直接丢弃

        # 限制单条长度（防止 LLM 失控写长篇）
        if len(line) > 500:
            line = line[:500]

        facts.append(line)

    _log(f"📝 [distill] [{assistant_id}] 提取出 {len(facts)} 条候选事实")
    return facts


# ==========================================
# 去重 / 更新 / 冲突标记
# ==========================================

def _normalize_text(text: str) -> str:
    """归一化文本用于相似度比较：去标签、去空格、转小写"""
    # 去掉 [标签] 前缀
    t = re.sub(r'^\[.*?\]\s*', '', text)
    # 去空格和标点
    t = re.sub(r'[\s\u3000，。、！？.,!?;:：；""''\'\"]+', '', t)
    return t.lower()


def _extract_subjects(text: str) -> set:
    """从文本中提取人名/实体（用于匹配去重）。
    简单实现：提取中文 2-4 字连续片段（近似人名）。"""
    t = re.sub(r'^\[.*?\]\s*', '', text)
    # 匹配中文连续字符（2-4字），作为候选实体
    matches = re.findall(r'[\u4e00-\u9fff]{2,4}', t)
    return set(matches) if matches else set()


def _is_same_fact(new_fact: str, existing_fact: str) -> bool:
    """
    判断两条事实是否是"同一事实的不同说法"。

    策略：
    1. 归一化后完全相同 → True
    2. 主体（人名集合）相同 + 关键词高度重叠 → True
    """
    n_norm = _normalize_text(new_fact)
    e_norm = _normalize_text(existing_fact)

    # 1. 归一化完全相同
    if n_norm == e_norm:
        return True

    # 2. 一方是另一方的子串
    if len(n_norm) > 4 and len(e_norm) > 4:
        if n_norm in e_norm or e_norm in n_norm:
            return True

    # 3. 人名集合相同 + 文本相似度
    n_names = _extract_subjects(new_fact)
    e_names = _extract_subjects(existing_fact)
    if n_names and e_names:
        # 人名交集占比高
        overlap = n_names & e_names
        if overlap and len(overlap) / max(len(n_names), len(e_names)) >= 0.6:
            # 进一步看归一化文本的字符重叠率
            common = sum(1 for c in n_norm if c in e_norm)
            similarity = common / max(len(n_norm), len(e_norm), 1)
            if similarity >= 0.65:
                return True

    return False


def _is_more_specific(new_fact: str, existing_fact: str) -> bool:
    """判断新事实是否比旧事实更具体（长度更长且包含旧事实核心内容）"""
    n_norm = _normalize_text(new_fact)
    e_norm = _normalize_text(existing_fact)
    return len(n_norm) > len(e_norm) + 3


def _is_conflict(new_fact: str, existing_fact: str) -> bool:
    """
    判断是否是冲突事实（如 喜欢→讨厌）。

    策略：检测反义关键词对。
    """
    conflict_pairs = [
        ("喜欢", "讨厌"), ("喜欢", "不喜欢"),
        ("爱", "恨"), ("爱", "讨厌"),
        ("好", "坏"),
        ("是", "不是"),
    ]
    n_norm = _normalize_text(new_fact)
    e_norm = _normalize_text(existing_fact)

    # 必须主体相似才判冲突
    n_names = _extract_subjects(new_fact)
    e_names = _extract_subjects(existing_fact)
    if not (n_names and e_names):
        return False
    overlap = n_names & e_names
    if not overlap or len(overlap) / max(len(n_names), len(e_names)) < 0.5:
        return False

    # 检查反义词
    for w1, w2 in conflict_pairs:
        if (w1 in e_norm and w2 in n_norm) or (w2 in e_norm and w1 in n_norm):
            return True

    return False


async def dedup_and_update(assistant_id: str, new_facts: list) -> dict:
    """
    【去重与更新】对提炼出的新事实，与已有 chat_messages 比对：
    - 相同事实 → 更新旧记录（如果新事实更具体）
    - 冲突事实 → 写入 [档案] 标记待确认，不覆盖
    - 全新事实 → 新增

    返回统计：{"new": N, "updated": N, "conflict": N, "skipped": N}
    """
    sb = _get_supabase()
    if not sb:
        _log("⚠️ [distill] 数据库未连接，跳过去重")
        return {"new": 0, "updated": 0, "conflict": 0, "skipped": len(new_facts)}

    if not new_facts:
        return {"new": 0, "updated": 0, "conflict": 0, "skipped": 0}

    stats = {"new": 0, "updated": 0, "conflict": 0, "skipped": 0}

    try:
        # 拉取该人设下的已有精华记忆（用于去重比对）
        def _fetch_existing():
            return sb.table("chat_messages").select("id, content, category, created_at") \
                .eq("assistant_id", assistant_id) \
                .order("created_at", desc=True) \
                .limit(DEDUP_SCAN_LIMIT).execute()
        res = await asyncio.to_thread(_fetch_existing)
        existing = res.data if res and res.data else []
    except Exception as e:
        _log(f"⚠️ [distill] 拉取已有记忆失败: {e}")
        return {"new": 0, "updated": 0, "conflict": 0, "skipped": len(new_facts)}

    for new_fact in new_facts:
        # 提取新事实的分类
        new_category = ""
        for c in ALLOWED_CATEGORIES:
            if new_fact.startswith(f"[{c}]"):
                new_category = c
                break

        # 与已有记忆比对
        matched_existing = None
        is_conflict = False

        for ex in existing:
            ex_content = ex.get("content", "")
            if _is_conflict(new_fact, ex_content):
                is_conflict = True
                matched_existing = ex
                break
            if _is_same_fact(new_fact, ex_content):
                matched_existing = ex
                break

        if is_conflict:
            # 冲突事实：不覆盖，标记待确认写入
            # 把冲突信息写入 [档案]
            conflict_note = (
                f"[档案] ⚠️待确认：发现可能与已有记忆冲突。"
                f"新信息：{_strip_tag(new_fact)}；"
                f"旧记忆：{_strip_tag(matched_existing.get('content', ''))}。"
                f"请人工确认哪个正确。"
            )
            if len(conflict_note) > 500:
                conflict_note = conflict_note[:500]
            await _insert_fact(sb, assistant_id, conflict_note, "档案")
            stats["conflict"] += 1
            _log(f"⚠️ [distill] [{assistant_id}] 检测到冲突，标记待确认: {_strip_tag(new_fact)[:40]}")

        elif matched_existing:
            # 相同事实：判断是否需要更新
            ex_content = matched_existing.get("content", "")
            ex_id = matched_existing.get("id")
            if _is_more_specific(new_fact, ex_content):
                # 新事实更具体 → 更新旧记录
                await _update_fact(sb, ex_id, new_fact, new_category)
                stats["updated"] += 1
                _log(f"🔄 [distill] [{assistant_id}] 更新旧记忆(id={ex_id}): {new_fact[:50]}")
            else:
                # 相同信息量 → 跳过
                stats["skipped"] += 1

        else:
            # 全新事实 → 新增
            await _insert_fact(sb, assistant_id, new_fact, new_category)
            stats["new"] += 1
            # 同时加入 existing 列表，避免后续新事实与它重复
            existing.append({"id": "temp", "content": new_fact, "category": new_category})

    _log(
        f"✅ [distill] [{assistant_id}] 提炼完成："
        f"新增{stats['new']} / 更新{stats['updated']} / "
        f"冲突{stats['conflict']} / 跳过{stats['skipped']}"
    )
    return stats


# ==========================================
# 数据库操作辅助
# ==========================================

def _strip_tag(text: str) -> str:
    """去掉 [标签] 前缀"""
    return re.sub(r'^\[.*?\]\s*', '', text)


async def _insert_fact(sb, assistant_id: str, content: str, category: str):
    """写入一条精华事实到 chat_messages"""
    try:
        def _do():
            return sb.table("chat_messages").insert({
                "assistant_id": assistant_id,
                "conversation_id": "",
                "content": content,
                "category": category,
                "role": "assistant",
            }).execute()
        await asyncio.to_thread(_do)
    except Exception as e:
        _log(f"❌ [distill] 写入事实失败: {e}")


async def _update_fact(sb, record_id: str, content: str, category: str):
    """更新一条已有精华事实"""
    try:
        update_fields = {"content": content}
        if category:
            update_fields["category"] = category
        def _do():
            return sb.table("chat_messages").update(update_fields).eq("id", record_id).execute()
        await asyncio.to_thread(_do)
    except Exception as e:
        _log(f"❌ [distill] 更新事实失败: {e}")


# ==========================================
# 一键提炼入口
# ==========================================

async def run_distill(assistant_id: str, conversation_text: str) -> dict:
    """
    【完整提炼流程】提取 + 去重 + 写入。

    供 gateway.py 归档后异步调用，也可供 MCP 工具 / 面板 API 调用。

    返回：
    - {"ok": True, "facts": [...], "stats": {...}}
    - {"ok": False, "error": "..."}
    """
    if not assistant_id or not conversation_text:
        return {"ok": False, "error": "assistant_id 和 conversation_text 不能为空"}

    try:
        # 1. 提取
        facts = await distill_facts(assistant_id, conversation_text)
        if not facts:
            return {"ok": True, "facts": [], "stats": {"new": 0, "updated": 0, "conflict": 0, "skipped": 0}}

        # 2. 去重 + 写入
        stats = await dedup_and_update(assistant_id, facts)

        return {"ok": True, "facts": facts, "stats": stats}
    except Exception as e:
        _log(f"❌ [distill] 提炼流程异常: {e}")
        return {"ok": False, "error": str(e)}


async def distill_from_archive(assistant_id: str, limit: int = 10) -> dict:
    """
    【从 chat_archive 拉取最近对话并提炼】

    拉取该人设最近 N 条归档记录，拼接成文本后提炼。

    参数：
    - assistant_id: 人设/线路名
    - limit: 拉取多少条归档记录（默认10）

    返回：
    - {"ok": True, "facts": [...], "stats": {...}}
    """
    sb = _get_supabase()
    if not sb:
        return {"ok": False, "error": "数据库未连接"}

    try:
        def _fetch():
            return sb.table("chat_archive").select("role, content, created_at") \
                .eq("assistant_id", assistant_id) \
                .order("created_at", desc=True) \
                .limit(limit).execute()
        res = await asyncio.to_thread(_fetch)
        if not res or not res.data:
            return {"ok": True, "facts": [], "stats": {"new": 0, "updated": 0, "conflict": 0, "skipped": 0},
                    "warning": "该人设下暂无归档记录"}

        # 拼接成对话文本（按时间正序）
        rows = list(reversed(res.data))
        parts = []
        for r in rows:
            role = r.get("role", "user")
            content = r.get("content", "")
            label = "用户" if role == "user" else "助手"
            parts.append(f"{label}：{content}")
        conversation_text = "\n".join(parts)

        return await run_distill(assistant_id, conversation_text)
    except Exception as e:
        _log(f"❌ [distill] 从归档提炼失败: {e}")
        return {"ok": False, "error": str(e)}