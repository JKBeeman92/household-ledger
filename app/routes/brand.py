import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from .. import brand_check, brand_kit
from ..brand_kit import BRAND_DEFAULT

router = APIRouter()
BRAND_DIR = brand_kit.BRAND_DIR   # tests point this at a temporary kit


@router.get("/brand/{path:path}")
def brand(path: str):
    if "/" in path or path.startswith("."):
        raise HTTPException(404)
    if path == "theme.css":
        brand_check.check_dir(BRAND_DIR)   # a stat per request; the check itself runs only when the file changed (#110)
    if path == "glyphs.svg":   # the default set with the kit's glyphs laid over it by id (docs/DESIGN.md, Glyphs)
        return Response(brand_kit.glyph_sprite(BRAND_DIR), media_type="image/svg+xml")
    p = brand_kit.find(path, BRAND_DIR)   # a missing mark cut falls back to the kit's nearest mark before the shipped one
    if p:
        return FileResponse(p)
    if path == "custom.css":
        return Response("/* no custom.css in BRAND_DIR */", media_type="text/css")
    raise HTTPException(404)
