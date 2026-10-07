# hibob-advanced-mcp

An MCP server for HiBob's [Workforce Planning API](https://apidocs.hibob.com/reference/workforce-planning), tasks, employee termination, and [Reports API](https://apidocs.hibob.com/reference/reports).

This complements a standard HiBob HRIS integration. Common HRIS functionality (people, time off, documents) belongs in the main integration; this server adds planned positions, openings, budgets, tasks and access to saved reports.

It runs over stdio, is installable with `uvx`, and authenticates with a HiBob **API service user**.

## HiBob setup

1. In HiBob, go to **Settings → Integrations → API service users** and create a service user. HiBob shows the **service user ID** and **token** once — copy both now, as they cannot be retrieved later.
2. Create (or reuse) a permission group containing that service user, and grant it:

   **Features → Workforce planning → Position management → Manage positions**

   To use the tasks tools, also grant the service user access to the **Tasks API** (read, and complete for `hibob_complete_task`).

   To terminate employees with `hibob_terminate_employee`, grant **People's data → People's fields → Edit** on the **Lifecycle** category, plus **View** on employees' names and work emails so the employee can be looked up.

   Reports need **Features → Reports → View reports according to people's data access rights**, plus access to the employees and fields included in each report. Reports containing formulas also need **Features → Formulas in Grids → View formulas in grids**. Reports do not require the workforce planning permission.

   Service users have no permissions by default. The server identifies the relevant permission when an endpoint denies access.
3. If your HiBob account restricts API access by IP address, allow the outbound IP of wherever this server runs.

Read-only workforce planning use still needs the same workforce planning grant. Use `HIBOB_READ_ONLY=true` (below) to omit employee/task/workforce mutation tools; report generation and downloads remain available.

## Configuration

| Environment variable | Required | Description |
| --- | --- | --- |
| `HIBOB_SERVICE_USER_ID` | yes | Service user ID (the Basic auth username). |
| `HIBOB_SERVICE_USER_TOKEN` | yes | Service user token (the Basic auth password). |
| `HIBOB_API_HOST` | no | Defaults to production (`api.hibob.com`). Set `api.sandbox.hibob.com` for HiBob's sandbox. A pasted URL such as `https://api.sandbox.hibob.com/v1` is accepted; only the hostname is used. |
| `HIBOB_READ_ONLY` | no | `true`, `1`, `yes` or `on` registers only the eighteen read tools; the eleven write tools are not exposed at all. |

Standard proxy variables (`HTTPS_PROXY`, `ALL_PROXY`) are honoured. A SOCKS5 proxy needs the optional `socks` extra — see the install line below.

## Running it

Pinned to a commit, which is how it should be deployed:

```bash
uvx --from 'git+https://github.com/JustParent/hibob-advanced-mcp@<GIT_SHA>' hibob-advanced-mcp
```

From a local checkout, during development:

```bash
uvx --from . hibob-advanced-mcp --test
```

`--test` prints the version, the resolved API base URL, whether credentials are set (never their values), the read-only state, and every registered tool, then exits. It verifies an install without needing an MCP client or live credentials.

With a SOCKS5 proxy:

```bash
uvx --from 'git+https://github.com/JustParent/hibob-advanced-mcp@<GIT_SHA>[socks]' hibob-advanced-mcp
```

### Claude Desktop

```json
{
  "mcpServers": {
    "hibob-workforce-planning": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/JustParent/hibob-advanced-mcp@<GIT_SHA>",
        "hibob-advanced-mcp"
      ],
      "env": {
        "HIBOB_SERVICE_USER_ID": "<service user ID>",
        "HIBOB_SERVICE_USER_TOKEN": "<service user token>"
      }
    }
  }
}
```

### Plugging into a sandboxed MCP integration

For a host that runs MCP servers as sandboxed subprocesses using the Claude Desktop config shape, the integration config is:

```json
{
  "server_type": "sandboxed",
  "sandbox_command": "uvx",
  "sandbox_args": [
    "--from",
    "git+https://github.com/JustParent/hibob-advanced-mcp@<GIT_SHA>",
    "hibob-advanced-mcp"
  ],
  "sandbox_runtime": "python",
  "auth_type": "none",
  "sandbox_env": {
    "HIBOB_SERVICE_USER_ID": "<service user ID>",
    "HIBOB_SERVICE_USER_TOKEN": "$SECRET_KEY"
  }
}
```

Paste the service user's **token** into the integration's secret key field: `$SECRET_KEY` is substituted with it inside the sandbox, so the token is never stored in the config itself. The service user ID is not a secret and goes in literally.

No `--with 'mcp<2'` argument is needed — this package pins the MCP SDK itself.

## Tools

Field IDs are passed as flat mappings, for example `{"/position/fte": 100}`. The `/position/` prefix may be omitted (`{"fte": 100}`). The server wraps values into HiBob's `{"value": ...}` envelope for you, and flattens search results back out.

### Read

| Tool | HiBob endpoint | Rate limit |
| --- | --- | --- |
| `hibob_list_workforce_fields` | metadata for `position`, `positionOpening` or `positionBudget` | 50/min |
| `hibob_get_workforce_form` | metadata for each section of the form, plus `GET /company/named-lists/{name}` for each list a field draws from | 50/min |
| `hibob_get_company_named_lists` | `GET /company/named-lists/{name}`, or `GET /company/named-lists` summarised to names and sizes | — |
| `hibob_resolve_list_values` | metadata for the object, then `GET /company/named-lists/{name}` for the field's list, both cached | 50/min each |
| `hibob_search_positions` | `POST /objects/position/search`; a free-text `query` is matched locally against title, code, department, site, job profile and holder | 100/min |
| `hibob_search_position_openings` | `POST /positions/position-openings/search` | 100/min |
| `hibob_get_openings_for_positions` | `POST /positions/position-openings/search`, every page | 100/min |
| `hibob_get_positions_under` | `POST /objects/position/search`, every position, walked in memory | 100/min |
| `hibob_search_position_budgets` | `POST /positions/position-budget/search`, plus `POST /objects/position/search` to name each budget's position | 100/min |
| `hibob_get_position_costs` | `POST /objects/position/search`, then `POST /positions/position-budget/search` for the budgets they reference | 100/min |
| `hibob_summarize_position_costs` | the same two searches, company-wide, aggregated in memory | 100/min |
| `hibob_list_open_tasks` | `GET /tasks` (HiBob caps this at 5,000 tasks, with no pagination) | — |
| `hibob_find_employee` | `POST /people/search` on work email; returns each match's ID, name and email | — |
| `hibob_get_employee_tasks` | `GET /tasks/people/{id}`, optionally filtered to open or closed | — |
| `hibob_list_reports` | `GET /company/reports`, permission-filtered saved report metadata | 20/min |
| `hibob_download_report` | `GET /company/reports/{reportId}/download`, JSON/CSV/XLSX | 20/min |
| `hibob_generate_report` | `GET /company/reports/{reportId}/download-async`, CSV/XLSX | 20/min |
| `hibob_download_generated_report` | `GET /company/reports/download/{reportName}`, one poll | 20/min |

Search results come back as `{"count": N, "entries": [{"values": {...}, "display": {...}}]}`. `values` holds the raw values including the IDs the write tools need; `display` holds HiBob's human-readable labels. The opening and budget searches are cursor-paginated and return `has_more` and `next_cursor`; the budget search takes a `limit` up to 1000 (HiBob rejects anything larger), which is one page for most companies. **Position search has no pagination** and ignores `limit` entirely, so request only the fields you need and filter where you can.

Seven of the read tools do work HiBob's API cannot do in one request:

- **`hibob_get_openings_for_positions`** answers "which openings belong to this position?". HiBob's opening search only filters by an opening's own ID, status or name, never by its parent position. The tool sends a filter every opening satisfies (`/positionOpening/id notEqual "1"`, the clause verified against HiBob's sandbox), pages through every opening 100 at a time, and joins on `/positionOpening/positionId` in memory. Pass several positions at once to pay for the scan once; a `statuses` filter is applied by HiBob and shortens it. Positions may be given by numeric ID or by name (`P-0000000368`); names are resolved in one search first and reported back in `resolved_positions`. The result reports `counts_by_position`, so a position with no openings shows as `0`, and `scan_complete`, which is false only if the scan hit its 10,000-opening safety cap.
- **`hibob_get_positions_under`** answers "which positions report up to me, and which are filled?". Position search cannot filter by manager position or by holder, but it returns every position in one unpaginated response with its manager position and the employee filling it, so the tool fetches them all (one request) and walks the reporting tree in memory. The top position can be given as a position ID, a position name (`P-...`), the holder's HiBob employee ID, the holder's work email or the holder's name; those last two matter because HiBob has no call from an employee to their position, and a name fitting several people returns `candidates` instead. An email costs one narrowly scoped call to HiBob's people search that asks for the employee ID and nothing else; it is the only use of the people API by the position tools; `hibob_find_employee` also uses it, to turn an email into an employee ID for the tasks tools, and `hibob_terminate_employee` to find the employee it terminates. The result lists each position beneath with its status, holder and own manager position, `depth` levels down (1 for direct reports), with `counts_by_status`; a `statuses` filter is applied after the walk so a vacant position under a filled one is never lost.
- **`hibob_search_position_budgets`** names the position each budget belongs to. A budget record carries no reference to its position — HiBob's only link runs the other way, as `/position/budget` on the position — so a budget fetched on its own genuinely cannot be attributed to anything, and the honest reading of one in isolation is that its cost belongs to nobody. The tool therefore scans every position for that reference and reports the owner as each entry's `position`. It sits beside `values` rather than among the field IDs because it is synthesised here, and HiBob can neither filter nor sort on it. `null` means no position references that budget; a failed lookup returns the budgets with `position_link_error` and no `position` key at all, which is deliberately not the same as `null`. Pass `include_position: false` for a pure total to skip the extra request.

- **`hibob_get_position_costs`** answers "what does this position cost?". **Cost is not a field on a position.** `/objects/position/search` exposes 38 fields and none of them are cost; the figures the HiBob UI shows on a position (expected base salary, total position cost, total converted cost, prorated cost) live on a separate `positionBudget` object. The only link is `/position/budget` on the position, an `entity_reference` holding the budget's ID — a budget carries **no** position ID of its own, so the join only exists in that one direction. The tool fetches the named positions, reads that reference off each, fetches exactly those budgets and merges them, in two requests; positions may be given by numeric ID or by name (`P-0000000368`), and names cost one more search, reported back in `resolved_positions`. Positions whose budget is missing are listed in `positions_without_budget` rather than dropped.

  Two HiBob behaviours make this hard to discover, and both fail silently. **Unrecognised field IDs are dropped without an error** — asking the position search for `/positionBudget/totalPositionCostCurrencyValue`, or for an entirely invented field, returns `200 OK` with the key simply absent, so a request for cost looks like it worked and came back empty. And **filtering is whitelisted**: positions filter only on `/position/status`, `/position/name`, `/position/hasOpenRequests` and `/position/id`, budgets only on `/positionBudget/id` and `/positionBudget/proRatedCostPercentage`. Anything else is a 400, so no cost figure can be filtered or grouped by HiBob at all.

- **`hibob_summarize_position_costs`** rolls that cost up across the company, or a department, or everything still vacant — the thing HiBob cannot do itself, since cost is neither filterable nor groupable. It fetches every position and every budget (two requests), joins them and aggregates here, optionally grouped by `department`, `site`, `status`, `jobProfile` or `currency` and restricted to given `statuses`. Only the **converted** figures are summed: HiBob reports each position's total in its own local currency, and a company can have many, so adding those together would produce a meaningless number, while the converted values share the company's reporting currency. If converted costs ever arrive in more than one currency, `total_converted_cost` is `null` and `totals_by_currency` carries a total per currency instead of one wrong number.

- **`hibob_resolve_list_values`** turns the option names a user chose back into the IDs HiBob wants. A form is offered by option name, and what comes back from it is the name; by then a bot may have lost the form response that paired names with IDs, and the ID of the list behind the field with it. The tool takes what is left, the field as an ID or its label (`Locations for hiring`, `Site`) and the names, finds the list from the field's metadata, fetches that one list, and matches each name exactly, ignoring case, against the items and, for a tree-shaped list, their path labels (`Spain > Madrid - Office`). It returns `values`, the IDs in the order given and ready to submit, and says whether the field is `multi`. A name matching nothing comes back under `unmatched` with the nearest items as `candidates`; one that two items share comes back under `ambiguous` rather than guessed at; a branch of a tree, which cannot be submitted, offers its leaves. Every list field on a form names this tool as `resolve_with`.

- **`hibob_get_workforce_form`** returns everything needed to fill in a create form as one blob: for `position` (the default) that is the position's fields plus the nested opening (required) and budget (optional) sections; for `positionOpening` or `positionBudget` just that object. Every list-backed field (department, site, job profile, currency, ...) arrives with its `options` resolved from the company's named lists, including the `id` to submit (a list longer than `max_options`, 100 by default, is truncated, and the field then says to fetch the rest with `hibob_get_company_named_lists`); fields with a fixed vocabulary (position type, recruitment status, pay periods) carry `allowed_values`; each field says whether it is `required`, and fields HiBob sets itself are listed separately as `read_only_fields`. Each section names the write tool argument it maps to (`position_fields`, `opening_fields`, `budget_fields` or `fields`), so the filled-in form can be passed straight to the create tool.

  A form has to be complete when it is generated (a Slack form, say, needs every option up front), and job profiles and manager positions run to a thousand items. So for a position form the tool takes what the user should be asked first: `department`, `job_profile` (a rough title) and `manager` (a name, `P-...` position name or ID). A department pre-fills its field and narrows the manager choices to that department's branch of the position tree and the job profiles to those naming the department; a title or a manager's name picks out the matching leaves, and a single match pre-fills the field as `value`. Tree-shaped lists are offered as their leaves, labelled by path (`Data > Head of Data > P-0001 · London · Jane Doe`). If any of these three fields still cannot be offered within `max_options`, the response carries `questions` (each with the argument to pass, a question to ask the user and, when few enough, `candidates`) instead of `sections`; answer them and call again.

### Write (omitted when `HIBOB_READ_ONLY` is set)

| Tool | HiBob endpoint | Rate limit |
| --- | --- | --- |
| `hibob_create_position` | `POST /workforce-planning/positions` | 10/min |
| `hibob_update_position` | `PATCH /workforce-planning/positions/{id}` | 10/min |
| `hibob_cancel_position` | `PATCH /workforce-planning/positions/{id}/cancel` | 10/min |
| `hibob_schedule_position_cancellation` | `POST /workforce-planning/positions/schedule-cancellation` | 10/min |
| `hibob_create_position_opening` | `POST .../position-openings` | 10/min |
| `hibob_update_position_opening` | `PATCH .../position-openings/{openingId}` | 10/min |
| `hibob_delete_position_opening` | `DELETE .../position-openings/{openingId}` | 10/min |
| `hibob_create_position_budget` | `POST .../position-budget` | 10/min |
| `hibob_update_position_budget` | `PATCH .../position-budget/{budgetId}`, the budget found from the position | 10/min |
| `hibob_complete_task` | `POST /tasks/{id}/complete`, sent once and never retried | 10/min |
| `hibob_terminate_employee` | `POST /employees/{id}/terminate`, sent once and never retried; `POST /people/search` and `GET /company/named-lists/{name}` first | 10/min |

Every tool that takes a position or an opening accepts it either as its numeric ID or as the name HiBob shows: `P-0000000368` for a position, `O-6853240227` for an opening. A name costs one search before the write, which comes out of the search limit rather than the write limit; a name that matches nothing, or that HiBob has let two records share, is refused rather than guessed at. Openings are always looked up before an update or delete, whether given by ID or by name, because the lookup returns the position each opening belongs to. A write addressed under the wrong position, such as `O-6853240227` beneath the ID of another position, is refused before anything is sent. Budgets have no name and need none: a position carries its budget's ID as `/position/budget`, so `hibob_update_position_budget` finds the budget from the position, and its `budget_id` argument is optional. If a `budget_id` is given it must be that position's budget, so an ID carried over from another position is refused; a second budget for a position that already has one is refused likewise. Each result reports the numeric `position_id` (and `opening_id` or `budget_id`) actually written to.

Writes are limited to ten calls a minute, so required fields are validated before a request is sent and write calls are never retried automatically. Read calls retry twice on 429 and 5xx responses, honouring `Retry-After`. Named lists and metadata are each limited to fifty calls a minute and a position form needs a dozen lists and three metadata calls, so both are cached in the server process for five minutes, shared between the form, named-list and resolve tools; a failed fetch is not cached. Every create and update reads the record back through its search endpoint and returns it with `verified`; a read-back failure is reported as `verification_error` rather than as a failed write, and a created opening is checked to belong to the position it was created under.

`hibob_create_position` creates one position per call, together with its first opening (HiBob requires one) and an optional budget. HiBob's API cannot give a new position a budget end date, so a role that ends takes `cancellation_date` instead: once the position is created, its cancellation is scheduled for that day (a second write call, and the same endpoint as `hibob_schedule_position_cancellation`). The date must fall after the budget date and the opening's expected start date, and is checked before anything is sent; `/position/endEffectiveDate` in `position_fields` is refused with a pointer to it. If the scheduling fails after the create, the result still carries the new IDs, with `verified: false` and HiBob's reason.

`hibob_cancel_position` cancels a position now; HiBob refuses it for a filled position. `hibob_schedule_position_cancellation` cancels one on a later date instead, such as when a fixed-term role ends: until then the position shows as "Cancelled soon", and at midnight on the date HiBob cancels it and unassigns whoever holds it. HiBob's API documents no way to undo a scheduled cancellation, so the tool looks the position up first and refuses, before anything is sent, a position already cancelled or cancelled soon, a date that is not a real day today or later, and a filled, starting or departing position unless `unassign_holder` is true, the error naming the holder so the user can be asked. The position is read back afterwards with its status and budget end date.

`hibob_terminate_employee` adds a termination entry to an employee's lifecycle; HiBob changes their status to Terminated on the termination date. HiBob's API documents no way to undo a termination, so everything is checked before the one request is sent. The employee may be given by employee ID or work email and must match exactly one active employee, whose name the result reports. `termination_reason` and `reason_type` may be given by name (`Resigned`) or ID, and are matched exactly, ignoring case, against the company's `terminationReason` and `lifecycleReasonType` lists; a name matching nothing, or several items, is refused with the closest items. `last_day_of_work` may not fall after `termination_date`, and a notice period needs both its length and its unit (`days`, `weeks`, `month` or `years`, as HiBob spells them). The tool does not revoke the employee's access to Bob, and nothing is read back afterwards: the result reports exactly what HiBob accepted.

## Reports

Configure a report and its filters in Bob, then call `hibob_list_reports` to
find its numeric ID. The API returns only reports and data visible to the
service user; a successful response does not prove the service user can see
the entire workforce or every requested field.

For programmatic use, download JSON with machine-readable values:

```json
{"report_id": 12345, "format": "json", "include_info": true}
```

Pass this to `hibob_download_report`. The result contains `status: "ready"`,
the parsed `data`, `byte_count`, and a SHA-256 checksum of the downloaded
bytes. `human_readable: "APPEND"` includes labels alongside IDs;
`"REPLACE"` replaces IDs with labels. This option is valid only for JSON.
An optional `locale` selects the report's column language.

CSV is returned as `text`, preserving leading zeroes, duplicate column names,
metadata rows and embedded newlines. XLSX is returned as `data_base64` with
`encoding: "base64"`. The server does not write files or silently truncate
reports. It enforces a 10 MiB decoded response limit, including compressed and
chunked downloads. Narrow the saved report if it exceeds that limit. Consume
large outputs in code and pass summaries to the agent to control context size.

For reports that take longer to generate:

1. Call `hibob_generate_report` with `report_id`, `format` (`csv` or `xlsx`),
   and optionally `include_info`/`locale`.
2. Persist the returned `report_name` and `format`. `status: "accepted"`
   means generation has started, not that the file is ready.
3. Call `hibob_download_generated_report` with that name and format.
   `status: "pending"` means Bob returned 204; poll the **same file** later.
   `status: "ready"` contains the complete data in the format above.

Each call performs one poll, with no long-running sleep loop. Downloads retry
transient HTTP errors using the shared read policy; starting generation is
never automatically retried because another request can create another file.
Respect the Reports API's **20 requests/minute per endpoint** limit. A generated
file's availability is controlled by Bob; persist successful downloads if you
need reproducible inputs. There is no server-side report cache.

The async endpoint's documented format enum lists CSV/XLSX; JSON is exposed
through the direct download endpoint. The returned `Location` is used only
to extract a file name. Downloads always use the configured API host and never
follow redirects or send credentials to the supplied location's host.

Reports are saved queries, not a durable event log. The download APIs do not
accept an arbitrary `since` cursor: date conditions belong to the report in
Bob. Validate which fields, deleted rows and effective-date changes a report
actually includes before treating it as a change feed. Keep downstream
processing checkpoints separately.

Official API contracts:
[list](https://apidocs.hibob.com/reference/get_company-reports),
[download](https://apidocs.hibob.com/reference/get_company-reports-reportid-download),
[generate](https://apidocs.hibob.com/reference/get_company-reports-reportid-download-async),
[poll](https://apidocs.hibob.com/reference/get_company-reports-download-reportname),
[permissions and limits](https://apidocs.hibob.com/reference/reports).

## Field cheat sheet

Required to create a position:

| Object | Required fields |
| --- | --- |
| `position` | `effectiveDate`, `fte`, `department`, `site`, `jobProfile` |
| `positionOpening` (nested, required) | `expectedStartDate` |
| `positionBudget` (nested, optional) | `salaryPayPeriod`, `currency` if the budget is supplied |

Updatable on a position: `name`, `effectiveDate`, `managerPositionId`, `positionType`, `fte`, `employmentType`, `department`, `site`, `jobProfile`, `reason`, plus custom fields (`/position/field_<number>`, IDs from `hibob_get_workforce_form`). Custom fields are passed to HiBob unverified: the result names them as `undocumented_fields`, a rejected update says they may be the reason, and a field HiBob accepted but did not keep shows up in `unconfirmed_fields` after the read-back. Fields HiBob sets itself (`id`, `status`, `filledBy`, ...) are refused before any request.

Filterable fields: `/position/status`, `/position/name`, `/position/hasOpenRequests`, `/position/id`; `/positionOpening/id`, `/positionOpening/status` (`vacant`, `starting`, `filled`, `departing`, `cancelled`, `onHold`, `cancelledSoon`), `/positionOpening/positionOpeningName`. A search without filters returns everything: HiBob refuses an empty filter list, so the server sends a clause every record satisfies.

Fields such as `department`, `site` and `jobProfile` take HiBob list item IDs, not names. `hibob_get_workforce_form` returns those IDs alongside each field; `hibob_get_company_named_lists` returns one list's items, or with no `list_name` just the names and sizes of every list, since the full contents of every list can run to tens of megabytes.

HiBob refuses a write value of the wrong JSON type, at least sometimes with a bare 400 that names no field, so the write tools send each documented field as the type HiBob's API reference gives it. A value is converted where that is unambiguous and refused before the request otherwise:

| Fields | HiBob wants | Converted | Refused |
|---|---|---|---|
| `site`, `jobProfile`, `managerPositionId` | a number | digits in a string, `"2555828"` | names, decimals, anything else |
| `fte` and the budget amounts (`expectedBaseSalary…`, `totalPositionCost…`, `expectedVariablePay…CurrencyValue`) | a number | a plain decimal in a string, `"65000.5"`; for an amount, the `{"value": n, "currency": c}` that budget searches return, when `c` is the budget's `currency` in the same write | `"100,000"`, `"€100k"`, `"100%"`, anything else |
| `effectiveDate`, `expectedStartDate` | `YYYY-MM-DD` | surrounding spaces | the day-first dates HiBob's searches display (`01/09/2026`), impossible days, anything else |
| `department`, `positionType`, `employmentType`, `recruitmentStatus`, `currency`, `salaryPayPeriod`, `variablePayPeriod` | a string | a whole number, `263717557` | anything else |

`null` is refused for all of them: HiBob allows it only on some creates, where leaving the field out does the same, and never on update. Custom fields (`/position/field_<number>`) go through as given, since HiBob's reference does not type them.

Tool arguments that name a position, opening, budget or list item take a number as readily as a string, since results show those IDs as numbers. Search filter values go to HiBob as strings, as it compares them, with `true`/`false` for yes/no fields.

## Development

```bash
uv venv
uv pip install -e '.[test,lint,typecheck]'
pytest
```

Lint, formatting and types are enforced in CI:

```bash
ruff check .          # add --fix to apply the automatic fixes
ruff format .         # CI runs --check, so format before pushing
mypy                  # non-strict; paths come from pyproject.toml
```

Type checking is deliberately non-strict — annotations are checked where they
exist, but untyped code is allowed. The package ships a `py.typed` marker, so
its annotations are visible to anything that imports it.

Inspect the tools interactively:

```bash
npx @modelcontextprotocol/inspector uvx --from . hibob-advanced-mcp
```

## License

MIT
