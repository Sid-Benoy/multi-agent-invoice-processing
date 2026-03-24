import sqlite3

conn = sqlite3.connect("data/inventory.db")
cursor = conn.cursor()

print("invoices", cursor.execute("SELECT COUNT(*) FROM invoices").fetchone()[0])
print("invoice_items", cursor.execute("SELECT COUNT(*) FROM invoice_items").fetchone()[0])
print("parsed", cursor.execute("SELECT COUNT(*) FROM invoices WHERE parse_status = 'parsed'").fetchone()[0])
print("unsupported", cursor.execute("SELECT COUNT(*) FROM invoices WHERE parse_status LIKE 'skipped_unsupported_format:%'").fetchone()[0])
print("inventory", cursor.execute("SELECT COUNT(*) FROM inventory").fetchone()[0])

print("inventory_seed")
for item, stock in cursor.execute("SELECT item, stock FROM inventory ORDER BY item"):
	print(f"  {item}: {stock}")

conn.close()
