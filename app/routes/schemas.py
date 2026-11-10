"""Publishes the JSON Schemas the Settings page validates against, so editors and other tools can use them."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from .. import auth, schema

router = APIRouter()


@router.get("/schemas/{name}.schema.json")
def get_schema(name: str, user=Depends(auth.require_user_api)):
    if name not in schema.NAMES:
        raise HTTPException(404)
    return JSONResponse(schema.load(name), media_type="application/schema+json")
