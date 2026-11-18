"""Synthetic parcel size, volume, weight and handling-cost assumptions for MIP.

Run from the project root with:
    python src/mip_parcel_profiles.py

This script writes only to data/processed/mip_network.duckdb.
It does not modify the historical parcel_delivery.duckdb database.
"""

from pathlib import Path
import duckdb


# ============================================================
# DATABASE CONFIGURATION
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "processed" / "mip_network.duckdb"


# ============================================================
# STEP 28: INITIALISE SYNTHETIC PARCEL SIZE PROFILES
# ============================================================

def initialise_parcel_profiles():
    """Create parcel-profile table and register synthetic assumptions.

    Existing rows are preserved so rerunning the script is safe.
    Volume is in cubic metres; weight is in kilograms; handling
    cost is AUD per parcel and excludes vehicle transportation.
    """

    # --------------------------------------------------------
    # 28.1 DEFINE THE SYNTHETIC PARCEL PROFILES
    # --------------------------------------------------------

    parcel_profiles = [
        ("Small", 0.005, 1.0, 0.10),
        ("Medium", 0.020, 4.0, 0.20),
        ("Large", 0.080, 10.0, 0.45),
        ("Oversized", 0.200, 20.0, 1.00),
    ]

    # --------------------------------------------------------
    # 28.2 VERIFY THE MIP DATABASE AND CREATE THE TABLE
    # --------------------------------------------------------

    if not DB_PATH.is_file():
        raise FileNotFoundError(
            f"MIP database not found: {DB_PATH}\n"
            "Run mip_facility_registry.py to initialise the MIP database first."
        )

    with duckdb.connect(str(DB_PATH)) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS mip_parcel_profiles (
                parcel_size VARCHAR PRIMARY KEY,
                volume_m3 DOUBLE NOT NULL,
                weight_kg DOUBLE NOT NULL,
                handling_cost_aud DOUBLE NOT NULL,
                assumption_source VARCHAR NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CHECK (volume_m3 > 0),
                CHECK (weight_kg > 0),
                CHECK (handling_cost_aud >= 0)
            )
        """)

        # ----------------------------------------------------
        # 28.3 REGISTER EACH PROFILE WITHOUT DUPLICATES
        # ----------------------------------------------------

        inserted_count = 0
        skipped_count = 0

        for size, volume_m3, weight_kg, handling_aud in parcel_profiles:
            exists = con.execute("""
                SELECT 1
                FROM mip_parcel_profiles
                WHERE parcel_size = ?
            """, [size]).fetchone()

            if exists is not None:
                skipped_count += 1
                print(f"Parcel profile {size} already exists.")
                continue

            con.execute("""
                INSERT INTO mip_parcel_profiles (
                    parcel_size,
                    volume_m3,
                    weight_kg,
                    handling_cost_aud,
                    assumption_source
                ) VALUES (?, ?, ?, ?, ?)
            """, [
                size,
                volume_m3,
                weight_kg,
                handling_aud,
                "Synthetic MIP modelling assumption",
            ])

            inserted_count += 1
            print(
                f"Registered {size}: {volume_m3:.3f} m3, "
                f"{weight_kg:.1f} kg, A${handling_aud:.2f} handling"
            )

        # ----------------------------------------------------
        # 28.4 READ BACK THE SAVED RECORDS
        # ----------------------------------------------------

        stored_profiles = con.execute("""
            SELECT parcel_size, volume_m3, weight_kg, handling_cost_aud
            FROM mip_parcel_profiles
            ORDER BY CASE parcel_size
                WHEN 'Small' THEN 1
                WHEN 'Medium' THEN 2
                WHEN 'Large' THEN 3
                WHEN 'Oversized' THEN 4
                ELSE 5
            END
        """).fetchall()

    # --------------------------------------------------------
    # 28.5 DISPLAY SUMMARY
    # --------------------------------------------------------

    print("\nMIP PARCEL SIZE PROFILES")
    print("-" * 70)

    for size, volume, weight, handling in stored_profiles:
        print(
            f"{size:10} | Volume: {volume:.3f} m3 | "
            f"Weight: {weight:.1f} kg | Handling: A${handling:.2f}"
        )

    print("-" * 70)
    print(f"New profiles registered: {inserted_count}")
    print(f"Existing profiles skipped: {skipped_count}")
    print(f"Total stored profiles: {len(stored_profiles)}")
    print("Source: Synthetic MIP modelling assumptions")

    # --------------------------------------------------------
    # 28.6 RETURN STRUCTURED RESULTS
    # --------------------------------------------------------

    return {
        "inserted_count": inserted_count,
        "skipped_count": skipped_count,
        "profile_count": len(stored_profiles),
        "profiles": stored_profiles,
    }


# ============================================================
# MAIN EXECUTION
# ============================================================

if __name__ == "__main__":
    parcel_profile_summary = initialise_parcel_profiles()
