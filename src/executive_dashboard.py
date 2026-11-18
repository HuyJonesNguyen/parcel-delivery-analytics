
from pathlib import Path

import duckdb
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.ticker import StrMethodFormatter
from matplotlib.patches import FancyBboxPatch


# ============================================================
# SECTION 1: PROJECT PATHS
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DB_PATH = PROJECT_ROOT / "data" / "processed" / "parcel_delivery.duckdb"

FIGURES_DIR = PROJECT_ROOT / "figures"
FIGURES_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# SECTION 2: EXTRACT DATA USING DUCKDB SQL
# ============================================================

# Open database in read-only mode.
# Make sure PyCharm's DuckDB data source is deactivated
# if another process is locking the database.

con = duckdb.connect(str(DB_PATH), read_only=True)

try:

    # --------------------------------------------------------
    # 2.1 Overall delivery KPIs
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # 2.2 Monthly delivery performance
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # 2.3 Service performance by route type
    # --------------------------------------------------------

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
    """

    service_df = con.execute(service_query).fetchdf()

    # --------------------------------------------------------
    # 2.4 Top five interstate routes by late-delivery volume
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # 2.5 Peak vs Non-Peak delivery performance
    # --------------------------------------------------------

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

finally:
    # Always release the database connection after extraction.
    con.close()


# ============================================================
# SECTION 3: PREPARE DATA USING PANDAS
# ============================================================

# ------------------------------------------------------------
# 3.1 Overall KPIs
# ------------------------------------------------------------

total_parcels = int(kpi_df.loc[0, "total_parcels"])
late_parcels = int(kpi_df.loc[0, "late_parcels"])

on_time_percentage = (
    100 * (total_parcels - late_parcels) / total_parcels
)


# ------------------------------------------------------------
# 3.2 Monthly on-time rate
# ------------------------------------------------------------

monthly_df["on_time_percentage"] = (
    100
    * (monthly_df["total_parcels"] - monthly_df["late_parcels"])
    / monthly_df["total_parcels"]
)

# Change 2025-01 into Jan, etc., for readable axis labels.
monthly_df["month_label"] = pd.to_datetime(
    monthly_df["month"] + "-01"
).dt.strftime("%b")


# ------------------------------------------------------------
# 3.3 Service performance
# ------------------------------------------------------------

service_df["on_time_percentage"] = (
    100
    * (service_df["total_parcels"] - service_df["late_parcels"])
    / service_df["total_parcels"]
)

service_pivot = service_df.pivot(
    index="route_type",
    columns="service_type",
    values="on_time_percentage"
)

service_pivot = service_pivot.loc[
    ["Interstate", "Intrastate"],
    ["Express", "Standard"]
]


# ------------------------------------------------------------
# 3.4 Route labels
# ------------------------------------------------------------

route_df["route"] = (
    route_df["origin_state"]
    + " -> "
    + route_df["destination_state"]
)


# ------------------------------------------------------------
# 3.5 Seasonal late-delivery rates
# ------------------------------------------------------------

season_df["late_percentage"] = (
    100 * season_df["late_parcels"] / season_df["total_parcels"]
)

season_df["season"] = pd.Categorical(
    season_df["season"],
    categories=["Non-Peak", "Peak"],
    ordered=True
)

season_df = season_df.sort_values("season")

peak_rate = float(
    season_df.loc[
        season_df["season"] == "Peak",
        "late_percentage"
    ].iloc[0]
)

nonpeak_rate = float(
    season_df.loc[
        season_df["season"] == "Non-Peak",
        "late_percentage"
    ].iloc[0]
)

season_difference = peak_rate - nonpeak_rate


# ============================================================
# SECTION 4: DASHBOARD COLOURS AND STYLING
# ============================================================

NAVY = "#172554"
BLUE = "#2563EB"
TEAL = "#0D9488"
AMBER = "#E59A32"

TEXT = "#263247"
MUTED = "#64748B"
GRID = "#E2E8F0"
BACKGROUND = "#F5F7FB"
WHITE = "#FFFFFF"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 10,
    "axes.titlesize": 12,
    "axes.titleweight": "bold",
    "axes.labelcolor": TEXT,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "figure.facecolor": BACKGROUND,
    "savefig.facecolor": BACKGROUND
})


# ============================================================
# SECTION 5: CREATE SINGLE-PAGE DASHBOARD
# ============================================================

# Increase the height to prevent visual elements overlapping.
fig = plt.figure(
    figsize=(16, 14),
    facecolor=BACKGROUND
)

# Divide the figure into:
# Row 1: Dashboard title
# Row 2: Three KPI cards
# Row 3: Monthly trend and service comparison
# Row 4: Route performance and seasonality
#
# The bottom 18% is reserved for executive insights and notes.

gs = fig.add_gridspec(
    nrows=4,
    ncols=6,
    height_ratios=[0.60, 1.25, 3.0, 3.0],
    hspace=0.78,
    wspace=0.45,
    left=0.09,
    right=0.96,
    top=0.96,
    bottom=0.19
)


# ------------------------------------------------------------
# 5.1 Header
# ------------------------------------------------------------

header_ax = fig.add_subplot(gs[0, :])
header_ax.axis("off")

header_ax.text(
    0, 0.72,
    "PARCEL DELIVERY PERFORMANCE",
    fontsize=23,
    fontweight="bold",
    color=NAVY,
    va="center",
    transform=header_ax.transAxes
)

header_ax.text(
    0, 0.12,
    "2025 Operational Overview | Synthetic Australian Logistics Dataset",
    fontsize=11,
    color=MUTED,
    va="center",
    transform=header_ax.transAxes
)


# ------------------------------------------------------------
# 5.2 KPI cards
# ------------------------------------------------------------

def draw_kpi_card(ax, label, value, description, value_color=NAVY):
    """
    Display a KPI inside a rounded white card.

    ax: Matplotlib axis used as the card container.
    label: KPI heading.
    value: Main number.
    description: Short explanation below the value.
    """

    ax.set_axis_off()

    # Draw card background with rounded corners.
    card = FancyBboxPatch(
        (0.01, 0.02),
        0.98,
        0.96,
        boxstyle="round,pad=0.01,rounding_size=0.04",
        transform=ax.transAxes,
        facecolor=WHITE,
        edgecolor=GRID,
        linewidth=0.8
    )

    ax.add_patch(card)

    # Place label near the top of the card.
    ax.text(
        0.07, 0.82,
        label.upper(),
        fontsize=10,
        fontweight="bold",
        color=MUTED,
        ha="left",
        va="center",
        transform=ax.transAxes
    )

    # Main KPI value, vertically separated from the label.
    ax.text(
        0.07, 0.48,
        value,
        fontsize=24,
        fontweight="bold",
        color=value_color,
        ha="left",
        va="center",
        transform=ax.transAxes
    )

    # Short description at the bottom.
    ax.text(
        0.07, 0.17,
        description,
        fontsize=8.5,
        color=MUTED,
        ha="left",
        va="center",
        transform=ax.transAxes
    )


kpi_ax1 = fig.add_subplot(gs[1, 0:2])
kpi_ax2 = fig.add_subplot(gs[1, 2:4])
kpi_ax3 = fig.add_subplot(gs[1, 4:6])

draw_kpi_card(
    kpi_ax1,
    "Total Parcels",
    f"{total_parcels:,}",
    "Delivered parcels in the dataset"
)

draw_kpi_card(
    kpi_ax2,
    "On-Time Delivery Rate",
    f"{on_time_percentage:.2f}%",
    "Delivered on or before the deadline",
    BLUE
)

draw_kpi_card(
    kpi_ax3,
    "Late Deliveries",
    f"{late_parcels:,}",
    "Delivered after the deadline",
    AMBER
)


# ------------------------------------------------------------
# 5.3 Set up four chart panels
# ------------------------------------------------------------

ax1 = fig.add_subplot(gs[2, 0:3])
ax2 = fig.add_subplot(gs[2, 3:6])
ax3 = fig.add_subplot(gs[3, 0:3])
ax4 = fig.add_subplot(gs[3, 3:6])

for ax in [ax1, ax2, ax3, ax4]:
    ax.set_facecolor(WHITE)

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    ax.spines["left"].set_color(GRID)
    ax.spines["bottom"].set_color(GRID)

    ax.set_axisbelow(True)


# ============================================================
# CHART 1: MONTHLY ON-TIME DELIVERY TREND
# ============================================================

ax1.plot(
    monthly_df["month_label"],
    monthly_df["on_time_percentage"],
    color=BLUE,
    marker="o",
    linewidth=2.5,
    markersize=5
)

ax1.set_title(
    "Monthly On-Time Delivery Trend",
    loc="left",
    pad=16,
    color=TEXT
)

ax1.set_ylabel("On-Time Rate (%)")

# Focus on the observed range while clearly displaying
# the y-axis values. This is suitable for a line chart.
ax1.set_ylim(55, 85)

ax1.grid(
    axis="y",
    color=GRID,
    alpha=0.8
)

# Label the December rate.
december_rate = float(
    monthly_df["on_time_percentage"].iloc[-1]
)

ax1.annotate(
    f"{december_rate:.1f}%",
    xy=(11, december_rate),
    xytext=(-6, 14),
    textcoords="offset points",
    ha="center",
    color=BLUE,
    fontweight="bold"
)


# ============================================================
# CHART 2: SERVICE PERFORMANCE COMPARISON
# ============================================================

service_pivot.plot(
    kind="bar",
    ax=ax2,
    color=[BLUE, TEAL],
    width=0.65,
    legend=True
)

ax2.set_title(
    "On-Time Rate by Service and Route",
    loc="left",
    pad=32,
    color=TEXT
)

ax2.set_xlabel("")
ax2.set_ylabel("On-Time Rate (%)")

# Full percentage range for an honest bar comparison.
ax2.set_ylim(0, 100)

ax2.tick_params(axis="x", rotation=0)

ax2.grid(
    axis="y",
    color=GRID,
    alpha=0.8
)

# Move legend above the plotting area.
ax2.legend(
    title=None,
    frameon=False,
    fontsize=9,
    loc="lower right",
    bbox_to_anchor=(1.0, 1.03),
    ncol=2
)

for container in ax2.containers:
    ax2.bar_label(
        container,
        fmt="%.1f%%",
        padding=3,
        fontsize=8
    )


# ============================================================
# CHART 3: TOP FIVE INTERSTATE ROUTES
# ============================================================

bars3 = ax3.barh(
    route_df["route"],
    route_df["late_parcels"],
    color=AMBER,
    height=0.62
)

ax3.invert_yaxis()

ax3.set_title(
    "Top 5 Routes by Late-Delivery Volume",
    loc="left",
    pad=16,
    color=TEXT
)

ax3.set_xlabel("Late Parcels")

ax3.grid(
    axis="x",
    color=GRID,
    alpha=0.8
)

ax3.xaxis.set_major_formatter(
    StrMethodFormatter("{x:,.0f}")
)

# Make room for labels beside the bars.
ax3.set_xlim(
    0,
    route_df["late_parcels"].max() * 1.23
)

for bar in bars3:
    width = bar.get_width()

    ax3.text(
        width + 90,
        bar.get_y() + bar.get_height() / 2,
        f"{int(width):,}",
        va="center",
        fontsize=9,
        color=TEXT
    )


# ============================================================
# CHART 4: PEAK VS NON-PEAK LATE-DELIVERY RATE
# ============================================================

bars4 = ax4.bar(
    season_df["season"].astype(str),
    season_df["late_percentage"],
    color=[TEAL, AMBER],
    width=0.55
)

ax4.set_title(
    "Late-Delivery Rate by Season",
    loc="left",
    pad=16,
    color=TEXT
)

ax4.set_ylabel("Late-Delivery Rate (%)")

# Bar charts should start from zero.
ax4.set_ylim(0, 55)

ax4.grid(
    axis="y",
    color=GRID,
    alpha=0.8
)

for bar in bars4:
    height = bar.get_height()

    ax4.text(
        bar.get_x() + bar.get_width() / 2,
        height + 1.4,
        f"{height:.2f}%",
        ha="center",
        va="bottom",
        fontsize=10,
        fontweight="bold",
        color=TEXT
    )


# ============================================================
# SECTION 6: EXECUTIVE INSIGHTS
# ============================================================

# The lower part of the figure is deliberately reserved
# for written insights and the data-source disclaimer.

largest_late_route = route_df.iloc[0]["route"]

# Background for the insights section.
insights_ax = fig.add_axes([0.09, 0.075, 0.87, 0.075])

insights_ax.set_axis_off()

insights_card = FancyBboxPatch(
    (0.0, 0.0),
    1.0,
    1.0,
    boxstyle="round,pad=0.01,rounding_size=0.04",
    transform=insights_ax.transAxes,
    facecolor=WHITE,
    edgecolor=GRID,
    linewidth=0.8
)

insights_ax.add_patch(insights_card)

insights_ax.text(
    0.02, 0.76,
    "KEY BUSINESS INSIGHTS",
    fontsize=11,
    fontweight="bold",
    color=NAVY,
    transform=insights_ax.transAxes
)

insights_ax.text(
    0.02, 0.47,
    f"Peak-season late deliveries are "
    f"{season_difference:.2f} percentage points higher than non-peak.",
    fontsize=10,
    color=TEXT,
    transform=insights_ax.transAxes
)

insights_ax.text(
    0.02, 0.19,
    f"{largest_late_route} has the largest number of late interstate deliveries.",
    fontsize=10,
    color=TEXT,
    transform=insights_ax.transAxes
)


# ------------------------------------------------------------
# 6.1 Source and limitation notes
# ------------------------------------------------------------

source_text = (
    "Source: Synthetic parcel_delivery.duckdb  |  "
    "On time = actual delivery date <= expected delivery date  |  "
    "Peak = Nov-Dec lodgement"
)

disclaimer_text = (
    "Simulation data for portfolio demonstration. "
    "Not actual Australia Post operational performance."
)

fig.text(
    0.09, 0.050,
    source_text,
    fontsize=8.5,
    color=MUTED,
    ha="left"
)

fig.text(
    0.09, 0.031,
    disclaimer_text,
    fontsize=8.5,
    color=MUTED,
    ha="left"
)


# ============================================================
# SECTION 7: EXPORT DASHBOARD
# ============================================================

dashboard_png = FIGURES_DIR / "executive_dashboard.png"
dashboard_pdf = FIGURES_DIR / "executive_dashboard.pdf"

# Save high-resolution raster version for GitHub.
fig.savefig(
    dashboard_png,
    dpi=200,
    facecolor=fig.get_facecolor()
)

# Save vector PDF for presentation and printing.
fig.savefig(
    dashboard_pdf,
    facecolor=fig.get_facecolor()
)

print("\nDashboard generated successfully!")
print(f"PNG: {dashboard_png}")
print(f"PDF: {dashboard_pdf}")

# Display the finished dashboard.
plt.close(fig)
