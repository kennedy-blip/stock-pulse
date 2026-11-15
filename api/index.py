"""Vercel discovers the ASGI `app` directly; Mangum is not needed."""
import hmac
import logging
import os
import pickle
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from src.database import fetch_all, log_forecasts
from src.pipeline import FEATURES, make_features

logger = logging.getLogger(__name__)
app = FastAPI(title='StockPulse', version='1.0.0')
ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def load_artifact():
    path = Path(os.getenv('MODEL_PATH', str(ROOT / 'ml/model.pkl')))
    # Pickle is executable: only load artifacts built by a trusted operator.
    with path.open('rb') as f:
        artifact = pickle.load(f)
    if artifact['features'] != FEATURES:
        raise ValueError('Model feature schema does not match the pipeline')
    return artifact


class PredictRequest(BaseModel):
    product_id: int = Field(gt=0)
    horizon_days: int = Field(default=7, ge=1, le=30)


@app.get('/api/health')
def health():
    try:
        return {'status': 'ok', 'model_version': load_artifact()['version']}
    except Exception:
        logger.exception('Model unavailable')
        raise HTTPException(status_code=503, detail='Model unavailable; train and deploy an artifact')


@app.post('/api/predict')
def predict(request: PredictRequest, x_api_key: str | None = Header(default=None)):
    key = os.getenv('STOCKPULSE_API_KEY')
    if not key:
        raise HTTPException(status_code=503, detail='Prediction API key is not configured')
    if not x_api_key or not hmac.compare_digest(x_api_key, key):
        raise HTTPException(status_code=401, detail='Invalid API key')
    try:
        artifact = load_artifact()
        products = fetch_all('products', 'id,sku,unit_price', [('id', 'eq', request.product_id)])
        if not products:
            raise HTTPException(status_code=404, detail='Product not found')
        today = date.today()
        sales = fetch_all('sales_transactions', 'id,product_id,quantity_sold,sale_date', [('product_id', 'eq', request.product_id), ('sale_date', 'lte', today.isoformat())])
        if not sales:
            raise HTTPException(status_code=422, detail='No historical sales for this product')
        factors = fetch_all('external_factors', 'date,is_holiday,local_price_index')
        forecasts = []
        for offset in range(1, request.horizon_days + 1):
            target = today + timedelta(days=offset)
            # A synthetic target row extends the calendar; lagged features exclude it.
            synthetic = {'product_id': request.product_id, 'sale_date': target.isoformat(), 'quantity_sold': 0}
            frame = make_features(sales + [synthetic], products, factors, end_date=target)
            row = frame.filter(frame['date'] == target).select(FEATURES)
            if row.null_count().row(0) != (0,) * len(FEATURES):
                raise HTTPException(status_code=422, detail='At least 30 days of daily history are required')
            value = max(0.0, float(artifact['model'].predict(row.to_numpy())[0]))
            error = artifact['residual_p90']
            forecasts.append({'product_id': request.product_id, 'forecast_date': target.isoformat(), 'predicted_demand': value, 'lower_bound': max(0.0, value - error), 'upper_bound': value + error, 'model_version': artifact['version']})
            sales.append({**synthetic, 'quantity_sold': value})
        log_forecasts(forecasts)
        return {'product_id': request.product_id, 'model_version': artifact['version'], 'forecasts': forecasts}
    except HTTPException:
        raise
    except FileNotFoundError:
        logger.exception('Model artifact missing')
        raise HTTPException(status_code=503, detail='Model unavailable; train and deploy an artifact')
    except Exception:
        logger.exception('Prediction failed')
        raise HTTPException(status_code=500, detail='Prediction failed; check server logs')
