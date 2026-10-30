import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager


class Conflict(ValueError):
    pass


def encode(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))


def label(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError("Labels must be nonempty strings of at most 128 characters")


def roles(value):
    if not isinstance(value, list) or not value:
        raise ValueError("At least one explicit role is required")
    for role in value:
        label(role)


def validate(spec):
    if not isinstance(spec, dict) or not isinstance(spec.get("states"), list) or not spec["states"]:
        raise ValueError("A workflow needs states")
    for state in spec["states"]:
        label(state)
    states = set(spec["states"])
    if len(states) != len(spec["states"]) or spec.get("initial") not in states or not isinstance(spec.get("transitions"), dict):
        raise ValueError("Invalid states, initial state or transitions")
    roles(spec.get("start_roles"))
    if spec.get("edit_roles"):
        roles(spec["edit_roles"])
    if not isinstance(spec.get("editable_states", []), list) or not set(spec.get("editable_states", [])) <= states:
        raise ValueError("Unknown editable states")
    for name, rule in spec["transitions"].items():
        label(name)
        if name.startswith("$") or not isinstance(rule, dict) or not isinstance(rule.get("from"), list) or not rule["from"] or not set(rule["from"]) <= states or rule.get("to") not in states:
            raise ValueError("Invalid transition graph")
        roles(rule.get("roles"))
        if not isinstance(rule.get("guards", []), list) or not isinstance(rule.get("effects", {}), dict):
            raise ValueError("Guards must be a list and effects an object")
        for guard in rule.get("guards", []):
            if not isinstance(guard, dict) or set(guard) != {"path", "op", "value"} or guard["op"] not in ("eq", "gte", "lte", "in"):
                raise ValueError("Invalid guard")
            label(guard["path"])
            if guard["op"] == "in" and not isinstance(guard["value"], list):
                raise ValueError("in guard requires a list")
    encode(spec)


def passes(data, guard):
    missing = object()
    current = data
    for key in guard["path"].split("."):
        current = current.get(key, missing) if isinstance(current, dict) else missing
    if current is missing:
        return False
    target, op = guard["value"], guard["op"]
    if op == "eq":
        return encode(current) == encode(target)
    if op == "in":
        return any(encode(current) == encode(value) for value in target)
    if type(current) not in (int, float) or type(target) not in (int, float):
        return False
    return current >= target if op == "gte" else current <= target


class Engine:
    def __init__(self, path="workflow.db", clock=time.time):
        if str(path) == ":memory:":
            raise ValueError("Use a filesystem database")
        self.path, self.clock = str(path), clock
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS definitions(name TEXT NOT NULL,version TEXT NOT NULL,spec TEXT NOT NULL,PRIMARY KEY(name,version));
                CREATE TABLE IF NOT EXISTS executions(id TEXT PRIMARY KEY,name TEXT NOT NULL,version TEXT NOT NULL,spec TEXT NOT NULL,state TEXT NOT NULL,revision INTEGER NOT NULL,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS history(seq INTEGER PRIMARY KEY,execution TEXT NOT NULL,revision INTEGER NOT NULL,actor TEXT NOT NULL,role TEXT NOT NULL,event TEXT NOT NULL,result TEXT NOT NULL,at REAL NOT NULL,UNIQUE(execution,revision));
                CREATE TABLE IF NOT EXISTS commands(execution TEXT NOT NULL,key TEXT NOT NULL,fingerprint TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(execution,key));
            """)

    @contextmanager
    def db(self, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def register(self, name, spec):
        label(name)
        validate(spec)
        packed = encode(spec)
        version = hashlib.sha256(packed.encode()).hexdigest()
        with self.db(True) as db:
            db.execute("INSERT OR IGNORE INTO definitions VALUES(?,?,?)", (name, version, packed))
        return version

    def start(self, name, version, data, *, actor, role):
        label(actor)
        label(role)
        if not isinstance(data, dict):
            raise ValueError("Execution data must be an object")
        packed = encode(data)
        with self.db(True) as db:
            definition = db.execute("SELECT spec FROM definitions WHERE name=? AND version=?", (name, version)).fetchone()
            if not definition:
                raise ValueError("Definition not found")
            spec = json.loads(definition["spec"])
            if role not in spec["start_roles"]:
                raise PermissionError("Role cannot start this workflow")
            execution = uuid.uuid4().hex
            db.execute("INSERT INTO executions VALUES(?,?,?,?,?,0,?)", (execution, name, version, definition["spec"], spec["initial"], packed))
            result = self._read(db, execution)
            self._history(db, result, actor, role, "created")
            return result

    def _read(self, db, execution):
        row = db.execute("SELECT id,name,version,state,revision,data FROM executions WHERE id=?", (execution,)).fetchone()
        if not row:
            raise ValueError("Execution not found")
        result = dict(row)
        result["data"] = json.loads(result["data"])
        return result

    def _history(self, db, result, actor, role, event):
        db.execute("INSERT INTO history(execution,revision,actor,role,event,result,at) VALUES(?,?,?,?,?,?,?)", (result["id"], result["revision"], actor, role, event, encode(result), self.clock()))

    def apply(self, execution, action, *, actor, role, expected_revision, command_key, patch=None):
        for value in (actor, role, command_key, action):
            label(value)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Expected revision must be a nonnegative integer")
        if action != "$edit" and patch is not None:
            raise ValueError("Transition data patches are not allowed")
        if action == "$edit" and not isinstance(patch, dict):
            raise ValueError("Edit requires an object patch")
        fingerprint = encode([action, actor, role, expected_revision, patch])
        with self.db(True) as db:
            previous = db.execute("SELECT * FROM commands WHERE execution=? AND key=?", (execution, command_key)).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise Conflict("Command key reused for a different request")
                return json.loads(previous["result"])
            current = self._read(db, execution)
            if current["revision"] != expected_revision:
                raise Conflict("Execution revision changed")
            spec = json.loads(db.execute("SELECT spec FROM executions WHERE id=?", (execution,)).fetchone()[0])
            if action == "$edit":
                if role not in spec.get("edit_roles", []) or current["state"] not in spec.get("editable_states", []):
                    raise PermissionError("Editing is not allowed for this state and role")
                current["data"].update(patch)
            else:
                rule = spec["transitions"].get(action)
                if not rule or current["state"] not in rule["from"]:
                    raise ValueError("Invalid transition from current state")
                if role not in rule["roles"]:
                    raise PermissionError("Role cannot perform this transition")
                if not all(passes(current["data"], guard) for guard in rule.get("guards", [])):
                    raise ValueError("Transition guard failed")
                current["state"] = rule["to"]
                current["data"].update(rule.get("effects", {}))
            current["revision"] += 1
            db.execute("UPDATE executions SET state=?,revision=?,data=? WHERE id=?", (current["state"], current["revision"], encode(current["data"]), execution))
            self._history(db, current, actor, role, action)
            db.execute("INSERT INTO commands VALUES(?,?,?,?)", (execution, command_key, fingerprint, encode(current)))
            return current

    def get(self, execution):
        with self.db() as db:
            return self._read(db, execution)

    def history(self, execution):
        with self.db() as db:
            return [dict(row) | {"result": json.loads(row["result"])} for row in db.execute("SELECT * FROM history WHERE execution=? ORDER BY revision", (execution,))]
