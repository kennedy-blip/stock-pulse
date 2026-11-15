"""Daily, leakage-free features shared by training and recursive inference."""
from datetime import date, timedelta
import polars as pl

FEATURES = ["product_id", "unit_price", "lag_1", "lag_7", "lag_14", "rolling_mean_7", "rolling_mean_30", "day_of_week", "month", "is_holiday", "local_price_index"]


def _day(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def make_features(sales, products, factors, end_date=None):
    """Aggregate stores, fill missing calendar days with zero, then shift before rolling.

    A missing transaction day is assumed to mean zero sales. External factors are
    global daily values; absent days default to non-holiday and price index 1.
    """
    if not sales or not products:
        raise ValueError("Sales and products are required")
    product_map = {int(p['id']): p for p in products}
    totals = {}
    for s in sales:
        pid, day = int(s['product_id']), _day(s['sale_date'])
        if pid in product_map:
            totals[(pid, day)] = totals.get((pid, day), 0.0) + float(s['quantity_sold'])
    if not totals:
        raise ValueError("No sales match the requested products")
    rows = []
    for pid in sorted({p for p, _ in totals}):
        dates = [d for p, d in totals if p == pid]
        day, last = min(dates), _day(end_date) if end_date else max(dates)
        while day <= last:
            rows.append({'product_id': pid, 'date': day, 'quantity_sold': totals.get((pid, day), 0.0), 'unit_price': float(product_map[pid]['unit_price'])})
            day += timedelta(days=1)
    if not rows:
        raise ValueError("No sales on or before the requested end date")
    df = pl.DataFrame(rows).sort(['product_id', 'date'])
    if factors:
        ext = pl.DataFrame([{'date': _day(f['date']), 'is_holiday': int(bool(f.get('is_holiday', False))), 'local_price_index': float(f['local_price_index']) if f.get('local_price_index') is not None else 1.0} for f in factors])
        df = df.join(ext, on='date', how='left')
    else:
        df = df.with_columns(pl.lit(None, dtype=pl.Int64).alias('is_holiday'), pl.lit(None, dtype=pl.Float64).alias('local_price_index'))
    return df.with_columns(
        pl.col('is_holiday').fill_null(0), pl.col('local_price_index').fill_null(1.0),
        pl.col('date').dt.weekday().alias('day_of_week'), pl.col('date').dt.month().alias('month'),
        *[pl.col('quantity_sold').shift(n).over('product_id').alias(f'lag_{n}') for n in (1, 7, 14)],
        *[pl.col('quantity_sold').shift(1).rolling_mean(window_size=n, min_samples=n).over('product_id').alias(f'rolling_mean_{n}') for n in (7, 30)],
    )
