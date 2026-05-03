-- ============================================================
-- Kakeibo (家計簿) Database Schema
-- For Supabase (PostgreSQL)
-- Run this in the Supabase SQL editor
-- ============================================================

-- ============================================================
-- ENUMS
-- ============================================================
create type account_kind as enum ('ACCOUNT', 'CARD');
create type owner_type as enum ('HOUSE', 'B', 'G');
create type category_type as enum ('INCOME', 'EXPENSE');
create type var_type as enum ('CARD', 'ADVANCE', 'PENSION', 'OTHER');
create type todo_status as enum ('TODO', 'DOING', 'DONE');

-- ============================================================
-- ACCOUNTS (口座 / カード)
-- ============================================================
create table accounts (
  id         bigint primary key generated always as identity,
  kind       account_kind not null default 'ACCOUNT',
  owner      owner_type   not null default 'HOUSE',
  name       text         not null,
  created_at timestamptz  not null default now()
);
create index idx_accounts_kind_owner on accounts(kind, owner);

-- ============================================================
-- CATEGORIES (カテゴリ)
-- ============================================================
create table categories (
  id         bigint primary key generated always as identity,
  type       category_type not null,
  code       text          not null default '',
  name       text          not null,
  sort_order int           not null default 0,
  created_at timestamptz   not null default now(),
  unique (type, name)
);
create index idx_categories_type on categories(type, sort_order);

-- ============================================================
-- MONTHLY_INCOMES (月次収入)
-- ============================================================
create table monthly_incomes (
  id          bigint primary key generated always as identity,
  month       date         not null,
  owner       owner_type   not null,
  category_id bigint       not null references categories(id) on delete restrict,
  amount      int          not null,
  memo        text         not null default '',
  created_at  timestamptz  not null default now(),
  unique (month, owner, category_id, memo)
);
create index idx_monthly_incomes_month on monthly_incomes(month desc);

-- ============================================================
-- MONTHLY_VARIABLE_EXPENSES (変動費)
-- ============================================================
create table monthly_variable_expenses (
  id          bigint     primary key generated always as identity,
  month       date       not null,
  owner       owner_type not null,
  var_type    var_type   not null,
  category_id bigint     not null references categories(id) on delete restrict,
  card_id     bigint     references accounts(id) on delete restrict,
  amount      int        not null,
  memo        text       not null default '',
  created_at  timestamptz not null default now()
);
create index idx_variable_expenses_month on monthly_variable_expenses(month desc);
create index idx_variable_expenses_owner on monthly_variable_expenses(owner, month desc);

-- ============================================================
-- FIXED_EXPENSE_TEMPLATES (固定費テンプレート)
-- ============================================================
create table fixed_expense_templates (
  id          bigint primary key generated always as identity,
  owner       owner_type not null,
  category_id bigint     not null references categories(id) on delete restrict,
  amount      int        not null,
  memo        text       not null default '',
  is_active   boolean    not null default true,
  created_at  timestamptz not null default now()
);

-- ============================================================
-- BANK_BALANCES (口座残高)
-- ============================================================
create table bank_balances (
  id         bigint primary key generated always as identity,
  month      date   not null,
  account_id bigint not null references accounts(id) on delete cascade,
  balance    int    not null,
  updated_at timestamptz not null default now(),
  unique (month, account_id)
);
create index idx_bank_balances_month on bank_balances(month desc);
create index idx_bank_balances_account on bank_balances(account_id, month desc);

-- ============================================================
-- MONTHLY_SNAPSHOTS (月確定サマリー)
-- ============================================================
create table monthly_snapshots (
  id                bigint primary key generated always as identity,
  month             date   not null unique,
  income            int    not null default 0,
  fixed             int    not null default 0,
  variable          int    not null default 0,
  expense_total     int    not null default 0,
  diff              int    not null default 0,
  kpi_total_assets  int    not null default 0,
  kpi_house_savings int    not null default 0,
  kpi_invest_total  int    not null default 0,
  rakuten_eval      int    not null default 0,
  rakuten_cash_free int    not null default 0,
  rakuten_bank_b    int    not null default 0,
  aeon_bank_house   int    not null default 0,
  locked_at         timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create index idx_snapshots_month on monthly_snapshots(month desc);

-- ============================================================
-- MONTHLY_TODOS (月次TODO)
-- ============================================================
create table monthly_todos (
  id           bigint primary key generated always as identity,
  month        date        not null,
  title        text        not null,
  note         text        not null default '',
  status       todo_status not null default 'TODO',
  sort_order   int         not null default 0,
  completed_at timestamptz,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create index idx_todos_month on monthly_todos(month, sort_order);

-- ============================================================
-- MONTHLY_DASHBOARD_MEMOS (月次メモ)
-- ============================================================
create table monthly_dashboard_memos (
  id         bigint primary key generated always as identity,
  month      date   not null unique,
  memo       text   not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- ============================================================
-- APP_CONFIG (アプリ設定)
-- ============================================================
create table app_config (
  key   text primary key,
  value text not null
);

insert into app_config (key, value) values
  ('house_savings_base_month', '2025-12-01'),
  ('house_savings_base_value', '0');

-- ============================================================
-- ROW LEVEL SECURITY (RLS)
-- ============================================================
alter table accounts                  enable row level security;
alter table categories                enable row level security;
alter table monthly_incomes           enable row level security;
alter table monthly_variable_expenses enable row level security;
alter table fixed_expense_templates   enable row level security;
alter table bank_balances             enable row level security;
alter table monthly_snapshots         enable row level security;
alter table monthly_todos             enable row level security;
alter table monthly_dashboard_memos   enable row level security;
alter table app_config                enable row level security;

-- Allow all operations for authenticated users
create policy "authenticated_all" on accounts
  for all to authenticated using (true) with check (true);
create policy "authenticated_all" on categories
  for all to authenticated using (true) with check (true);
create policy "authenticated_all" on monthly_incomes
  for all to authenticated using (true) with check (true);
create policy "authenticated_all" on monthly_variable_expenses
  for all to authenticated using (true) with check (true);
create policy "authenticated_all" on fixed_expense_templates
  for all to authenticated using (true) with check (true);
create policy "authenticated_all" on bank_balances
  for all to authenticated using (true) with check (true);
create policy "authenticated_all" on monthly_snapshots
  for all to authenticated using (true) with check (true);
create policy "authenticated_all" on monthly_todos
  for all to authenticated using (true) with check (true);
create policy "authenticated_all" on monthly_dashboard_memos
  for all to authenticated using (true) with check (true);
create policy "authenticated_all" on app_config
  for all to authenticated using (true) with check (true);

-- ============================================================
-- SEED DATA: Default Categories
-- ============================================================
insert into categories (type, code, name, sort_order) values
  -- Income categories
  ('INCOME', '', '給与', 1),
  ('INCOME', '', 'ボーナス', 2),
  ('INCOME', '', '副収入', 3),
  ('INCOME', '', 'その他収入', 9),
  -- Expense categories (linked to var_type by code)
  ('EXPENSE', 'CARD',    'カード支払い', 1),
  ('EXPENSE', 'ADVANCE', '立替',        2),
  ('EXPENSE', 'PENSION', '年金・保険',   3),
  ('EXPENSE', 'OTHER',   'その他',       4),
  -- Additional expense categories
  ('EXPENSE', '', '食費',     10),
  ('EXPENSE', '', '日用品',   11),
  ('EXPENSE', '', '交通費',   12),
  ('EXPENSE', '', '医療費',   13),
  ('EXPENSE', '', '娯楽費',   14),
  ('EXPENSE', '', '教育費',   15),
  ('EXPENSE', '', 'お小遣い', 16),
  ('EXPENSE', '', '家賃',     17),
  ('EXPENSE', '', '光熱費',   18),
  ('EXPENSE', '', '通信費',   19);
