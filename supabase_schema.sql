-- ==========================================
-- OrangeChat / 橘瓣记忆库 建表脚本
-- 在 Supabase SQL Editor 中执行
-- ==========================================

-- 启用 pgvector 扩展（向量检索第五阶段才用，先开启不影响）
create extension if not exists vector;
create extension if not exists pg_trgm;  -- 关键词模糊检索加速

-- ==========================================
-- 1. 对话归档表 chat_archive
-- 保存原始聊天内容（原文），用于回看/重新提炼/排查上下文
-- ==========================================
create table if not exists chat_archive (
    id uuid primary key default gen_random_uuid(),
    assistant_id text not null,                  -- 人设/线路名，如 骆云影_联姻线
    conversation_id text default '',             -- 会话标记（仅记录来源，不作为隔离边界）
    role text not null check (role in ('user','assistant','system')),
    content text not null default '',
    category text default 'archive',
    created_at timestamptz default now()
);

create index if not exists idx_chat_archive_assistant_created
    on chat_archive (assistant_id, created_at desc);
create index if not exists idx_chat_archive_content_trgm
    on chat_archive using gin (content gin_trgm_ops);

-- ==========================================
-- 2. 精华记忆表 chat_messages
-- 提炼后的原子事实，按 assistant_id 隔离。content 必须以 [标签] 开头
-- ==========================================
create table if not exists chat_messages (
    id uuid primary key default gen_random_uuid(),
    assistant_id text not null,                  -- 人设/线路名（记忆隔离边界）
    conversation_id text default '',             -- 来源会话（仅记录，不隔离）
    role text default 'assistant',
    content text not null,                       -- 必须以 [标签] 开头，如 [关系] xxx
    category text default '',                    -- 关系/剧情/喜好/雷点/设定/档案
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create index if not exists idx_chat_messages_assistant_cat
    on chat_messages (assistant_id, category, created_at desc);
create index if not exists idx_chat_messages_content_trgm
    on chat_messages using gin (content gin_trgm_ops);

-- updated_at 自动更新触发器
create or replace function trg_set_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

drop trigger if exists trg_chat_messages_updated_at on chat_messages;
create trigger trg_chat_messages_updated_at
    before update on chat_messages
    for each row execute function trg_set_updated_at();

-- ==========================================
-- 3. 人设/线路表 personas
-- 管理面板可见的人设或线路
-- ==========================================
create table if not exists personas (
    id text primary key,                         -- 人设/线路名，如 骆云影_联姻线
    display_name text not null,                  -- 展示名
    is_visible boolean default true,             -- 是否在面板可见
    sort_order int default 100,                  -- 排序权重（小靠前）
    created_at timestamptz default now()
);

-- 预填确认可见的人设
insert into personas (id, display_name, is_visible, sort_order) values
    ('默认助手_技术线', '默认助手_技术线', true, 10),
    ('骆云影_联姻线',   '骆云影_联姻线',   true, 20),
    ('测试助手',       '测试助手',       true, 30)
on conflict (id) do nothing;

-- 预填隐藏的 assistant_id（UUID/调试/未映射等）
insert into personas (id, display_name, is_visible, sort_order) values
    ('diagnose',         '诊断(隐藏)',   false, 900),
    ('debug',            '调试(隐藏)',   false, 910),
    ('manual',           '手动(隐藏)',   false, 920),
    ('unknown',          '未知(隐藏)',   false, 930),
    ('未映射',           '未映射(隐藏)', false, 940),
    ('kiro_技术咨询线',  'kiro技术(隐藏)', false, 950)
on conflict (id) do nothing;

-- ==========================================
-- 4. persona_map：assistant_id 显示名映射
-- 把 UUID 或内部名映射成中文名
-- ==========================================
create table if not exists persona_map (
    assistant_id text primary key,               -- 实际写入的 id（可能是 UUID）
    display_name text not null,                  -- 面板展示名
    persona_id text references personas(id) on delete set null,  -- 关联人设
    created_at timestamptz default now()
);

-- ==========================================
-- 5. 向量表 chat_message_embeddings（第五阶段用）
-- 现在先建好结构，不写入；on delete cascade 保证主表删除时向量自动清理
-- ==========================================
create table if not exists chat_message_embeddings (
    id uuid primary key default gen_random_uuid(),
    message_id uuid references chat_messages(id) on delete cascade,
    assistant_id text not null,
    category text,
    content text not null,
    embedding vector(1024),                      -- BAAI/bge-m3 实测维度，按需调整
    embedding_model text default 'BAAI/bge-m3',
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create index if not exists idx_embeddings_message
    on chat_message_embeddings (message_id);
create index if not exists idx_embeddings_assistant
    on chat_message_embeddings (assistant_id);

-- 向量相似度检索索引（IVFFLAT，第五阶段启用）
-- create index if not exists idx_embeddings_vector
--     on chat_message_embeddings using ivfflat (embedding vector_cosine_ops) with (lists = 100);

drop trigger if exists trg_embeddings_updated_at on chat_message_embeddings;
create trigger trg_embeddings_updated_at
    before update on chat_message_embeddings
    for each row execute function trg_set_updated_at();

-- ==========================================
-- 6. RLS 策略（第四阶段，默认先注释，确认 service_role 稳定后开启）
-- 开启后：前端 anon key 无法访问，仅 service_role（网关后端持有）可读写
-- ==========================================

-- alter table chat_archive enable row level security;
-- alter table chat_messages enable row level security;
-- alter table personas enable row level security;
-- alter table persona_map enable row level security;
-- alter table chat_message_embeddings enable row level security;

-- service_role 自动绕过 RLS，无需额外 policy。
-- 如需前端 anon 直读 personas（只读），可单独加：
-- create policy "public read personas" on personas for select using (is_visible = true);

-- ==========================================
-- 完成。执行后可在网关面板用 API_SECRET 访问 /api/panel/*
-- ==========================================