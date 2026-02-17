# Tohle je main skript, vytvoří kopii původní databáze s aplikací filtrů


import the_01_celan_database
import the_02_filter_funguje_nefunguje
import the_03_filter_technical_quality
import the_04_filter_old_web

# SOURCE_DB ... databáze na kterou se aplikuje filtr
SOURCE_DB = "companies.db"
# TARGET_DB ... kopie původní databáze, která se uloží s použitím filtrů, vyhodnocena filtry
TARGET_DB = "companies_filtered.db"

def main():
    # Napřed se v databázi vyčistí sloupce "funguje", "web_verified" a přidí se sloupec "popis"
    # vytvoří se kopie takto upravené databáze, která se potom bude upravovat o filtry
    the_01_celan_database.main(source_db=SOURCE_DB, target_db=TARGET_DB)

    # Pak se aplikuje základní filtr - jestli stránka funguje nebo ne, doplní se sloupec "funguje"
    the_02_filter_funguje_nefunguje.main(target_db=TARGET_DB)

    # Potom se pro řádky, co projdou filtrem funguje/nefunguje, 
    # přidá filtr na technickou kvalitu. Pokud najde aspoň něco, 
    # napíše do sloupce "web_verified" hodnotu "nekvalitní grafika" a do popisu napíše co přesně není v pořádku
    # Legenda co znamenají jednotlivé údaje ve sloupci "popis" je k nalezení v souboru "poznamky.txt"
    the_03_filter_technical_quality.main(target_db=TARGET_DB)

    # Po proběhnutí filtu na technické věci proběhne filtr víc zaměřený na strukturu stránek, 
    # i když jsou technicky OK. Projdou se všechny řádky co prošly filtrem funguje/nefunguje,
    # Kde se najde nějaký nedostatek podle tohoto filtru, dopíše se do sloupce "popis" co se našlo
    # a pokud ve sloupci "web_verified" ještě nic není a něco se našlo, tak se tam dopíše
    # "nekvalitní grafika"
    the_04_filter_old_web.main(target_db=TARGET_DB)

    """
    Témhle způsobem se získá databáze, na kterou byl aplikován filtr
    """
    print(f"Aplikace filtrů proběhla úspěšně, databáze je uložena v souboru: {TARGET_DB}")

    # Dál se to potom může rozšířit, že přidáš třeba další skript, který všude kde je zatím "NULL"
    # ve sloupci web_verified, tak se vepíše hodnota "unverified" atd apod, jako např:
    # the_05_fill_unverified_where_NULL.main(target_db=TARGET_DB)
    pass

if __name__ == "__main__":
    main()