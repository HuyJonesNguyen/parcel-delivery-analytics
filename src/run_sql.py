from pathlib import Path
import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DB_PATH = PROJECT_ROOT / "data" / "processed" / "parcel_delivery.duckdb"
SQL_PATH = PROJECT_ROOT / "sql" / "01_data_quality.sql"

con = duckdb.connect(str(DB_PATH), read_only=True)

query = SQL_PATH.read_text(encoding="utf-8")

result = con.execute(query).fetchdf()

print(result)
print(f"\nRows returned: {len(result)}")

con.close()