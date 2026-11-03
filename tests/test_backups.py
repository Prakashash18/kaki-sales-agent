import sqlite3
from pathlib import Path
from app.backups import online_backup

def test_online_sqlite_backup_includes_committed_data(tmp_path):
    original=tmp_path/"original.sqlite3"
    destination=tmp_path/"backup.sqlite3"
    con=sqlite3.connect(str(original))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE audit_test(id INTEGER PRIMARY KEY, action TEXT)")
    con.execute("INSERT INTO audit_test(action) VALUES (?)",("approved",))
    con.commit()
    online_backup(original,destination)
    with sqlite3.connect(str(destination)) as recovered:
        assert recovered.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert recovered.execute("SELECT action FROM audit_test").fetchone()[0]=="approved"
    con.close()
