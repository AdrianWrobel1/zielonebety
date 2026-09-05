import sqlite3
con = sqlite3.connect('zielonebety.db')
cur = con.cursor()
cur.execute("SELECT id, created_at FROM snapshots WHERE snapshot_type='SCAN_CYCLE_RESULT' AND payload LIKE '%cev_02d5368ef81606b5%' ORDER BY created_at DESC LIMIT 5")
for r in cur.fetchall():
    print(r)
con.close()
