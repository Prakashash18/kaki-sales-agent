"""Authenticated SQLite online snapshot export for manual off-site backups.

Render's disk snapshots are not a substitute for a validated, offsite SQLite
backup. The database is copied via sqlite3.backup, never a raw file copy while
WAL is active. An owner can download a snapshot over authenticated HTTPS.
"""
import os
import sqlite3
import tempfile
import pathlib
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from .identity import require_role
from .db import engine

router=APIRouter(prefix="/admin")

def db_file():
    url=str(engine.url)
    if not url.startswith("sqlite:///"):raise HTTPException(409,"SQLite backup is only available for SQLite deployments")
    path=pathlib.Path(engine.url.database)
    if not path.is_absolute():raise HTTPException(409,"Production backup requires an absolute SQLite path")
    if os.getenv("APP_ENV")=="production" and not str(path).startswith("/var/data/"):
        raise HTTPException(409,"Source database is not on the persistent Render disk")
    if not path.is_file():raise HTTPException(503,"Database is not available")
    return path

def online_backup(source,destination):
    with sqlite3.connect(str(source),timeout=30) as src:
        with sqlite3.connect(str(destination),timeout=30) as target:
            src.backup(target,pages=250,sleep=0.05)
            checked=target.execute("PRAGMA integrity_check").fetchone()
            if not checked or checked[0]!="ok":
                raise RuntimeError("SQLite backup integrity check failed")

@router.post("/backup")
def download_backup(request:Request,csrf_token:str=Form(...)):
    require_role(request,"owner",csrf=csrf_token)
    if os.getenv("KAKI_ENABLE_BACKUP_EXPORT","false").lower()!="true":
        raise HTTPException(503,"Set KAKI_ENABLE_BACKUP_EXPORT=true to enable owner backup downloads")
    source=db_file()
    descriptor,path=tempfile.mkstemp(prefix="kaki-backup-",suffix=".sqlite3")
    os.close(descriptor)
    try:
        online_backup(source,path)
        return FileResponse(path,media_type="application/octet-stream",
          filename="kaki-offsite-backup.sqlite3",
          background=BackgroundTask(lambda: os.path.exists(path) and os.remove(path)),
          headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})
    except Exception:
        if os.path.exists(path):os.remove(path)
        raise HTTPException(503,"Could not create a verified backup")
