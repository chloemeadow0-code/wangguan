-- ============================================================
-- 🧨 橘瓣记忆库 一键删表重建（彻底版）
-- ============================================================
-- 适用场景：无论表处于什么乱七八糟的状态，
--           先全部删掉，再全新创建，一了百了。
--
-- ⚠️ 警告：会清空 chat_archive / chat_messages / personas /
--         persona_map / chat_message_embeddings 的全部数据！
--         如果库里有重要数据请先在 Supabase 导出备份。
--
-- 在 Supabase SQL Editor 中执行一次即可。
-- ============================================================

-- 扩展
create extension if not exists vector;
create extension if not exists pg_trgm;

-- ============================================================
-- 第一步：全部删除（CASCADE 连带外键/索引/触发器一起删）
-- ============================================================
drop table if exists chat_message_embeddings cascade;
drop table if exists persona_map cascade;
drop table if exists chat_messages cascade;
drop table if exists chat_archive cascade;
drop table if exists personas cascade;

-- 清掉可能残留的触发器函数
drop function if exists trg_set_updated_at() cascade;

-- ============================================================
-- 第二步：全新创建
-- ============================================================

-- 1. chat_archive（对话归档）
create table chat_archive (
    id uuid primary key default gen_random_uuid(),
    assistant_id text not null,
    conversation_id text default '',
    role text check (role in ('user','assistant','system')),
    content text default '',
    category text default 'archive',
    created_at timestamptz default now()
);
create index idx_chat_archive_assistant_created
    on chat_archive (assistant_id, created_at desc);
create index idx_chat_archive_content_trgm
    on chat_archive using gin (content gin_trgm_ops);

-- 2. chat_messages（精华记忆）
create table chat_messages (
    id uuid primary key default gen_random_uuid(),
    assistant_id text not null,
    conversation_id text default '',
    role text default 'assistant',
    content text not null,
    category text default '',
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);
create index idx_chat_messages_assistant_cat
    on chat_messages (assistant_id, category, created_at desc);
create index idx_chat_messages_content_trgm
    on chat_messages using gin (content gin_trgm_ops);

-- 3. personas（人设/线路表）
create table personas (
    id text primary key,
    display_name text not null,
    is_visible boolean default true,
    is_active boolean default false,        -- 🌟 是否当前激活（全局唯一）
    system_prompt text default '',          -- 🌟 该线路的人设提示词
    sort_order int default 100,
    created_at timestamptz default now()
);
-- 保证全局最多一条激活人设
create unique index uniq_personas_active
    on personas (is_active)
    where is_active = true;

-- 可见人设请在面板「人设管理」中自行添加，此处不预填任何数据。

-- 隐藏人设（系统兜底用，不出现在面板筛选里）
insert into personas (id, display_name, is_visible, sort_order) values
    ('diagnose',         '诊断(隐藏)',     false, 900),
    ('debug',            '调试(隐藏)',     false, 910),
    ('manual',           '手动(隐藏)',     false, 920),
    ('unknown',          '未知(隐藏)',     false, 930),
    ('未映射',           '未映射(隐藏)',   false, 940),
    ('kiro_技术咨询线',  'kiro技术(隐藏)', false, 950);

-- 4. persona_map（映射表）
create table persona_map (
    assistant_id text primary key,
    display_name text not null,
    persona_id text references personas(id) on delete set null,
    created_at timestamptz default now()
);

-- 5. chat_message_embeddings（向量表，预留给第五阶段）
create table chat_message_embeddings (
    id uuid primary key default gen_random_uuid(),
    message_id uuid references chat_messages(id) on delete cascade,
    assistant_id text,
    category text,
    content text,
    embedding vector(1024),
    embedding_model text default 'BAAI/bge-m3',
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);
create index idx_embeddings_message
    on chat_message_embeddings (message_id);
create index idx_embeddings_assistant
    on chat_message_embeddings (assistant_id);

-- ============================================================
-- 第三步：updated_at 自动更新触发器
-- ============================================================
create or replace function trg_set_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

create trigger trg_chat_messages_updated_at
    before update on chat_messages
    for each row execute function trg_set_updated_at();

create trigger trg_embeddings_updated_at
    before update on chat_message_embeddings
    for each row execute function trg_set_updated_at();

-- ============================================================
-- ✅ 全部重建完成！
-- 五张表全新创建，结构干净，面板/网关/MCP 工具都能正常用。
-- ============================================================