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
| `HIBOB_READ_ONLY` | no | `true`, `1`, `yes` or `on` registers only the five read tools; the eight write tools are not exposed at all. |

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
| `hibob_get_company_named_lists` | `GET /company/named-lists` | — |
| `hibob_search_positions` | `POST /objects/position/search` | 100/min |
| `hibob_search_position_openings` | `POST /positions/position-openings/search` | 100/min |
| `hibob_search_position_budgets` | `POST /positions/position-budget/search` | 100/min |

Search results come back as `{"count": N, "entries": [{"values": {...}, "display": {...}}]}`. `values` holds the raw values including the IDs the write tools need; `display` holds HiBob's human-readable labels. The opening and budget searches are cursor-paginated and return `has_more` and `next_cursor`; **position search has no pagination**, so request only the fields you need and filter where you can.

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

Writes are limited to ten calls a minute, so required fields are validated before a request is sent and write calls are never retried automatically. Read calls retry twice on 429 and 5xx responses, honouring `Retry-After`.

`hibob_create_position` creates one position per call, together with its first opening (HiBob requires one) and an optional budget.

## Field cheat sheet

Required to create a position:

| Object | Required fields |
| --- | --- |
| `position` | `effectiveDate`, `fte`, `department`, `site`, `jobProfile` |
| `positionOpening` (nested, required) | `expectedStartDate` |
| `positionBudget` (nested, optional) | `salaryPayPeriod`, `currency` if the budget is supplied |

Updatable on a position: `name`, `effectiveDate`, `managerPositionId`, `positionType`, `fte`, `employmentType`, `department`, `site`, `jobProfile`, `reason`.

Filterable fields: `/position/status`, `/position/name`, `/position/hasOpenRequests`, `/position/id`; `/positionOpening/id`, `/positionOpening/status` (`vacant`, `starting`, `filled`, `departing`), `/positionOpening/positionOpeningName`.

Fields such as `department`, `site` and `jobProfile` take HiBob list item IDs, not names. Resolve them with `hibob_get_company_named_lists` before creating or updating a position.

## Development

```bash
uv venv
uv pip install -e '.[test,lint]'
pytest
```

Lint and formatting are enforced in CI by [ruff](https://docs.astral.sh/ruff/):

```bash
ruff check .          # add --fix to apply the automatic fixes
ruff format .         # CI runs --check, so format before pushing
```

Inspect the tools interactively:

```bash
npx @modelcontextprotocol/inspector uvx --from . hibob-advanced-mcp
```

## License

MIT
