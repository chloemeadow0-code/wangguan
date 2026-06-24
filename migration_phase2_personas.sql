-- ============================================================
-- 🌟 橘瓣记忆库 第二阶段增量迁移：人设隔离 + 记忆注入
-- ============================================================
-- 适用场景：在已有的 migration_rebuild_all.sql 基础上，
--           给 personas 表增加 system_prompt / is_active 字段，
--           用于支持"当前激活人设"与"按线路归档/注入"。
--
-- ✅ 非破坏性：不会删表、不会清数据，可重复执行。
-- ============================================================

-- 1. personas 表增加 system_prompt（该线路的人设提示词）
alter table personas
    add column if not exists system_prompt text default '';

-- 2. personas 表增加 is_active（是否当前激活，全局唯一）
--    一次只有一条 is_active=true，代表当前橘瓣聊天使用的线路
alter table personas
    add column if not exists is_active boolean default false;

-- 3. 给 is_active 加唯一约束（保证全局最多一条激活）
--    用 partial unique index：只约束 is_active=true 的行，允许多条 false 共存
drop index if exists uniq_personas_active;
create unique index uniq_personas_active
    on personas (is_active)
    where is_active = true;

-- 4. 如果当前没有任何激活人设，把第一条可见人设设为激活（兜底）
do $$
begin
    if not exists (select 1 from personas where is_active = true) then
        update personas
        set is_active = true
        where id = (
            select id from personas
            where is_visible = true
            order by sort_order asc, created_at asc
            limit 1
        );
    end if;
end $$;

-- ============================================================
-- ✅ 迁移完成
-- 接下来在面板「人设管理」里可以：
--   - 编辑 system_prompt（人设提示词）
--   - 点击「设为当前」切换 is_active
-- 网关会自动读取 is_active 的人设进行归档与记忆注入。
-- ============================================================