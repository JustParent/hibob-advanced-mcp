# People API observations (Harriet HiBob Demo tenant, api.hibob.com, 2026-10-07)

Captured read-only from inside the justparent web container (integration 3); employee
records scrubbed. Write probe made on david@harriethq.com's own record; every value restored.

## Reads
- fields metadata: 200, 239 fields
- custom-tables metadata: 200
- people search with no filters: 200 
-   directory size: 115; first entry keys: ['/root/displayName', '/root/email', '/root/id', '/work/title', 'displayName', 'email', 'humanReadable', 'id', 'work']
- POST /people/{id} (employee_read.json): 200; top-level keys: ['/internal/status', '/root/displayName', '/root/email', '/root/firstName', '/root/id', '/work/site', '/work/startDate', 'displayName', 'email', 'firstName', 'home', 'humanReadable', 'id', 'internal', 'work']
- POST /people/{id} (employee_read_raw.json): 200; top-level keys: ['/internal/status', '/root/displayName', '/root/email', '/root/firstName', '/root/id', '/work/site', '/work/startDate', 'displayName', 'email', 'firstName', 'home', 'id', 'internal', 'work']
- POST /people/{email}: 200
- POST /people/<unknown id>: 404 
- GET /people/{id}/work: 200 application/json
- GET /people/{id}/employment: 200 application/json
- GET /people/{id}/salaries: 200 application/json
- GET /bulk/people/entitlement: 200 application/json
- GET /bulk/people/entitlements: 404 None
- GET /bulk/people/deduction: 200 application/json
- GET /bulk/people/deductions: 404 None
- GET /bulk/people/dependents: 200 application/json
- GET /bulk/people/right-to-work: 200 application/json
- GET /bulk/people/variable: 200 application/json
- GET /bulk/people/work: 200 application/json
- GET /people/{id}/variables (wrong path): 200 text/html; charset=UTF-8

## Write probe
- named list terminationReason 404 None 
- named list terminationreason 200 application/json 11
- originals: {"home.mobilePhone": null, "about.hobbies": [], "personal.pronouns": null, "employee.buddy": null, "employee.workLocationId": null, "work.site": "London (Demo)", "work.title": null}
- list sizes hobbies/pronouns/locations: 42 9 0 | location item sample: []
- PUT text home.mobilePhone: 200 '' -> read "07700 900123" after 0.1s
-   restore home.mobilePhone: 200 '' -> now null
- PUT multi-list about.hobbies ids ['Animals', 'Be A Background TV Star']: 200 '' -> read ["Animals", "Be A Background TV Star"] after 0.7s
-   restore about.hobbies: 200 '' -> now []
- PUT list personal.pronouns id 'He / Him': 200 '' -> read "He / Him" after 0.1s
-   restore personal.pronouns: 200 '' -> now null
- PUT employee-reference employee.buddy as id string: 200 '' -> read "3987598587553907167" after 0.0s
-   restore employee.buddy: 200 '' -> now null
- PUT historical work.title via PUT: 304 '' -> read null after Nones
- PUT text work.site via PUT: 304 '' -> read null after Nones
- final: {"home.mobilePhone": null, "about.hobbies": [], "personal.pronouns": null, "employee.buddy": null, "employee.workLocationId": null, "work.site": "London (Demo)", "work.title": null}

## Conclusions
- People reads return BOTH slash keys (only for non-null values, as {"value": ...}) and nested
  category objects; humanReadable is nested. Root fields' jsonPath has no 'root.' prefix.
- Metadata has no 'calculated' flag; derived fields must be listed explicitly.
- work.site is plain text and answers 304 to PUT; the writable site is work.siteId (list_id,
  historical). work.reportsTo has type 'employee'.
- PUT /people with a historical field answers 304 and stores nothing.
- Writes are visible in reads within 0.7 s.
- Named-list names are case-sensitive: 'terminationreason', not 'terminationReason'.
- People search with no filter returns the whole directory (115 employees).
- Bulk read paths: /bulk/people/{entitlement,deduction,dependents,right-to-work,variable,work}.
- A wrong path (/people/{id}/variables) answers 200 with text/html.

## Tool runs (hibob_update_employee / hibob_get_employee, live, all values restored)
- A combined PUT /people answered 200 while silently not storing employee.jobRoleId; alone,
  employee.jobRoleId and employee.jobFamilyId answer 304 as string or number (they follow the
  job profile), so the Jobs fields are routed as not writable.
- Read-back confirmed text, list, multi-list and employee-reference writes on the first read.
- Repeating an identical write answers 304, reported as "unchanged".
- POST /employees/{id}/start-date with {"startDate", "reason"} is reflected in work.startDate at once.
- A people read asking for an employee.* field returns an "employee" category object beside the
  record's fields: it is not a wrapper.
- Not exercised: PUT /people/{id}/email (it sends a verification email and changes the login).

## Dated rows (live, david@harriethq.com, 2026-10-07; rows dated 2030, none deleted)
- POST /people/{id}/work with `siteId` alone (no `site`), nulls omitted and `reason` accepted: 200; the new row was read back with every column as sent. A second row copied department, manager and site from the first row (base chosen by date, not the current row).
- POST /people/{id}/employment with every non-null column copied, including the derived `fte`, `weeklyHours`, `hoursInDayNotWorked`, `actualWorkingPattern` and `siteWorkingPattern`: 200, nothing differed on read-back.
- POST /people/{id}/salaries without `payFrequency`: 400 "Missing pay frequency" (the reference lists only `base` and `payPeriod` as required). With all three: 200. The salary table has no reason column.
- With no earlier salary row, HiBob treated the first salary row as `isCurrent: true` although it was dated 2030, so the employee's current `payroll.salary.payment` changed at once. The work and employment rows dated 2030 stayed non-current.
- Real labels: "Employment contract", "Salary pay period", "Salary pay frequency", "Base salary", "Personal mobile".
- No custom columns exist on any table in this tenant, so the nested-and-top-level custom column shapes are still unverified.
- Rows left on david@harriethq.com: work 1000020300101 (2030-01-01) and 1000020300102 (2030-01-02); employment 1000020300101 (2030-01-01); salary 1000020300101 (2030-01-01) and 1000020300102 (2030-01-02). Salary now reads 100000 USD Annual / Monthly.

## Records (live, david@harriethq.com, 2026-10-07; nothing deleted)
- Entitlement, deduction and variable pay (dated 2030-01-01) were added and read back intact; HiBob answered each with `{"entryId": n}`. `payType` is the list behind variable pay's type ("Bonus"); `variablePayPeriod` behind its payment period. A second identical entitlement call reported the existing record instead of adding one. A second deduction on the same date and type (different amount) was refused by HiBob: 400 "Duplicate effective date for deduction, please update the effective date 2030-01-01". Duplicate entitlements and variable pay were not tried.
- Training (lists `trainingName`, `trainingStatus`, `trainingFrequency`) added and read back; the write answers 200 with no body and the tool found the row by diffing the table.
- Equity: `equityType`, `grantType` and `grantStatus` are lists (`equityTypes`: Options, RSU, Stock award; `grantTypes`; `grantStatuses`); a free-text equity type is refused ("API check's not a valid equityTypes definition"). 200 with no body.
- Bank accounts: HiBob refuses a flat body ("Missing required field: values"), contrary to its reference; the row goes in `{"values": [row]}` like custom tables. Account type list `bankaccounttype` is Current/Savings/Other (the reference says Checking/Savings). A bank account with only bank name, nickname and account type was added and read back. Rows read back carry a `humanReadable` copy of each value, so masking covers it too.
- Not written live: any account, IBAN, routing or document number (a standing rule of the executor, even for test data); right-to-work records (they also set the right-to-work expiry field); dependents; custom tables (none exist in the tenant). Their bodies are as documented, except bank accounts above.
- Rows left on david@harriethq.com: entitlement 26839977 (2030-01-01), deduction 56858716 (2030-01-01), variable pay 2185610 (2030-01-01), training 1879438, equity 544445, bank account 16571610 (bank name and nickname "API check", no numbers).

## Manager and change type (live, david@harriethq.com, 2026-10-07)
- "Manager" (`work.manager`, an employee reference) and "Reports to" (`work.reportsTo`) are the same value; a work row holds it once as `reportsTo`, and setting it through "Manager" was stored and read back. Both were wrongly refused as calculated before.
- "Change type" (`work.workChangeType`, list `workChangeType`) is accepted on a work POST and read back ("Promotion"); a row written without it is tagged "Other".
- Row left on david@harriethq.com: work 1000020300103 (2030-01-03: title CEO, manager Alan Tullin, change type Promotion).

## Custom table "Pet ownership" (live, david@harriethq.com, 2026-10-07)
- Metadata: `GET /people/custom-tables/metadata` lists it (`root__table_1791380280851`, category `root`) with columns Name (text) and Species (list). A list column's list is the named list `<table id>.<column id>` (dotted), fetched like any other; its item IDs are numeric strings.
- POST: a flat body gets 400 "Missing required field: values"; `{"values": [row]}` works (200, no body), settling the docs conflict: custom tables need the wrapper, as bank accounts do.
- PUT: a flat body works and patches (the untouched column kept its value). A wrapped PUT answers 200 but changes nothing (the row kept its earlier name), so a PUT must stay flat.
- Rows come back with `id`, `changedBy`, the columns at the top level and a `humanReadable` copy.
- hibob_add_employee_record added rows by label ("Name", "Species": "dog" resolved to the list item), found the new row by diffing the table, treated a repeat call as already present, and answered a species that matched nothing exactly ("Fish") with the near candidate ("Goldfish").
- GET of a custom table, like bank accounts, answers 400 HTML when sent with `Content-Type: application/json`; justparent's base client sends it on every request, so its custom-table reads failed until it got an opt-out.
