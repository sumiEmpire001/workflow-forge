import json
import tempfile
from pathlib import Path
from workflow_forge import Engine

SPEC = {"states": ["draft", "submitted", "approved"], "initial": "draft", "start_roles": ["author"], "edit_roles": ["author"], "editable_states": ["draft"], "transitions": {
    "submit": {"from": ["draft"], "to": "submitted", "roles": ["author"]},
    "approve": {"from": ["submitted"], "to": "approved", "roles": ["reviewer"], "guards": [{"path": "amount", "op": "lte", "value": 1000}], "effects": {"approved": True}}
}}

if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        engine = Engine(Path(directory) / "workflow.db")
        version = engine.register("synthetic-approval", SPEC)
        run = engine.start("synthetic-approval", version, {"amount": 100}, actor="demo-author", role="author")
        engine.apply(run["id"], "submit", actor="demo-author", role="author", expected_revision=0, command_key="submit")
        final = engine.apply(run["id"], "approve", actor="demo-reviewer", role="reviewer", expected_revision=1, command_key="approve")
        print(json.dumps(final, indent=2))
