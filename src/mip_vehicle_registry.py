"""Step 29: Synthetic vehicle registry for the parcel-network MIP model.

Run from the project root:
    python src/mip_vehicle_registry.py

All capacity and cost figures are hypothetical modelling assumptions.
They are NOT Australia Post fleet specifications or operating costs.
This module does not modify the historical parcel_delivery.duckdb database.
"""

from pathlib import Path
import math
import duckdb


# ============================================================
# STEP 29.1: LOCATE THE MIP DATABASE
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "processed" / "mip_network.duckdb"


# ============================================================
# STEP 29.2: DEFINE SYNTHETIC VEHICLE ASSUMPTIONS
# ============================================================

# The volume below is USABLE cargo volume, not nominal body volume.
# Each rate represents one dispatched vehicle on one one-way trip.
# Dispatch + distance costs are charged PER TRIP, not per parcel.
# Payload is an additional capacity constraint alongside volume.
# No actual carrier quotations or vehicle specifications are implied.
VEHICLE_TYPES = [
    # ID, name, usable m3, payload kg, dispatch AUD, running AUD/km
    ("VAN", "Delivery Van", 8.0, 1_000.0, 35.0, 0.65),
    ("SMALL_TRUCK", "Small Truck", 16.0, 3_000.0, 55.0, 0.95),
    ("MEDIUM_TRUCK", "Medium Truck", 24.0, 5_000.0, 80.0, 1.50),
    ("LARGE_TRUCK", "Large Truck", 50.0, 12_000.0, 140.0, 2.10),
]

ASSUMPTION_SOURCE = "Synthetic MIP modelling assumption (not Australia Post data)"


# ============================================================
# STEP 29: INITIALISE AND REGISTER VEHICLE TYPES
# ============================================================

def initialise_vehicle_registry():
    """Create and seed the synthetic vehicle registry, preserving existing rows.

    Returns a summary dictionary. Existing values are NOT silently overwritten;
    changing a cost or capacity later should be a deliberate model-version change.
    """

    # --------------------------------------------------------
    # 29.1 VALIDATE INPUT ASSUMPTIONS BEFORE DATABASE WRITES
    # --------------------------------------------------------
    seen_ids = set()
    for (
        vehicle_type_id,
        vehicle_name,
        usable_volume_m3,
        max_payload_kg,
        fixed_dispatch_cost_aud,
        operating_cost_per_km_aud,
    ) in VEHICLE_TYPES:
        if not vehicle_type_id or vehicle_type_id in seen_ids:
            raise ValueError(f"Missing/duplicate vehicle ID: {vehicle_type_id!r}")
        seen_ids.add(vehicle_type_id)
        if not vehicle_name or not vehicle_name.strip():
            raise ValueError(f"Missing vehicle name: {vehicle_type_id}")
        for label, value in (
            ("usable_volume_m3", usable_volume_m3),
            ("max_payload_kg", max_payload_kg),
            ("fixed_dispatch_cost_aud", fixed_dispatch_cost_aud),
            ("operating_cost_per_km_aud", operating_cost_per_km_aud),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{vehicle_type_id}: {label} must be numeric")
            if not math.isfinite(value):
                raise ValueError(f"{vehicle_type_id}: {label} must be finite")
            must_be_positive = label in ("usable_volume_m3", "max_payload_kg")
            if (must_be_positive and value <= 0) or (not must_be_positive and value < 0):
                raise ValueError(f"{vehicle_type_id}: invalid {label}={value}")

    # --------------------------------------------------------
    # 29.2 CREATE TABLE IN SHARED MIP DATABASE
    # --------------------------------------------------------
    if not DB_PATH.is_file():
        raise FileNotFoundError(
            f"MIP database missing: {DB_PATH}\n"
            "Run src/mip_facility_registry.py first."
        )

    with duckdb.connect(str(DB_PATH)) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS mip_vehicle_types (
                vehicle_type_id VARCHAR PRIMARY KEY,
                vehicle_name VARCHAR NOT NULL,
                usable_volume_m3 DOUBLE NOT NULL,
                max_payload_kg DOUBLE NOT NULL,
                fixed_dispatch_cost_aud DOUBLE NOT NULL,
                operating_cost_per_km_aud DOUBLE NOT NULL,
                assumption_source VARCHAR NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CHECK (usable_volume_m3 > 0),
                CHECK (max_payload_kg > 0),
                CHECK (fixed_dispatch_cost_aud >= 0),
                CHECK (operating_cost_per_km_aud >= 0)
            )
        """)

        # ----------------------------------------------------
        # 29.3 INSERT ONLY MISSING VEHICLE PROFILES
        # ----------------------------------------------------
        inserted_count = 0
        skipped_count = 0
        con.execute("BEGIN TRANSACTION")
        try:
            for row in VEHICLE_TYPES:
                vehicle_type_id = row[0]
                existing = con.execute(
                    "SELECT 1 FROM mip_vehicle_types WHERE vehicle_type_id = ?",
                    [vehicle_type_id],
                ).fetchone()

                if existing is not None:
                    skipped_count += 1
                    print(f"Vehicle type {vehicle_type_id} already exists.")
                    continue

                con.execute("""
                    INSERT INTO mip_vehicle_types (
                        vehicle_type_id,
                        vehicle_name,
                        usable_volume_m3,
                        max_payload_kg,
                        fixed_dispatch_cost_aud,
                        operating_cost_per_km_aud,
                        assumption_source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, [*row, ASSUMPTION_SOURCE])
                inserted_count += 1
                print(f"Registered vehicle type: {vehicle_type_id}")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

        # ----------------------------------------------------
        # 29.4 READ BACK STORED DATA FOR VALIDATION
        # ----------------------------------------------------
        vehicles = con.execute("""
            SELECT
                vehicle_type_id,
                vehicle_name,
                usable_volume_m3,
                max_payload_kg,
                fixed_dispatch_cost_aud,
                operating_cost_per_km_aud
            FROM mip_vehicle_types
            ORDER BY usable_volume_m3, vehicle_type_id
        """).fetchall()

    # --------------------------------------------------------
    # 29.5 DISPLAY RESULTS
    # --------------------------------------------------------
    print("\nMIP VEHICLE TYPES")
    print("-" * 90)
    for (
        vehicle_type_id,
        vehicle_name,
        volume,
        payload,
        dispatch,
        per_km,
    ) in vehicles:
        print(
            f"{vehicle_name:14} | "
            f"Usable volume: {volume:5.1f} m3 | "
            f"Payload: {payload:8,.0f} kg | "
            f"Dispatch: A${dispatch:6.2f} | "
            f"A${per_km:.2f}/km"
        )
    print("-" * 90)
    print(f"New vehicle types registered: {inserted_count}")
    print(f"Existing vehicle types skipped: {skipped_count}")
    print(f"Total stored vehicle types: {len(vehicles)}")
    print("Assumptions: synthetic; not actual Australia Post fleet or prices")
    print("Note: per-trip cost = dispatch cost + modelled km * operating cost/km")
    print("Note: number of trips will be determined in the later MILP model")

    # --------------------------------------------------------
    # 29.6 RETURN STRUCTURED RESULTS
    # --------------------------------------------------------
    return {
        "inserted_count": inserted_count,
        "skipped_count": skipped_count,
        "vehicle_type_count": len(vehicles),
        "vehicles": vehicles,
    }


# ============================================================
# MAIN EXECUTION: RUN STEP 29 INDEPENDENTLY
# ============================================================

if __name__ == "__main__":
    vehicle_registry_summary = initialise_vehicle_registry()
