-- ============================================================
-- 🔒 橘瓣记忆库 第四阶段：RLS 安全收口（Row Level Security）
-- ============================================================
-- 目的：消除 Supabase 邮件"RLS 未开启"告警，防止 anon/publishable
--       key 持有者直接读写表数据。
--
-- 安全模型：
--   网关后端持有 service_role key  → 绕过 RLS，可正常增删改查
--   前端只持有 API_SECRET           → 只能通过网关访问，不直连表
--   anon / 普通登录用户            → RLS 拦截，完全无权限
--
-- 本脚本：开启 + force RLS，且【不创建任何 policy】。
--   无 policy = 默认拒绝一切非 service_role 的访问（最稳）。
--
-- ✅ 可重复执行：表不存在时自动跳过，不会报错。
-- ============================================================

do $$
declare
  t text;
  -- 文档「十一·4 需要处理的表」全量清单
  tbls text[] := array[
    -- 橘瓣核心（重点）
    'chat_messages',
    'chat_archive',
    'personas',
    'persona_map',
    -- 向量表（第五阶段预留，一并收口）
    'chat_message_embeddings',
    -- 通用网关表（可能存在）
    'memories',
    'user_facts',
    'reminders',
    'memory_house',
    'expenses',
    'device_data'
  ];
begin
  foreach t in array tbls loop
    if to_regclass(t) is not null then
      -- 1. 开启行级安全
      execute format('alter table %I enable row level security', t);
      -- 2. 强制：即使 table owner 也受 RLS 约束（service_role 仍绕过）
      execute format('alter table %I force row level security', t);
      raise notice '✅ 已开启 RLS (enable + force): %', t;
    else
      raise notice '⏭️ 表不存在，跳过: %', t;
    end if;
  end loop;
end $$;

-- ============================================================
-- 验证：执行下面查询应看到所有表 rls=true、force=true
-- ============================================================
-- select
--   tablename,
--   rowsecurity  as rls_enabled,
--   forcerowsecurity as rls_forced
-- from pg_tables
-- where schemaname = 'public'
--   and tablename in (
--     'chat_messages','chat_archive','personas','persona_map',
--     'chat_message_embeddings','memories','user_facts',
--     'reminders','memory_house','expenses','device_data'
--   );

-- ============================================================
-- ⚠️ 回退脚本（仅当网关未配 service_role 导致全挂时临时使用）
--    执行后会重新放开所有访问，请尽快修好 service_role 再重跑上方主脚本！
-- ============================================================
-- do $$
-- declare t text;
-- begin
--   foreach t in array array[
--     'chat_messages','chat_archive','personas','persona_map',
--     'chat_message_embeddings','memories','user_facts',
--     'reminders','memory_house','expenses','device_data'
--   ] loop
--     if to_regclass(t) is not null then
--       execute format('alter table %I no force row level security', t);
--       execute format('alter table %I disable row level security', t);
--     end if;
--   end loop;
-- end $$;

-- ============================================================
-- ✅ 完成。
-- 接下来按文档「十一·5 实施顺序」测试：
--   1. 确认 Zeabur 环境变量 SUPABASE_KEY = service_role key（不是 anon）
--   2. 面板查询/新增/编辑/删除 全部正常
--   3. MCP 工具 memory_write / archive_write 正常
--   4. 橘瓣聊天自动归档/提炼正常
-- ============================================================