
from pathlib import Path
import duckdb
import pandas as pd
import matplotlib.pyplot as plt


# ============================================================
# SECTION 1: PROJECT PATHS AND DATABASE CONNECTION
# ============================================================

# Find the root directory of the project.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Define the existing DuckDB database location.
DB_PATH = PROJECT_ROOT / "data" / "processed" / "parcel_delivery.duckdb"

# Define where Matplotlib figures will be saved.
FIGURES_DIR = PROJECT_ROOT / "figures"
FIGURES_DIR.mkdir(parents=True, exist_ok=True)

# Connect in read-only mode because this script only analyses data.
con = duckdb.connect(str(DB_PATH), read_only=True)


# ============================================================
# SECTION 2: EXTRACT AND AGGREGATE DATA USING DUCKDB SQL
# ============================================================

# ------------------------------------------------------------
# 2.1 Overall Delivery KPIs
# ------------------------------------------------------------

# Business question:
# How many parcels were delivered, how many arrived late,
# and what percentage were delivered on time?

kpi_query = """
    SELECT
        COUNT(*) AS total_parcels,
        SUM(
            CASE
                WHEN actual_delivery_date > expected_delivery_date
                THEN 1 ELSE 0
            END
        ) AS late_parcels
    FROM parcels
"""

kpi_df = con.execute(kpi_query).fetchdf()


# ------------------------------------------------------------
# 2.2 Monthly On-Time Delivery Performance
# ------------------------------------------------------------

# Group parcels by their lodgement month (YYYY-MM).
# Count total parcels and parcels delivered late.

monthly_query = """
    SELECT
        STRFTIME(lodgement_date, '%Y-%m') AS month,
        COUNT(*) AS total_parcels,
        SUM(
            CASE
                WHEN actual_delivery_date > expected_delivery_date
                THEN 1 ELSE 0
            END
        ) AS late_parcels
    FROM parcels
    GROUP BY month
    ORDER BY month
"""

monthly_df = con.execute(monthly_query).fetchdf()


# ------------------------------------------------------------
# 2.3 Express vs Standard Service Performance
# ------------------------------------------------------------

# Compare both service types within interstate and intrastate
# deliveries, instead of mixing different route types.

service_query = """
    SELECT
        service_type,
        IF(
            origin_state <> destination_state,
            'Interstate',
            'Intrastate'
        ) AS route_type,
        COUNT(*) AS total_parcels,
        SUM(
            CASE
                WHEN actual_delivery_date > expected_delivery_date
                THEN 1 ELSE 0
            END
        ) AS late_parcels
    FROM parcels
    GROUP BY service_type, route_type
    ORDER BY route_type, service_type
"""

service_df = con.execute(service_query).fetchdf()


# ------------------------------------------------------------
# 2.4 Top 5 Interstate Routes by Late Deliveries
# ------------------------------------------------------------

# Include only interstate routes.
# Group by origin/destination pair, calculate late counts,
# and return the five routes with the most late parcels.

route_query = """
    SELECT
        origin_state,
        destination_state,
        COUNT(*) AS total_parcels,
        SUM(
            CASE
                WHEN actual_delivery_date > expected_delivery_date
                THEN 1 ELSE 0
            END
        ) AS late_parcels
    FROM parcels
    WHERE origin_state <> destination_state
    GROUP BY origin_state, destination_state
    ORDER BY late_parcels DESC, total_parcels DESC
    LIMIT 5
"""

route_df = con.execute(route_query).fetchdf()


# ------------------------------------------------------------
# 2.5 Peak vs Non-Peak Delivery Performance
# ------------------------------------------------------------

# Peak season: parcels lodged during November or December.
# Non-Peak: parcels lodged during January through October.

season_query = """
    SELECT
        IF(
            MONTH(lodgement_date) IN (11, 12),
            'Peak',
            'Non-Peak'
        ) AS season,
        COUNT(*) AS total_parcels,
        SUM(
            CASE
                WHEN actual_delivery_date > expected_delivery_date
                THEN 1 ELSE 0
            END
        ) AS late_parcels
    FROM parcels
    GROUP BY season
"""

season_df = con.execute(season_query).fetchdf()


# ------------------------------------------------------------
# 2.6 Close DuckDB connection
# ------------------------------------------------------------

# All SQL data has been retrieved as pandas DataFrames.
# We no longer need to keep the database connection open.
con.close()


# ============================================================
# SECTION 3: PREPARE DATA USING PANDAS
# ============================================================

# ------------------------------------------------------------
# 3.1 Overall KPIs
# ------------------------------------------------------------

# Extract scalar values from the one-row KPI DataFrame.
total_parcels = int(kpi_df.loc[0, "total_parcels"])
late_parcels = int(kpi_df.loc[0, "late_parcels"])

# On-time = not late, including deliveries made early.
on_time_percentage = (
    100 * (total_parcels - late_parcels) / total_parcels
)

print("\nOverall Delivery KPIs:")
print("Total parcels:", total_parcels)
print("Late parcels:", late_parcels)
print("On-time percentage:", round(on_time_percentage, 2))


# ------------------------------------------------------------
# 3.2 Monthly on-time percentage
# ------------------------------------------------------------

monthly_df["on_time_percentage"] = (
    100
    * (monthly_df["total_parcels"] - monthly_df["late_parcels"])
    / monthly_df["total_parcels"]
)

print("\nMonthly delivery performance:")
print(monthly_df)


# ------------------------------------------------------------
# 3.3 Service-level on-time percentage
# ------------------------------------------------------------

service_df["on_time_percentage"] = (
    100
    * (service_df["total_parcels"] - service_df["late_parcels"])
    / service_df["total_parcels"]
)

# Reshape four rows into a table where:
# - Index = Interstate / Intrastate
# - Columns = Express / Standard
# - Values = On-time percentages
service_pivot = service_df.pivot(
    index="route_type",
    columns="service_type",
    values="on_time_percentage"
)

# Explicitly control presentation order.
service_pivot = service_pivot.loc[
    ["Interstate", "Intrastate"],
    ["Express", "Standard"]
]

print("\nService comparison:")
print(service_pivot)


# ------------------------------------------------------------
# 3.4 Create readable route labels
# ------------------------------------------------------------

# Example: NSW and VIC become "NSW -> VIC".
route_df["route"] = (
    route_df["origin_state"]
    + " -> "
    + route_df["destination_state"]
)

print("\nTop five late-delivery routes:")
print(route_df[["route", "late_parcels"]])


# ------------------------------------------------------------
# 3.5 Seasonal late percentage and ordering
# ------------------------------------------------------------

season_df["late_percentage"] = (
    100 * season_df["late_parcels"] / season_df["total_parcels"]
)

# Define a custom categorical order.
season_df["season"] = pd.Categorical(
    season_df["season"],
    categories=["Non-Peak", "Peak"],
    ordered=True
)

season_df = season_df.sort_values("season")

print("\nPeak vs Non-Peak performance:")
print(season_df)


# ============================================================
# SECTION 4: CREATE INDIVIDUAL MATPLOTLIB CHARTS
# ============================================================

# ------------------------------------------------------------
# CHART 1: Monthly On-Time Delivery Performance
# ------------------------------------------------------------

fig1, ax1 = plt.subplots(figsize=(11, 5))

ax1.plot(
    monthly_df["month"],
    monthly_df["on_time_percentage"],
    marker="o",
    linewidth=2
)

ax1.set_title("Monthly On-Time Delivery Performance (2025)")
ax1.set_xlabel("Lodgement Month")
ax1.set_ylabel("On-Time Delivery Rate (%)")

ax1.tick_params(axis="x", rotation=45)

# Horizontal gridlines make changes easier to compare.
ax1.grid(axis="y", alpha=0.3)

fig1.tight_layout()

output_path1 = FIGURES_DIR / "monthly_on_time_performance.png"
fig1.savefig(output_path1, dpi=200, bbox_inches="tight")

print(f"\nMonthly chart saved to: {output_path1}")


# ------------------------------------------------------------
# CHART 2: Express vs Standard On-Time Performance
# ------------------------------------------------------------

fig2, ax2 = plt.subplots(figsize=(9, 5))

# pandas automatically creates grouped bars using Matplotlib.
service_pivot.plot(
    kind="bar",
    ax=ax2,
    width=0.65
)

ax2.set_title("On-Time Rate by Service and Route Type (2025)")
ax2.set_xlabel("Route Type")
ax2.set_ylabel("On-Time Delivery Rate (%)")

ax2.tick_params(axis="x", rotation=0)

# A zero baseline prevents tiny differences being exaggerated.
ax2.set_ylim(0, 100)

ax2.grid(axis="y", alpha=0.3)
ax2.set_axisbelow(True)

ax2.legend(title="Service Type")

fig2.tight_layout()

output_path2 = FIGURES_DIR / "service_on_time_comparison.png"
fig2.savefig(output_path2, dpi=200, bbox_inches="tight")

print(f"Service chart saved to: {output_path2}")


# ------------------------------------------------------------
# CHART 3: Top 5 Interstate Routes by Late Deliveries
# ------------------------------------------------------------

fig3, ax3 = plt.subplots(figsize=(10, 5))

ax3.barh(
    route_df["route"],
    route_df["late_parcels"]
)

# Put the highest late-delivery count at the top.
ax3.invert_yaxis()

ax3.set_title("Top 5 Interstate Routes by Late Deliveries (2025)")
ax3.set_xlabel("Number of Late Parcels")
ax3.set_ylabel("Delivery Route")

ax3.grid(axis="x", alpha=0.3)
ax3.set_axisbelow(True)

# Display exact counts beside each bar.
for i, value in enumerate(route_df["late_parcels"]):
    ax3.text(
        value + 100,
        i,
        f"{value:,}",
        va="center"
    )

# Leave extra space so the numbers are not cut off.
ax3.set_xlim(
    0,
    route_df["late_parcels"].max() * 1.18
)

fig3.tight_layout()

output_path3 = FIGURES_DIR / "top_5_late_delivery_routes.png"
fig3.savefig(output_path3, dpi=200, bbox_inches="tight")

print(f"Route chart saved to: {output_path3}")


# ------------------------------------------------------------
# CHART 4: Peak vs Non-Peak Late Delivery Rate
# ------------------------------------------------------------

fig4, ax4 = plt.subplots(figsize=(8, 5))

bars = ax4.bar(
    season_df["season"].astype(str),
    season_df["late_percentage"],
    width=0.55
)

ax4.set_title("Late Delivery Rate: Peak vs Non-Peak (2025)")
ax4.set_xlabel("Lodgement Season")
ax4.set_ylabel("Late Delivery Rate (%)")

# Start at zero for fair comparisons.
ax4.set_ylim(0, 50)

ax4.grid(axis="y", alpha=0.3)
ax4.set_axisbelow(True)

# Display exact percentages above each bar.
for bar in bars:
    height = bar.get_height()

    ax4.text(
        bar.get_x() + bar.get_width() / 2,
        height + 1,
        f"{height:.2f}%",
        ha="center",
        va="bottom"
    )

fig4.tight_layout()

output_path4 = FIGURES_DIR / "peak_vs_nonpeak_late_rate.png"
fig4.savefig(output_path4, dpi=200, bbox_inches="tight")

print(f"Seasonal chart saved to: {output_path4}")


# ============================================================
# SECTION 5: DISPLAY ALL FIGURES
# ============================================================

# Each figure has already been saved before display.
# plt.show() may open four windows, depending on your backend.
plt.show()
