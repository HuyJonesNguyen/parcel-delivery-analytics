"""
MIP transport modelling for a synthetic Australia Post-inspired network.

Step 26: Calculate directed facility-to-facility Haversine distances.
Step 27: Per-parcel linehaul rate, kept as a simple benchmark.

All locations and costs are synthetic or approximate modelling inputs.
They are not actual Australia Post routes, prices or operating records.

Run from the project root:
    python src/mip_transport_costs.py

Prerequisite:
    python src/mip_facility_registry.py

Next step:
    python src/mip_parcel_profiles.py
"""

import math

import duckdb

from mip_common import DISTANCE_MULTIPLIER, MIP_DB_PATH
from mip_facility_registry import validate_facility_coordinates


# ============================================================
# DATABASE CONFIGURATION
# ============================================================

DB_PATH = MIP_DB_PATH


# ============================================================
# STEP 26: CALCULATE FACILITY DISTANCE MATRIX
# ============================================================

def calculate_facility_distance_matrix():
    """Calculate and save directed Haversine distances for all
    Active and Candidate facilities.

    This is the only place the distance matrix is built. Self-pairs
    are excluded. The previous matrix is replaced inside one
    transaction, so repeated runs are safe.

    Distances are straight-line. Steps 31 and 32 multiply them by
    DISTANCE_MULTIPLIER (mip_common.py) to approximate road length.
    """

    # --------------------------------------------------------
    # 26.1 VERIFY DATABASE AND COORDINATE COVERAGE
    # --------------------------------------------------------

    if not DB_PATH.is_file():
        raise FileNotFoundError(
            f"MIP database not found: {DB_PATH}. "
            "Run mip_facility_registry.py first."
        )

    # Reuse the Step 25 check: every facility has valid coordinates
    # and every candidate is labelled as hypothetical.
    validation = validate_facility_coordinates()

    if not validation["ready_for_distance_modelling"]:
        raise ValueError(
            "Facility coordinates are incomplete or misclassified. "
            "Fix the Step 25 warnings before calculating distances."
        )

    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        facilities = con.execute("""
            SELECT
                f.facility_id,
                c.latitude,
                c.longitude
            FROM facility_registry AS f
            INNER JOIN mip_facility_coordinates AS c
                ON f.facility_id = c.facility_id
            WHERE f.status IN ('Active', 'Candidate')
            ORDER BY f.facility_id
        """).fetchall()

    if len(facilities) < 2:
        raise ValueError("At least two Active/Candidate facilities are required.")

    # --------------------------------------------------------
    # 26.2 DEFINE HAVERSINE DISTANCE CALCULATION
    # --------------------------------------------------------

    def haversine(lat1, lon1, lat2, lon2):
        earth_radius_km = 6371.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)

        a = (
            math.sin(delta_phi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2)
            * math.sin(delta_lambda / 2) ** 2
        )

        # Guard against floating-point rounding errors.
        a = min(1.0, max(0.0, a))
        return earth_radius_km * 2 * math.asin(math.sqrt(a))

    # --------------------------------------------------------
    # 26.3 GENERATE DIRECTED FACILITY PAIRS
    # --------------------------------------------------------

    distance_records = []
    for origin_id, lat1, lon1 in facilities:
        for destination_id, lat2, lon2 in facilities:
            if origin_id == destination_id:
                continue

            distance_records.append((
                origin_id,
                destination_id,
                round(haversine(lat1, lon1, lat2, lon2), 3)
            ))

    expected_pairs = len(facilities) * (len(facilities) - 1)
    if len(distance_records) != expected_pairs:
        raise ValueError("Unexpected number of distance pairs.")

    # --------------------------------------------------------
    # 26.4 CREATE AND POPULATE DISTANCE TABLE
    # --------------------------------------------------------

    with duckdb.connect(str(DB_PATH)) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS mip_facility_distances (
                origin_facility_id VARCHAR NOT NULL,
                destination_facility_id VARCHAR NOT NULL,
                distance_km DOUBLE NOT NULL,
                distance_method VARCHAR NOT NULL,
                calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                PRIMARY KEY (origin_facility_id, destination_facility_id),
                FOREIGN KEY (origin_facility_id)
                    REFERENCES facility_registry(facility_id),
                FOREIGN KEY (destination_facility_id)
                    REFERENCES facility_registry(facility_id),
                CHECK (distance_km >= 0),
                CHECK (origin_facility_id <> destination_facility_id)
            )
        """)

        # If the insert fails, the previous matrix is kept.
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute("DELETE FROM mip_facility_distances")
            con.executemany("""
                INSERT INTO mip_facility_distances (
                    origin_facility_id,
                    destination_facility_id,
                    distance_km,
                    distance_method
                )
                VALUES (?, ?, ?, 'Haversine')
            """, distance_records)
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

        saved_count = con.execute("""
            SELECT COUNT(*) FROM mip_facility_distances
        """).fetchone()[0]

    if saved_count != expected_pairs:
        raise ValueError("Saved distance matrix has an unexpected row count.")

    # --------------------------------------------------------
    # 26.5 DISPLAY AND RETURN RESULTS
    # --------------------------------------------------------

    print("\nMIP FACILITY DISTANCE MATRIX")
    print("-" * 70)
    print(f"Total facilities: {len(facilities)}")
    print(f"Expected directed pairs: {expected_pairs}")
    print(f"Calculated directed pairs: {saved_count}")
    print("Distance method: Haversine")
    print("Distance unit: kilometres")

    print("\nSAMPLE DISTANCES FROM MELBOURNE (F001)")
    print("-" * 70)
    samples = sorted(
        (record for record in distance_records if record[0] == "F001"),
        key=lambda record: record[2]
    )
    for origin_id, destination_id, distance_km in samples[:10]:
        print(f"{origin_id} -> {destination_id}: {distance_km:,.3f} km")

    return {
        "facility_count": len(facilities),
        "expected_pairs": expected_pairs,
        "calculated_pairs": saved_count,
        "distance_method": "Haversine",
        "distance_unit": "km",
    }


# ============================================================
# STEP 27: PER-PARCEL LINEHAUL RATE (BENCHMARK ONLY)
# ============================================================

def calculate_linehaul_costs():
    """Estimate a flat per-parcel linehaul rate for every facility pair.

    This was the first, simplest transport cost model: every parcel
    pays a rate per kilometre, as if space were bought from another
    carrier. Steps 31 and 32 replace it with vehicle trips, where
    cost depends on how full each vehicle is.

    The table is kept as a benchmark. No later step reads it.
    """

    # --------------------------------------------------------
    # 27.1 DEFINE SYNTHETIC TRANSPORT COST ASSUMPTIONS
    # --------------------------------------------------------

    cost_per_parcel_km_aud = 0.015
    handling_cost_per_parcel_aud = 0.20

    # --------------------------------------------------------
    # 27.2 RETRIEVE SAVED DISTANCE MATRIX
    # --------------------------------------------------------

    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        distance_records = con.execute("""
            SELECT
                origin_facility_id,
                destination_facility_id,
                distance_km
            FROM mip_facility_distances
            WHERE distance_method = 'Haversine'
            ORDER BY origin_facility_id, destination_facility_id
        """).fetchall()

    if not distance_records:
        raise ValueError("No Haversine distances found. Run Step 26 first.")

    # --------------------------------------------------------
    # 27.3 CALCULATE TRANSPORT COSTS
    # --------------------------------------------------------

    transport_records = []
    for origin_id, destination_id, distance_km in distance_records:
        modelled_distance_km = distance_km * DISTANCE_MULTIPLIER
        cost_per_parcel_aud = (
            modelled_distance_km * cost_per_parcel_km_aud
            + handling_cost_per_parcel_aud
        )
        transport_records.append((
            origin_id,
            destination_id,
            distance_km,
            round(modelled_distance_km, 3),
            round(cost_per_parcel_aud, 4),
            DISTANCE_MULTIPLIER,
            cost_per_parcel_km_aud,
            handling_cost_per_parcel_aud,
        ))

    # --------------------------------------------------------
    # 27.4 CREATE TRANSPORT COST TABLE
    # --------------------------------------------------------

    with duckdb.connect(str(DB_PATH)) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS mip_linehaul_costs (
                origin_facility_id VARCHAR NOT NULL,
                destination_facility_id VARCHAR NOT NULL,
                haversine_distance_km DOUBLE NOT NULL,
                modelled_distance_km DOUBLE NOT NULL,
                cost_per_parcel_aud DOUBLE NOT NULL,
                distance_multiplier DOUBLE NOT NULL,
                cost_per_parcel_km_aud DOUBLE NOT NULL,
                handling_cost_per_parcel_aud DOUBLE NOT NULL,
                calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                PRIMARY KEY (origin_facility_id, destination_facility_id),
                FOREIGN KEY (origin_facility_id)
                    REFERENCES facility_registry(facility_id),
                FOREIGN KEY (destination_facility_id)
                    REFERENCES facility_registry(facility_id),
                CHECK (haversine_distance_km >= 0),
                CHECK (modelled_distance_km >= 0),
                CHECK (cost_per_parcel_aud >= 0),
                CHECK (origin_facility_id <> destination_facility_id)
            )
        """)

        # ----------------------------------------------------
        # 27.5 REPLACE PREVIOUS RESULTS TRANSACTIONALLY
        # ----------------------------------------------------

        con.execute("BEGIN TRANSACTION")
        try:
            con.execute("DELETE FROM mip_linehaul_costs")
            con.executemany("""
                INSERT INTO mip_linehaul_costs (
                    origin_facility_id,
                    destination_facility_id,
                    haversine_distance_km,
                    modelled_distance_km,
                    cost_per_parcel_aud,
                    distance_multiplier,
                    cost_per_parcel_km_aud,
                    handling_cost_per_parcel_aud
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, transport_records)
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

        saved_count = con.execute("""
            SELECT COUNT(*) FROM mip_linehaul_costs
        """).fetchone()[0]

    if saved_count != len(distance_records):
        raise ValueError("Transport cost count does not match distance matrix.")

    # --------------------------------------------------------
    # 27.6 DISPLAY AND RETURN RESULTS
    # --------------------------------------------------------

    print("\nPER-PARCEL LINEHAUL RATE (BENCHMARK ONLY)")
    print("-" * 70)
    print(f"Distance multiplier: {DISTANCE_MULTIPLIER:.2f}")
    print(f"Cost per parcel-km: A${cost_per_parcel_km_aud:.4f}")
    print(f"Handling cost per parcel: A${handling_cost_per_parcel_aud:.2f}")
    print(f"Transport cost records: {saved_count}")
    print("Not used by Steps 30 to 32, which cost vehicle trips instead.")

    return {
        "distance_multiplier": DISTANCE_MULTIPLIER,
        "cost_per_parcel_km_aud": cost_per_parcel_km_aud,
        "handling_cost_per_parcel_aud": handling_cost_per_parcel_aud,
        "transport_cost_records": saved_count,
    }


# ============================================================
# MAIN EXECUTION: RUN STEPS 26 AND 27 IN ORDER
# ============================================================

if __name__ == "__main__":
    distance_summary = calculate_facility_distance_matrix()
    linehaul_summary = calculate_linehaul_costs()