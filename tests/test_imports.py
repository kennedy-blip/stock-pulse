import io
import json
from datetime import date
from uuid import uuid4
import pytest
from openpyxl import Workbook
from fastapi.testclient import TestClient
from api import imports, index
from src.imports import read_rows, validate

MAPPING = {'sku':'SKU', 'sale_date':'Date', 'quantity_sold':'Qty', 'unit_price':'Price', 'store_location':'Store'}
CSV = b'SKU,Date,Qty,Price,Store\nABC,2025-01-01,2,12.50,Main\n'


def test_validate_csv_and_xlsx():
    data = validate('sales.csv', CSV, MAPPING)
    assert data['valid_rows'][0]['sku'] == 'ABC'
    assert data['errors'] == []
    book = Workbook(); sheet = book.active
    sheet.append(['SKU','Date','Qty','Price','Store'])
    sheet.append(['ABC', date(2025,1,1), 2, 12.5, 'Main'])
    output = io.BytesIO(); book.save(output)
    assert validate('sales.xlsx', output.getvalue(), MAPPING)['valid_rows'][0]['sale_date'] == '2025-01-01'


def test_validation_and_formula_rejection():
    bad = b'SKU,Date,Qty,Price,Store\nABC,2025-01-01,-1,12.50,Main\n'
    assert validate('bad.csv', bad, MAPPING)['errors'][0]['row'] == 2
    with pytest.raises(ValueError):
        validate('bad.csv', CSV, {**MAPPING, 'sku': 'Qty'})
    book = Workbook(); book.active.append(['SKU']); book.active.append(['=1+1'])
    output = io.BytesIO(); book.save(output)
    with pytest.raises(ValueError, match='formulas'):
        read_rows('bad.xlsx', output.getvalue())


def test_auth_preview_confirm_and_owner_isolation(monkeypatch):
    owner = str(uuid4()); other = str(uuid4()); stored = []
    monkeypatch.setattr(imports, 'authenticated_user', lambda auth: owner if auth == 'Bearer one' else other if auth == 'Bearer two' else (_ for _ in ()).throw(__import__('fastapi').HTTPException(401)))
    class Query:
        def __init__(self): self.owner = None
        def insert(self, rows): stored.extend(rows); return self
        def select(self, cols): return self
        def eq(self, col, val): assert col == 'owner_id'; self.owner=val; return self
        def order(self, *args, **kwargs): return self
        def limit(self, *args): return self
        def execute(self): return type('R', (), {'data': [row for row in stored if row['owner_id']==self.owner]})()
    class DB:
        def table(self, name): assert name=='client_sales_imports'; return Query()
    monkeypatch.setattr(imports, 'user_database', lambda authorization: DB())
    client = TestClient(index.app)
    def upload(token, confirm):
        return client.post('/api/imports/sales', headers={'Authorization': token}, files={'file': ('sales.csv', CSV, 'text/csv')}, data={'mapping':json.dumps(MAPPING), 'confirm':str(confirm).lower()})
    assert upload('bad', False).status_code == 401
    assert upload('Bearer one', False).json()['valid_count'] == 1
    assert not stored
    assert upload('Bearer one', True).json()['imported_count'] == 1
    assert stored[0]['owner_id'] == owner
    assert client.get('/api/imports/sales', headers={'Authorization':'Bearer two'}).json()['sales'] == []
    assert len(client.get('/api/imports/sales', headers={'Authorization':'Bearer one'}).json()['sales']) == 1


def test_verified_user_is_used_and_invalid_token_fails(monkeypatch):
    uid = str(uuid4())
    class Auth:
        def get_user(self, token):
            if token != 'valid':
                raise ValueError('invalid')
            return type('Response', (), {'user': type('User', (), {'id': uid})()})()
    monkeypatch.setattr(imports, 'fresh_client', lambda: type('Client', (), {'auth': Auth()})())
    assert imports.authenticated_user('Bearer valid') == uid
    with pytest.raises(__import__('fastapi').HTTPException) as e:
        imports.authenticated_user('Bearer invalid')
    assert e.value.status_code == 401


def test_database_uses_request_token(monkeypatch):
    seen = []
    class Postgrest:
        def auth(self, token): seen.append(token)
    client = type('Client', (), {'postgrest': Postgrest()})()
    monkeypatch.setattr(imports, 'fresh_client', lambda: client)
    assert imports.user_database('Bearer user-token') is client
    assert seen == ['user-token']
