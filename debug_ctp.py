import sqlite3
con = sqlite3.connect('zielonebety.db')
cur = con.cursor()
cur.execute("SELECT id, snapshot_type, created_at FROM snapshots WHERE payload LIKE '%5.40%' AND payload LIKE '%Huddersfield%'")
for r in cur.fetchall():
    print(r)
cur.execute("SELECT id, snapshot_type, created_at FROM snapshots WHERE payload LIKE '%5.4%' AND payload LIKE '%Huddersfield%'")
for r in cur.fetchall():
    print(r)
con.close()
