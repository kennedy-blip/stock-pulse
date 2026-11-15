-- Apply after client_imports.sql. Each operation is performed with the user's
-- access token; PostgreSQL RLS is the final tenant isolation boundary.
create policy "sales select own" on public.client_sales_imports for select to authenticated using (owner_id = auth.uid());
create policy "sales insert own" on public.client_sales_imports for insert to authenticated with check (owner_id = auth.uid());
grant select, insert on public.client_sales_imports to authenticated;
grant usage, select on sequence public.client_sales_imports_id_seq to authenticated;
