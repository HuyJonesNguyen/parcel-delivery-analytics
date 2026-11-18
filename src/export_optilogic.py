
from pathlib import Path
import duckdb
import pandas as pd

# ============================================================
# 1. PATHS
# ============================================================

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "processed" / "parcel_delivery.duckdb"
OUT = ROOT / "data" / "optilogic"

OUT.mkdir(parents=True, exist_ok=True)

if not DB_PATH.exists():
    raise FileNotFoundError(f"Database not found: {DB_PATH}")


# ============================================================
# 2. LOAD NETWORK AND AGGREGATED DEMAND
# ============================================================

with duckdb.connect(str(DB_PATH), read_only=True) as con:

    # One facility per state/territory in the synthetic dataset.
    nodes = con.execute("""
        SELECT
            facility_id AS node_id,
            facility_name AS node_name,
            state,
            facility_type
        FROM facilities
        ORDER BY state
    """).fetchdf()

    # Monthly origin-destination demand by service.
    demand = con.execute("""
        SELECT
            origin_state,
            destination_state,
            service_type,
            STRFTIME(lodgement_date, '%Y-%m') AS month,
            COUNT(*) AS parcel_count,
            ROUND(SUM(weight_kg), 2) AS weight_kg
        FROM parcels
        GROUP BY
            origin_state,
            destination_state,
            service_type,
            month
        ORDER BY
            month,
            origin_state,
            destination_state,
            service_type
    """).fetchdf()

    # Route performance with explicitly defined denominators.
    performance = con.execute("""
        SELECT
            origin_state,
            destination_state,
            service_type,
            COUNT(*) AS parcel_count,

            SUM(
                CASE
                    WHEN actual_delivery_date > expected_delivery_date
                    THEN 1 ELSE 0
                END
            ) AS late_parcels,

            ROUND(
                AVG(
                    DATE_DIFF(
                        'day',
                        lodgement_date,
                        actual_delivery_date
                    )
                ),
                3
            ) AS average_delivery_days,

            ROUND(
                QUANTILE_CONT(
                    DATE_DIFF(
                        'day',
                        lodgement_date,
                        actual_delivery_date
                    ),
                    0.95
                ),
                3
            ) AS p95_delivery_days

        FROM parcels
        GROUP BY
            origin_state,
            destination_state,
            service_type
    """).fetchdf()


# ============================================================
# 3. VALIDATE THE NETWORK AND PARCEL COUNTS
# ============================================================

# Verify each state maps to exactly one facility.
if nodes["state"].duplicated().any():
    raise ValueError(
        "Expected one synthetic facility per state."
    )

valid_states = set(nodes["state"])

for frame in [demand, performance]:
    for col in ["origin_state", "destination_state"]:
        unknown = set(frame[col]) - valid_states
        if unknown:
            raise ValueError(
                f"Unknown states in {col}: {unknown}"
            )

# Each parcel belongs to exactly one monthly demand group.
total_demand = int(demand["parcel_count"].sum())

if total_demand != 500_000:
    raise ValueError(
        f"Expected 500,000 parcels, found {total_demand:,}"
    )

# Confirm route-performance totals reconcile with demand.
if int(performance["parcel_count"].sum()) != total_demand:
    raise ValueError("Demand and route totals do not reconcile.")


# ============================================================
# 4. CREATE ROUTE METRICS
# ============================================================

performance["late_rate_pct"] = (
    performance["late_parcels"]
    / performance["parcel_count"]
    * 100
).round(2)

performance["on_time_rate_pct"] = (
    100 - performance["late_rate_pct"]
).round(2)

performance["route_type"] = (
    performance["origin_state"]
    .eq(performance["destination_state"])
    .map({True: "Intrastate", False: "Interstate"})
)


# ============================================================
# 5. ADD FACILITY IDS TO DEMAND AND PERFORMANCE
# ============================================================

state_to_node = nodes.set_index("state")["node_id"]

for frame in [demand, performance]:
    frame["origin_node_id"] = (
        frame["origin_state"].map(state_to_node)
    )
    frame["destination_node_id"] = (
        frame["destination_state"].map(state_to_node)
    )

    if (
        frame["origin_node_id"].isna().any()
        or frame["destination_node_id"].isna().any()
    ):
        raise ValueError("Failed to map state to facility.")


# ============================================================
# 6. CREATE SCENARIO ASSUMPTION TEMPLATE
# ============================================================

# Values are deliberately blank where the parcel dataset
# does not contain observed operating inputs.
#
# Do not treat these as historical facility capacities
# or real transportation costs.

assumptions = nodes[[
    "node_id",
    "node_name",
    "state"
]].copy()

assumptions["daily_capacity_parcels"] = pd.NA
assumptions["processing_time_hours"] = pd.NA
assumptions["fixed_operating_cost_aud_per_day"] = pd.NA

# This template will be populated with explicitly
# documented simulation and optimisation assumptions.


# ============================================================
# 7. EXPORT STAGING FILES
# ============================================================

outputs = {
    "network_nodes.csv": nodes,
    "monthly_demand.csv": demand,
    "route_performance.csv": performance,
    "scenario_assumptions.csv": assumptions,
}

for filename, df in outputs.items():
    path = OUT / filename
    df.to_csv(path, index=False)

    print(f"Exported {filename}: {len(df):,} rows")


# ============================================================
# 8. RECONCILIATION REPORT
# ============================================================

print("\nINTEGRATION VALIDATION")
print("-" * 45)
print(f"Facilities: {len(nodes):,}")
print(f"Monthly demand rows: {len(demand):,}")
print(f"Route performance rows: {len(performance):,}")
print(f"Total parcels: {total_demand:,}")
print(f"Unique services: {sorted(demand['service_type'].unique())}")
print("Validation passed.")
print(f"\nOutput folder: {OUT}")

