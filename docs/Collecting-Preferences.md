# Collecting Shift Preferences and Running the Solver

Short operating procedure for a preference-collection campaign on the Mu2e
Shift Scheduler (https://mu2e-shifts.fnal.gov). Steps 1, 2, and 4–6 require an
administrator account; step 3 is done by every collaborator.

## 1. Create the schedule

**Admin → Shift Setup** (`/admin/shift-setup`):

1. Enter the schedule name (e.g. `Fall 2026`) and pick a classification
   (e.g. *General Shifts*, *Run Coordinators*).
2. Set the start and stop dates.
3. Define the blocks — one row per repeating day group, e.g.
   *Weekdays* with Mon–Thu checked and *Weekends* with Fri–Sun checked.
   Leave all days unchecked for a block of N consecutive days
   ("Block length"), e.g. a 7-day on-call block.
4. Define the shifts per day — name, start/stop time, and weight
   (points; blank uses the day/night defaults from `config/config.yaml`).
5. Choose the repetition rate (weekly for day-of-week blocks).
6. Click **Generate schedule**. Re-using an existing name overwrites it
   after a confirmation page.

Alternatively, upload an existing block CSV with a name and classification on
the **Schedules** page.

## 2. Open preference collection

**Admin → Configuration → Preference collection schedule**: select the
schedule and **Save**.

From this point the *Submit Preferences* page lists exactly that schedule's
shifts, with a banner naming it. Every submission is written server-side to

```
/app/csv/<schedule-slug>-prefs.csv     e.g. fall-2026-prefs.csv
```

in the solver's people-CSV format (`name, pref_1, pref_2, …`, most recent
submission per person). If no schedule is selected, the page falls back to the
legacy `shifts.csv` file and submissions stay in `preferences.json` only.

## 3. Collaborators submit preferences

Each shifter logs in (Fermilab SSO), opens **Preferences → Submit
Preferences**, and drags the shifts they are willing to take into ranked order
(top = most preferred). Ranking every shift is not required. Re-submitting
under the same name prompts to overwrite the previous ranking.

Progress can be watched on **Preferences → View Preferences** (one row per
person) and `/preferences/submissions` (raw log, with a download link for the
`-prefs.csv` file).

## 4. Run the solver

**Scheduler** page (`/schedule`):

1. **Stored schedule**: pick the same schedule from the dropdown
   (this replaces the shifts CSV upload).
2. **People CSV**: click **Browse server…** and select
   `<schedule-slug>-prefs.csv`.
3. Set the point constraints (target / min / max per person; α and the
   pass-2 bounds under *Advanced* if needed).
4. Click **Run Scheduler**.

Command-line equivalent, after downloading the two files:

```bash
mu2e-shift-scheduler solve \
    --shifts fall-2026.csv --people fall-2026-prefs.csv \
    --target 2 --min 1 --max 4 --out assignments.csv
```

## 5. Review and save the results

The results page shows fill rate, preference satisfaction, and per-person
load. If it looks right, click **Save Results…**, keep
**"Also save assignments to schedule"** checked, and save. The assignments
are stored on the schedule and a results JSON lands in server data storage.

Infeasible? The error names the capacity problem — widen min/max, lower the
target, or add people, then re-run.

## 6. Publish

The **Calendar** (the landing page after login) now shows the schedule's tab
with a name on every covered day ("Day: Bob" for multi-day blocks) and
"Empty" for unfilled slots; names link to contact info. Exports (shifts CSV,
assignments CSV/JSON) are on the calendar header and the Schedules page.

## Notes

- **One campaign at a time**: the preference page serves a single
  admin-selected schedule. Switching the selection starts filling that
  schedule's own `-prefs.csv`; earlier submissions stay tagged to their
  schedule.
- **Contact info**: the generated `-prefs.csv` has names and preferences
  only. To get contact popovers on the calendar, add optional
  `email`/`phone`/`institution` columns to the people CSV before solving —
  they are carried into the saved assignments.
- **Overwrite semantics**: schedule names are unique (case-insensitive);
  regenerating "Fall 2026" replaces its shifts and keeps saved assignments
  only for shift IDs that still exist.
