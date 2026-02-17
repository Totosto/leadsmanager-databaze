
import sqlite3
import requests
import concurrent.futures
import time
import os

SOURCE_DB = 'companies.db'
TARGET_DB = 'http_companies.db'
MAX_WORKERS = 80
TIMEOUT = 5
CHUNK_SIZE = 50

def get_db_info():
    """Reads schema from source DB."""
    if not os.path.exists(SOURCE_DB):
        raise FileNotFoundError(f"{SOURCE_DB} not found!")
        
    conn = sqlite3.connect(SOURCE_DB)
    cursor = conn.cursor()
    
    cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='companies'")
    res = cursor.fetchone()
    if not res:
        raise ValueError("Table 'companies' not found in source DB.")
    create_sql = res[0]
    
    cursor.execute("SELECT * FROM companies LIMIT 0")
    columns = [desc[0] for desc in cursor.description]
    
    conn.close()
    return create_sql, columns

def init_target_db(create_sql):
    """Recreates target DB with WAL mode."""
    conn = sqlite3.connect(TARGET_DB)
    conn.execute("PRAGMA journal_mode=WAL;")  # Enable Write-Ahead Logging for concurrency
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS companies")
    cursor.execute(create_sql)
    conn.commit()
    conn.close()

def save_chunk(rows, columns):
    """Saves a chunk of rows to the database."""
    if not rows:
        return
    conn = sqlite3.connect(TARGET_DB)
    cursor = conn.cursor()
    placeholders = ",".join(["?" for _ in columns])
    sql = f"INSERT INTO companies VALUES ({placeholders})"
    cursor.executemany(sql, rows)
    conn.commit()
    conn.close()

def check_https(row, web_idx):
    """Checks if website supports HTTPS."""
    raw_web = row[web_idx]
    if not raw_web:
        return None
        
    url = raw_web.strip()
    if url.startswith('http://'):
        url = url.replace('http://', 'https://', 1)
    elif not url.startswith('https://'):
        url = 'https://' + url
        
    try:
        requests.head(url, timeout=TIMEOUT, verify=True, allow_redirects=True)
        return row
    except:
        try:
            requests.get(url, timeout=TIMEOUT, stream=True, verify=True)
            return row
        except:
            return None

def main():
    print(f"--- HTTPS Filter Script ---", flush=True)
    
    try:
        create_sql, columns = get_db_info()
    except Exception as e:
        print(f"Error reading schema: {e}", flush=True)
        return

    print(f"Schema detected. Columns: {len(columns)}", flush=True)
    
    try:
        web_idx = columns.index('web')
    except ValueError:
        print("CRITICAL: Column 'web' not found!", flush=True)
        return
        
    print(f"Reading data from {SOURCE_DB}...", flush=True)
    conn = sqlite3.connect(SOURCE_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM companies")
    all_rows = cursor.fetchall()
    conn.close()
    
    total = len(all_rows)
    print(f"Loaded {total} companies. Starting checks...", flush=True)
    
    init_target_db(create_sql)
    
    valid_rows_buffer = []
    processed = 0
    saved_count = 0
    start_time = time.time()
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(check_https, row, web_idx): row for row in all_rows}
        
        for future in concurrent.futures.as_completed(futures):
            processed += 1
            result = future.result()
            if result:
                valid_rows_buffer.append(result)
            
            # Save chunk
            if len(valid_rows_buffer) >= CHUNK_SIZE:
                save_chunk(valid_rows_buffer, columns)
                saved_count += len(valid_rows_buffer)
                valid_rows_buffer = []

            if processed % 200 == 0 or processed == total:
                elapsed = time.time() - start_time
                percent = (processed / total) * 100
                rate = processed / elapsed if elapsed > 0 else 0
                print(f"Progress: {processed}/{total} ({percent:.1f}%) | Valid: {saved_count + len(valid_rows_buffer)} | Speed: {rate:.1f}/s", flush=True)
                
    # Save remaining
    if valid_rows_buffer:
        save_chunk(valid_rows_buffer, columns)
        saved_count += len(valid_rows_buffer)

    print(f"\nScan finished in {time.time() - start_time:.1f}s.", flush=True)
    print(f"Total valid companies saved: {saved_count}", flush=True)
    print("Done.", flush=True)

if __name__ == "__main__":
    main()
