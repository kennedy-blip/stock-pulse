"""Bounded CSV/XLSX parsing and strict validation for client-owned imports."""
import csv
import hashlib
import io
import json
import zipfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from openpyxl import load_workbook

MAX_BYTES = 2 * 1024 * 1024
MAX_UNCOMPRESSED = 20 * 1024 * 1024
MAX_ROWS = 500
MAX_COLUMNS = 50
REQUIRED = ('sku', 'sale_date', 'quantity_sold', 'unit_price')
OPTIONAL = ('store_location',)


def read_rows(filename, content):
    if not content or len(content) > MAX_BYTES:
        raise ValueError('File must be between 1 byte and 2 MB')
    suffix = Path(filename or '').suffix.lower()
    try:
        if suffix == '.csv':
            text = content.decode('utf-8-sig')
            rows = list(csv.reader(io.StringIO(text, newline=''), strict=True))
        elif suffix == '.xlsx':
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if sum(item.file_size for item in archive.infolist()) > MAX_UNCOMPRESSED:
                    raise ValueError('Excel file expands beyond 20 MB')
            wb = load_workbook(io.BytesIO(content), read_only=True, data_only=False, keep_links=False)
            try:
                ws = wb.worksheets[0]
                if ws.max_row > MAX_ROWS + 1 or ws.max_column > MAX_COLUMNS:
                    raise ValueError('File exceeds the row or column limit')
                rows = []
                for values in ws.iter_rows():
                    if any(c.data_type == 'f' for c in values):
                        raise ValueError('Excel formulas are not supported; upload values instead')
                    rows.append([c.value for c in values])
            finally:
                wb.close()
        else:
            raise ValueError('Only .csv and .xlsx files are supported')
    except (UnicodeError, csv.Error, zipfile.BadZipFile, KeyError, OSError) as exc:
        raise ValueError('File is malformed or is not UTF-8 CSV / valid XLSX') from exc
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('Unable to read spreadsheet') from exc
    if len(rows) < 2 or len(rows) > MAX_ROWS + 1:
        raise ValueError(f'File must contain 1–{MAX_ROWS} data rows')
    if any(len(row) > MAX_COLUMNS for row in rows):
        raise ValueError('File exceeds 50 columns')
    headers = [str(v).strip() if v is not None else '' for v in rows[0]]
    if not all(headers) or len(headers) != len(set(headers)):
        raise ValueError('Headers must be nonempty and unique')
    return headers, rows[1:]


def validate(filename, content, mapping):
    headers, rows = read_rows(filename, content)
    if not isinstance(mapping, dict) or set(mapping) - set(REQUIRED + OPTIONAL):
        raise ValueError('Mapping contains unknown fields')
    if any(not isinstance(mapping.get(k), str) or mapping[k] not in headers for k in REQUIRED):
        raise ValueError('Map sku, sale_date, quantity_sold and unit_price to file headers')
    if any(not isinstance(v, str) or v not in headers for v in mapping.values()) or len(set(mapping.values())) != len(mapping):
        raise ValueError('Each mapped column must exist and be unique')
    index = {k: headers.index(v) for k, v in mapping.items()}
    valid, errors = [], []
    for row_number, row in enumerate(rows, 2):
        def val(key):
            pos = index.get(key)
            return row[pos] if pos is not None and pos < len(row) else None
        try:
            sku = str(val('sku') or '').strip()
            if not sku or len(sku) > 100:
                raise ValueError('SKU must be 1–100 characters')
            raw_date = val('sale_date')
            if isinstance(raw_date, datetime):
                raw_date = raw_date.date()
            d = raw_date if isinstance(raw_date, date) else date.fromisoformat(str(raw_date).strip())
            if d > date.today():
                raise ValueError('Sale date cannot be in the future')
            qty = Decimal(str(val('quantity_sold')).strip())
            if not qty.is_finite() or qty < 0 or qty != qty.to_integral_value() or qty > 2147483647:
                raise ValueError('Quantity must be a non-negative integer')
            price = Decimal(str(val('unit_price')).strip())
            if not price.is_finite() or price < 0 or price >= Decimal('10000000000') or price.as_tuple().exponent < -2:
                raise ValueError('Unit price must be non-negative with at most two decimal places')
            store = str(val('store_location') or '').strip()
            if len(store) > 200:
                raise ValueError('Store location is too long')
            valid.append({'row_number': row_number, 'sku': sku, 'sale_date': d.isoformat(), 'quantity_sold': int(qty), 'unit_price': str(price), 'store_location': store})
        except (ValueError, InvalidOperation, OverflowError) as exc:
            errors.append({'row': row_number, 'message': str(exc) or 'Invalid value'})
    digest = hashlib.sha256(content + b'\0' + json.dumps(mapping, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'headers': headers, 'valid_rows': valid, 'errors': errors, 'import_hash': digest}
