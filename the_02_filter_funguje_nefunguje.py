#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
import requests
import concurrent.futures as cf
import time
import re
from urllib.parse import urlparse

MAX_WORKERS = 60          # pro 16k webů klidně 40–80
TIMEOUT = 6               # sec
CONNECT_TIMEOUT = 4       # sec
CHUNK_COMMIT = 300        # batch updates do DB
USER_AGENT = "Mozilla/5.0 (compatible; WebAuditBot/1.0; +https://example.invalid)"

# Heuristiky pro "parked domain / suspended / domain for sale"
PARKED_PATTERNS = [
    r"domain\s+for\s+sale",
    r"this\s+domain\s+is\s+for\s+sale",
    r"buy\s+this\s+domain",
    r"parked\s+free",
    r"domain\s+parked",
    r"suspended",
    r"account\s+suspended",
    r"service\s+unavailable",
    r"not\s+found",
    r"error\s+establishing\s+a\s+database\s+connection",
]

def normalize_candidates(raw: str) -> list[str]:
    """Z web hodnoty vyrobí kandidátní URL (https -> http)."""
    if not raw:
        return []

    s = raw.strip()
    if not s:
        return []

    # když je tam třeba "www.example.cz/..." bez schematu
    # nebo "example.cz"
    if not re.match(r"^https?://", s, flags=re.I):
        s = s.lstrip("/")
        return [f"https://{s}", f"http://{s}"]

    # když začíná http://, zkusíme i https:// variantu jako první
    if s.lower().startswith("http://"):
        return [re.sub(r"^http://", "https://", s, flags=re.I), s]

    # když začíná https://, zkusíme i http:// jako fallback
    if s.lower().startswith("https://"):
        return [s, re.sub(r"^https://", "http://", s, flags=re.I)]

    return [s]

def looks_parked_or_dead(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    for pat in PARKED_PATTERNS:
        if re.search(pat, t):
            return True
    return False

def request_ok(url: str) -> tuple[bool, str]:
    """
    Zkusí, zda web funguje.
    Vrací (ok, reason).
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

    # 1) HEAD (rychlejší), ale hodně webů HEAD blokuje → fallback na GET
    try:
        r = requests.head(
            url,
            timeout=(CONNECT_TIMEOUT, TIMEOUT),
            allow_redirects=True,
            verify=True if url.lower().startswith("https://") else False,
            headers=headers,
        )
        # některé weby vrací 405/403 na HEAD – to neznamená mrtvé
        if r.status_code in (200, 204, 301, 302, 307, 308, 403, 405):
            return True, f"HEAD:{r.status_code}"
        # tvrdé chyby
        if 400 <= r.status_code < 600:
            return False, f"HTTP_STATUS:{r.status_code}"
    except requests.exceptions.SSLError:
        return False, "SSL_ERROR"
    except requests.exceptions.ConnectTimeout:
        return False, "CONNECT_TIMEOUT"
    except requests.exceptions.ReadTimeout:
        return False, "READ_TIMEOUT"
    except requests.exceptions.ConnectionError:
        return False, "CONNECTION_ERROR"
    except Exception:
        return False, "HEAD_ERROR"

    # 2) GET (stream, stáhneme jen kousek)
    try:
        r = requests.get(
            url,
            timeout=(CONNECT_TIMEOUT, TIMEOUT),
            allow_redirects=True,
            verify=True if url.lower().startswith("https://") else False,
            headers=headers,
            stream=True,
        )
        status = r.status_code
        if 200 <= status < 400:
            # přečti jen malý kousek
            chunk = ""
            try:
                for part in r.iter_content(chunk_size=4096):
                    if not part:
                        continue
                    chunk += part.decode("utf-8", errors="ignore")
                    if len(chunk) > 40000:
                        break
            except Exception:
                chunk = ""

            if looks_parked_or_dead(chunk):
                return False, "PARKED_OR_SUSPENDED"

            return True, f"GET:{status}"

        return False, f"HTTP_STATUS:{status}"
    except requests.exceptions.SSLError:
        return False, "SSL_ERROR"
    except requests.exceptions.ConnectTimeout:
        return False, "CONNECT_TIMEOUT"
    except requests.exceptions.ReadTimeout:
        return False, "READ_TIMEOUT"
    except requests.exceptions.ConnectionError:
        return False, "CONNECTION_ERROR"
    except Exception:
        return False, "GET_ERROR"

def check_company(row: tuple, web_idx: int) -> tuple[int, bool, str]:
    """
    Vrátí (company_id, ok, reason)
    """
    company_id = row[0]
    raw_web = row[web_idx]
    candidates = normalize_candidates(raw_web)

    if not candidates:
        return company_id, False, "NO_WEB"

    # postupně zkus kandidáty
    last_reason = "UNKNOWN"
    for url in candidates:
        ok, reason = request_ok(url)
        if ok:
            return company_id, True, f"OK:{reason}"
        last_reason = f"{urlparse(url).scheme}:{reason}"

    return company_id, False, last_reason

def ensure_columns(conn: sqlite3.Connection) -> None:
    """
    Zkontroluje, že existuje popis (kdyby náhodou).
    """
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(companies);")
    cols = {r[1] for r in cur.fetchall()}
    if "popis" not in cols:
        cur.execute("ALTER TABLE companies ADD COLUMN popis TEXT;")
        conn.commit()

def fetch_targets(conn: sqlite3.Connection) -> list[tuple]:
    """
    Vytáhne firmy ke kontrole:
    web_verified is NULL nebo prázdné.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM companies
        WHERE web_verified IS NULL OR TRIM(web_verified) = '';
    """)
    return cur.fetchall()

def main(
        target_db: str,
):
    t0 = time.time()
    print("=== Filter #1: funguje / nefunguje ===")

    conn = sqlite3.connect(target_db)
    conn.execute("PRAGMA journal_mode=WAL;")
    ensure_columns(conn)

    # zjisti index sloupce web
    cur = conn.cursor()
    cur.execute("SELECT * FROM companies LIMIT 0;")
    columns = [d[0] for d in cur.description]
    try:
        web_idx = columns.index("web")
    except ValueError:
        raise SystemExit("CRITICAL: sloupec 'web' neexistuje v tabulce companies")

    rows = fetch_targets(conn)
    total = len(rows)
    print(f"Loaded targets: {total}")

    if total == 0:
        print("Nothing to do.")
        conn.close()
        return

    updates = []
    processed = 0
    ok_count = 0
    bad_count = 0

    with cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(check_company, row, web_idx) for row in rows]
        for fut in cf.as_completed(futures):
            processed += 1
            company_id, ok, reason = fut.result()

            if ok:
                ok_count += 1
                # funguje=1, web_verified nech prázdné, popis nech prázdné
                updates.append(( "1", None, None, company_id))
            else:
                bad_count += 1
                # funguje=0, web_verified=nefunguje, popis=důvod
                updates.append(( "0", "nefunguje", reason, company_id))

            # batch commit
            if len(updates) >= CHUNK_COMMIT:
                cur.executemany(
                    "UPDATE companies SET funguje=?, web_verified=?, popis=? WHERE id=?",
                    updates
                )
                conn.commit()
                updates = []

            if processed % 200 == 0 or processed == total:
                elapsed = time.time() - t0
                rate = processed / elapsed if elapsed > 0 else 0
                print(f"Progress: {processed}/{total} | OK:{ok_count} | BAD:{bad_count} | {rate:.1f}/s")

    # flush zbytku
    if updates:
        cur.executemany(
            "UPDATE companies SET funguje=?, web_verified=?, popis=? WHERE id=?",
            updates
        )
        conn.commit()

    conn.close()
    print(f"Done in {time.time() - t0:.1f}s | OK:{ok_count} | BAD:{bad_count}")

if __name__ == "__main__":
    main()
