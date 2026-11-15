"""Train locally with `python -m ml.train`; ship the resulting trusted artifact."""
import os
import pickle
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from lightgbm import LGBMRegressor
from sklearn.metrics import mean_squared_error
from src.database import fetch_all
from src.pipeline import FEATURES, make_features


def main():
    sales = fetch_all('sales_transactions', 'id,product_id,quantity_sold,sale_date')
    products = fetch_all('products', 'id,sku,unit_price')
    factors = fetch_all('external_factors', 'date,is_holiday,local_price_index')
    df = make_features(sales, products, factors).drop_nulls(FEATURES)
    dates = sorted(df['date'].unique().to_list())
    if len(dates) < 10 or df.height < 50:
        raise ValueError('Need at least 50 complete daily feature rows spanning 10 dates; provide more history (at least 40 days per SKU).')
    cutoff = dates[max(1, int(len(dates) * 0.8))]
    train = df.filter(df['date'] < cutoff)
    test = df.filter(df['date'] >= cutoff)
    model = LGBMRegressor(n_estimators=300, learning_rate=0.05, num_leaves=31, verbosity=-1, random_state=42, n_jobs=1)
    model.fit(train.select(FEATURES).to_numpy(), train['quantity_sold'].to_numpy())
    actual = test['quantity_sold'].to_numpy()
    predicted = np.maximum(0, model.predict(test.select(FEATURES).to_numpy()))
    rmse = float(np.sqrt(mean_squared_error(actual, predicted)))
    # Symmetric empirical 90% residual interval, not a calibrated multi-step PI.
    error = float(np.quantile(np.abs(actual - predicted), 0.9))
    version = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    artifact = {'model': model, 'features': FEATURES, 'version': version, 'residual_p90': error, 'rmse': rmse}
    path = Path(os.getenv('MODEL_PATH', 'ml/model.pkl'))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as f:
        pickle.dump(artifact, f)
    print(f'Saved {path}; version={version}, validation RMSE={rmse:.3f}, residual p90={error:.3f}')

if __name__ == '__main__':
    main()
