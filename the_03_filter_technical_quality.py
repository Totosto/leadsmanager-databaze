#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
import requests
import concurrent.futures as cf
import re
import time

MAX_WORKERS = 50
TIMEOUT = 6
CONNECT_TIMEOUT = 4

USER_AGENT = "Mozilla/5.0 (compatible; WebAuditBot/1.0)"

def normalize_url(raw):
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None
    if not s.startswith(("http://", "https://")):
        return "https://" + s
    return s

def fetch_targets(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT id, web
        FROM companies
        WHERE funguje='1'
        AND (web_verified IS NULL OR TRIM(web_verified)='')
    """)
    return cur.fetchall()

def check_site(row):
    company_id, raw_web = row
    flags = []

    url = normalize_url(raw_web)
    if not url:
        return company_id, []

    try:
        r = requests.get(
            url,
            timeout=(CONNECT_TIMEOUT, TIMEOUT),
            allow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            verify=True
        )
    except requests.exceptions.SSLError:
        flags.append("SSL_ERROR")
        return company_id, flags
    except Exception:
        flags.append("REQUEST_FAIL")
        return company_id, flags

    final_url = r.url

    # 1️⃣ HTTPS strict check
    if not final_url.lower().startswith("https://"):
        flags.append("NO_HTTPS")

    html = r.text.lower()

    # 2️⃣ Viewport check
    if '<meta name="viewport"' not in html:
        flags.append("NO_VIEWPORT")

    # 3️⃣ Thin content check
    text_only = re.sub("<[^<]+?>", "", html)
    text_only = re.sub(r"\s+", " ", text_only).strip()

    if len(text_only) < 400:
        flags.append("THIN_CONTENT")

    return company_id, flags

def main(
        target_db: str
):
    print("=== Filter #2: Technical Quality ===")
    t0 = time.time()

    conn = sqlite3.connect(target_db)
    conn.execute("PRAGMA journal_mode=WAL;")

    rows = fetch_targets(conn)
    total = len(rows)

    print(f"Targets: {total}")

    cur = conn.cursor()
    updates = []

    processed = 0
    bad_count = 0

    with cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(check_site, row) for row in rows]

        for fut in cf.as_completed(futures):
            processed += 1
            company_id, flags = fut.result()

            if flags:
                bad_count += 1
                popis = ", ".join(flags)
                updates.append(("nekvalitní grafika", popis, company_id))

            if len(updates) >= 200:
                cur.executemany(
                    "UPDATE companies SET web_verified=?, popis=? WHERE id=?",
                    updates
                )
                conn.commit()
                updates = []

            if processed % 200 == 0 or processed == total:
                rate = processed / (time.time() - t0)
                print(f"Progress: {processed}/{total} | Flagged:{bad_count} | {rate:.1f}/s")

    if updates:
        cur.executemany(
            "UPDATE companies SET web_verified=?, popis=? WHERE id=?",
            updates
        )
        conn.commit()

    conn.close()

    print(f"Done in {time.time()-t0:.1f}s | Flagged:{bad_count}")

if __name__ == "__main__":
    main()
