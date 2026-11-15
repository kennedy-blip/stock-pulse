# StockPulse

A FastAPI demand-forecasting service using Supabase, Polars and a global LightGBM model. The repository contains the application and training code, **not a pre-trained model**. A useful model requires your historical sales data; health and prediction will return 503 until an artifact is trained and deployed.

## Setup

1. Create or select a Supabase project. Run `supabase/schema.sql` in its SQL editor (this is initial schema setup, not a migration for existing differently shaped tables). Insert products, historical sales, and optionally daily external factors. `product_id` refers to `products.id`; sales across all stores are summed. RLS is enabled with no public policies; server access uses the service role. Never expose that key to browsers or commit it.
2. Use Python 3.11+, then install dependencies:
   ```sh
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Set environment variables in your shell (or source a protected local environment file; the app does not automatically load `.env`):
   ```sh
   export SUPABASE_URL='https://YOUR-PROJECT.supabase.co'
   export SUPABASE_SERVICE_ROLE_KEY='your-service-role-key'
   export STOCKPULSE_API_KEY='a-long-random-secret'
   ```
   Find the project URL and service-role key in the Supabase dashboard. The API key is your own secret, not a Supabase key. Use a secret manager for production. See `.env.example` for the names.
4. Train on the populated database:
   ```sh
   python -m ml.train
   ```
   This writes `ml/model.pkl` and reports time-split validation RMSE. Training needs at least 50 complete feature rows over 10 distinct dates after the initial 30-day warm-up. Only load trusted pickle artifacts. Retrain and redeploy when data changes; the model is cached per serverless instance.
5. Run locally: `uvicorn api.index:app --host 0.0.0.0 --port 8000`. Check `GET /api/health`. Request a forecast:
   ```sh
   curl -X POST http://localhost:8000/api/predict \
     -H 'Content-Type: application/json' -H "X-API-Key: $STOCKPULSE_API_KEY" \
     -d '{"product_id":1,"horizon_days":7}'
   ```
   Forecasts for tomorrow through 1–30 days ahead are inserted into `demand_forecasts` before being returned. Repeated requests create separate log entries. API access is protected by a shared key; add rate limiting and stronger per-user authorization at the gateway before exposing to untrusted users.

## Vercel

Train the model first, include the trusted `ml/model.pkl` in the Git deployment (ensure it contains no sensitive data), and import this GitHub repository in Vercel. Set the three environment variables above as server-side variables for the desired environments, then deploy. `api/index.py` exposes an ASGI `app`; Vercel's Python runtime handles it directly. `vercel.json` routes `/api/*` and includes the artifact in the function bundle. Check build logs for runtime/native dependency compatibility and function size limits, and verify `/api/health` and a prediction after deployment. Python dependencies are version-ranged; lock them in your deployment process for reproducible model/runtime versions. Model files generated with incompatible library versions may not load.

## Modeling assumptions and limitations

* Missing sales days are treated as zero, not missing data or stockouts; sales through the current UTC date are considered complete. No inventory levels or replenishment recommendations are modeled.
* Calendar lags are computed within each SKU; rolling windows use only prior days. Predictions recursively feed future lags. Current product price is used for all historical days. Product IDs are numeric model features; new SKUs and changing prices need careful validation. Category and weather are stored but not used as model features.
* External factors are global by date. Missing future factors default to non-holiday and price index 1. Supply future values for better forecasts. Weather is not currently modeled.
* Bounds use the 90th percentile absolute residual on the held-out one-step validation set, clipped at zero. They are illustrative, not calibrated 90% multi-step prediction intervals. Monitor drift and validate per SKU/horizon before operational use.
* Fetching whole tables and rebuilding history on every recursive step is intended for modest datasets. Large production workloads need bounded queries, efficient materialized daily aggregates, pagination consistency, monitoring, quotas, and load testing against serverless time/memory limits.

## Tests

```sh
pip install pytest httpx
python -m pytest -q
```
