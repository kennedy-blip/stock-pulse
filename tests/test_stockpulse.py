from datetime import date, timedelta
import numpy as np
from fastapi.testclient import TestClient
from src.pipeline import FEATURES, make_features
from api import index


def sample_sales(pid=1, days=40, start=date(2025, 1, 1)):
    return [{'product_id': pid, 'sale_date': (start + timedelta(days=i)).isoformat(), 'quantity_sold': i + 1} for i in range(days)]


def test_features_are_daily_and_leakage_free():
    sales = sample_sales(days=32)
    sales.pop(1)  # missing date is zero, not a missing lag
    frame = make_features(sales, [{'id': 1, 'unit_price': 2}], [])
    row = frame.filter(frame['date'] == date(2025, 2, 1)).row(0, named=True)
    assert row['lag_1'] == 31
    assert row['lag_7'] == 25
    assert row['rolling_mean_7'] == sum(range(25, 32)) / 7
    assert row['rolling_mean_30'] == (sum(range(2, 32)) - 2) / 30
    assert row['day_of_week'] == 6


def test_groups_do_not_leak():
    sales = sample_sales(1) + sample_sales(2)
    for row in sales:
        if row['product_id'] == 2:
            row['quantity_sold'] = 100
    frame = make_features(sales, [{'id': 1, 'unit_price': 2}, {'id': 2, 'unit_price': 3}], [])
    first = frame.filter((__import__('polars').col('product_id') == 2)).row(0, named=True)
    assert first['lag_1'] is None and first['rolling_mean_30'] is None


class Model:
    def predict(self, x):
        return np.array([x[0, FEATURES.index('lag_1')] + 1])


def test_predict_recursive_and_logs(monkeypatch):
    today = date.today()
    sales = sample_sales(days=40, start=today - timedelta(days=39))
    def fetch(table, *args):
        return {'products': [{'id': 1, 'sku': 'A', 'unit_price': 2}], 'sales_transactions': sales.copy(), 'external_factors': []}[table]
    logged = []
    monkeypatch.setenv('STOCKPULSE_API_KEY', 'secret')
    monkeypatch.setattr(index, 'fetch_all', fetch)
    monkeypatch.setattr(index, 'log_forecasts', lambda rows: logged.extend(rows))
    monkeypatch.setattr(index, 'load_artifact', lambda: {'model': Model(), 'version': 'test', 'residual_p90': 2})
    client = TestClient(index.app)
    assert client.post('/api/predict', json={'product_id': 1}).status_code == 401
    response = client.post('/api/predict', headers={'X-API-Key': 'secret'}, json={'product_id': 1, 'horizon_days': 2})
    assert response.status_code == 200, response.text
    assert [r['predicted_demand'] for r in response.json()['forecasts']] == [41, 42]
    assert len(logged) == 2
