import os
import glob
import sqlite3
import webbrowser
import threading
from datetime import datetime
from flask import Flask, render_template, jsonify, request

PORT = 5000
DEFAULT_DB = "companies_filtered.db"

app = Flask(__name__)

KNOWN_DATABASES = [
    "companies_filtered.db",
    "bad_design_companies.db",
    "http_companies.db",
    "companies.db",
]

ALLOWED_MODES = {"all", "bad", "unverified", "dead"}

ALLOWED_FLAGS = [
    "NO_HTTPS",
    "NO_VIEWPORT",
    "THIN_CONTENT",
    "SSL_ERROR",
    "REQUEST_FAIL",
    "OLD_HTML_PATTERNS",
    "TABLE_LAYOUT_HEAVY",
    "INLINE_STYLE_HEAVY",
    "NO_META_DESCRIPTION",
    "TITLE_MISSING",
    "TITLE_WEAK",
    "NON_HTML_HOMEPAGE",
]

ALLOWED_STATUS_MODE = {"all", "empty", "contains"}


# =========================
# Helpers
# =========================
def is_safe_db_name(db_name: str) -> bool:
    return (
        isinstance(db_name, str)
        and db_name.endswith(".db")
        and "/" not in db_name
        and "\\" not in db_name
        and ".." not in db_name
        and os.path.exists(db_name)
    )

def get_db_connection(db_name: str):
    if not os.path.exists(db_name):
        return None
    conn = sqlite3.connect(db_name)
    conn.row_factory = sqlite3.Row
    return conn

def normalize_web_url(web: str) -> str:
    if not web:
        return ""
    w = web.strip()
    if not w:
        return ""
    if not w.lower().startswith(("http://", "https://")):
        return "http://" + w
    return w

def companies_table_exists(conn: sqlite3.Connection) -> bool:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='companies'")
    return cur.fetchone() is not None

def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None

def column_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]
    return col in cols

def parse_flags_param(raw: str) -> list[str]:
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    allowed = set(ALLOWED_FLAGS)
    out = []
    seen = set()
    for p in parts:
        if p in allowed and p not in seen:
            out.append(p)
            seen.add(p)
    return out

def clamp_int(val: str, lo: int, hi: int, default: int) -> int:
    try:
        x = int(val)
    except Exception:
        return default
    return max(lo, min(hi, x))

def build_where_and_params(
    mode: str,
    min_flags: int,
    required_flags: list[str],
    status_mode: str,
    status_query: str,
) -> tuple[str, list]:
    where = ["web IS NOT NULL AND TRIM(web) != ''"]
    params: list = []

    if mode == "bad":
        where.append("web_verified = ?")
        params.append("nekvalitní grafika")
    elif mode == "dead":
        where.append("web_verified = ?")
        params.append("nefunguje")
    elif mode == "unverified":
        where.append("(web_verified IS NULL OR TRIM(web_verified) = '')")

    if min_flags > 0:
        where.append("""
            (
                CASE
                    WHEN popis IS NULL OR TRIM(popis) = '' THEN 0
                    ELSE (LENGTH(popis) - LENGTH(REPLACE(popis, ',', '')) + 1)
                END
            ) >= ?
        """)
        params.append(min_flags)

    if required_flags:
        where.append("popis IS NOT NULL AND TRIM(popis) != ''")
        for f in required_flags:
            where.append("popis LIKE ?")
            params.append(f"%{f}%")

    if status_mode == "empty":
        where.append("(status IS NULL OR TRIM(status) = '')")
    elif status_mode == "contains":
        q = (status_query or "").strip()
        if q:
            where.append("status LIKE ?")
            params.append(f"%{q}%")

    return " AND ".join(where), params


def open_browser():
    webbrowser.open(f"http://127.0.0.1:{PORT}/")


# =========================
# Routes
# =========================
@app.route("/")
def index():
    return render_template("index_02.html")

@app.route("/api/databases")
def list_databases():
    found = []
    for db in KNOWN_DATABASES:
        if os.path.exists(db) and db not in found:
            found.append(db)
    for db in glob.glob("*.db"):
        if db not in found:
            found.append(db)
    return jsonify(found)

@app.route("/api/flags")
def list_flags():
    return jsonify(ALLOWED_FLAGS)

@app.route("/api/companies")
def api_companies():
    db_name = request.args.get("db", DEFAULT_DB)
    mode = request.args.get("mode", "all").strip().lower()
    min_flags = clamp_int(request.args.get("min_flags", "0"), 0, 50, 0)
    required_flags = parse_flags_param(request.args.get("flags", ""))

    status_mode = request.args.get("status_mode", "all").strip().lower()
    status_query = request.args.get("status_query", "")

    if not is_safe_db_name(db_name):
        return jsonify({"error": "Invalid database name"}), 400
    if mode not in ALLOWED_MODES:
        return jsonify({"error": f"Invalid mode. Allowed: {sorted(ALLOWED_MODES)}"}), 400
    if status_mode not in ALLOWED_STATUS_MODE:
        return jsonify({"error": f"Invalid status_mode. Allowed: {sorted(ALLOWED_STATUS_MODE)}"}), 400

    conn = get_db_connection(db_name)
    if conn is None:
        return jsonify({"companies": [], "current_db": db_name})

    try:
        if not companies_table_exists(conn):
            conn.close()
            return jsonify({"companies": [], "current_db": db_name})

        # Kontaktní sloupce jsou volitelné (ne všude musí být)
        base_cols = ["id", "name", "web", "funguje", "web_verified", "popis", "status"]
        contact_cols = ["phone", "email", "instagram", "facebook"]
        cols_present = set()

        for col in base_cols + contact_cols:
            if column_exists(conn, "companies", col):
                cols_present.add(col)

        # pokud chybí některé základní, fallback minimální
        if not {"id", "name", "web"}.issubset(cols_present):
            cur = conn.execute("SELECT id, name, web FROM companies WHERE web IS NOT NULL AND TRIM(web) != '' ORDER BY id ASC")
            rows = cur.fetchall()
            conn.close()
            companies = [{"id": r["id"], "name": r["name"], "web": normalize_web_url(r["web"])} for r in rows]
            return jsonify({"companies": companies, "current_db": db_name})

        # Sestav SELECT dynamicky podle dostupných sloupců
        select_cols = ["id", "name", "web"]
        for c in ["funguje", "web_verified", "popis", "status", "phone", "email", "instagram", "facebook"]:
            if c in cols_present:
                select_cols.append(c)

        where_sql, params = build_where_and_params(
            mode=mode,
            min_flags=min_flags,
            required_flags=required_flags,
            status_mode=status_mode,
            status_query=status_query,
        )

        sql = f"""
            SELECT {", ".join(select_cols)}
            FROM companies
            WHERE {where_sql}
            ORDER BY id ASC
        """

        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        conn.close()

        companies = []
        for r in rows:
            item = {
                "id": r["id"],
                "name": r["name"],
                "web": normalize_web_url(r["web"]),
            }
            # optional fields
            for c in ["funguje", "web_verified", "popis", "status", "phone", "email", "instagram", "facebook"]:
                if c in r.keys():
                    item[c] = r[c]
            companies.append(item)

        return jsonify({
            "companies": companies,
            "current_db": db_name,
            "mode": mode,
            "min_flags": min_flags,
            "flags": required_flags,
            "status_mode": status_mode,
            "status_query": status_query,
        })

    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500


@app.route("/api/companies/<int:company_id>/status", methods=["POST"])
def update_status(company_id: int):
    db_name = request.args.get("db", DEFAULT_DB)
    if not is_safe_db_name(db_name):
        return jsonify({"error": "Invalid database name"}), 400

    data = request.get_json(silent=True) or {}
    new_status = (data.get("status") or "").strip()

    if len(new_status) > 200:
        return jsonify({"error": "Status too long (max 200 chars)"}), 400

    conn = get_db_connection(db_name)
    if conn is None:
        return jsonify({"error": "DB not connected"}), 500

    try:
        if not column_exists(conn, "companies", "status"):
            conn.close()
            return jsonify({"error": "Column 'status' does not exist"}), 400

        conn.execute("UPDATE companies SET status=? WHERE id=?", (new_status if new_status else None, company_id))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "id": company_id, "status": new_status})
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500


@app.route("/api/export", methods=["POST"])
def export_filtered_database():
    db_name = request.args.get("db", DEFAULT_DB)
    mode = request.args.get("mode", "all").strip().lower()
    min_flags = clamp_int(request.args.get("min_flags", "0"), 0, 50, 0)
    required_flags = parse_flags_param(request.args.get("flags", ""))

    status_mode = request.args.get("status_mode", "all").strip().lower()
    status_query = request.args.get("status_query", "")

    if not is_safe_db_name(db_name):
        return jsonify({"error": "Invalid database name"}), 400
    if mode not in ALLOWED_MODES:
        return jsonify({"error": f"Invalid mode. Allowed: {sorted(ALLOWED_MODES)}"}), 400
    if status_mode not in ALLOWED_STATUS_MODE:
        return jsonify({"error": f"Invalid status_mode. Allowed: {sorted(ALLOWED_STATUS_MODE)}"}), 400

    src = get_db_connection(db_name)
    if src is None:
        return jsonify({"error": "DB not connected"}), 500

    try:
        where_sql, params = build_where_and_params(
            mode=mode,
            min_flags=min_flags,
            required_flags=required_flags,
            status_mode=status_mode,
            status_query=status_query,
        )

        cur = src.execute(f"SELECT id FROM companies WHERE {where_sql}", params)
        company_ids = [r["id"] for r in cur.fetchall()]

        if not company_ids:
            src.close()
            return jsonify({"error": "No rows match filters, nothing to export."}), 400

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = os.path.splitext(os.path.basename(db_name))[0]
        out_name = f"export_{base}_{ts}.db"

        if os.path.exists(out_name):
            os.remove(out_name)
        dst = sqlite3.connect(out_name)
        dst.execute("PRAGMA journal_mode=WAL;")

        tables_to_copy = ["companies"]
        if table_exists(src, "company_categories"):
            tables_to_copy.append("company_categories")
        if table_exists(src, "categories"):
            tables_to_copy.append("categories")

        for t in tables_to_copy:
            cur = src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,))
            row = cur.fetchone()
            if row and row["sql"]:
                dst.execute(row["sql"])
        dst.commit()

        def chunks(lst, n=900):
            for i in range(0, len(lst), n):
                yield lst[i:i+n]

        for ch in chunks(company_ids):
            placeholders = ",".join(["?"] * len(ch))
            cur = src.execute(f"SELECT * FROM companies WHERE id IN ({placeholders})", ch)
            rows = cur.fetchall()
            if not rows:
                continue
            cols = rows[0].keys()
            col_list = ",".join(cols)
            qmarks = ",".join(["?"] * len(cols))
            dst.executemany(
                f"INSERT INTO companies ({col_list}) VALUES ({qmarks})",
                [tuple(r[c] for c in cols) for r in rows]
            )

        if "company_categories" in tables_to_copy:
            category_ids = set()
            for ch in chunks(company_ids):
                placeholders = ",".join(["?"] * len(ch))
                cur = src.execute(
                    f"SELECT * FROM company_categories WHERE company_id IN ({placeholders})",
                    ch
                )
                rows = cur.fetchall()
                if rows:
                    cols = rows[0].keys()
                    col_list = ",".join(cols)
                    qmarks = ",".join(["?"] * len(cols))
                    dst.executemany(
                        f"INSERT INTO company_categories ({col_list}) VALUES ({qmarks})",
                        [tuple(r[c] for c in cols) for r in rows]
                    )
                    if "category_id" in cols:
                        for r in rows:
                            if r["category_id"] is not None:
                                category_ids.add(r["category_id"])

            if "categories" in tables_to_copy and category_ids:
                cat_ids = list(category_ids)
                for ch in chunks(cat_ids):
                    placeholders = ",".join(["?"] * len(ch))
                    cur = src.execute(f"SELECT * FROM categories WHERE id IN ({placeholders})", ch)
                    rows = cur.fetchall()
                    if rows:
                        cols = rows[0].keys()
                        col_list = ",".join(cols)
                        qmarks = ",".join(["?"] * len(cols))
                        dst.executemany(
                            f"INSERT INTO categories ({col_list}) VALUES ({qmarks})",
                            [tuple(r[c] for c in cols) for r in rows]
                        )

        dst.commit()
        dst.close()
        src.close()

        return jsonify({"success": True, "export_db": out_name, "count": len(company_ids)})

    except Exception as e:
        try:
            src.close()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print("Starting viewer...")
    threading.Timer(1.2, open_browser).start()
    app.run(port=PORT, debug=False)
