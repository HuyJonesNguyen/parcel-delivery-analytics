"""Step 30: Build parcel demand by departure period for the MIP model.

Run from the project root:
    python src/mip_demand.py

Prerequisites:
    python src/mip_facility_registry.py   (scenarios)
    python src/mip_parcel_profiles.py     (volume and handling per size)

What this step does:
    1. Reads the historical parcels in READ-ONLY mode.
    2. Groups them into departure periods using lodgement_date
       (PERIOD_DAYS in mip_common.py, 7 = weekly).
    3. Gives each parcel a size from its recorded weight_kg.
    4. Scales the result by each scenario's demand multiplier.

Why periods matter: a truck cannot wait a year to fill up. Each
period's parcels must leave in that period, so quiet weeks send
part-empty vehicles and the November-December peak needs more.

Weights are the recorded synthetic weights. Volumes come from the
size profile, because the parcels table has no dimensions.
Next step:
    python src/mip_truck_allocation.py
"""

import duckdb

from mip_common import (
    MIP_DB_PATH,
    PERIOD_DAYS,
    SIZE_WEIGHT_BANDS_KG,
    SOURCE_DB_PATH,
)


# ============================================================
# STEP 30.1: ASSUMPTIONS
# ============================================================

VALID_STATES = {"NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"}

REQUIRED_PARCEL_COLUMNS = {
    "parcel_id",
    "origin_state",
    "destination_state",
    "lodgement_date",
    "weight_kg",
}

ASSUMPTION_SOURCE = (
    "Historical synthetic parcels grouped by lodgement period; "
    "size from recorded weight; volume from synthetic size profile"
)


def _size_case_sql():
    """Turn SIZE_WEIGHT_BANDS_KG into a SQL CASE expression.

    Example result:
        CASE WHEN weight_kg < 1.0 THEN 'Small'
             WHEN weight_kg < 3.0 THEN 'Medium'
             WHEN weight_kg < 10.0 THEN 'Large'
             ELSE 'Oversized' END
    """
    *bounded, (last_size, last_limit) = SIZE_WEIGHT_BANDS_KG

    if last_limit is not None:
        raise ValueError("The last weight band must have no upper limit.")

    parts = ["CASE"]
    previous_limit = 0.0

    for size, limit in bounded:
        if limit is None or limit <= previous_limit:
            raise ValueError("Weight bands must have increasing limits.")
        parts.append(f"WHEN weight_kg < {float(limit)} THEN '{size}'")
        previous_limit = limit

    parts.append(f"ELSE '{last_size}' END")
    return " ".join(parts)


# ============================================================
# STEP 30: GENERATE AND SAVE DEMAND BY PERIOD, LANE AND SIZE
# ============================================================

def initialise_mip_demand():
    """Aggregate historical parcels into period demand for every scenario.

    One row per scenario, period, origin state, destination state
    and parcel size. Intrastate parcels are included so the totals
    reconcile with the source. Steps 31 and 32 skip them.

    The demand pattern of the historical year is reused for the
    scenario's planning year, multiplied by demand_multiplier.
    A repeat run replaces each scenario's rows.
    """

    # --------------------------------------------------------
    # 30.1 VERIFY DATABASES AND SETTINGS
    # --------------------------------------------------------

    if not isinstance(PERIOD_DAYS, int) or PERIOD_DAYS < 1:
        raise ValueError("PERIOD_DAYS must be a positive whole number.")

    if not SOURCE_DB_PATH.is_file():
        raise FileNotFoundError(f"Historical source database missing: {SOURCE_DB_PATH}")

    if not MIP_DB_PATH.is_file():
        raise FileNotFoundError(
            f"MIP database missing: {MIP_DB_PATH}. "
            "Run src/mip_facility_registry.py first."
        )

    size_names = [size for size, _ in SIZE_WEIGHT_BANDS_KG]

    with duckdb.connect(str(MIP_DB_PATH)) as con:

        # ----------------------------------------------------
        # 30.2 CHECK PROFILES AND SCENARIOS
        # ----------------------------------------------------

        stored_sizes = {
            row[0] for row in con.execute(
                "SELECT parcel_size FROM mip_parcel_profiles"
            ).fetchall()
        }

        missing_sizes = set(size_names) - stored_sizes
        if missing_sizes:
            raise ValueError(
                f"Missing parcel profiles: {sorted(missing_sizes)}. "
                "Run python src/mip_parcel_profiles.py first."
            )

        scenarios = con.execute("""
            SELECT scenario_id, planning_year, demand_multiplier
            FROM mip_scenarios
            ORDER BY scenario_id
        """).fetchall()

        if not scenarios:
            raise ValueError(
                "No scenarios registered. "
                "Run src/mip_facility_registry.py first."
            )

        # ----------------------------------------------------
        # 30.3 ATTACH THE HISTORICAL DATABASE (READ-ONLY)
        # ----------------------------------------------------

        # Attaching lets one SQL statement read the parcels and
        # write the aggregate. No parcel rows travel through Python.
        con.execute(
            f"ATTACH '{SOURCE_DB_PATH.as_posix()}' AS src (READ_ONLY)"
        )

        parcel_columns = {
            row[0].lower() for row in con.execute(
                "DESCRIBE src.parcels"
            ).fetchall()
        }

        missing_columns = REQUIRED_PARCEL_COLUMNS - parcel_columns
        if missing_columns:
            raise ValueError(
                f"src.parcels is missing columns: {sorted(missing_columns)}"
            )

        # ----------------------------------------------------
        # 30.4 VALIDATE THE SOURCE PARCELS
        # ----------------------------------------------------

        (
            source_count,
            distinct_ids,
            missing_values,
            bad_weights,
            first_year,
            last_year,
        ) = con.execute("""
            SELECT
                COUNT(*),
                COUNT(DISTINCT parcel_id),
                COUNT(*) FILTER (
                    WHERE parcel_id IS NULL
                       OR origin_state IS NULL
                       OR destination_state IS NULL
                       OR lodgement_date IS NULL
                       OR weight_kg IS NULL
                ),
                COUNT(*) FILTER (WHERE weight_kg <= 0),
                MIN(YEAR(lodgement_date)),
                MAX(YEAR(lodgement_date))
            FROM src.parcels
        """).fetchone()

        if source_count == 0 or distinct_ids != source_count:
            raise ValueError(
                "Source parcels must have a unique parcel_id "
                f"(rows={source_count}, unique={distinct_ids})."
            )

        if missing_values or bad_weights:
            raise ValueError(
                f"Source parcels have {missing_values} rows with missing "
                f"values and {bad_weights} with non-positive weight."
            )

        # One calendar year of history = one year of demand.
        # More than one year would double-count annual demand.
        if first_year != last_year:
            raise ValueError(
                f"Source parcels cover {first_year} to {last_year}. "
                "This step expects exactly one calendar year."
            )

        source_year = int(first_year)

        days_in_year = con.execute(
            "SELECT DAYOFYEAR(MAKE_DATE(?, 12, 31))", [source_year]
        ).fetchone()[0]

        # The last period absorbs leftover days (365 = 52 x 7 + 1).
        period_count = days_in_year // PERIOD_DAYS

        if period_count < 1:
            raise ValueError("PERIOD_DAYS is longer than the year.")

        # ----------------------------------------------------
        # 30.5 AGGREGATE PARCELS BY PERIOD, LANE AND SIZE
        # ----------------------------------------------------

        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE base_demand AS
            SELECT
                LEAST(
                    {period_count},
                    (DAYOFYEAR(lodgement_date) - 1) // {PERIOD_DAYS} + 1
                ) AS period,
                UPPER(TRIM(origin_state)) AS origin_state,
                UPPER(TRIM(destination_state)) AS destination_state,
                {_size_case_sql()} AS parcel_size,
                COUNT(*) AS parcel_count,
                SUM(weight_kg) AS total_weight_kg
            FROM src.parcels
            GROUP BY ALL
        """)

        found_states = {
            state
            for row in con.execute("""
                SELECT DISTINCT origin_state, destination_state
                FROM base_demand
            """).fetchall()
            for state in row
        }

        invalid_states = sorted(found_states - VALID_STATES)
        if invalid_states:
            raise ValueError(
                f"Invalid source state values: {invalid_states}. "
                "No MIP demand data has been written."
            )

        aggregated_count = con.execute(
            "SELECT SUM(parcel_count) FROM base_demand"
        ).fetchone()[0]

        if aggregated_count != source_count:
            raise ValueError(
                f"Demand reconciliation failed: source={source_count}, "
                f"aggregate={aggregated_count}. No data written."
            )

        # ----------------------------------------------------
        # 30.6 CREATE THE DEMAND TABLE
        # ----------------------------------------------------

        con.execute("""
            CREATE TABLE IF NOT EXISTS mip_demand_by_period (
                scenario_id VARCHAR NOT NULL,
                period INTEGER NOT NULL,
                source_period_start DATE NOT NULL,
                origin_state VARCHAR NOT NULL,
                destination_state VARCHAR NOT NULL,
                parcel_size VARCHAR NOT NULL,
                parcel_count BIGINT NOT NULL,
                total_volume_m3 DOUBLE NOT NULL,
                total_weight_kg DOUBLE NOT NULL,
                total_handling_cost_aud DOUBLE NOT NULL,
                period_days INTEGER NOT NULL,
                assumption_source VARCHAR NOT NULL,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (
                    scenario_id, period, origin_state,
                    destination_state, parcel_size
                ),
                FOREIGN KEY (parcel_size)
                    REFERENCES mip_parcel_profiles(parcel_size),
                CHECK (period >= 1),
                CHECK (parcel_count >= 0),
                CHECK (total_volume_m3 >= 0),
                CHECK (total_weight_kg >= 0),
                CHECK (total_handling_cost_aud >= 0)
            )
        """)

        # ----------------------------------------------------
        # 30.7 SAVE ONE COPY PER SCENARIO, SCALED BY ITS MULTIPLIER
        # ----------------------------------------------------

        # Remove old rows first, in their own step. This also clears
        # rows left by a different PERIOD_DAYS or a deleted scenario.
        con.execute("DELETE FROM mip_demand_by_period")

        scenario_totals = []

        con.execute("BEGIN TRANSACTION")
        try:
            for scenario_id, planning_year, multiplier in scenarios:

                con.execute("""
                    INSERT INTO mip_demand_by_period (
                        scenario_id, period, source_period_start,
                        origin_state, destination_state, parcel_size,
                        parcel_count, total_volume_m3, total_weight_kg,
                        total_handling_cost_aud, period_days,
                        assumption_source
                    )
                    SELECT
                        ?,
                        d.period,
                        MAKE_DATE(?, 1, 1)
                            + CAST((d.period - 1) * ? AS INTEGER),
                        d.origin_state,
                        d.destination_state,
                        d.parcel_size,
                        CAST(ROUND_EVEN(d.parcel_count * ?, 0) AS BIGINT),
                        d.parcel_count * p.volume_m3 * ?,
                        d.total_weight_kg * ?,
                        d.parcel_count * p.handling_cost_aud * ?,
                        ?,
                        ?
                    FROM base_demand AS d
                    INNER JOIN mip_parcel_profiles AS p
                        ON d.parcel_size = p.parcel_size
                """, [
                    scenario_id, source_year, PERIOD_DAYS,
                    multiplier, multiplier, multiplier, multiplier,
                    PERIOD_DAYS, ASSUMPTION_SOURCE,
                ])

                scenario_totals.append((
                    scenario_id, planning_year, multiplier,
                    *con.execute("""
                        SELECT
                            COUNT(*),
                            SUM(parcel_count),
                            SUM(total_volume_m3),
                            SUM(total_weight_kg),
                            SUM(parcel_count) FILTER (
                                WHERE origin_state <> destination_state
                            )
                        FROM mip_demand_by_period
                        WHERE scenario_id = ?
                    """, [scenario_id]).fetchone()
                ))

            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

        # ----------------------------------------------------
        # 30.8 READ BACK FIGURES FOR THE REPORT
        # ----------------------------------------------------

        size_mix = con.execute("""
            SELECT parcel_size, SUM(parcel_count)
            FROM base_demand
            GROUP BY parcel_size
        """).fetchall()

        # Interstate parcels per period: shows the seasonal peak.
        period_volumes = con.execute("""
            SELECT period, SUM(parcel_count)
            FROM base_demand
            WHERE origin_state <> destination_state
            GROUP BY period
            ORDER BY period
        """).fetchall()

        con.execute("DETACH src")

    # --------------------------------------------------------
    # 30.9 REPORT
    # --------------------------------------------------------

    size_counts = dict(size_mix)
    busiest = max(period_volumes, key=lambda row: row[1])
    quietest = min(period_volumes, key=lambda row: row[1])
    average = sum(row[1] for row in period_volumes) / len(period_volumes)

    print("\nMIP PARCEL DEMAND BY PERIOD, LANE AND SIZE")
    print("-" * 72)
    print(f"Historical year used as the demand pattern: {source_year}")
    print(f"Historical synthetic parcel rows: {source_count:,}")
    print(f"Days per departure period: {PERIOD_DAYS}")
    print(f"Periods in the year: {period_count}")

    print("\nPARCEL SIZE MIX (FROM RECORDED WEIGHT)")
    for size in size_names:
        count = size_counts.get(size, 0)
        print(
            f"{size:10} | {count:>8,} parcels "
            f"| {100 * count / source_count:5.2f}%"
        )

    print("\nINTERSTATE PARCELS PER PERIOD (HISTORICAL)")
    print(f"Average period:  {average:>8,.0f}")
    print(f"Quietest period: {quietest[1]:>8,} (period {quietest[0]})")
    print(f"Busiest period:  {busiest[1]:>8,} (period {busiest[0]})")
    print(f"Busiest / average: {busiest[1] / average:.2f}x")

    print("\nDEMAND SAVED BY SCENARIO")
    for (
        scenario_id, planning_year, multiplier,
        rows, parcels, volume, weight, interstate
    ) in scenario_totals:
        print(
            f"{scenario_id:15} | year {planning_year} "
            f"| x{multiplier:.2f} "
            f"| {parcels:>8,} parcels "
            f"({interstate:,} interstate) "
            f"| {volume:>9,.1f} m3 | {weight:>11,.1f} kg "
            f"| {rows:,} rows"
        )

    print("-" * 72)
    print("Original parcel database: unchanged (read-only)")
    print("Volumes and handling costs: synthetic size profiles")

    return {
        "source_year": source_year,
        "source_parcel_count": source_count,
        "period_days": PERIOD_DAYS,
        "period_count": period_count,
        "size_counts": size_counts,
        "busiest_period": busiest[0],
        "scenarios": [row[0] for row in scenario_totals],
    }


# ============================================================
# MAIN EXECUTION
# ============================================================

if __name__ == "__main__":
    demand_summary = initialise_mip_demand()