import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from .. import brand_check
from ..web_helpers import ROOT

router = APIRouter()
BRAND_DEFAULT = ROOT / "web" / "static" / "brand"
BRAND_DIR = Path(os.environ.get("BRAND_DIR", "/data/brand"))


@router.get("/brand/{path:path}")
def brand(path: str):
    if "/" in path or path.startswith("."):
        raise HTTPException(404)
    if path == "theme.css":
        brand_check.check_dir(BRAND_DIR)   # a stat per request; the check itself runs only when the file changed (#110)
    for base in (BRAND_DIR, BRAND_DEFAULT):
        p = base / path
        if p.is_file():
            return FileResponse(p)
    if path == "custom.css":
        return Response("/* no custom.css in BRAND_DIR */", media_type="text/css")
    raise HTTPException(404)
