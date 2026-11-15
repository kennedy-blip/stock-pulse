# StockPulse

A FastAPI demand-forecasting service using Supabase, Polars and a global LightGBM model. The included `ml/deployed_model.py` and `ml/model.pkl` contain a **demo model trained on synthetic data**, not a production forecast model. Replace it with a model trained on your own historical sales before operational use. Health and prediction return 503 if the artifact is missing.

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
   export SUPABASE_ANON_KEY='your-publishable-or-anon-key'
   ```
   Find the project URL and service-role key in the Supabase dashboard. The API key is your own secret, not a Supabase key. Use a secret manager for production. See `.env.example` for the names.
4. Train on the populated database:
   ```sh
   python -m ml.train
   ```
   This writes `ml/model.pkl` for offline use and `ml/deployed_model.py` for inference, and reports time-split validation RMSE. **Do not publish or commit models trained on private client data**: model parameters can reveal information about the training data. Training needs at least 50 complete feature rows over 10 distinct dates after the initial 30-day warm-up. Only load trusted pickle artifacts. Retrain and redeploy when data changes; the model is cached per serverless instance.
5. Run locally: `uvicorn api.index:app --host 0.0.0.0 --port 8000`. Check `GET /api/health`. Request a forecast:
   ```sh
   curl -X POST http://localhost:8000/api/predict \
     -H 'Content-Type: application/json' -H "X-API-Key: $STOCKPULSE_API_KEY" \
     -d '{"product_id":1,"horizon_days":7}'
   ```
   Forecasts for tomorrow through 1–30 days ahead are inserted into `demand_forecasts` before being returned. Repeated requests create separate log entries. API access is protected by a shared key; add rate limiting and stronger per-user authorization at the gateway before exposing to untrusted users.

## Vercel

Train a model first and include `ml/deployed_model.py` in the deployment only if its training data is safe to publish. Import this GitHub repository in Vercel. Set the four environment variables above for the desired environments, then deploy. `api/index.py` exposes an ASGI `app`; Vercel's Python runtime handles it directly. `vercel.json` routes `/api/*`; the inference artifact is a Python module explicitly imported by the serverless function, avoiding pickle compatibility and asset tracing issues. Check build logs for runtime/native dependency compatibility and function size limits, and verify `/api/health` and a prediction after deployment. Python dependencies are version-ranged; lock them in your deployment process for reproducible model/runtime versions. Model files generated with incompatible library versions may not load.

## Demo model provenance

The bundled demonstration model was trained on 120 synthetic daily records per demo SKU, reconstructed locally from the seeded `stocks` Supabase data after checking counts, dates, and values through the Supabase connector. The local training run did not connect to Supabase directly. Its validation RMSE is approximately 0.029 on synthetic data, which does not indicate real-world accuracy. The demo rows are labeled `DEMO-*`; do not mix them with real sales for production training.

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

## Customer account imports (early access)

The landing page supports Supabase Auth signup/sign-in and reviewed CSV/XLSX imports. Apply `supabase/client_imports.sql` and then `supabase/client_imports_rls.sql` as migrations before enabling it. Configure `SUPABASE_ANON_KEY` to the project’s public anon or publishable key. Authentication requires Supabase email/password sign-in to be enabled; account confirmation follows the project's Auth settings. The API validates bearer tokens with Supabase and always derives ownership from the verified user, never the upload. Client imports live in a separate RLS-enabled table. Import queries use the verified user’s token with the public key, and RLS enforces owner-scoped access in addition to explicit owner filters. The separate shared demo prediction endpoint still uses the service role and is not suitable for client data.

Imports accept up to 500 rows and 2 MB of UTF-8 CSV or `.xlsx`, with `sku`, `sale_date`, `quantity_sold`, `unit_price`, and optional `store_location` mapped from file headers. Dates are ISO dates or Excel dates, quantities non-negative integers, prices non-negative with at most two decimals. Preview reports errors; confirmation revalidates and atomically inserts the whole file. Identical file/mapping imports by the same user are rejected. The file is not persisted; only normalized rows are stored. The interface keeps an access token in tab-scoped session storage and does not refresh expired sessions; sign in again if necessary.

**Important:** Imports are not yet connected to model training or forecasting. The existing shared-key prediction endpoint is demo-only and must not be used for customer data. No PDF/OCR, self-service retraining, account deletion UI, gateway rate limits, upload malware scanning, comprehensive audit logging, or production tenant isolation review exists yet. Set up abuse protection, security testing, and operational monitoring before exposing account endpoints publicly. Do not upload sensitive data until those controls are in place.

### GitHub sign-in

Enable the GitHub provider in Supabase Auth with a GitHub OAuth App whose callback is `https://<project-ref>.supabase.co/auth/v1/callback`. Set `SUPABASE_ANON_KEY` to a **publishable** (`sb_publishable_...`) key; the public config endpoint rejects secret keys. Add the app's exact origin and `/` to Supabase Auth's redirect allow list (and the stable branch-preview origin if preview sign-in is needed). The app uses Supabase JS's PKCE flow and keeps its session in tab-scoped session storage. Do not expose provider secrets. GitHub sign-in is independent of SMTP, though email/password signup still needs email delivery.

The checked-in browser bundle is built from `web/auth-client.js`. To rebuild it after dependency changes:

```sh
npm install --prefix .cache/oauth --no-save @supabase/supabase-js@2.99.2 esbuild@0.25.12
NODE_PATH=.cache/oauth/node_modules .cache/oauth/node_modules/.bin/esbuild web/auth-client.js --bundle --format=esm --platform=browser --minify --outfile=auth-client.bundle.js
```
