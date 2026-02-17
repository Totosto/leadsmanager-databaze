import sqlite3
import shutil
import os

def main(
        source_db: str,
        target_db: str,
    ):
    print("=== Preparing filtered database with ordered column ===")

    if not os.path.exists(source_db):
        print(f"ERROR: {source_db} not found.")
        return

    if os.path.exists(target_db):
        print(f"Removing existing {target_db}...")
        os.remove(target_db)

    print("Copying source database...")
    shutil.copyfile(source_db, target_db)

    conn = sqlite3.connect(target_db)
    cursor = conn.cursor()

    # 1️⃣ Získat info o původních sloupcích
    cursor.execute("PRAGMA table_info(companies);")
    columns_info = cursor.fetchall()
    original_columns = [col[1] for col in columns_info]

    # 2️⃣ Vytvořit nový seznam sloupců s popis za web_verified
    new_columns = []
    for col in original_columns:
        new_columns.append(col)
        if col == "web_verified":
            new_columns.append("popis")

    # 3️⃣ Vytvořit CREATE TABLE dotaz ručně
    column_defs = []
    for col in columns_info:
        name = col[1]
        col_type = col[2]
        pk = col[5]

        definition = f"{name} {col_type}"
        if pk:
            definition += " PRIMARY KEY"
        column_defs.append(definition)

        if name == "web_verified":
            column_defs.append("popis TEXT")

    create_sql = f"CREATE TABLE companies_new ({', '.join(column_defs)});"

    cursor.execute(create_sql)

    # 4️⃣ Zkopírovat data
    insert_columns = ", ".join(original_columns)
    select_columns = ", ".join(original_columns)

    cursor.execute(f"""
        INSERT INTO companies_new ({insert_columns})
        SELECT {select_columns} FROM companies;
    """)

    # 5️⃣ Nastavit hodnoty NULL
    cursor.execute("""
        UPDATE companies_new
        SET funguje = NULL,
            web_verified = NULL;
    """)

    # 6️⃣ Smazat původní tabulku a přejmenovat novou
    cursor.execute("DROP TABLE companies;")
    cursor.execute("ALTER TABLE companies_new RENAME TO companies;")

    conn.commit()
    conn.close()

    print("Done.")
    print(f"New database created: {target_db}")

if __name__ == "__main__":
    main()
