# Time off API observations (Harriet HiBob Demo tenant, api.hibob.com, 2026-10-13)

Probed with the service user in `.env`. Write probes were made on David Employee
(3987598099261424086), reporting to David Manager (3987598041094816210), on the
Holiday policy ("Holiday policy": days, `approvalRequired: true`, allowance 0, no
minimum balance). Every request created was cancelled; who's out afterwards held the
same 10 requests as before.

## Reads
- GET /timeoff/policy-types: 200 `{"policyTypes": [...]}`, 7 types, display names
  ("PTO (USA)"). Who's out gives both `policyType` ("PtoUSA") and
  `policyTypeDisplayName` ("PTO (USA)").
- GET /timeoff/policy-types/{name} (URL-quoted display name): 200 `{"name", "activity",
  "unit"}`. Unknown name: 400 `loa.policy.policy_type_does_not_exits` "Policy type
  doesn't exist".
- GET /timeoff/policies/names?policyTypeName=Holiday: `{"policies": ["Holiday policy"]}`.
- GET /timeoff/policies?policyName=Holiday%20policy: `name, unit, allowance, maxBalance,
  minBalance, yosIncrease, minTimeOffRequestDuration, approvalRequired, description,
  bookingWorkDaysOnly`.
- GET /timeoff/policy-types/Sick/reason-codes: `{"reasonCodes": []}`.
- GET /timeoff/whosout?from&to&includeHourly&includePending&includePrivate: 200
  `{"outs": [...]}`; keys `employeeId, employeeDisplayName, policyType,
  policyTypeDisplayName, requestId (int), status, visibility, startDate, startPortion,
  endDate, endPortion, type`. `status` is "approved" or "pending" only. Cancelled
  requests drop out at once. Without from/to: 200 with the Bob login page (HTML).
  `to` before `from`: 400 `invalid.request`.
- GET /timeoff/employees/{id}/requests/{requestId}: 200 with `status`
  "pending_approval" | "approved" | "canceled", `approved` (bool), `totalDuration`,
  `durations` per day, `previousRequestId`, `originalRequestId`, approver and decline
  fields. **Another employee's request under this employee's ID: 404.** Unknown ID: 404.
- GET /timeoff/employees/{id}/balance?policyType&date: 200. Without `date`: 200 with the
  login page (HTML). Unknown policy type: 400 "Policy type doesn't exist".
- GET /timeoff/requests/changes?since=2026-05-01T00:00:00.000Z: 200 `{"changes": [...]}`
  with `changeType` "Created" | "Canceled", one entry per request (a request created
  and cancelled shows only "Canceled"). since more than six months back: 400
  `exception.changes.api.date.range.too.large` "date range should be up to six months".
- No endpoint approves or declines a request (HiBob's reference has only webhooks for
  those), so a declined request could not be made.

## Booking: POST /timeoff/employees/{id}/requests
- `requestRangeType: days`, all_day portions, `skipManagerApproval: false`: 200
  `{"id": n}`, reads back "pending_approval".
- `skipManagerApproval: true`: reads back "approved".
- Past dates are accepted (2026-10-05 and 2026-10-06 when today was 2026-10-13).
- Single half day as `days` with both portions "morning" (or both "afternoon"): 200,
  `totalDuration` 0.5.
- Overlap: 400 `timeoff_reject_reason.conflicted.not.allow.override`, error "Conflicts
  with request/s which cannot be overridden:", args ["Pending approval Holiday request
  from 23/11/2026 (morning)"].
- Weekend only: 400 `timeoff_reject_reason.zeroDurationRequestOnNonWorkingDay.onBehalf`
  "Employee isn't working on Saturday, Sunday." args ["Saturday, Sunday"].
- End before start: 400 `{"error": "requirement failed: Request end date can't be
  earlier than the start date.", "errors": {"obj": ...}}`.
- Unknown policy type: 400 "Policy type doesn't exist".

## Cancelling: DELETE /timeoff/employees/{id}/requests/{requestId}
Every DELETE answered 200 with an empty body, and the request read "canceled"
immediately afterwards.

| Request | Result |
| --- | --- |
| future, pending | canceled |
| future, approved | canceled (no pending-cancellation step) |
| starts today, approved | canceled |
| past, pending | canceled |
| past, approved | canceled |
| in progress (yesterday to tomorrow), approved | canceled, whole request |
| already canceled | 200 again, nothing changes |
| **David Employee's request, DELETE under David Manager's ID** | **canceled**: the employee in the path is ignored |
| unknown request ID | 404 |

HiBob's permission reference names only "People's Data > Time off > Requests >
Create, edit and cancel people's requests that haven't been approved yet" for
requests, yet this service user cancelled approved requests; its permission group was
not inspected.
