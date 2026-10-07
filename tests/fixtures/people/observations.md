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
