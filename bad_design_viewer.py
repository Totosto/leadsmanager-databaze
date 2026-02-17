
import sqlite3
import webbrowser
import threading
import sys
import os
import glob
from flask import Flask, render_template, jsonify, request

# Configuration
PORT = 5000
DEFAULT_DB = 'http_companies.db'

app = Flask(__name__)

def get_db_connection(db_name):
    if not os.path.exists(db_name):
        return None
    conn = sqlite3.connect(db_name)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    return render_template('index.html')


# List of databases to display in the switcher
KNOWN_DATABASES = [
    'bad_design_companies.db',
    'http_companies.db',
    'companies.db'
]

@app.route('/api/databases')
def list_databases():
    # Return existence status of known databases + any other .db files
    found_dbs = []
    
    # First add known ones in order if they exist
    for db in KNOWN_DATABASES:
        if os.path.exists(db):
            found_dbs.append(db)
            
    # Then add any other .db files not already listed
    all_dbs = glob.glob('*.db')
    for db in all_dbs:
        if db not in found_dbs:
            found_dbs.append(db)
            
    return jsonify(found_dbs)

@app.route('/api/companies')
def api_companies():
    db_name = request.args.get('db', DEFAULT_DB)
    
    # Security check: only allow .db files in current dir
    if not db_name.endswith('.db') or '/' in db_name or '\\' in db_name:
         return jsonify({'error': 'Invalid database name'}), 400

    conn = get_db_connection(db_name)
    if conn is None:
        # If default DB doesn't exist yet, try to find another one
        if db_name == DEFAULT_DB:
             dbs = glob.glob('*.db')
             if dbs:
                 conn = get_db_connection(dbs[0])
                 if conn:
                      db_name = dbs[0]
        
        if conn is None:
            return jsonify([])
    
    try:
        # Fetch web and name. Ensure we only get valid webs.
        # We check if table exists first (some DBs might not have companies table)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='companies'")
        if not cursor.fetchone():
             conn.close()
             return jsonify([])

        query = "SELECT id, name, web FROM companies WHERE web IS NOT NULL AND web != '' ORDER BY id ASC"
        cursor = conn.execute(query)
        rows = cursor.fetchall()
        conn.close()
        
        # Convert to list of dicts
        companies = []
        for row in rows:
            web = row['web']
            if not web.startswith('http'):
                web = 'http://' + web
            companies.append({
                'id': row['id'],
                'name': row['name'],
                'web': web
            })
            
        return jsonify({'companies': companies, 'current_db': db_name})
    except Exception as e:
        print(f"Database error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/companies/<int:company_id>', methods=['DELETE'])
def delete_company(company_id):
    db_name = request.args.get('db', DEFAULT_DB)
    
    conn = get_db_connection(db_name)
    if conn is None:
        return jsonify({'error': 'Database not connected'}), 500
    
    try:
        conn.execute('DELETE FROM companies WHERE id = ?', (company_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        print(f"Error deleting company {company_id}: {e}")
        return jsonify({'error': str(e)}), 500

def open_browser():
    webbrowser.open(f'http://127.0.0.1:{PORT}/')

if __name__ == '__main__':
    print(f"Starting viewer...")
    
    # Open browser after a slight delay to allow server to start
    threading.Timer(1.5, open_browser).start()
    
    app.run(port=PORT, debug=False)
