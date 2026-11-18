from pathlib import Path

import numpy as np
import pandas as pd


SEED = 2026
N_PARCELS = 500_000

rng = np.random.default_rng(SEED)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"

RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)


cities = [
    "Melbourne",
    "Sydney",
    "Brisbane",
    "Adelaide",
    "Perth",
    "Hobart",
    "Canberra",
    "Darwin"
]

states = [
    "VIC",
    "NSW",
    "QLD",
    "SA",
    "WA",
    "TAS",
    "ACT",
    "NT"
]

facility_ids = [
    f"F{i:03d}"
    for i in range(1, len(cities) + 1)
]

facilities = pd.DataFrame({
    "facility_id": facility_ids,
    "facility_name": cities,
    "state": states,
    "facility_type": "Major Processing"
})

facilities.to_csv(
    RAW_DATA_DIR / "facilities.csv",
    index=False
)
print(facilities)

state_probabilities = [
    0.27,  # VIC
    0.33,  # NSW
    0.15,  # QLD
    0.07,  # SA
    0.10,  # WA
    0.05,  # TAS
    0.02,  # ACT
    0.01   # NT
]

origin_states = rng.choice(
    states,
    size=N_PARCELS,
    p=state_probabilities
)

print(
    pd.Series(origin_states)
    .value_counts(normalize=True)
    .sort_values(ascending=False)
)

INTRASTATE_PROBABILITY = 0.65

is_intrastate = rng.random(N_PARCELS) < INTRASTATE_PROBABILITY

destination_states = rng.choice(
    states,
    size=N_PARCELS,
    p=state_probabilities
)


# For intrastate parcels, destination must equal origin
destination_states[is_intrastate] = origin_states[is_intrastate]
invalid = (~is_intrastate) & (destination_states == origin_states)
while invalid.any():

    destination_states[invalid] = rng.choice(
        states,
        size=invalid.sum(),
        p=state_probabilities
    )

    invalid = (~is_intrastate) & (destination_states == origin_states)

print("\n--- Route validation ---")
print(f"Intrastate parcels: {is_intrastate.sum():,}")
print(f"Interstate parcels: {(~is_intrastate).sum():,}")
print(f"Invalid interstate destinations: {invalid.sum():,}")

# -------------------------
# Parcel attributes
# -------------------------

parcel_ids = [
    f"P{i:07d}"
    for i in range(1, N_PARCELS + 1)
]

service_types = rng.choice(
    ["Standard", "Express"],
    size=N_PARCELS,
    p=[0.75, 0.25]
)

weights = rng.lognormal(
    mean=0.2,
    sigma=0.8,
    size=N_PARCELS
)

weights = np.clip(weights, 0.05, 22)
weights = np.round(weights, 2)

print("\n--- Parcel attribute validation ---")

print("First 5 parcel IDs:")
print(parcel_ids[:5])

print("\nService distribution:")
print(
    pd.Series(service_types)
    .value_counts(normalize=True)
)

print("\nWeight summary:")
print(
    pd.Series(weights).describe()
)

dates = pd.date_range(
    start="2025-01-01",
    end="2025-12-31",
    freq="D"
)

date_weights = np.ones(len(dates))
# Higher parcel volume during November
date_weights[dates.month == 11] *= 1.5

# Even higher parcel volume during December
date_weights[dates.month == 12] *= 1.8

is_weekend = dates.dayofweek >= 5
date_weights[is_weekend] *= 0.65
date_probabilities = date_weights / date_weights.sum()

lodgement_dates = rng.choice(
    dates,
    size=N_PARCELS,
    p=date_probabilities
)
lodgement_hours = rng.integers(
    low=8,
    high=20,
    size=N_PARCELS
)

lodgement_minutes = rng.integers(
    low=0,
    high=60,
    size=N_PARCELS
)
expected_days = np.where(
    service_types == "Express",
    np.where(is_intrastate, 1, 2),
    np.where(is_intrastate, 3, 5)
)

expected_delivery_dates = (
    pd.to_datetime(lodgement_dates)
    + pd.to_timedelta(expected_days, unit="D")
)

print("\n--- Expected delivery validation ---")

validation = pd.DataFrame({
    "service_type": service_types,
    "is_intrastate": is_intrastate,
    "expected_days": expected_days
})

print(
    validation
    .groupby(["service_type", "is_intrastate"])["expected_days"]
    .agg(["count", "min", "max"])
)

delivery_variance = rng.choice(
    [-1, 0, 1, 2, 3],
    size=N_PARCELS,
    p=[0.10, 0.65, 0.15, 0.07, 0.03]
)
lodgement_months = pd.DatetimeIndex(lodgement_dates).month

is_peak_season = np.isin(
    lodgement_months,
    [11, 12]
)

peak_delay = (
    is_peak_season
    & (rng.random(N_PARCELS) < 0.20)
)
delivery_variance[peak_delay] += 1

interstate_delay = (
    (~is_intrastate)
    & (rng.random(N_PARCELS) < 0.10)
)

delivery_variance[interstate_delay] += 1

actual_delivery_dates = (
    expected_delivery_dates
    + pd.to_timedelta(delivery_variance, unit="D")
)
print("\n--- Delivery performance ---")

print(
    pd.Series(delivery_variance)
    .value_counts()
    .sort_index()
)
on_time = actual_delivery_dates <= expected_delivery_dates

print(
    f"\nOverall on-time rate: {on_time.mean():.2%}"
)

parcels = pd.DataFrame({
    "parcel_id": parcel_ids,
    "origin_state": origin_states,
    "destination_state": destination_states,
    "lodgement_date": pd.to_datetime(lodgement_dates),
    "expected_delivery_date": expected_delivery_dates,
    "actual_delivery_date": actual_delivery_dates,
    "service_type": service_types,
    "weight_kg": weights
})

print("\n--- Parcels table ---")
print(parcels.head())

print("\nShape:")
print(parcels.shape)

print("\nData types:")
print(parcels.dtypes)

parcels.to_csv(
    RAW_DATA_DIR / "parcels.csv",
    index=False
)

# -------------------------
# Tracking events
# -------------------------

state_to_facility = dict(zip(states, facility_ids))

print(state_to_facility)

origin_facilities = np.array([
    state_to_facility[state]
    for state in origin_states
])

destination_facilities = np.array([
    state_to_facility[state]
    for state in destination_states
])

lodgement_timestamps = pd.to_datetime(lodgement_dates)
lodged_times = (
    lodgement_timestamps
    + pd.to_timedelta(
        rng.integers(8, 18, size=N_PARCELS),
        unit="h"
    )
)
origin_processed_times = (
    lodged_times
    + pd.to_timedelta(
        rng.integers(2, 9, size=N_PARCELS),
        unit="h"
    )
)
transit_hours = np.where(
    is_intrastate,
    rng.integers(6, 18, size=N_PARCELS),
    rng.integers(18, 48, size=N_PARCELS)
)

destination_processed_times = (
    origin_processed_times
    + pd.to_timedelta(transit_hours, unit="h")
)

tracking_check = pd.DataFrame({
    "parcel_id": parcel_ids[:10],
    "origin": origin_states[:10],
    "destination": destination_states[:10],
    "lodged": lodged_times[:10],
    "origin_processed": origin_processed_times[:10],
    "destination_processed": destination_processed_times[:10]
})

print("\n--- Tracking timestamp validation ---")
print(tracking_check)

delivery_hours = rng.integers(
    8,
    19,
    size=N_PARCELS
)

delivered_times = pd.Series(
    pd.to_datetime(actual_delivery_dates)
    + pd.to_timedelta(delivery_hours, unit="h")
)

invalid_delivery_order = (
    delivered_times <= destination_processed_times
)

print(
    "Invalid delivery sequences:",
    invalid_delivery_order.sum()
)

delivered_times[invalid_delivery_order] = (
    destination_processed_times[invalid_delivery_order]
    + pd.to_timedelta(
        rng.integers(
            2,
            7,
            size=invalid_delivery_order.sum()
        ),
        unit="h"
    )
)
invalid_delivery_order = (
    delivered_times <= destination_processed_times
)

print(
    "Invalid delivery sequences after fix:",
    invalid_delivery_order.sum()
)

# -------------------------
# Build tracking events table
# -------------------------

lodged_events = pd.DataFrame({
    "parcel_id": parcel_ids,
    "facility_id": origin_facilities,
    "event_type": "Lodged",
    "event_timestamp": lodged_times
})

origin_processed_events = pd.DataFrame({
    "parcel_id": parcel_ids,
    "facility_id": origin_facilities,
    "event_type": "Origin Processed",
    "event_timestamp": origin_processed_times
})

destination_processed_events = pd.DataFrame({
    "parcel_id": parcel_ids,
    "facility_id": destination_facilities,
    "event_type": "Destination Processed",
    "event_timestamp": destination_processed_times
})

delivered_events = pd.DataFrame({
    "parcel_id": parcel_ids,
    "facility_id": destination_facilities,
    "event_type": "Delivered",
    "event_timestamp": delivered_times
})

tracking_events = pd.concat(
    [
        lodged_events,
        origin_processed_events,
        destination_processed_events,
        delivered_events
    ],
    ignore_index=True
)
tracking_events.insert(
    0,
    "event_id",
    [
        f"E{i:07d}"
        for i in range(1, len(tracking_events) + 1)
    ]
)

print("\n--- Tracking events table ---")
print(tracking_events.head())

print("\nShape:")
print(tracking_events.shape)

print("\nEvents by type:")
print(tracking_events["event_type"].value_counts())

print(
    "\nDuplicate event IDs:",
    tracking_events["event_id"].duplicated().sum()
)

tracking_events.to_csv(
    RAW_DATA_DIR / "tracking_events.csv",
    index=False
)