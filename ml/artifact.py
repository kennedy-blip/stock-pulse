"""Write a portable, import-traceable LightGBM model module for deployment.

Only export models whose training data may be included in the deployment. A model
can leak information about training records; never publish private client models.
"""
import base64
import json
import zlib
from pathlib import Path


def export_module(model, features, version, residual_p90, rmse, path='ml/deployed_model.py'):
    text = model.booster_.model_to_string()
    encoded = base64.b85encode(zlib.compress(text.encode('utf-8'), 9)).decode('ascii')
    metadata = {'features': features, 'version': version, 'residual_p90': residual_p90, 'rmse': rmse}
    Path(path).write_text('"""Generated LightGBM deployment artifact; do not edit by hand."""\n'
                          'MODEL_B85 = ' + repr(encoded) + '\n'
                          'METADATA_JSON = ' + repr(json.dumps(metadata, sort_keys=True)) + '\n', encoding='utf-8')
