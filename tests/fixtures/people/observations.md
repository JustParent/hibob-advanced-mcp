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

## Mandatory custom-table column (live, "Pet ownership" with a mandatory currency column "Cost", 2026-10-07)
- Metadata marks it `mandatory: true`, type `currency`. A POST without it is refused: 400 "column_... column is mandatory" (nothing written). A PUT of other columns on an older row that has no value for it is fine (200, patch semantics); a PUT that sets it to null is refused with the same 400.
- A currency value is `{"value": 12, "currency": "GBP"}`; rows read back that way, with `humanReadable` "£12.00".
- HiBob silently drops a currency it cannot read: a bare number (25) and an unreal code ("pounds") both answered 200, passed the mandatory check, and stored Cost as null. An unreal three-letter code ("XYZ") is dropped the same way. The tool rejects a bare number and a non-three-letter code before sending; for a made-up three-letter code its read-back reports `verified: false` and names the empty required column.
- justparent surfaces HiBob's "column is mandatory" message when a create lacks it (after its flat-then-wrapped attempts).
- Rows left on david@harriethq.com (Pet ownership): Goldie 16588405, "API check bare cost" 16588451, "API check bad currency" 16588452, "API check no cost"-style attempts were refused, "JP with cost" 16588453, "API check XYZ" 16588462 (Cost empty); Rex 16587837 now has Cost 5 GBP.

## Address (live, david@harriethq.com, 2026-10-07)
- Address is a real effective-dated table (rows with `id`, `effectiveDate`, `endEffectiveDate`, `isCurrent`, `canBeDeleted`, `change`, `line1`, `line2`, `city`, `postCode`, `country`, `usaState`, `activeEffectiveDate`, `customColumns`), but only a bulk read exists: `GET /bulk/people/address` answers 200 JSON for the whole tenant (44 of 115 employees had a row). It is not in HiBob's docs or scopes list and ignores an `employeeIds` filter.
- No per-employee route exists. `GET /people/{id}/address` (and `addresses`, `home-address`, `address-history`, and ~20 other names) answers 200 HTML (the web app's fallback); `POST`/`PUT`/`DELETE` on `/people/{id}/address[/{entry}]` answer 404 HTML. Controls: `/people/{id}/work` answers JSON, and `DELETE /people/{id}/work/1` answers a JSON 400 "Entry not found".
- `PUT /people/{id}` with address keys answers 304 and stores nothing, in every shape tried: nested, dotted, slash (`/address/city`), `table.address`, an array or `values` wrapper, with `activeEffectiveDate` or a top-level `effectiveDate`, on an employee with no address row at all. An invalid country, a made-up field (`address.bogus`) and a made-up category all answer 304 as well, so HiBob drops the address keys without validating them; a bad list value on a writable field (`personal.pronouns`) answers a JSON 400 and a bad date a JSON 400.
- Slip: a control PUT of `employee.buddy` = "123" answered 200 and stored it (HiBob does not check the id); restored to null.

### Correction: the web app's own route works with the service user (2026-10-07, same day)
The console's request (user's screenshot) is `POST https://app.hibob.com/api/table/address/address/{employeeId}`. The same path on `api.hibob.com` (no `/v1`) accepts the service user's basic auth, so the public API host does reach it; `/v1/table/address/address/{id}` is a 404 HTML page. It is undocumented and internal, so HiBob may change it without notice. Whether an OAuth bearer token is accepted there is untested.
- POST creates a row: 200, empty body. Body is flat (no `values` wrapper): `effectiveDate`, `line1`, `line2`, `city`, `postCode`, `usaState` (the State/Province/Region text, e.g. "England"), `country` (list `countries`, by name), plus the console's `endEffectiveDate`, `change`, `change.reason`, `workChangeType`, `gglAddress` (all null; not needed).
- GET on the same path (`/api/table/address/address/{id}`) answers JSON `{"values": [rows]}` for one employee, so no bulk read is needed. `.../history` and `/api/table/address/{id}` are 404.
- It replaces the row wholesale like work/employment: a POST of only `effectiveDate` + `city` stored a row with line1, postCode, country and usaState all null. Every column must be carried forward from the row in force.
- A missing `effectiveDate` is not refused: it answered 200 and stored a row dated today, which became the current address. The tool must ask for the date.
- A country not in the list: 400 JSON `{"error": "The value \"Not A Country\" isn't valid in list countries.", "errors": {"country": ...}}`; nothing written.
- A repeat effective date: 400 JSON `{"key": "exception.history.duplicated.bulk", "error": "Duplicate effective date for address, please update the effective date."}`.
- A row with a future date is `isCurrent: false` until that day; `address.*` in `POST /people/{id}` shows only the current row.
- A reason is stored only when nested: `{"change": {"reason": "..."}}` is kept (`change.reason` on the row); a flat `"change.reason"` key and a bare `"reason"` key are accepted (200) and dropped.
- GET on the route answers the same whether or not it carries `Content-Type: application/json` or `includeHumanReadable=true`, and `app.hibob.com` accepts the service user too.
- hibob_get_employee (history "address") and hibob_update_employee (City, Address line 1, Zip/Post/Postal code, Country, State/Province/Region, with an effective date and a reason) ran live through the tools: rows carried forward, the country matched by name against the `countries` list, a repeat date refused before sending, a missing date asked for, read-back confirmed. An unmatched country is a question, not an error.
- Rows left on david@harriethq.com: the user's own console row 1000020261008 (17 Baalbec Road, 2026-10-08), 1000020261007 ("NoDate", dated today, current), and rows dated 2026-10-12 to 2026-10-17 from the tool runs and reason probes ("2 Live Lane", Liveville/Liveton/Livetown). The 2026-10-09 and 2026-10-10 probe rows no longer exist (removed in the UI).
