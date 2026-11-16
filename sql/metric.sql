-- Index discharges are attributed to the discharging hospital and month.
-- The dashboard is a subset of the source index rows. Both count returns
-- from the same independent inpatient encounter feed.
WITH index_discharges AS (
    SELECT e.scenario_id, e.encounter_id, e.patient_id, e.hospital_name,
           substr(e.discharge_date, 1, 7) AS cohort_month, e.discharge_date
    FROM encounters AS e
    WHERE e.scenario_id = :scenario_id
      AND substr(e.discharge_date, 1, 7) IN (:prior_month, :current_month)
      AND e.principal_diagnosis = 'HF'
      AND e.setting = 'inpatient'
      AND e.discharged_alive = 1
),
index_outcomes AS (
    SELECT i.encounter_id, i.cohort_month, i.hospital_name,
           r.encounter_id IS NOT NULL AS in_dashboard,
           EXISTS (
               SELECT 1
               FROM encounters AS followup
               JOIN source_coverage AS coverage
                 ON coverage.scenario_id = followup.scenario_id
                AND coverage.hospital_id = followup.hospital_id
               WHERE followup.scenario_id = i.scenario_id
                 AND followup.patient_id = i.patient_id
                 AND followup.setting = 'inpatient'
                 AND followup.planned = 0
                 AND followup.admit_date > i.discharge_date
                 AND followup.admit_date <= date(i.discharge_date, '+30 days')
                 AND followup.admit_date <= coverage.observed_through
           ) AS readmitted
    FROM index_discharges AS i
    LEFT JOIN reporting_index AS r
      ON r.scenario_id = i.scenario_id
     AND r.encounter_id = i.encounter_id
)
-- Aggregate index outcomes.
SELECT cohort_month, hospital_name,
       COUNT(*) AS source_discharges,
       SUM(readmitted) AS source_readmissions,
       1.0 * SUM(readmitted) / COUNT(*) AS source_rate,
       SUM(in_dashboard) AS dashboard_discharges,
       SUM(in_dashboard * readmitted) AS dashboard_readmissions,
       1.0 * SUM(in_dashboard * readmitted) / NULLIF(SUM(in_dashboard), 0) AS dashboard_rate
FROM index_outcomes
GROUP BY cohort_month, hospital_name
ORDER BY cohort_month, hospital_name;
