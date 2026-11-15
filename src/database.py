"""Server-side Supabase access; never expose the service role key to clients."""
import os
from functools import lru_cache
from supabase import create_client


@lru_cache(maxsize=1)
def get_client():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured")
    return create_client(url, key)


def fetch_all(table, columns="*", filters=None):
    """Paginate explicitly: PostgREST typically caps each response at 1,000 rows."""
    rows, offset, page_size = [], 0, 1000
    while True:
        query = get_client().table(table).select(columns)
        for column, operator, value in filters or []:
            query = getattr(query, operator)(column, value)
        # Stable ordering prevents page boundaries from changing arbitrarily.
        query = query.order("date" if table == "external_factors" else "id")
        page = query.range(offset, offset + page_size - 1).execute().data or []
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += len(page)


def log_forecasts(rows):
    if rows:
        get_client().table("demand_forecasts").insert(rows).execute()
