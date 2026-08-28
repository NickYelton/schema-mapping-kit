"""Regenerate the sample fixtures. Run: uv run python samples/generate.py

Each fixture encodes a distinct class of real-world mess. The committed outputs are what the
tests assert against; this script exists so the mess is documented and reproducible.

Rows are built from a fixed seed so regeneration is byte-stable.
"""

import json
import random
from pathlib import Path

import xlsxwriter

HERE = Path(__file__).parent
ROW_COUNT = 30
SEED = 20240315

PRODUCTS = [
    ("SKU-4471", "Widget, large", "24.99"),
    ("SKU-1120", "Grommet 12mm", "0.85"),
    ("SKU-9902", "Bearing assembly", "1142.00"),
    ("SKU-3345", "Hex bolt M8", "0.12"),
    ("SKU-8823", "Drive belt", "38.50"),
    ("SKU-6610", "Coupling sleeve", "76.40"),
    ("SKU-2287", "Seal kit", "12.75"),
]
CUSTOMERS = ["CUST-8891", "CUST-4417", "CUST-2210", "CUST-7734", "CUST-9001", "CUST-5560"]
STATUSES = ["pending", "shipped", "delivered", "cancelled", "returned"]
DISCOUNTS = ["0", "5", "10", "15", "20"]


def build_rows(rng: random.Random) -> list[dict]:
    """Order/line-item rows with realistic repetition: customers and SKUs recur, and about a
    third of orders carry a second line."""
    rows: list[dict] = []
    order_seq = 101
    while len(rows) < ROW_COUNT:
        order_id = f"ORD-{order_seq:06d}"
        order_seq += 1
        customer = rng.choice(CUSTOMERS)
        status = rng.choice(STATUSES)
        day = rng.randint(1, 28)
        ordered = (2024, 3, day)
        shipped = None if status in {"pending", "cancelled"} else (2024, 3, min(day + 3, 31))
        for line in range(1, rng.choice([1, 1, 1, 2]) + 1):
            if len(rows) >= ROW_COUNT:
                break
            sku, name, price = rng.choice(PRODUCTS)
            rows.append(
                {
                    "order_id": order_id,
                    "line_number": line,
                    "customer_id": customer,
                    "sku": sku,
                    "product_name": name,
                    "quantity": rng.choice([1, 2, 3, 4, 6, 12, 24, 100, 500]),
                    "unit_price": price,
                    "currency": "USD",
                    "discount_pct": rng.choice(DISCOUNTS),
                    "ordered": ordered,
                    "shipped": shipped,
                    "status": status,
                }
            )
    return rows


def iso(date: tuple[int, int, int] | None) -> str:
    return "" if date is None else f"{date[0]:04d}-{date[1]:02d}-{date[2]:02d}"


def us(date: tuple[int, int, int] | None) -> str:
    return "" if date is None else f"{date[1]:02d}/{date[2]:02d}/{date[0]:04d}"


def euro(date: tuple[int, int, int] | None) -> str:
    return "" if date is None else f"{date[2]:02d}/{date[1]:02d}/{date[0]:04d}"


HEADER = [
    "order_id", "line_number", "customer_id", "sku", "product_name", "quantity",
    "unit_price", "currency", "discount_pct", "order_date", "ship_date", "status",
]


def write_clean(rows: list[dict]) -> None:
    lines = [",".join(HEADER)]
    for r in rows:
        cells = [
            r["order_id"], str(r["line_number"]), r["customer_id"], r["sku"],
            f'"{r["product_name"]}"' if "," in r["product_name"] else r["product_name"],
            str(r["quantity"]), r["unit_price"], r["currency"], r["discount_pct"],
            iso(r["ordered"]), iso(r["shipped"]), r["status"],
        ]
        lines.append(",".join(cells))
    (HERE / "orders_clean.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_messy(rows: list[dict], rng: random.Random) -> None:
    """Preamble junk, cp1252 encoding, semicolon delimiter, money symbols, several null
    spellings, mixed-case status, and a column named X7 whose values alone identify it."""
    preamble = [
        "Acme Industrial Supply – Order Export",
        "Generated 15/03/2024 09:12:44 by j.ramirez@acme.example",
        "CONFIDENTIAL – internal use only",
        "",
    ]
    header = [
        "Ord #", "Ln", "Cust", "Item SKU", "Description", "Qty", "Price Each",
        "Curr", "Disc %", "Ordered On", "Shipped", "X7",
    ]
    null_spellings = ["N/A", "-", "", "NULL", "n/a"]
    out = []
    for index, r in enumerate(rows):
        status = r["status"]
        cased = [status.upper(), status.capitalize(), status][index % 3]
        price = f"${float(r['unit_price']):,.2f}"
        name = "Café table leg" if index == 3 else r["product_name"]

        qty = str(r["quantity"])
        cust = r["customer_id"]
        ordered = us(r["ordered"])
        shipped = us(r["shipped"]) or rng.choice(null_spellings)
        disc = f"{r['discount_pct']}%"

        # Scatter a few unparseable values so the rejection report has real work to do.
        if index == 7:
            ordered = "13/45/2024"
        if index == 11:
            qty = "NULL"
        if index == 14:
            cust = ""
        if index == 18:
            price = "n/a"
        if index == 22:
            disc = "N/A"
        if index == 25:
            ordered = "not recorded"

        out.append([
            r["order_id"], str(r["line_number"]), cust, r["sku"], name, qty, price,
            r["currency"], disc, ordered, shipped, cased,
        ])

    lines = preamble + [";".join(header)] + [";".join(c) for c in out]
    (HERE / "orders_messy.csv").write_bytes(("\n".join(lines) + "\n").encode("cp1252"))


GERMAN_NAMES = {
    "Widget, large": "Widget, gross",
    "Grommet 12mm": "Dichtring 12mm",
    "Bearing assembly": "Lagereinheit",
    "Hex bolt M8": "Sechskantschraube M8",
    "Drive belt": "Antriebsriemen",
    "Coupling sleeve": "Kupplungshuelse",
    "Seal kit": "Dichtungssatz",
}
GERMAN_STATUS = {
    "pending": "offen", "shipped": "versandt", "delivered": "geliefert",
    "cancelled": "storniert", "returned": "retourniert",
}


def de_number(value: str) -> str:
    """Render 1142.00 as the German 1.142,00."""
    whole, _, frac = f"{float(value):.2f}".partition(".")
    grouped = f"{int(whole):,}".replace(",", ".")
    return f"{grouped},{frac}"


def write_euro(rows: list[dict]) -> None:
    """Three sheets, a two-row merged header, DD/MM/YYYY dates, and 1.234,56 decimals."""
    path = HERE / "orders_euro.xlsx"
    book = xlsxwriter.Workbook(str(path), {"constant_memory": False})

    cover = book.add_worksheet("Deckblatt")
    cover.write(0, 0, "Bestellungen Export")
    cover.write(1, 0, "Zeitraum: 01.03.2024 - 31.03.2024")

    sheet = book.add_worksheet("Bestellungen")
    groups = ["Bestellung", "", "", "Artikel", "", "", "Betrag", "", "", "Termine", "", ""]
    fields = [
        "Bestellnr", "Pos", "Kundennr", "Artikelnr", "Bezeichnung", "Menge",
        "Einzelpreis", "Waehrung", "Rabatt", "Bestelldatum", "Lieferdatum", "Status",
    ]
    for span in ((0, 2), (3, 5), (6, 8), (9, 11)):
        sheet.merge_range(0, span[0], 0, span[1], groups[span[0]])
    for col, value in enumerate(fields):
        sheet.write(1, col, value)

    for r_index, r in enumerate(rows, start=2):
        values = [
            r["order_id"].replace("ORD", "BST"), r["line_number"],
            r["customer_id"].replace("CUST", "KND"), r["sku"].replace("SKU", "ART"),
            GERMAN_NAMES[r["product_name"]], r["quantity"],
            de_number(r["unit_price"]), "EUR", de_number(r["discount_pct"]),
            euro(r["ordered"]), euro(r["shipped"]), GERMAN_STATUS[r["status"]],
        ]
        for c_index, value in enumerate(values):
            sheet.write(r_index, c_index, value)

    notes = book.add_worksheet("Hinweise")
    notes.write(0, 0, "Betraege in EUR. Datumsformat TT/MM/JJJJ.")
    book.close()


def write_nested(rows: list[dict]) -> None:
    """Nested objects, inconsistent keys between records, and missing fields."""
    names = {
        "CUST-8891": "Northgate Fabrication", "CUST-4417": "Pemberton Works",
        "CUST-2210": "Voss Assembly", "CUST-7734": "Ashby Tooling",
        "CUST-9001": "Halloran Metals", "CUST-5560": "Kestrel Plant",
    }
    records = []
    for index, r in enumerate(rows):
        customer: dict = {"id": r["customer_id"]}
        if index % 7 != 3:
            customer["name"] = names[r["customer_id"]]

        record: dict = {
            "orderId": r["order_id"],
            "line": r["line_number"],
            "customer": customer,
            "item": {"sku": r["sku"], "name": r["product_name"]},
            "price": {"amount": float(r["unit_price"]), "currency": "USD"},
            "orderedAt": f"{iso(r['ordered'])}T09:14:00Z",
            "state": r["status"],
        }
        # Every seventh record spells quantity differently, as a real export drift would.
        record["qty" if index % 7 == 2 else "quantity"] = r["quantity"]
        if index % 5 != 4:
            record["discountPct"] = int(r["discount_pct"])
        if r["shipped"]:
            record["shippedAt"] = f"{iso(r['shipped'])}T16:02:00Z"
        records.append(record)

    lines = [json.dumps(r, separators=(",", ":")) for r in records]
    (HERE / "orders_nested.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    rng = random.Random(SEED)
    rows = build_rows(rng)
    write_clean(rows)
    write_messy(rows, random.Random(SEED + 1))
    write_euro(rows)
    write_nested(rows)
    print(f"wrote {len(rows)}-row fixtures to {HERE}")
