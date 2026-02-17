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

# ---- Heuristiky pro "starý web" ----
OLD_TAGS = ["<font", "<center", "<marquee", "<blink"]
OLD_ATTRS = ["bgcolor=", "align=", "border=", "cellpadding=", "cellspacing="]

WEAK_TITLES = {
    "home", "index", "úvod", "uvod", "titulní", "titulni",
    "welcome", "start", "homepage"
}

def normalize_url(raw: str) -> str | None:
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None
    if not s.startswith(("http://", "https://")):
        return "https://" + s
    return s

def extract_title(html_lower: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html_lower, flags=re.I | re.S)
    if not m:
        return ""
    title = re.sub(r"\s+", " ", m.group(1)).strip()
    return title

def has_meta_description(html_lower: str) -> bool:
    # jednoduchý check pro name="description"
    return 'name="description"' in html_lower or "name='description'" in html_lower

def count_tables(html_lower: str) -> int:
    return html_lower.count("<table")

def count_inline_styles(html_lower: str) -> int:
    # počítáme výskyty style="
    return html_lower.count('style="') + html_lower.count("style='")

def detect_flags(html: str) -> list[str]:
    flags = []
    hl = html.lower()

    # OLD_HTML_PATTERNS
    old_hits = 0
    for t in OLD_TAGS:
        if t in hl:
            old_hits += 1
    for a in OLD_ATTRS:
        if a in hl:
            old_hits += 1
    if old_hits >= 2:
        flags.append("OLD_HTML_PATTERNS")

    # TABLE_LAYOUT_HEAVY
    tbl = count_tables(hl)
    # hodně tabulek na homepage často znamená starý layout
    if tbl >= 8:
        flags.append(f"TABLE_LAYOUT_HEAVY({tbl})")

    # INLINE_STYLE_HEAVY
    inl = count_inline_styles(hl)
    if inl >= 60:
        flags.append(f"INLINE_STYLE_HEAVY({inl})")

    # NO_META_DESCRIPTION
    if not has_meta_description(hl):
        flags.append("NO_META_DESCRIPTION")

    # TITLE_WEAK / TITLE_MISSING
    title = extract_title(hl)
    if not title:
        flags.append("TITLE_MISSING")
    else:
        tnorm = re.sub(r"[^a-z0-9áéíóúůýčďěňřšťž ]+", " ", title.lower())
        tnorm = re.sub(r"\s+", " ", tnorm).strip()
        if len(tnorm) <= 4:
            flags.append(f"TITLE_WEAK({tnorm})")
        elif tnorm in WEAK_TITLES:
            flags.append(f"TITLE_WEAK({tnorm})")

    return flags

def append_popis(existing: str | None, new_flags: list[str]) -> str | None:
    if not new_flags:
        return existing

    existing = (existing or "").strip()
    # zabráníme duplicitám: když už flag existuje v textu, nepřidávat znovu
    to_add = []
    for f in new_flags:
        if f not in existing:
            to_add.append(f)

    if not to_add:
        return existing if existing else None

    add_str = ", ".join(to_add)
    if not existing:
        return add_str
    return existing + ", " + add_str

def fetch_targets(conn: sqlite3.Connection) -> list[tuple[int, str, str | None, str | None]]:
    """
    Bereme jen funguje=1 a web_verified je NULL nebo nekvalitní grafika.
    Taháme i popis, abychom mohli appendovat.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT id, web, web_verified, popis
        FROM companies
        WHERE funguje='1'
          AND (web_verified IS NULL OR TRIM(web_verified)='' OR web_verified='nekvalitní grafika')
    """)
    return cur.fetchall()

def analyze_one(row: tuple[int, str, str | None, str | None]):
    company_id, web, web_verified, popis = row
    url = normalize_url(web)
    if not url:
        return company_id, web_verified, popis, []  # nic nepřidáváme

    try:
        r = requests.get(
            url,
            timeout=(CONNECT_TIMEOUT, TIMEOUT),
            allow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            verify=True,
        )
        # bereme jen HTML
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "text/html" not in ctype and "application/xhtml" not in ctype:
            # když to není html, často to bývá pdf nebo něco divného
            return company_id, web_verified, popis, ["NON_HTML_HOMEPAGE"]

        html = r.text
    except requests.exceptions.SSLError:
        # SSL_ERROR už může existovat z filtru #2; tady jen případně doplníme
        return company_id, web_verified, popis, ["SSL_ERROR"]
    except Exception:
        return company_id, web_verified, popis, ["REQUEST_FAIL"]

    flags = detect_flags(html)
    return company_id, web_verified, popis, flags

def main(
        target_db: str
):
    print("=== Filter #3: HTML signals (append) ===")
    t0 = time.time()

    conn = sqlite3.connect(target_db)
    conn.execute("PRAGMA journal_mode=WAL;")
    cur = conn.cursor()

    rows = fetch_targets(conn)
    total = len(rows)
    print(f"Targets: {total}")

    updates = []
    processed = 0
    changed = 0

    with cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(analyze_one, row) for row in rows]
        for fut in cf.as_completed(futures):
            processed += 1
            company_id, web_verified, popis, new_flags = fut.result()

            new_popis = append_popis(popis, new_flags)

            # web_verified: nastavíme na nekvalitní grafika jen pokud je prázdné/NULL a máme aspoň 1 nový flag
            new_web_verified = web_verified
            if new_flags:
                if new_web_verified is None or str(new_web_verified).strip() == "":
                    new_web_verified = "nekvalitní grafika"

            # Update jen když se něco změnilo
            if new_popis != popis or new_web_verified != web_verified:
                updates.append((new_web_verified, new_popis, company_id))
                changed += 1

            if len(updates) >= 250:
                cur.executemany(
                    "UPDATE companies SET web_verified=?, popis=? WHERE id=?",
                    updates
                )
                conn.commit()
                updates = []

            if processed % 200 == 0 or processed == total:
                rate = processed / (time.time() - t0)
                print(f"Progress: {processed}/{total} | Updated:{changed} | {rate:.1f}/s")

    if updates:
        cur.executemany(
            "UPDATE companies SET web_verified=?, popis=? WHERE id=?",
            updates
        )
        conn.commit()

    conn.close()
    print(f"Done in {time.time()-t0:.1f}s | Updated:{changed}")

if __name__ == "__main__":
    main()
