import csv
import json
import re
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

BASE_DIR = Path(__file__).resolve().parent
INVOICES_DIR = BASE_DIR / "invoices"
DB_PATH = BASE_DIR / "inventory.db"


def normalize_money(value):
    if value is None:
        return None
    cleaned = str(value).strip().replace("$", "").replace(",", "")
    cleaned = cleaned.replace("O", "0")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def normalize_qty(value):
    if value is None:
        return None
    cleaned = str(value).strip().replace(",", "").replace("O", "0")
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def normalize_invoice_number(value):
    if not value:
        return None
    text = str(value).strip().upper()
    text = text.replace("#", " ")
    text = re.sub(r"\s+", " ", text)

    match = re.search(r"INV[-\s]?(\d{3,6})", text)
    if match:
        return f"INV-{match.group(1)}"

    digits = re.search(r"(\d{3,6})", text)
    if digits:
        return f"INV-{digits.group(1)}"

    return text


def normalize_date(value):
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    lowered = text.lower()
    if lowered in {"yesterday", "today", "tomorrow", "null", "none"}:
        return None

    text = text.replace("2O", "20")

    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%b %d %Y",
        "%B %d, %Y",
        "%d-%b-%Y",
        "%d-%b-%y",
        "%d %b %Y",
        "%d %B %Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    return text


def parse_txt_content(text):
    lines = text.splitlines()

    def search(pattern):
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        return match.group(1).strip() if match else None

    invoice_number = (
        search(r"Invoice\s*Number\s*:\s*([^\n]+)")
        or search(r"Inv\s*#\s*:\s*([^\n]+)")
        or search(r"INV\s*NO\s*:\s*([^\n]+)")
        or search(r"INVOICE\s*#\s*([A-Za-z0-9\- ]+)")
        or search(r"Invoice\s*:\s*([^\n]+)")
    )

    vendor = (
        search(r"Vendor\s*:\s*([^\n]+)")
        or search(r"Vndr\s*:\s*([^\n]+)")
        or search(r"FROM\s*:\s*([^\n]+)")
    )

    invoice_date = search(r"^\s*Date\s*:\s*([^\n]+)") or search(r"^\s*Dt\s*:\s*([^\n]+)")
    due_date = (
        search(r"Due\s*Date\s*:\s*([^\n]+)")
        or search(r"Due\s*Dt\s*:\s*([^\n]+)")
        or search(r"^\s*DUE\s*:\s*([^\n]+)")
        or search(r"^\s*Due\s*:\s*([^\n]+)")
    )

    subtotal = normalize_money(search(r"Subtotal\s*:\s*\$?([0-9,\.O]+)"))
    tax_amount = normalize_money(
        search(r"Tax(?:\s*\([^\)]*\))?\s*:\s*\$?([0-9,\.O]+)") or search(r"Sales\s*Tax\s*:\s*\$?([0-9,\.O]+)")
    )
    total = normalize_money(
        search(r"Total\s*Amount\s*:\s*\$?([0-9,\.O]+)")
        or search(r"\bTOTAL\s*:\s*\$?([0-9,\.O]+)")
        or search(r"\bTotal\s*:\s*\$?([0-9,\.O]+)")
        or search(r"\bAmt\s*:\s*\$?([0-9,\.O]+)")
    )

    payment_terms = search(r"Payment\s*Terms\s*:\s*([^\n]+)") or search(r"Pymnt\s*Terms\s*:\s*([^\n]+)") or search(r"Terms\s*:\s*([^\n]+)")

    item_patterns = [
        re.compile(r"^\s*([A-Za-z][A-Za-z0-9 ()\-]+?)\s+qty\s*:?\s*(-?\d+)\s+(?:unit\s*price\s*:?\s*\$?([0-9,\.O]+)|@\s*\$?([0-9,\.O]+))", re.IGNORECASE),
        re.compile(r"^\s*-\s*([A-Za-z][A-Za-z0-9 ()\-]+?)\s+x\s*(-?\d+)\s+\$?([0-9,\.O]+)", re.IGNORECASE),
        re.compile(r"^\s*([A-Za-z][A-Za-z0-9 ()\-]+?)\s+(-?\d+)\s+\$?([0-9,\.O]+)\s+\$?([0-9,\.O]+)(?:\s+.+)?\s*$", re.IGNORECASE),
    ]

    items = []
    for line in lines:
        stripped = line.strip("- ")
        for pattern in item_patterns:
            match = pattern.match(line)
            if not match:
                continue

            if pattern is item_patterns[0]:
                name = match.group(1)
                qty = normalize_qty(match.group(2))
                price = normalize_money(match.group(3) or match.group(4))
                line_total = qty * price if qty is not None and price is not None else None
            elif pattern is item_patterns[1]:
                name = match.group(1)
                qty = normalize_qty(match.group(2))
                price = normalize_money(match.group(3))
                line_total = qty * price if qty is not None and price is not None else None
            else:
                name = match.group(1)
                qty = normalize_qty(match.group(2))
                price = normalize_money(match.group(3))
                line_total = normalize_money(match.group(4))

            items.append(
                {
                    "item": re.sub(r"\s+", " ", name).strip(),
                    "quantity": qty,
                    "unit_price": price,
                    "line_total": line_total,
                }
            )
            break

        if not items and stripped.startswith("Widget") and "$" in line:
            generic = re.match(r"^([A-Za-z][A-Za-z0-9 ()\-]+?)\s+(-?\d+)\s+\$?([0-9,\.O]+)", stripped)
            if generic:
                name = generic.group(1)
                qty = normalize_qty(generic.group(2))
                price = normalize_money(generic.group(3))
                items.append(
                    {
                        "item": re.sub(r"\s+", " ", name).strip(),
                        "quantity": qty,
                        "unit_price": price,
                        "line_total": qty * price if qty is not None and price is not None else None,
                    }
                )

    return {
        "invoice_number": normalize_invoice_number(invoice_number),
        "vendor": vendor,
        "invoice_date": normalize_date(invoice_date),
        "due_date": normalize_date(due_date),
        "subtotal": subtotal,
        "tax_amount": tax_amount,
        "total": total,
        "currency": None,
        "payment_terms": payment_terms,
        "items": items,
    }


def parse_json_invoice(path):
    data = json.loads(path.read_text(encoding="utf-8"))

    vendor_data = data.get("vendor") or {}
    if isinstance(vendor_data, dict):
        vendor = vendor_data.get("name")
    else:
        vendor = str(vendor_data)

    items = []
    for row in data.get("line_items", []):
        qty = normalize_qty(row.get("quantity"))
        price = normalize_money(row.get("unit_price"))
        amount = normalize_money(row.get("amount"))
        if amount is None and qty is not None and price is not None:
            amount = qty * price

        items.append(
            {
                "item": row.get("item"),
                "quantity": qty,
                "unit_price": price,
                "line_total": amount,
            }
        )

    return {
        "invoice_number": normalize_invoice_number(data.get("invoice_number")),
        "vendor": vendor,
        "invoice_date": normalize_date(data.get("date")),
        "due_date": normalize_date(data.get("due_date")),
        "subtotal": normalize_money(data.get("subtotal")),
        "tax_amount": normalize_money(data.get("tax_amount")),
        "total": normalize_money(data.get("total")),
        "currency": data.get("currency"),
        "payment_terms": data.get("payment_terms"),
        "items": items,
    }


def parse_xml_invoice(path):
    root = ET.fromstring(path.read_text(encoding="utf-8"))

    def pick_text(xpath):
        node = root.find(xpath)
        return node.text.strip() if node is not None and node.text else None

    items = []
    for item in root.findall("./line_items/item"):
        name = item.findtext("name")
        qty = normalize_qty(item.findtext("quantity"))
        price = normalize_money(item.findtext("unit_price"))
        line_total = qty * price if qty is not None and price is not None else None
        items.append(
            {
                "item": name,
                "quantity": qty,
                "unit_price": price,
                "line_total": line_total,
            }
        )

    return {
        "invoice_number": normalize_invoice_number(pick_text("./header/invoice_number")),
        "vendor": pick_text("./header/vendor"),
        "invoice_date": normalize_date(pick_text("./header/date")),
        "due_date": normalize_date(pick_text("./header/due_date")),
        "subtotal": normalize_money(pick_text("./totals/subtotal")),
        "tax_amount": normalize_money(pick_text("./totals/tax_amount")),
        "total": normalize_money(pick_text("./totals/total")),
        "currency": pick_text("./header/currency"),
        "payment_terms": pick_text("./payment_terms"),
        "items": items,
    }


def parse_csv_invoice(path):
    rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    if not rows:
        return {"items": []}

    first_header = [cell.strip().lower() for cell in rows[0]]

    if first_header == ["field", "value"]:
        data = {}
        items = []
        pending_item = None

        for row in rows[1:]:
            if len(row) < 2:
                continue
            key = row[0].strip().lower()
            value = row[1].strip()

            if key == "item":
                pending_item = {"item": value, "quantity": None, "unit_price": None, "line_total": None}
                items.append(pending_item)
            elif key == "quantity" and pending_item is not None:
                pending_item["quantity"] = normalize_qty(value)
            elif key == "unit_price" and pending_item is not None:
                pending_item["unit_price"] = normalize_money(value)
                qty = pending_item.get("quantity")
                price = pending_item.get("unit_price")
                if qty is not None and price is not None:
                    pending_item["line_total"] = qty * price
            else:
                data[key] = value

        return {
            "invoice_number": normalize_invoice_number(data.get("invoice_number")),
            "vendor": data.get("vendor"),
            "invoice_date": normalize_date(data.get("date")),
            "due_date": normalize_date(data.get("due_date")),
            "subtotal": normalize_money(data.get("subtotal")),
            "tax_amount": normalize_money(data.get("tax")),
            "total": normalize_money(data.get("total")),
            "currency": data.get("currency"),
            "payment_terms": data.get("payment_terms"),
            "items": items,
        }

    header = [h.strip().lower() for h in rows[0]]
    idx = {name: i for i, name in enumerate(header)}

    items = []
    meta = {}

    def get_cell(row, name):
        pos = idx.get(name)
        if pos is None or pos >= len(row):
            return None
        value = row[pos].strip()
        return value or None

    for row in rows[1:]:
        if not row or all(not c.strip() for c in row):
            continue

        label = (get_cell(row, "unit price") or "").lower()
        amount = get_cell(row, "line total")

        if label.startswith("subtotal"):
            meta["subtotal"] = normalize_money(amount)
            continue
        if label.startswith("tax"):
            meta["tax_amount"] = normalize_money(amount)
            continue
        if label.startswith("total"):
            meta["total"] = normalize_money(amount)
            continue

        item = get_cell(row, "item")
        if not item:
            continue

        qty = normalize_qty(get_cell(row, "qty"))
        price = normalize_money(get_cell(row, "unit price"))
        line_total = normalize_money(get_cell(row, "line total"))
        if line_total is None and qty is not None and price is not None:
            line_total = qty * price

        items.append(
            {
                "item": item,
                "quantity": qty,
                "unit_price": price,
                "line_total": line_total,
            }
        )

        if "invoice_number" not in meta:
            meta["invoice_number"] = get_cell(row, "invoice number")
            meta["vendor"] = get_cell(row, "vendor")
            meta["invoice_date"] = get_cell(row, "date")
            meta["due_date"] = get_cell(row, "due date")

    return {
        "invoice_number": normalize_invoice_number(meta.get("invoice_number")),
        "vendor": meta.get("vendor"),
        "invoice_date": normalize_date(meta.get("invoice_date")),
        "due_date": normalize_date(meta.get("due_date")),
        "subtotal": meta.get("subtotal"),
        "tax_amount": meta.get("tax_amount"),
        "total": meta.get("total"),
        "currency": None,
        "payment_terms": None,
        "items": items,
    }


def parse_txt_invoice(path):
    text = path.read_text(encoding="utf-8")
    return parse_txt_content(text)


def parse_pdf_invoice(path):
    reader = PdfReader(str(path))
    pages_text = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        pages_text.append(page_text)

    text = "\n".join(pages_text)
    parsed = parse_txt_content(text)

    if not parsed.get("invoice_number"):
        parsed["invoice_number"] = normalize_invoice_number(path.stem.replace("invoice_", "INV-"))

    return parsed


def regenerate_sample_pdfs():
    script_path = BASE_DIR / "generate_pdfs.py"
    if not script_path.exists():
        return

    result = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True)
    if result.returncode != 0:
        print("Warning: could not regenerate PDFs from generate_pdfs.py")
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip())


def parse_invoice_file(path):
    suffix = path.suffix.lower()
    if suffix == ".json":
        return parse_json_invoice(path), "parsed"
    if suffix == ".xml":
        return parse_xml_invoice(path), "parsed"
    if suffix == ".csv":
        return parse_csv_invoice(path), "parsed"
    if suffix == ".txt":
        return parse_txt_invoice(path), "parsed"
    if suffix == ".pdf":
        return parse_pdf_invoice(path), "parsed"
    return {"items": []}, f"skipped_unsupported_format:{suffix}"


def ensure_tables(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS inventory (
            item TEXT PRIMARY KEY,
            stock INTEGER NOT NULL
        )
        """
    )

    cursor.executemany(
        """
        INSERT INTO inventory (item, stock)
        VALUES (?, ?)
        ON CONFLICT(item) DO UPDATE SET
            stock = excluded.stock
        """,
        [
            ("WidgetA", 15),
            ("WidgetB", 10),
            ("GadgetX", 5),
            ("FakeItem", 0),
        ],
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT UNIQUE,
            invoice_number TEXT,
            vendor TEXT,
            invoice_date TEXT,
            due_date TEXT,
            subtotal REAL,
            tax_amount REAL,
            total REAL,
            currency TEXT,
            payment_terms TEXT,
            file_format TEXT,
            parse_status TEXT,
            raw_content TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS invoice_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            item_name TEXT,
            quantity INTEGER,
            unit_price REAL,
            line_total REAL,
            FOREIGN KEY(invoice_id) REFERENCES invoices(id)
        )
        """
    )


def seed_invoices():
    regenerate_sample_pdfs()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    ensure_tables(cursor)

    files = sorted(INVOICES_DIR.glob("invoice_*"))
    inserted_count = 0

    for path in files:
        parsed, parse_status = parse_invoice_file(path)
        raw_content = path.read_text(encoding="utf-8", errors="ignore")

        cursor.execute(
            """
            INSERT INTO invoices (
                source_file, invoice_number, vendor, invoice_date, due_date,
                subtotal, tax_amount, total, currency, payment_terms,
                file_format, parse_status, raw_content
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_file) DO UPDATE SET
                invoice_number = excluded.invoice_number,
                vendor = excluded.vendor,
                invoice_date = excluded.invoice_date,
                due_date = excluded.due_date,
                subtotal = excluded.subtotal,
                tax_amount = excluded.tax_amount,
                total = excluded.total,
                currency = excluded.currency,
                payment_terms = excluded.payment_terms,
                file_format = excluded.file_format,
                parse_status = excluded.parse_status,
                raw_content = excluded.raw_content
            """,
            (
                path.name,
                parsed.get("invoice_number"),
                parsed.get("vendor"),
                parsed.get("invoice_date"),
                parsed.get("due_date"),
                parsed.get("subtotal"),
                parsed.get("tax_amount"),
                parsed.get("total"),
                parsed.get("currency"),
                parsed.get("payment_terms"),
                path.suffix.lower().lstrip("."),
                parse_status,
                raw_content,
            ),
        )

        cursor.execute("SELECT id FROM invoices WHERE source_file = ?", (path.name,))
        invoice_id_row = cursor.fetchone()
        if not invoice_id_row:
            continue

        invoice_id = invoice_id_row[0]
        cursor.execute("DELETE FROM invoice_items WHERE invoice_id = ?", (invoice_id,))

        for item in parsed.get("items", []):
            cursor.execute(
                """
                INSERT INTO invoice_items (invoice_id, item_name, quantity, unit_price, line_total)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    invoice_id,
                    item.get("item"),
                    item.get("quantity"),
                    item.get("unit_price"),
                    item.get("line_total"),
                ),
            )

        inserted_count += 1

    cursor.execute(
        """
        DELETE FROM invoice_items
        WHERE invoice_id NOT IN (SELECT id FROM invoices)
        """
    )

    conn.commit()

    cursor.execute("SELECT COUNT(*) FROM invoices")
    invoice_rows = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM invoice_items")
    item_rows = cursor.fetchone()[0]

    conn.close()

    print(f"Loaded {inserted_count} files into {DB_PATH.name}")
    print(f"Rows in invoices: {invoice_rows}")
    print(f"Rows in invoice_items: {item_rows}")


if __name__ == "__main__":
    seed_invoices()
