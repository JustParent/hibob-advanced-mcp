# Employee record updates: design

Date: 2026-10-07
Status: approved in conversation, awaiting written-spec review

## Goal

Let an HR user, through an agent, read and change any employee data HiBob's
API can write, phrased the way they think about it ("move Jane to Marketing
and make Sam her manager from 1 November", "add a £5k bonus for Raj"),
without knowing or caring whether a value is a plain field or a column of an
effective-dated table.

Success means:

- A change lands in the right HiBob endpoint with every other value intact.
- Nothing is written from a guess: an unknown or ambiguous field, value or
  person, and a missing effective date, come back as questions.
- The result says exactly what was written, what HiBob confirmed, and what it
  could not confirm.

## Decisions taken

| Question | Decision |
| --- | --- |
| Scope of data | Everything HiBob's API can write (option C), including pay and bank details. |
| Tables | Create new rows only. Editing or deleting existing rows is out of scope for now. |
| Confirmation | Write straight away once the request is complete (option B). The agent confirms with the user beforehand; the server still refuses anything incomplete or ambiguous. |
| Interaction style | Conversational (option A). No form-generation tool. |
| Matching | Exact, ignoring case. Ambiguity returns candidates, never a guess. |
| Effective date | Never defaulted. Missing → a `needs_input` question. |
| Tool shape | Two intent-level write tools plus two read tools (approach 1). |

## Out of scope

- Editing or deleting existing table rows (`PUT`/`DELETE .../{entry_id}`).
- Address (historical, no table endpoint), lifecycle (read-only; termination
  has `hibob_terminate_employee`), and the historical fields HiBob documents
  no write path for (Work Location, Employer, Disability status).
- Calculated fields (`fullName`, tenure, `fte`, `percentageOfAnnualSalary`)
  and document fields.
- Working patterns (employment `workingPattern`, `personalWorkingPatternType`):
  a row copies them as read, but they cannot be changed yet.
- Clearing a value (`null`), creating employees, adding named-list items,
  revoking Bob access.

## Background: how HiBob stores employee data

Facts below come from HiBob's API reference (read 2026-10-07) and from the
justparent repo's HiBob adapter (`justparent/bots/integrations/hibob.py`).
Anything marked **verify** is inferred and is checked in the sandbox before
the phase that depends on it (see "Verification").

- **Plain fields** are written with `PUT /people/{id}`, which behaves as a
  patch. The body is nested by category (`{"home": {"mobilePhone": ...}}`);
  `root.` fields go at the top level (`{"firstName": ...}`); custom fields go
  under `<category>.custom` (`{"work": {"custom": {"field_123": ...}}}`).
  10/min. Fields the service user cannot edit are dropped silently with a
  200. A 304 means nothing changed. Reads may lag a write by up to 20 s.
- **Historical fields** (`historical: true` in `GET /company/people/fields`)
  are the current row of an effective-dated table and cannot be written with
  `PUT /people`. justparent observed a silent 304 (**verify**: 304 or 400).
- **Single-current tables**: one row is in effect at a time.
  - Work: `/people/{id}/work`. Unique on effective date + site. Columns:
    `title` and `department` (list item IDs), `site` or `siteId`,
    `reportsTo: {id}`, `reason`, `customColumns`.
  - Employment: `/people/{id}/employment`. Unique on effective date.
  - Salary: `/people/{id}/salaries`. Create and delete only. Reads are
    10/min. Columns: `base: {value, currency}`, `payPeriod`, `payFrequency`,
    `customColumns`.
  - A new row must carry **every** column: HiBob stores an omitted column as
    empty (justparent; docs are explicit for `PUT`, silent for `POST`).
    `POST` returns no entry ID.
- **Record tables** hold several rows at once:

  | Record type | Write | Read | Date | Required | List values sent as |
  | --- | --- | --- | --- | --- | --- |
  | Variable pay | `POST /people/{id}/variable` | `GET` same | required | `amount`, `variableType`, `paymentPeriod` | value (`Bonus`) |
  | Entitlement | `POST /people/{id}/entitlement` | bulk | required | `entitlement`, `amount` | name |
  | Deduction | `POST /people/{id}/deduction` | bulk | required | `deduction`, `amount` | name |
  | Equity | `POST /people/{id}/equities` | `GET` same | none | `quantity`, `equityType` | **verify** |
  | Training | `POST /people/{id}/training` | `GET` same | none | `name` (**verify**) | ID |
  | Bank account | `POST /people/{id}/bank-accounts` | `GET` same | none | none documented | **verify** |
  | Dependent | `POST /people/{id}/dependents` | bulk | none | none documented | n/a |
  | Right to work | `POST /people/{id}/right-to-work` | bulk | none | none documented | **verify** |
  | Custom table | `POST /people/custom-tables/{id}/{table_id}` | `GET` same | none | `mandatory` columns from metadata | ID |

  Variable pay, entitlement, deduction, dependents and right to work return
  `{"entryId": n}`; the others return no body. Bulk reads are
  `GET /bulk/people/<table>?employeeIds=<id>` (**verify** exact paths).
  A custom-table `POST` body is `{"values": [{"column_<id>": value}]}`.
- **Separate endpoints**: work email `PUT /people/{id}/email` (20/min; HiBob
  re-sends verification); start date `POST /employees/{id}/start-date`
  (`{startDate, reason?}`, 20/min).
- **Permissions**: Edit on the field's category (or the table) under
  People's data > People's fields; reading every table row also needs
  View history. A table read the service user may only partly see reports
  `restricted_columns: {no_view_permission: [...],
  no_view_history_permission: [...]}`.
- **Quirks** (justparent): `includeHumanReadable` must be the string
  `"true"`; a wrong path or parameter returns HiBob's HTML login page with a
  200; a table read can return an empty body; a 401 or 403 must not be
  retried (more than 50 in 10 s blocks the IP for 5 minutes).

## Tools

All four take the employee as an ID (string or number), a work email or a
display name. ID and
email are resolved with `POST /people/{identifier}` (which also finds
inactive employees); a name is matched exactly, ignoring case and repeated
spaces, against a
directory of active employees, and several matches return candidates with
their email and title. Both write tools are omitted when `HIBOB_READ_ONLY`
is set, and send each write once, never retrying.

### `hibob_list_employee_fields(search?)` (read)

What can be changed and how. Built from `GET /company/people/fields` and
`GET /people/custom-tables/metadata`. Each field gives `id`, `label`,
`category`, `type`, `list` (the named list's name; item counts would cost a
list fetch per field against a 50/min limit) and `write`, one of:

- `field`: a plain update.
- `dated` with `table`: `work`, `employment` or `salary`; needs an effective
  date.
- `email` or `start_date`: has its own endpoint.
- `not_writable` with `reason`: calculated, lifecycle, address, no API,
  document.

Record types are listed with their columns (label, ID, type, list,
`required`) and whether they need a date. `search` keeps fields and record
types whose label contains it, ignoring case.

### `hibob_get_employee(employee, fields?, history?)` (read)

Current values of the requested fields (labels or IDs; when omitted: display
name, work email, job title, department, site, manager, start date and
status), each as `{"value", "display"}` using
`humanReadable: "APPEND"`. `history` names tables or record types whose rows
to include, newest first, each row with its display labels. Restricted
columns are reported, not hidden.

### `hibob_update_employee(employee, changes, effective_date?, reason?, allow_later_rows?)` (write)

`changes` maps a field label or ID to its new value. The response is one of:

- `{"status": "needs_input", "employee": {...}, "questions": [...]}`. Nothing
  was written. Each question gives the `argument` to supply, a `question` to
  put to the user, what it `applies_to`, and `candidates` where there are
  some.
- `{"status": "updated", "employee": {...}, "applied": [...],
  "unconfirmed": [...], "warnings": [...]}`. Each applied entry gives
  `field`, `from`, `to` and `via` (`"field"`, `"email"`, `"start date"`,
  or `"work row from 2026-11-01"`).
- `{"status": "partial", ..., "applied": [...], "failed": {...},
  "not_sent": [...]}`.
- An error beginning `Error:` for anything a question cannot fix.

`reason` goes into the `reason` column of any work or employment row the
call adds, and into the start-date request.

### `hibob_add_employee_record(employee, record_type, values, effective_date?)` (write)

Adds one row to a record table or a custom table. `record_type` is a label
("Variable pay", a custom table's name) or ID; `values` maps column labels or
IDs to values. Missing required columns, a missing required date, and
unmatched or ambiguous values return `needs_input` questions, with the
column's options when it is list-backed. On success:
`{"status": "added", "employee", "record_type", "entry_id"?, "row",
"verified"}`.

## Resolution and routing

### Fields

Field metadata and custom-table metadata are cached for five minutes. A field
is matched by ID in any of HiBob's spellings (`work.title`, `/work/title`,
`firstName` for `root.firstName`) or by its label, exactly, ignoring case. A
label several fields share (for example "Type") is a question whose
candidates read `Category > Label`.

### Routing a change

In order:

1. `root.email` → email endpoint, with a warning that HiBob re-sends
   verification.
2. The start-date field → start-date endpoint.
3. Calculated or document fields → refused.
4. `historical: true`:
   - `work.*` → a column of a new work row: `work.title` → `title`,
     `work.department` → `department`, `work.site` → `siteId`,
     `work.reportsTo` → `reportsTo: {id}`, `work.customColumns.column_N` →
     `customColumns.column_N`.
   - `payroll.employment.*` → a column of a new employment row: contract,
     type, salary pay type, FLSA code, holiday calendar.
   - `payroll.salary.*` → a column of a new salary row: `payment` → `base`
     (an amount with its currency), pay period, pay frequency. The derived
     yearly and monthly payments are refused.
   - anything else → refused as not writable.
5. Everything else → a plain field in the one `PUT /people/{id}` body.

The field-ID-to-column map is inferred from the docs and is fixed against the
sandbox's real metadata in phase 1.

### Values

- **List, multi-list, hierarchy-list**: a name, a path label
  (`Spain > Madrid`) or an ID, resolved against the field's named list with
  the existing `resolve_list_values`. Sent as the item's ID, or its name
  where the endpoint wants names (see the record table).
- **Employee reference** (`reportsTo` and custom employee fields): ID, email
  or display name, resolved as for `employee`.
- **Date**: `YYYY-MM-DD`, a real day (`iso_date`).
- **Number**: a number or a plain decimal string, as the position tools
  accept.
- **Currency**: `{"value", "currency"}`; a bare number when the row being
  copied supplies the currency; otherwise a question asking for it.
- **Boolean, text**: as given.
- **`null`**: refused.

### Effective dates

- Any change routed to a work, employment or salary row with no
  `effective_date` → one question covering all of them: "From what date
  should Jane's job title, manager and salary change?". It is returned with
  any other questions from the same call (an ambiguous value, say), so the
  user answers them together. The checks that need the date (base row,
  same-day and later rows) run once it is given.
- An `effective_date` other than today alongside a plain-field change →
  refused, naming the plain fields: HiBob changes them immediately and
  cannot schedule them.
- Records: required for variable pay, entitlement and deduction; refused for
  the rest.

## Write mechanics

### A new dated row (work, employment, salary)

1. Read the table (`GET /people/{id}/<table>`, `includeHumanReadable` as the
   string `"true"`). Rows come back oldest first.
2. If the read reports any `restricted_columns`, refuse, naming the columns
   and the permission to grant (View, or View history). A row built from a
   partial read would blank the hidden columns.
3. The **base row** is the latest row dated before the new effective date.
   The row marked `isCurrent` is not used: a row may be added in the past or
   the future. Two rows sharing that latest date → refused (no single row to
   copy). No such row → refused: there is nothing to carry forward. The one
   exception is a salary table with no earlier row: the first salary row has
   no base, so the changes must give the amount with its currency and the
   pay period, or a question asks for them.
4. A row already dated that day, at any site → refused: changing an existing
   row is out of scope.
5. Any row dated after the new one that holds a different value in a column
   being changed → a `needs_input` question naming it and what it still says
   ("A work row from 2027-01-01 still has the title 'Analyst', so this
   change lasts until then. Go ahead?"), answered with
   `allow_later_rows: true`. A later row that already holds the new value
   needs no question.
6. Build the new row: copy every non-null column of the base row except
   bookkeeping (`id`, `effectiveDate`, `endEffectiveDate`,
   `activeEffectiveDate`, `isCurrent`, `canBeDeleted`, `change`,
   `creationDate`, `modificationDate`, `workChangeType`, `humanReadable`,
   `changedBy`); reduce `reportsTo` to `{id}`; send the ID beside the label
   it describes, not both: drop `site` when `siteId` is present,
   `calendarName` when `calendarId` is, `standardWorkingPattern` when its
   ID is. Derived employment columns (`fte`, `weeklyHours`,
   `hoursInDayNotWorked`, the actual and site working patterns) are copied
   as read, so the new row stays consistent with its base whether HiBob
   recomputes or stores them; they cannot be changed here. Custom columns
   are sent in both shapes HiBob is known to take: nested under
   `customColumns` as its reference documents, and as top-level `column_*`
   keys as justparent's integration sends them. Lay the changes over the
   copy, merging nested objects key by key, then set `effectiveDate` and,
   on work and employment rows, `reason` (HiBob's salary table has no reason
   column; the result says the reason was not recorded there).
7. A bare amount on a salary change takes its currency from the base row;
   with no base row a question asks for it. `null` is still refused.
8. If every changed column already holds the requested value in the base row,
   no row is added and the result says so.

### Findings from the demo tenant (2026-10-07)

From bulk reads of 119 employees' rows and a restoring write probe:

- Rows come back oldest first, with `isCurrent` and `endEffectiveDate`; no
  employee had two rows on one date.
- Work: `title` and `department` are strings (list item IDs, equal to the
  names for built-in lists), `siteId` an integer beside `site` (the name),
  `reportsTo` an object with a string `id` and display fields; no custom
  columns anywhere.
- Employment: `contract` is "Full time" (not the documented "Full-Time");
  `calendarId` is an integer; type, salary pay type, FLSA code and working
  patterns were null; `fte`, `weeklyHours`, `hoursInDayNotWorked`,
  `actualWorkingPattern` and `siteWorkingPattern` were always present.
- Salary: `base {value, currency}`, `payPeriod`, `payFrequency`; 44 of 119
  employees had rows; the salary read is limited to 10 a minute.
- List IDs are integers for `site` and `calendar`, strings elsewhere; list
  names are case-sensitive.

Verified live with writes (dated 2030, on one employee):

- A work row sent with `siteId` alone (no `site`), nulls omitted, is accepted
  and read back intact; a later row copies from the row before its date.
- An employment row copying every non-null column, derived ones included, is
  accepted and read back intact.
- HiBob refuses a salary row without a pay frequency ("Missing pay
  frequency") although its reference lists only the amount and pay period as
  required, so a first salary row needs all three.
- With no earlier salary row HiBob counts the first row as current whatever
  its date; the tool warns when that row is future-dated.
- Custom columns could not be checked: no table in the tenant has any.

### A record

Build the row from the resolved values only; nothing is copied. A duplicate
HiBob would reject is refused first: deduction on effective date and type,
variable pay likewise (**verify**). Read the table back afterwards (bulk read
where there is no single-employee read) and find the new row by `entryId`
where HiBob returns one, otherwise by matching the values sent.

### Order, atomicity, read-back

- Every lookup and check runs before the first write. Any problem: nothing is
  written.
- Writes go in a fixed order: new table rows (work, employment, salary), the
  plain-field `PUT`, start date, email last (it sends an invitation). Each is
  sent once. If one fails, the rest are not sent and the response is
  `partial`. Nothing is rolled back.
- **Table rows** are read back and found by effective date; every column
  sent is compared except `reason` and the derived employment columns.
- **Plain fields** are read back with `POST /people/{id}`. A mismatch is
  read again, up to three reads over about 15 seconds, because HiBob both
  lags and silently drops fields the service user cannot edit. Fields that
  still differ go in `unconfirmed`, with both explanations.
- **304** from the `PUT` is handled explicitly (the client currently treats
  it as success with no body): "HiBob changed nothing: the values were
  already set, or these fields cannot be changed this way."

## Errors and permissions

- A 403 on an employee call names the permission for the category involved,
  using the field's `categoryDisplayName`: "People's data > People's fields:
  Edit on Payroll" for a write, "View history" for a table read. Bank
  accounts and other sensitive fields say so.
- A 400 keeps HiBob's message and points to `hibob_list_employee_fields`.
  Known messages are explained: "Duplicate effective date for work" (a row
  exists that day), "Work entry must include site".
- A 404 on `/people/` or `/employees/` points to `hibob_find_employee`.
- A 2xx whose body is HTML is an error ("HiBob answered with its login page:
  the endpoint or a parameter is wrong"), raised in the client so every tool
  benefits.
- Writes, 401s and 403s are never retried (already true of the client).

## Module layout

- `people_fields.py` (pure): normalise field and custom-table metadata;
  match a field by ID or label; classify its route; map a field ID to its
  table column; the record-type registry (endpoint, read method, date rule,
  required columns, list-value form, uniqueness).
- `employee_rows.py` (pure): choose the base row; detect a same-day or later
  row; check restricted columns; carry forward and merge; build the nested
  `PUT /people` body; compare a read-back.
- `people_api.py`: the reads, with metadata and named lists cached: field
  metadata, custom-table metadata, a named list, one employee's fields
  (`POST /people/{identifier}`), one of their tables.
- `employee_directory.py`: resolve an employee or employee reference by ID,
  email or name (directory cached five minutes).
- `employee_values.py` (pure): turn a given value into what HiBob takes, by
  field type, raising a question where only the user can settle it.
- `employee_updates.py`: `hibob_update_employee` and
  `hibob_add_employee_record`.
- `employees.py`: gains `hibob_list_employee_fields` and
  `hibob_get_employee` beside `hibob_terminate_employee`.
- The employee tools get their own `NamedListCache` for field metadata,
  custom-table metadata, named lists and the directory.

Tool counts after all phases: 20 read, 13 write.

## Testing

- Unit tests for every pure function: matching, routing, column mapping,
  base-row choice, same-day and later rows, restricted columns, merge,
  `PUT` body nesting, read-back comparison.
- Tool tests against `respx`: each route; each `needs_input` (missing date,
  missing currency, ambiguous field, value or person, later rows, missing
  required column); each refusal (same-day row, restricted columns, not
  writable, scheduled plain field, no base row); write order and `partial`;
  one send per write; 304; read-back retries ending in `unconfirmed` (sleep
  injected); `entryId` handling; HTML responses.
- Fixtures from a read-only sandbox pass, personal data scrubbed: field
  metadata, custom-table metadata, one employee's work, employment and
  salary tables, one people read with `humanReadable: "APPEND"`.
- Gating, stdio and README tool counts updated in each phase.

## Verification in the sandbox

Needs a working service-user token (the one in `.env` currently returns 401
`tokenNotMatch`). Reads are free to run. Each write below needs the user's
explicit OK at the time, is sent once from a script that blocks any other
write, and is described before it runs.

| Phase | Question | How |
| --- | --- | --- |
| 1 | Real field IDs for work, employment and salary columns; which fields are calculated | Read metadata |
| 1 | Shape of `POST /people/{id}` reads (slash keys or nested) | Read one employee |
| 1 | People search with no filter returns the directory | Read |
| 1 | Bulk read paths for entitlement, deduction, dependents, right to work | Read |
| 2 | `PUT /people` with a historical field: 304 or 400 | One write |
| 2 | How long a plain-field write takes to show in a read | Same write |
| 3 | Does a work row sent with only the changed column copy the rest forward | One write |
| 3 | `customColumns` nested or flattened; `site` with `siteId` | One write |
| 4 | Variable pay uniqueness; equity, training, bank-account list forms | Read metadata, one write each if needed |

## Delivery

One PR per phase, after the terminate PR merges:

1. **Read**: `hibob_list_employee_fields`, `hibob_get_employee`, directory,
   sandbox fixtures.
2. **Plain fields**: `hibob_update_employee` for plain fields, email and
   start date; dated fields refused with "not supported yet".
3. **Dated rows**: work, employment and salary rows in
   `hibob_update_employee`.
4. **Records**: `hibob_add_employee_record`.
