# Workflow Forge

A persistent configurable state-machine engine with role-gated approval transitions, data guards, immutable definition versions, controlled edits, idempotent commands and ordered history. Built for Lord_Sumi's portfolio.

## Test it

On GitHub: **Actions → Tests and demo → Run workflow → main → Run workflow**. Read each job's tests and sample scenario. Locally, use Python 3.10+ and the standard library:

```bash
python -m unittest discover -s tests -v
python demo.py
```

The synthetic demo starts a draft, submits it as an author and approves it as a reviewer. It uses a temporary database and does not communicate with real people or external services.

## Definitions and execution

See `SPEC` in demo.py for a complete definition. Each definition explicitly declares states, an initial state, allowed starting roles and named transitions. Each transition declares valid source states, a destination, allowed roles, optional guards and optional fixed data effects.

```python
from workflow_forge import Engine
from demo import SPEC

engine = Engine("workflow.db")
version = engine.register("approval", SPEC)
run = engine.start("approval", version, {"amount": 100}, actor="author-1", role="author")
receipt = engine.apply(run["id"], "submit", actor="author-1", role="author", expected_revision=0, command_key="submit-001")
```

Definitions are canonical JSON identified by SHA-256. Every execution stores its definition snapshot, so registering a changed version does not silently alter existing work. SQLite transactions serialize commands, revision checks, state changes, history and command receipts.

## Guards, edits and retries

Guards read dot-separated paths from execution data. Supported operations are strict canonical JSON `eq`, numeric `gte`/`lte`, and list-membership `in`. Missing paths or inappropriate types fail the guard. No expressions, shell commands or Python `eval` are executed. JSON number representations matter for strict equality: `1` and `1.0` differ, as do `true` and `1`.

Arbitrary data patches cannot accompany a transition. Use the `$edit` action with a shallow object patch only when the definition's `edit_roles` and `editable_states` permit it. Fixed transition effects are trusted definition configuration. This prevents a caller from passing one set of guard inputs while changing them invisibly during approval.

Every mutation requires an expected revision and a command key. Identical retries return the original receipt without another state change; conflicting reuse fails. A previous receipt can be older than the current state—use `get` for current state. Rejected transitions do not consume command keys or increment revisions. Retrying after corrected data or a different state requires the current revision and a valid transition path; this is not a background job retry scheduler.

## Verification and boundaries

Local check on 31 October 2026: **14 tests passed**. Cases cover the approval flow, invalid transitions, role restrictions, failed guards, edit restrictions, definition version isolation, optimistic concurrency, simultaneous duplicate requests, nonfinite input and historical receipts. CI also runs the complete synthetic approval demo.

Actor and role values must come from an authenticated embedding application. This library does not authenticate those claims, provide tenant isolation, restrict administrative inspection, or enforce separation of duties between two identities sharing authorized roles. History records actor and role strings for audit, not verified human identity. Execution data and history are stored without encryption. Protect the database and define roles carefully.

No hosted API/UI, approval notifications, scheduled jobs, external side-effect execution, long-running task runner, data deletion or automatic workflow migration is included. Edits and effects merge top-level keys; nested objects are replaced as values. Workflow definitions are trusted configuration, not untrusted uploaded programs. Large state/history volumes are not performance-benchmarked.

MIT license; see LICENSE.
