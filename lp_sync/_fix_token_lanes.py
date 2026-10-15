import sqlite3
c = sqlite3.connect("/opt/lpboard/data/ledger.db")
c.execute("UPDATE tokens SET lanes=?", ('["L1","L2","L3","L4","L5","HQ"]',))
c.commit()
print(c.execute("SELECT name,lanes FROM tokens").fetchall())
