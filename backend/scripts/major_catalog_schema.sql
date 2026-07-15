-- 国考专业目录表（学科门类 → 专业类 → 专业 三级）
-- 数据来源：教育部《学位授予和人才培养学科目录》（2022年版）
-- 与岗位表 product.positions.major_required 解耦，作为专业选择的权威数据源

begin;

create table if not exists product.major_categories (
    code        varchar(2) primary key,           -- 学科门类代码，如 01
    name        varchar(50) not null,             -- 学科门类名称，如 哲学
    display_order int not null default 0,
    created_at  timestamptz not null default now()
);

create table if not exists product.major_sub_categories (
    code          varchar(4) primary key,         -- 专业类代码，如 0101
    category_code varchar(2) not null,            -- 所属门类
    name          varchar(50) not null,           -- 专业类名称，如 哲学类
    display_order int not null default 0,
    created_at    timestamptz not null default now(),
    constraint fk_sub_category_parent
        foreign key (category_code)
        references product.major_categories(code)
);

create table if not exists product.major_catalog (
    code              varchar(8) primary key,     -- 专业代码，如 010101
    sub_category_code varchar(4) not null,        -- 所属专业类
    name              varchar(100) not null,      -- 专业名称，如 哲学
    display_order     int not null default 0,
    created_at        timestamptz not null default now(),
    constraint fk_major_sub_parent
        foreign key (sub_category_code)
        references product.major_sub_categories(code)
);

create index if not exists idx_major_sub_category
    on product.major_catalog(sub_category_code);
create index if not exists idx_major_sub_parent
    on product.major_sub_categories(category_code);
create index if not exists idx_major_name
    on product.major_catalog(name);
create index if not exists idx_major_name_trgm
    on product.major_catalog using gin (name gin_trgm_ops);

-- 启用 pg_trgm 扩展（如果尚未启用）以支持模糊搜索
do $$
begin
    if not exists (
        select 1 from pg_extension where extname = 'pg_trgm'
    ) then
        create extension pg_trgm;
    end if;
end$$;

commit;
