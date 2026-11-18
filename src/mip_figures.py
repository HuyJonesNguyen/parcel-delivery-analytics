"""Figures for the MIP results, saved as PNG files for the README.

This script lives in src/ with the other scripts. The charts are
always saved in the figures/ folder at the project root, wherever
the script is run from.

Run after Steps 31, 32 and 33:
    python src/mip_figures.py

A chart whose step has not been run yet is skipped with a message.

Reads the result tables in data/processed/mip_network.duckdb and
writes three charts to the figures/ folder:

    figures/mip_linehaul_cost.png       Step 31 against Step 32
    figures/mip_network_map.png         legs the optimised plan drives
    figures/mip_candidate_costs.png     Step 33 candidate comparison

Nothing is typed in by hand: every number comes from the database,
so the charts change when the model is rerun.
"""

import duckdb
import matplotlib.pyplot as plt

from mip_common import MIP_DB_PATH, PROJECT_ROOT


# ============================================================
# SETTINGS
# ============================================================

# Charts go to <project root>/figures, not next to this script.
FIGURES_DIR = PROJECT_ROOT / "figures"
FIGURES_DIR.mkdir(parents=True, exist_ok=True)

# The scenario shown in the linehaul charts.
SCENARIO_ID = "BASE_2027"

# A leg is drawn on the map if it is driven in at least this
# share of the periods.
MAP_MIN_SHARE_OF_PERIODS = 0.75

# Colours shared by all three charts.
BLUE = "#1F5F8B"     # the optimised plan, truck cost
AMBER = "#B4690E"    # candidates, fixed cost, transfer hubs
GREY = "#566573"     # the baseline
TEXT = "#17212B"
MUTED = "#566573"
GRID = "#D5DCE3"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 10,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.titlecolor": TEXT,
    "axes.labelcolor": MUTED,
    "xtick.color": MUTED,
    "ytick.color": TEXT,
    "figure.facecolor": "white",
    "savefig.facecolor": "white",
})


def _clean_axes(ax):
    """Remove the box around a chart and keep a light grid."""
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.set_axisbelow(True)


def _save(fig, name):
    path = FIGURES_DIR / name
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {path}")
    return path


# ============================================================
# CHART 1: DIRECT SHIPPING AGAINST THE OPTIMISED NETWORK
# ============================================================

def chart_linehaul_cost(con):

    baseline_cost, baseline_trips = con.execute("""
        SELECT SUM(truck_cost_aud), SUM(trips)
        FROM mip_direct_baseline
        WHERE scenario_id = ? AND is_cheapest
    """, [SCENARIO_ID]).fetchone()

    truck_cost, plan_trips = con.execute("""
        SELECT SUM(total_truck_cost_aud), SUM(trips)
        FROM mip_network_trips
        WHERE scenario_id = ?
    """, [SCENARIO_ID]).fetchone()

    transfer_cost = con.execute("""
        SELECT SUM(transfer_handling_cost_aud)
        FROM mip_network_routes
        WHERE scenario_id = ?
    """, [SCENARIO_ID]).fetchone()[0]

    if baseline_cost is None or truck_cost is None:
        raise ValueError("Run Steps 31 and 32 before drawing this chart.")

    plan_cost = truck_cost + transfer_cost
    saving = 1 - plan_cost / baseline_cost

    labels = [
        f"Every lane direct\n(Step 31, {baseline_trips:,} trips)",
        f"Optimised network\n(Step 32, {plan_trips:,} trips)",
    ]
    values = [baseline_cost, plan_cost]

    fig, ax = plt.subplots(figsize=(9, 3.2))

    bars = ax.barh(labels, values, color=[GREY, BLUE], height=0.55)
    ax.invert_yaxis()

    for bar, value in zip(bars, values):
        ax.text(
            value + baseline_cost * 0.012,
            bar.get_y() + bar.get_height() / 2,
            f"A${value:,.0f}",
            va="center", color=TEXT, fontweight="bold"
        )

    ax.set_title(
        f"Interstate truck cost per year: {saving:.0%} lower with the optimised network",
        loc="left", pad=14
    )
    ax.set_xlim(0, baseline_cost * 1.2)
    ax.xaxis.set_major_formatter(lambda x, _: f"A${x / 1e6:.1f}M")
    ax.grid(axis="x", color=GRID)
    ax.tick_params(axis="y", length=0)
    _clean_axes(ax)

    return _save(fig, "mip_linehaul_cost.png")


# ============================================================
# CHART 2: MAP OF THE LEGS THE OPTIMISED PLAN DRIVES
# ============================================================

def chart_network_map(con):

    sites = con.execute("""
        SELECT f.state, f.facility_name, c.longitude, c.latitude
        FROM facility_registry AS f
        INNER JOIN mip_facility_coordinates AS c
            ON f.facility_id = c.facility_id
        WHERE f.status = 'Active'
    """).fetchall()

    period_count = con.execute("""
        SELECT COUNT(DISTINCT period)
        FROM mip_network_trips
        WHERE scenario_id = ?
    """, [SCENARIO_ID]).fetchone()[0]

    if not period_count:
        raise ValueError("Run Step 32 before drawing this chart.")

    # A leg counts once whichever direction it is driven.
    legs = con.execute("""
        SELECT
            LEAST(origin_state, destination_state),
            GREATEST(origin_state, destination_state),
            COUNT(DISTINCT period)
        FROM mip_network_trips
        WHERE scenario_id = ?
        GROUP BY ALL
        HAVING COUNT(DISTINCT period) >= ?
    """, [SCENARIO_ID, MAP_MIN_SHARE_OF_PERIODS * period_count]).fetchall()

    # The two facilities where most parcels change vehicles.
    hubs = [row[0] for row in con.execute("""
        SELECT via_state, SUM(parcel_count)
        FROM mip_network_routes
        WHERE scenario_id = ? AND via_state IS NOT NULL
        GROUP BY via_state
        ORDER BY 2 DESC
        LIMIT 2
    """, [SCENARIO_ID]).fetchall()]

    position = {state: (lon, lat) for state, _, lon, lat in sites}
    all_pairs = len(sites) * (len(sites) - 1) // 2

    fig, ax = plt.subplots(figsize=(9, 5.6))

    for state_a, state_b, _ in legs:
        (x1, y1), (x2, y2) = position[state_a], position[state_b]
        ax.plot([x1, x2], [y1, y2], color=BLUE, linewidth=2, alpha=0.75, zorder=1)

    # Where each city label sits relative to its dot, in points.
    label_offset = {
        "ACT": (8, -14), "NSW": (10, -3), "QLD": (10, -3), "TAS": (10, -4),
        "VIC": (10, -12), "SA": (-10, -14), "WA": (0, -17), "NT": (0, 10),
    }
    label_align = {"SA": "right", "WA": "center", "NT": "center"}

    for state, name, lon, lat in sites:
        is_hub = state in hubs
        ax.scatter(
            lon, lat,
            s=150 if is_hub else 70,
            color=AMBER if is_hub else "white",
            edgecolor=TEXT, linewidth=1.4, zorder=2
        )
        ax.annotate(
            name, (lon, lat),
            xytext=label_offset.get(state, (10, -3)),
            textcoords="offset points",
            ha=label_align.get(state, "left"),
            color=TEXT if is_hub else MUTED,
            fontweight="bold" if is_hub else "normal",
        )

    ax.set_title(
        f"The plan drives {len(legs)} of {all_pairs} connections in most weeks",
        loc="left", pad=14
    )
    ax.text(
        0, -0.04,
        "Blue line: leg driven in at least "
        f"{MAP_MIN_SHARE_OF_PERIODS:.0%} of periods.   "
        "Amber dot: main transfer hub chosen by the solver.",
        transform=ax.transAxes, color=MUTED, fontsize=9, va="top"
    )

    ax.set_aspect(1.15)
    ax.margins(0.12)
    ax.axis("off")

    return _save(fig, "mip_network_map.png")


# ============================================================
# CHART 3: CANDIDATE FACILITIES, FIXED COST PLUS EXTRA TRUCKS
# ============================================================

def chart_candidate_costs(con):

    tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
    if "mip_candidate_comparison" not in tables:
        raise ValueError(
            "Run Step 33 first: python src/mip_facility_opening.py"
        )

    n_parcels = con.execute("""
        SELECT n_parcels
        FROM mip_candidate_comparison
        ORDER BY calculated_at DESC
        LIMIT 1
    """).fetchone()

    if n_parcels is None:
        raise ValueError(
            "Step 33 found no facility over capacity, so there is "
            "nothing to compare. Raise N_PARCELS and rerun it."
        )

    n_parcels = n_parcels[0]

    options = con.execute("""
        SELECT state, candidates_opened, fixed_cost_aud,
               extra_truck_cost_aud, total_extra_cost_aud, is_best
        FROM mip_candidate_comparison
        WHERE n_parcels = ?
        ORDER BY state DESC, total_extra_cost_aud
    """, [n_parcels]).fetchall()

    names = dict(con.execute(
        "SELECT facility_id, facility_name FROM facility_registry"
    ).fetchall())

    labels = []
    for state, opened, _, _, _, is_best in options:
        readable = " + ".join(
            names.get(facility_id, facility_id).replace(" Hub", "")
            for facility_id in opened.split(" + ")
        )
        labels.append(f"{state}: {readable}" + ("  (best)" if is_best else ""))

    fixed = [row[2] for row in options]
    trucks = [row[3] for row in options]
    totals = [row[4] for row in options]

    fig, ax = plt.subplots(figsize=(9, 0.75 * len(options) + 1.9))

    ax.barh(labels, fixed, color=AMBER, height=0.55, label="Fixed cost of the candidate")
    ax.barh(labels, trucks, left=fixed, color=BLUE, height=0.55, label="Extra truck cost")
    ax.invert_yaxis()

    for index, total in enumerate(totals):
        ax.text(
            total + max(totals) * 0.012, index,
            f"A${total:,.0f}",
            va="center", color=TEXT, fontweight="bold"
        )

    for tick, row in zip(ax.get_yticklabels(), options):
        if row[5]:
            tick.set_fontweight("bold")

    ax.set_title(
        f"Yearly cost of each candidate at {n_parcels:,} parcels",
        loc="left", pad=30
    )
    ax.legend(
        frameon=False, ncol=2, loc="lower left",
        bbox_to_anchor=(0, 1.0), fontsize=9
    )
    ax.set_xlim(0, max(totals) * 1.2)
    ax.xaxis.set_major_formatter(lambda x, _: f"A${x / 1e3:,.0f}k")
    ax.grid(axis="x", color=GRID)
    ax.tick_params(axis="y", length=0)
    _clean_axes(ax)

    return _save(fig, "mip_candidate_costs.png")


# ============================================================
# MAIN EXECUTION
# ============================================================

if __name__ == "__main__":

    if not MIP_DB_PATH.is_file():
        raise FileNotFoundError(f"MIP database not found: {MIP_DB_PATH}")

    with duckdb.connect(str(MIP_DB_PATH), read_only=True) as con:

        for draw_chart in (
            chart_linehaul_cost,
            chart_network_map,
            chart_candidate_costs,
        ):
            # A chart needs its step's results. If they are missing,
            # say which step to run and carry on with the next chart.
            try:
                draw_chart(con)
            except (ValueError, duckdb.CatalogException) as problem:
                print(f"Skipped {draw_chart.__name__}: {problem}")