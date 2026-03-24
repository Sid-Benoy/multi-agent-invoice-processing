import sqlite3

conn = sqlite3.connect("data/inventory.db")
cursor = conn.cursor()

rows = cursor.execute(
    """
    SELECT i.source_file, COUNT(it.id) AS item_count
    FROM invoices i
    LEFT JOIN invoice_items it ON it.invoice_id = i.id
    GROUP BY i.id
    ORDER BY i.source_file
    """
).fetchall()

for source_file, item_count in rows:
    print(f"{source_file}: {item_count}")

conn.close()
