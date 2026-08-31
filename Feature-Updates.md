The following are features that should be added to the shift scheduler

# Schedule Names

* Each schedule should allow for a global name to be specified.  For example "Fall 2026" and for classification like "General Shifts" or "Oncall DAQ Experts"
* The names should enforce a uniqueness critera so submitting a new "Fall 2026" would overwrite the current one, but submitting "Fall 2026 v2" would create a new schedule.
* The classifications should match a taxonomy set in the admin configuration pages.  It should be possible to add new classification items.

# View origanization

# Calendar Views

* There should be a calendar view pages for the current month.  
* On the view their should be a tab for "General Shifts", "Run coordinators", "Oncall DAQ experts", etc... This should match the taxonomy.  Which tabs are displayed should be selectable from the admin configuration pages.
* The calendar view should be able to be changed to see other months and years.
* The calendar that is shown should be set in the admin configuration pages.
* There should be an option to see "Today", "Week", "Month".  Month is the default

* From the admin configuration view it should be possible to set the "default" calendar that is displayed
* Calendars should be selectable from the storage in the container or from the users machine.

* If a shift is assigned to a person then their name should be displayed.
* If a shift is not assigned to anyone then it should be displayed as "Empty"

The default view that is seen after login should be the current calendar view.

# Shift Ranges

* Each shift period should have a name associated with it.  For example "Day", "Evening", "Night" or "DAQ Expert Day", "DAQ Expert Night"
* Shifts can be specified to be for a single day or for multiple days, i.e. monday-thurday friday-sunday
* When displaying the assignments for a multi-day shift period on the calendar, a name should be placed on the calendar for each day they are assigned.  For example: if the shift is "Day" monday-friday then if Bob is assigned this shift, the calendar should list "Day: Bob" on Monday, "Day: Bob" on Tuesday, etc...
* The name that is displayed should be a link to the person's contact information.

# File selection

* Files should be selectable from the user's machine and from the storage in the container.
* The browser buttons on all pages should allow for these selections.
* There should be an upload function that allows files to be push to the storage in the container.
* There should be a download function that allows for files to be downloaded from the storage in the container.
* There should be an export function that allows for results to be downloaded.

# Shift Setup Page

There should be a page that allows an admin to contruct a shift schedule.  On this page the admin will specify a start date and stop date for the schedule.  Then they will specify the number of shifts per day, the name of each shift, and the time range for each shift.  Then they will specify the shift length in days and the repetition rate of the shift (day, week, 2 week, Month) Then the should select the days of the week (for multi-day shifts) from a list of checkboxes for the days of the week.  The last thing they will set is the "weight" value for the shift.  This is a floating point number.

When the admin is done configuring the setup they will hit the "Generate schedule" button to create a schedule that can be loaded.

