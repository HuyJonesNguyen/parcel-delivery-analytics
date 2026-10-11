
-- Parcel Delivery Analytics | DuckDB SQL


-- Phase 1: Data Quality and Reliability


-- Q1: Does every parcel have exactly four tracking events?
SELECT
    p.parcel_id,
    COUNT(t.event_id) AS event_count
FROM parcels AS p
LEFT JOIN tracking_events AS t
    ON p.parcel_id = t.parcel_id
GROUP BY p.parcel_id
HAVING COUNT(t.event_id) <> 4;


-- Q1 (summary check): How many parcels have an invalid tracking-event count?
SELECT
    COUNT(*) AS total_parcels,
    SUM(
        CASE
            WHEN event_count <> 4 THEN 1
            ELSE 0
        END
    ) AS invalid_parcels
FROM (
    SELECT
        p.parcel_id,
        COUNT(t.event_id) AS event_count
    FROM parcels AS p
    LEFT JOIN tracking_events AS t
        ON p.parcel_id = t.parcel_id
    GROUP BY p.parcel_id
) AS parcel_counts;


-- Q2: What percentage of Delivered tracking records have a date that disagrees with the parcel management system?
WITH total_deliveries as (
    SELECT
        COUNT(*) as total_deliveries
    FROM tracking_events
    WHERE event_type = 'Delivered'
),
mismatched_deliveries as (
    SELECT
        COUNT(*) as mismatched_deliveries
    FROM parcels p
    JOIN tracking_events t
        ON p.parcel_id = t.parcel_id
    WHERE t.event_type = 'Delivered'
      AND p.actual_delivery_date <> CAST(t.event_timestamp as DATE)
)
SELECT
    t.total_deliveries,
    m.mismatched_deliveries,
    ROUND(
        100.0 * m.mismatched_deliveries
        / NULLIF(t.total_deliveries, 0),
        2
    ) AS mismatch_percentage
FROM total_deliveries t
CROSS JOIN mismatched_deliveries m;


-- Q2 (quick count): How many delivery dates disagree between tables?
SELECT
    COUNT(*) AS total_deliveries,
    COUNT(*) FILTER (
        WHERE CAST(t.event_timestamp AS DATE)
              <> p.actual_delivery_date
    ) AS mismatched_delivery_dates
FROM parcels AS p
JOIN tracking_events AS t
    ON p.parcel_id = t.parcel_id
WHERE t.event_type = 'Delivered';


-- Q2 (sample records): Show 10 parcels with mismatched dates.
SELECT
    p.parcel_id,
    p.actual_delivery_date,
    CAST(t.event_timestamp AS DATE) AS tracking_delivery_date
FROM parcels p
JOIN tracking_events t
    ON p.parcel_id = t.parcel_id
WHERE t.event_type = 'Delivered'
  AND p.actual_delivery_date <> CAST(t.event_timestamp AS DATE)
LIMIT 10;


-- Q3: When dates disagree, is tracking earlier or later than the parcel-system date?
With delivery_classify as (SELECT p.parcel_id,
    p.actual_delivery_date ,
    CAST(t.event_timestamp AS DATE) as tracking_date,
       CASE
       WHEN p.actual_delivery_date < CAST(t.event_timestamp AS DATE) THEN 'Tracking Later'
        WHEN p.actual_delivery_date > CAST(t.event_timestamp AS DATE) THEN 'Tracking Earlier'
       ELSE 'Matching'
        END as tracking_status
FROM parcels p
JOIN tracking_events t
    ON p.parcel_id = t.parcel_id
WHERE t.event_type = 'Delivered')
SELECT tracking_status as discrepancy_type,
       COUNT(distinct d.parcel_id) as parcel_count,
       Round(COUNT(distinct d.parcel_id) * 100.0 / SUM(COUNT(distinct d.parcel_id)) OVER(), 2) AS percentage
FROM delivery_classify d
GROUP BY tracking_status;


-- Q4: How many parcels change their On Time/Late classification depending on which delivery date is used?
With delivery_classify as (SELECT p.parcel_id,
       CASE
       WHEN p.expected_delivery_date < CAST(t.event_timestamp AS DATE) THEN 'Late'
       ELSE 'On time'
        END as tracking_status,
        CASE
       WHEN p.expected_delivery_date < p.actual_delivery_date THEN 'Late'
       ELSE 'On time'
        END as parcel_status
FROM parcels p
JOIN tracking_events t
    ON p.parcel_id = t.parcel_id
WHERE t.event_type = 'Delivered')
SELECT parcel_status, tracking_status, count(distinct parcel_id) as parcel_count
FROM delivery_classify
GROUP BY parcel_status, tracking_status
ORDER BY parcel_status, tracking_status;


-- Q5: Are classification changes more common for Express or Standard deliveries?
With delivery_classify as (SELECT distinct p.parcel_id,
        p.service_type,
       CASE
       WHEN p.expected_delivery_date < CAST(t.event_timestamp AS DATE) THEN 'Late'
       ELSE 'On time'
        END as tracking_status,
        CASE
       WHEN p.expected_delivery_date < p.actual_delivery_date THEN 'Late'
       ELSE 'On time'
        END as parcel_status
FROM parcels p
JOIN tracking_events t
    ON p.parcel_id = t.parcel_id
WHERE t.event_type = 'Delivered')
SELECT service_type,
       COUNT(distinct parcel_id) as total_parcels,
       SUM(CASE WHEN tracking_status <> parcel_status THEN 1 ELSE 0 END) as changed_classifications,
       Round(100 * SUM(CASE WHEN tracking_status <> parcel_status THEN 1 ELSE 0 END) / COUNT(distinct parcel_id), 2) as change_percentage
FROM delivery_classify
GROUP BY service_type
ORDER BY change_percentage DESC;


-- Q6: Among Express parcels, are classification changes more common on interstate or intrastate routes?
With delivery_classify as (SELECT distinct p.parcel_id,
        IF(p.origin_state <> p.destination_state, 'Interstate', 'Intrastate') as route_type,
       CASE
       WHEN p.expected_delivery_date < CAST(t.event_timestamp AS DATE) THEN 'Late'
       ELSE 'On time'
        END as tracking_status,
        CASE
       WHEN p.expected_delivery_date < p.actual_delivery_date THEN 'Late'
       ELSE 'On time'
        END as parcel_status
FROM parcels p
JOIN tracking_events t
    ON p.parcel_id = t.parcel_id
WHERE t.event_type = 'Delivered' and p.service_type = 'Express')
SELECT route_type,
       COUNT(distinct parcel_id) as total_parcels,
       SUM(CASE WHEN tracking_status <> parcel_status THEN 1 ELSE 0 END) as changed_classifications,
       Round(100 * SUM(CASE WHEN tracking_status <> parcel_status THEN 1 ELSE 0 END) / COUNT(distinct parcel_id), 2) as change_percentage
FROM delivery_classify
GROUP BY route_type
ORDER BY change_percentage DESC;


-- Q7: Are Express interstate classification changes concentrated in particular lodgement months?
With delivery_classify as (SELECT distinct p.parcel_id,
    STRFTIME(p.lodgement_date, '%Y-%m') as lodgement_month,
    IF(p.origin_state <> p.destination_state, 'Interstate', 'Intrastate') as route_type,
       CASE
       WHEN p.expected_delivery_date < CAST(t.event_timestamp AS DATE) THEN 'Late'
       ELSE 'On time'
        END as tracking_status,
        CASE
       WHEN p.expected_delivery_date < p.actual_delivery_date THEN 'Late'
       ELSE 'On time'
        END as parcel_status
FROM parcels p
JOIN tracking_events t
    ON p.parcel_id = t.parcel_id
WHERE t.event_type = 'Delivered'
  and p.service_type = 'Express')
SELECT lodgement_month,
       route_type,
       COUNT(distinct parcel_id) as total_parcels,
       SUM(CASE WHEN tracking_status <> parcel_status THEN 1 ELSE 0 END) as changed_classifications,
       Round(100 * SUM(CASE WHEN tracking_status <> parcel_status THEN 1 ELSE 0 END) / COUNT(distinct parcel_id), 2) as change_percentage
FROM delivery_classify
WHERE route_type = 'Interstate'
GROUP BY lodgement_month, route_type
ORDER BY lodgement_month ASC;


-- Phase 2: Route Performance and Operational Priorities


-- Q8: Which five interstate routes have the lowest on-time rates, among routes with at least 500 parcels?
-- Restored from your earlier Challenge 8 solution.
WITH delivery_classify as (
    SELECT distinct parcel_id,
           origin_state,
           destination_state,
           IF(origin_state <> destination_state, 'Interstate', 'Intrastate') as route_type,
           CASE
               WHEN actual_delivery_date > expected_delivery_date THEN 'Late'
               ELSE 'On time'
           END as parcel_status
    FROM parcels
)
SELECT origin_state,
       destination_state,
       COUNT(distinct parcel_id) as total_parcels,
       SUM(CASE WHEN parcel_status = 'Late' THEN 1 ELSE 0 END) as late_parcels,
       Round(100 - 100 * SUM(CASE WHEN parcel_status = 'Late' THEN 1 ELSE 0 END)
             / COUNT(distinct parcel_id), 2) as on_time_percentage
FROM delivery_classify
WHERE route_type = 'Interstate'
GROUP BY origin_state, destination_state
HAVING COUNT(distinct parcel_id) >= 500
ORDER BY on_time_percentage ASC, total_parcels DESC
LIMIT 5;


-- Q9: Which five interstate routes contribute the most late deliveries, and what share of all interstate late deliveries does each account for?
With delivery_classify as (SELECT distinct p.parcel_id,
    p.origin_state,
    p.destination_state,
    IF(p.origin_state <> p.destination_state, 'Interstate', 'Intrastate') as route_type,
        CASE
       WHEN p.expected_delivery_date < p.actual_delivery_date THEN 'Late'
       ELSE 'On time'
        END as parcel_status
FROM parcels p
JOIN tracking_events t
    ON p.parcel_id = t.parcel_id
WHERE t.event_type = 'Delivered')
SELECT origin_state,
       destination_state,
       COUNT(distinct parcel_id) as total_parcels,
       SUM(CASE WHEN parcel_status == 'Late' THEN 1 ELSE 0 END) as late_parcels,
       Round(100 * SUM(CASE WHEN parcel_status == 'Late' THEN 1 ELSE 0 END) /
             SUM(SUM(CASE WHEN parcel_status == 'Late' THEN 1 ELSE 0 END)) OVER(), 2) as late_contribution_percentage
FROM delivery_classify
WHERE route_type = 'Interstate'
GROUP BY origin_state, destination_state
ORDER BY SUM(CASE WHEN parcel_status == 'Late' THEN 1 ELSE 0 END) DESC,
         COUNT(distinct parcel_id) DESC
LIMIT 5;


-- Phase 3: Scenario Analysis


-- Q10: If the five highest-late-volume interstate routes reduce late deliveries by 10%, how much does the overall interstate on-time rate improve?
WITH interstate_parcels AS (
    SELECT
        parcel_id,
        origin_state,
        destination_state,
        CASE
            WHEN actual_delivery_date > expected_delivery_date
            THEN 1
            ELSE 0
        END AS is_late
    FROM parcels
    WHERE origin_state <> destination_state
),

route_performance AS (
    SELECT
        origin_state,
        destination_state,
        SUM(is_late) AS late_parcels
    FROM interstate_parcels
    GROUP BY origin_state, destination_state
),

top_5_routes AS (
    SELECT
        origin_state,
        destination_state,
        late_parcels
    FROM route_performance
    ORDER BY late_parcels DESC
    LIMIT 5
),

improvement AS (
    SELECT
        ROUND(SUM(late_parcels) * 0.10, 2)
            AS late_deliveries_avoided
    FROM top_5_routes
),

overall_performance AS (
    SELECT
        COUNT(*) AS total_interstate_parcels,
        SUM(is_late) AS current_late_parcels
    FROM interstate_parcels
)

SELECT
    o.total_interstate_parcels,
    o.current_late_parcels,
    i.late_deliveries_avoided,

    ROUND(
        100.0 * (o.total_interstate_parcels - o.current_late_parcels)
        / o.total_interstate_parcels,
        2
    ) AS current_on_time_percentage,

    ROUND(
        100.0 * (
            o.total_interstate_parcels
            - o.current_late_parcels
            + i.late_deliveries_avoided
        ) / o.total_interstate_parcels,
        2
    ) AS projected_on_time_percentage,

    ROUND(
        100.0 * i.late_deliveries_avoided
        / o.total_interstate_parcels,
        2
    ) AS improvement_percentage_points

FROM overall_performance o
CROSS JOIN improvement i;


-- Phase 4: Service Performance and Customer Expectations


-- Q11: Does Express have a higher on-time rate than Standard across interstate and intrastate deliveries?
WITH route_service_table AS (
    SELECT
        parcel_id,
        service_type,
        IF(origin_state <> destination_state, 'Interstate', 'Intrastate') as route_type,
        CASE
            WHEN actual_delivery_date > expected_delivery_date
            THEN 1
            ELSE 0
        END AS is_late
    FROM parcels
)

SELECT service_type,
       route_type,
       COUNT(*) as total_parcels,
       SUM(is_late) as late_parcels,
       Round(100 - 100 * SUM(is_late) / COUNT(distinct parcel_id), 2) as on_time_percentage
FROM route_service_table
GROUP BY service_type, route_type
ORDER BY route_type ASC,
         service_type ASC;


-- Q12: How much faster does Express deliver compared with Standard, based on average and median delivery days?
-- Restored from your earlier Challenge 12 solution.
WITH route_service_table AS (
    SELECT
        parcel_id,
        service_type,
        IF(origin_state <> destination_state, 'Interstate', 'Intrastate') as route_type,
        DATE_DIFF('day', lodgement_date, actual_delivery_date) as actual_delivery_time,
        DATE_DIFF('day', lodgement_date, expected_delivery_date) as promised_delivery_time
    FROM parcels
)
SELECT service_type,
       route_type,
       COUNT(*) as total_parcels,
       Round(AVG(actual_delivery_time), 2) as avg_actual_delivery_days,
       Round(AVG(promised_delivery_time), 2) as avg_promised_delivery_days,
       Round(MEDIAN(actual_delivery_time), 2) as median_actual_delivery_days
FROM route_service_table
GROUP BY service_type, route_type
ORDER BY route_type ASC, service_type ASC;


-- Q13: Among late parcels only, which service has more severe delays (average, median, P95, maximum)?
WITH route_service_table AS (
    SELECT
        parcel_id,
        service_type,
        DATE_DIFF('day', expected_delivery_date, actual_delivery_date) as delay_days,
        CASE
            WHEN actual_delivery_date > expected_delivery_date
            THEN 1
            ELSE 0
        END AS is_late
    FROM parcels
    WHERE actual_delivery_date > expected_delivery_date
)

SELECT service_type,
       SUM(is_late) as late_parcels,
       Round(AVG(delay_days), 2) as avg_delay_days,
       Round(MEDIAN(delay_days), 2) as median_delay_days,
       Round(QUANTILE_CONT(delay_days, 0.95), 2) as p95_delay_days,
       Round(MAX(delay_days), 2) as max_delay_days
FROM route_service_table
GROUP BY service_type
ORDER BY service_type ASC;


-- Phase 5: Understanding Delivery Delay Drivers


-- Q14: Are November–December parcels more likely to be late or severely late than other months?
WITH route_service_table AS (
    SELECT
        parcel_id,
        IF(MONTH(lodgement_date) BETWEEN 11 AND 12, 'Peak', 'Non-Peak') as season,
        DATE_DIFF('day', expected_delivery_date, actual_delivery_date) as actual_delivery_time,
        CASE
            WHEN actual_delivery_date > expected_delivery_date
            THEN 1
            ELSE 0
        END AS is_late,
        IF(DATE_DIFF('day', expected_delivery_date, actual_delivery_date) >= 3, 1, 0) as is_severely_late
    FROM parcels
)

SELECT season,
       COUNT(*) as total_parcels,
       SUM(is_late) as late_parcels,
       SUM(is_severely_late) as severely_late_parcels,
       ROUND(100 * SUM(is_late) / COUNT(*), 2) as late_percentage,
       ROUND(100 * SUM(is_severely_late) / COUNT(*), 2) as severely_late_percentage
FROM route_service_table
GROUP BY season
ORDER BY CASE WHEN season = 'Peak' THEN 0 ELSE 1 END;


-- Q15: Does peak season affect Express and Standard late-delivery rates differently?
WITH route_service_table AS (
    SELECT
        parcel_id,
        service_type,
        IF(MONTH(lodgement_date) BETWEEN 11 AND 12, 'Peak', 'Non-Peak') as season,
        CASE
            WHEN actual_delivery_date > expected_delivery_date
            THEN 1
            ELSE 0
        END AS is_late
    FROM parcels
)

SELECT service_type,
       season,
       COUNT(*) as total_parcels,
       SUM(is_late) as late_parcels,
       ROUND(100 * SUM(is_late) / COUNT(*), 2) as late_percentage
FROM route_service_table
GROUP BY service_type, season
ORDER BY service_type ASC,
         CASE WHEN season = 'Peak' THEN 0 ELSE 1 END;


-- Q16: Are heavier parcels more likely to arrive late and do they have longer average delivery times?
-- TODO: Query not yet written.
-- Weight categories:
-- Light: weight_kg < 1
-- Medium: weight_kg >= 1 AND weight_kg < 5
-- Heavy: weight_kg >= 5

WITH weight_table AS (
    SELECT
        parcel_id,
        CASE
            WHEN weight_kg < 1 THEN 'Light'
            WHEN weight_kg >= 5 THEN 'Heavy'
            ELSE 'Medium'
        END as weight_category,
        CASE
            WHEN actual_delivery_date > expected_delivery_date
            THEN 1
            ELSE 0
        END AS is_late,
        DATE_DIFF('day', lodgement_date, actual_delivery_date) as actual_delivery_time
    FROM parcels
)

SELECT weight_category,
       COUNT(*) as total_parcels,
       SUM(is_late) as late_parcels,
       ROUND(100 * SUM(is_late) / COUNT(*), 2) as late_percentage,
       ROUND( AVG(actual_delivery_time), 2) as avg_delivery_days
FROM weight_table
GROUP BY weight_category
ORDER BY list_position(['Light', 'Medium', 'Heavy'], weight_category);