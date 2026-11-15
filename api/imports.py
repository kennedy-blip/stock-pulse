"""Authenticated, account-scoped CSV/XLSX import endpoints."""
import json
import logging
import os
from uuid import UUID

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field
from supabase import create_client
from src.imports import MAX_BYTES, read_rows, validate

router = APIRouter(prefix='/api')
logger = logging.getLogger(__name__)


def fresh_client():
    url, key = os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_ANON_KEY')
    if not url or not key:
        raise HTTPException(503, 'Authentication service is not configured')
    # Use a fresh public-key client for each request; authentication mutates session state.
    return create_client(url, key)


def authenticated_user(authorization: str | None):
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, 'Bearer token required')
    token = authorization[7:].strip()
    if not token or len(token) > 8192:
        raise HTTPException(401, 'Invalid bearer token')
    try:
        response = fresh_client().auth.get_user(token)
        user_id = str(UUID(response.user.id))
        return user_id
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, 'Invalid or expired session')


def user_database(authorization: str):
    # RLS evaluates the verified user's JWT, never a service-role credential.
    client = fresh_client()
    client.postgrest.auth(authorization[7:].strip())
    return client


@router.get('/auth/config')
def public_auth_config():
    url, key = os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_ANON_KEY')
    # Never return a service role key, even if the environment is misconfigured.
    if not url or not key or not key.startswith('sb_publishable_'):
        raise HTTPException(503, 'Public authentication is not configured')
    return {'url': url, 'publishable_key': key}


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)


@router.post('/auth/signup')
def signup(body: Credentials):
    try:
        response = fresh_client().auth.sign_up({'email': body.email, 'password': body.password})
        # A verification email may be required; never assume a session exists.
        return {'session': {'access_token': response.session.access_token} if response.session else None, 'message': 'Check your email if verification is required.'}
    except HTTPException:
        raise
    except Exception:
        logger.exception('Signup failed')
        raise HTTPException(400, 'Could not create account; check your details and try again')


@router.post('/auth/login')
def login(body: Credentials):
    try:
        response = fresh_client().auth.sign_in_with_password({'email': body.email, 'password': body.password})
        if not response.session:
            raise HTTPException(401, 'Sign-in failed')
        return {'access_token': response.session.access_token}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, 'Sign-in failed; check your credentials and email verification')


async def limited_file(file: UploadFile):
    try:
        content = await file.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise HTTPException(413, 'File exceeds 2 MB')
        return content
    finally:
        await file.close()


@router.post('/imports/columns')
async def columns(file: UploadFile = File(...), authorization: str | None = Header(default=None)):
    authenticated_user(authorization)
    content = await limited_file(file)
    try:
        headers, rows = read_rows(file.filename, content)
        return {'headers': headers, 'row_count': len(rows)}
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.post('/imports/sales')
async def import_sales(file: UploadFile = File(...), mapping: str = Form(...), confirm: bool = Form(False), authorization: str | None = Header(default=None)):
    owner_id = authenticated_user(authorization)
    content = await limited_file(file)
    try:
        parsed = json.loads(mapping)
        result = validate(file.filename, content, parsed)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc))
    preview = {'row_count': len(result['valid_rows']) + len(result['errors']), 'valid_count': len(result['valid_rows']), 'errors': result['errors'][:50], 'error_count': len(result['errors']), 'sample': result['valid_rows'][:5]}
    if not confirm:
        return preview
    if result['errors']:
        raise HTTPException(422, {'message': 'Fix all errors before importing', **preview})
    records = [{**row, 'owner_id': owner_id, 'import_hash': result['import_hash']} for row in result['valid_rows']]
    try:
        # A single insert is atomic; the unique constraint prevents exact re-imports.
        user_database(authorization).table('client_sales_imports').insert(records).execute()
    except Exception as exc:
        if getattr(exc, 'code', None) == '23505':
            raise HTTPException(409, 'This file and mapping have already been imported')
        logger.exception('Sales import failed')
        raise HTTPException(500, 'Import failed; no rows were saved')
    return {'imported_count': len(records)}


@router.get('/imports/sales')
def list_sales(authorization: str | None = Header(default=None)):
    owner_id = authenticated_user(authorization)
    try:
        result = user_database(authorization).table('client_sales_imports').select('sku,sale_date,quantity_sold,unit_price,store_location').eq('owner_id', owner_id).order('sale_date', desc=True).limit(100).execute()
        return {'sales': result.data or [], 'limit': 100}
    except Exception:
        logger.exception('Sales listing failed')
        raise HTTPException(500, 'Unable to load sales')
