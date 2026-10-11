from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parent.parent

RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DATA_DIR = PROJECT_ROOT / "data" / "processed"

PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = PROCESSED_DATA_DIR / "parcel_delivery.duckdb"


con = duckdb.connect(str(DB_PATH))

print("Connected to DuckDB:")
print(DB_PATH)

facilities_path = RAW_DATA_DIR / "facilities.csv"

con.execute(f"""
    CREATE OR REPLACE TABLE facilities AS
    SELECT *
    FROM read_csv_auto('{facilities_path.as_posix()}')
""")

print("\nFacilities table:")
print(
    con.execute("""
        SELECT *
        FROM facilities
    """).fetchdf()
)

print("\nFacility count:")
print(
    con.execute("""
        SELECT COUNT(*) AS facility_count
        FROM facilities
    """).fetchdf()
)

print("\nFacilities schema:")
print(
    con.execute("""
        DESCRIBE facilities
    """).fetchdf()
)

parcels_path = RAW_DATA_DIR / "parcels.csv"

con.execute(f"""
    CREATE OR REPLACE TABLE parcels AS
    SELECT *
    FROM read_csv_auto('{parcels_path.as_posix()}')
""")

print("\nParcels validation:")

print(con.execute("""
    SELECT
        COUNT(*) AS total_rows,
        COUNT(DISTINCT parcel_id) AS unique_parcels,
        COUNT(*) - COUNT(DISTINCT parcel_id) AS duplicate_ids
    FROM parcels
""").fetchdf())

print("\nParcels schema:")
print(con.execute("DESCRIBE parcels").fetchdf())

tracking_path = RAW_DATA_DIR / "tracking_events.csv"

con.execute(f"""
    CREATE OR REPLACE TABLE tracking_events AS
    SELECT *
    FROM read_csv_auto('{tracking_path.as_posix()}')
""")

print("\nTracking events validation:")

print(con.execute("""
    SELECT
        COUNT(*) AS total_events,
        COUNT(DISTINCT event_id) AS unique_events,
        COUNT(DISTINCT parcel_id) AS unique_parcels
    FROM tracking_events
""").fetchdf())

print("\nTracking events schema:")
print(con.execute("DESCRIBE tracking_events").fetchdf())

print("\nTracking event distribution:")

print(con.execute("""
    SELECT
        event_type,
        COUNT(*) AS event_count
    FROM tracking_events
    GROUP BY event_type
    ORDER BY event_type
""").fetchdf())
con.close()
