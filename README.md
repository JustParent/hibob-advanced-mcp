# hibob-advanced-mcp

An MCP server for HiBob's [Workforce Planning API](https://apidocs.hibob.com/reference/workforce-planning) — planned positions, their openings, and their budgets.

This complements a standard HiBob HRIS integration rather than replacing it. Common HRIS functionality (people, time off, documents) belongs in the main integration; this server exposes the workforce planning surface that has no equivalent in other HRIS systems, so it can be enabled only for the customers who plan headcount in HiBob.

It runs over stdio, is installable with `uvx`, and authenticates with a HiBob **API service user**.

## HiBob setup

1. In HiBob, go to **Settings → Integrations → API service users** and create a service user. HiBob shows the **service user ID** and **token** once — copy both now, as they cannot be retrieved later.
2. Create (or reuse) a permission group containing that service user, and grant it:

   **Features → Workforce planning → Position management → Manage positions**

   Service users have no permissions by default. Without this grant every call returns 403, and this server will tell you to add exactly this permission.
3. If your HiBob account restricts API access by IP address, allow the outbound IP of wherever this server runs.

Read-only use still needs the same grant — HiBob does not offer a narrower workforce planning permission. Use `HIBOB_READ_ONLY=true` (below) if you want the server itself to refuse to make changes.

## Configuration

| Environment variable | Required | Description |
| --- | --- | --- |
| `HIBOB_SERVICE_USER_ID` | yes | Service user ID (the Basic auth username). |
| `HIBOB_SERVICE_USER_TOKEN` | yes | Service user token (the Basic auth password). |
| `HIBOB_API_HOST` | no | Defaults to production (`api.hibob.com`). Set `api.sandbox.hibob.com` for HiBob's sandbox. A pasted URL such as `https://api.sandbox.hibob.com/v1` is accepted; only the hostname is used. |
| `HIBOB_READ_ONLY` | no | `true`, `1`, `yes` or `on` registers only the seven read tools; the eight write tools are not exposed at all. |

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
| `hibob_search_positions` | `POST /objects/position/search`; a free-text `query` is matched locally against title, code, department, site, job profile and holder | 100/min |
| `hibob_search_position_openings` | `POST /positions/position-openings/search` | 100/min |
| `hibob_get_openings_for_positions` | `POST /positions/position-openings/search`, every page | 100/min |
| `hibob_get_positions_under` | `POST /objects/position/search`, every position, walked in memory | 100/min |
| `hibob_search_position_budgets` | `POST /positions/position-budget/search` | 100/min |
| `hibob_get_position_costs` | `POST /objects/position/search`, then `POST /positions/position-budget/search` for the budgets they reference | 100/min |
| `hibob_summarize_position_costs` | the same two searches, company-wide, aggregated in memory | 100/min |

Search results come back as `{"count": N, "entries": [{"values": {...}, "display": {...}}]}`. `values` holds the raw values including the IDs the write tools need; `display` holds HiBob's human-readable labels. The opening and budget searches are cursor-paginated and return `has_more` and `next_cursor`; the budget search takes a `limit` up to 1000 (HiBob rejects anything larger), which is one page for most companies. **Position search has no pagination** and ignores `limit` entirely, so request only the fields you need and filter where you can.

Five of the read tools do work HiBob's API cannot do in one request:

- **`hibob_get_openings_for_positions`** answers "which openings belong to this position?". HiBob's opening search only filters by an opening's own ID, status or name, never by its parent position. The tool sends a filter every opening satisfies (`/positionOpening/id notEqual "1"`, the clause verified against HiBob's sandbox), pages through every opening 100 at a time, and joins on `/positionOpening/positionId` in memory. Pass several position IDs at once to pay for the scan once; a `statuses` filter is applied by HiBob and shortens it. The result reports `counts_by_position`, so a position with no openings shows as `0`, and `scan_complete`, which is false only if the scan hit its 10,000-opening safety cap.
- **`hibob_get_positions_under`** answers "which positions report up to me, and which are filled?". Position search cannot filter by manager position or by holder, but it returns every position in one unpaginated response with its manager position and the employee filling it, so the tool fetches them all (one request) and walks the reporting tree in memory. The top position can be given as a position ID, a position name (`P-...`), the holder's HiBob employee ID, the holder's work email or the holder's name; those last two matter because HiBob has no call from an employee to their position, and a name fitting several people returns `candidates` instead. An email costs one narrowly scoped call to HiBob's people search that asks for the employee ID and nothing else; it is the server's only use of the people API. The result lists each position beneath with its status, holder and own manager position, `depth` levels down (1 for direct reports), with `counts_by_status`; a `statuses` filter is applied after the walk so a vacant position under a filled one is never lost.
- **`hibob_get_position_costs`** answers "what does this position cost?". **Cost is not a field on a position.** `/objects/position/search` exposes 38 fields and none of them are cost; the figures the HiBob UI shows on a position (expected base salary, total position cost, total converted cost, prorated cost) live on a separate `positionBudget` object. The only link is `/position/budget` on the position, an `entity_reference` holding the budget's ID — a budget carries **no** position ID of its own, so the join only exists in that one direction. The tool fetches the named positions, reads that reference off each, fetches exactly those budgets and merges them, in two requests. Positions whose budget is missing are listed in `positions_without_budget` rather than dropped.

  Two HiBob behaviours make this hard to discover, and both fail silently. **Unrecognised field IDs are dropped without an error** — asking the position search for `/positionBudget/totalPositionCostCurrencyValue`, or for an entirely invented field, returns `200 OK` with the key simply absent, so a request for cost looks like it worked and came back empty. And **filtering is whitelisted**: positions filter only on `/position/status`, `/position/name`, `/position/hasOpenRequests` and `/position/id`, budgets only on `/positionBudget/id` and `/positionBudget/proRatedCostPercentage`. Anything else is a 400, so no cost figure can be filtered or grouped by HiBob at all.

- **`hibob_summarize_position_costs`** rolls that cost up across the company, or a department, or everything still vacant — the thing HiBob cannot do itself, since cost is neither filterable nor groupable. It fetches every position and every budget (two requests), joins them and aggregates here, optionally grouped by `department`, `site`, `status`, `jobProfile` or `currency` and restricted to given `statuses`. Only the **converted** figures are summed: HiBob reports each position's total in its own local currency, and a company can have many, so adding those together would produce a meaningless number, while the converted values share the company's reporting currency. If converted costs ever arrive in more than one currency, `total_converted_cost` is `null` and `totals_by_currency` carries a total per currency instead of one wrong number.

- **`hibob_get_workforce_form`** returns everything needed to fill in a create form as one blob: for `position` (the default) that is the position's fields plus the nested opening (required) and budget (optional) sections; for `positionOpening` or `positionBudget` just that object. Every list-backed field (department, site, job profile, currency, ...) arrives with its `options` resolved from the company's named lists, including the `id` to submit (a list longer than `max_options`, 100 by default, is truncated, and the field then says to fetch the rest with `hibob_get_company_named_lists`); fields with a fixed vocabulary (position type, recruitment status, pay periods) carry `allowed_values`; each field says whether it is `required`, and fields HiBob sets itself are listed separately as `read_only_fields`. Each section names the write tool argument it maps to (`position_fields`, `opening_fields`, `budget_fields` or `fields`), so the filled-in form can be passed straight to the create tool.

  A form has to be complete when it is generated (a Slack form, say, needs every option up front), and job profiles and manager positions run to a thousand items. So for a position form the tool takes what the user should be asked first: `department`, `job_profile` (a rough title) and `manager` (a name, `P-...` position name or ID). A department pre-fills its field and narrows the manager choices to that department's branch of the position tree and the job profiles to those naming the department; a title or a manager's name picks out the matching leaves, and a single match pre-fills the field as `value`. Tree-shaped lists are offered as their leaves, labelled by path (`Data > Head of Data > P-0001 · London · Jane Doe`). If any of these three fields still cannot be offered within `max_options`, the response carries `questions` (each with the argument to pass, a question to ask the user and, when few enough, `candidates`) instead of `sections`; answer them and call again.

### Write (omitted when `HIBOB_READ_ONLY` is set)

| Tool | HiBob endpoint | Rate limit |
| --- | --- | --- |
| `hibob_create_position` | `POST /workforce-planning/positions` | 10/min |
| `hibob_update_position` | `PATCH /workforce-planning/positions/{id}` | 10/min |
| `hibob_cancel_position` | `PATCH /workforce-planning/positions/{id}/cancel` | 10/min |
| `hibob_create_position_opening` | `POST .../position-openings` | 10/min |
| `hibob_update_position_opening` | `PATCH .../position-openings/{openingId}` | 10/min |
| `hibob_delete_position_opening` | `DELETE .../position-openings/{openingId}` | 10/min |
| `hibob_create_position_budget` | `POST .../position-budget` | 10/min |
| `hibob_update_position_budget` | `PATCH .../position-budget/{budgetId}` | 10/min |

Writes are limited to ten calls a minute, so required fields are validated before a request is sent and write calls are never retried automatically. Read calls retry twice on 429 and 5xx responses, honouring `Retry-After`. Named lists are limited to fifty calls a minute and a position form needs a dozen or more, so fetched lists are cached in the server process for five minutes; a failed fetch is not cached. Every create and update reads the record back through its search endpoint and returns it with `verified`; a read-back failure is reported as `verification_error` rather than as a failed write, and a created opening is checked to belong to the position it was created under.

`hibob_create_position` creates one position per call, together with its first opening (HiBob requires one) and an optional budget.

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
