import concurrent.futures
import copy
import tempfile
import unittest
from pathlib import Path
from workflow_forge import Engine, Conflict
from workflow_forge.engine import passes
from demo import SPEC


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "workflow.db"
        self.engine = Engine(self.path)
        self.version = self.engine.register("approval", SPEC)
        self.run = self.engine.start("approval", self.version, {"amount": 100}, actor="author", role="author")

    def submit(self, **options):
        return self.engine.apply(self.run["id"], "submit", **({"actor": "author", "role": "author", "expected_revision": 0, "command_key": "submit"} | options))

    def test_approval_flow_and_ordered_history(self):
        self.submit()
        result = self.engine.apply(self.run["id"], "approve", actor="reviewer", role="reviewer", expected_revision=1, command_key="approve")
        self.assertEqual(result["state"], "approved")
        self.assertTrue(result["data"]["approved"])
        self.assertEqual([r["event"] for r in self.engine.history(self.run["id"])], ["created", "submit", "approve"])

    def test_invalid_transitions_do_not_change_revision(self):
        with self.assertRaises(ValueError):
            self.engine.apply(self.run["id"], "approve", actor="reviewer", role="reviewer", expected_revision=0, command_key="early")
        self.assertEqual(self.engine.get(self.run["id"])["revision"], 0)

    def test_role_checks_cover_start_and_approval(self):
        with self.assertRaises(PermissionError):
            self.engine.start("approval", self.version, {}, actor="outsider", role="guest")
        self.submit()
        with self.assertRaises(PermissionError):
            self.engine.apply(self.run["id"], "approve", actor="author", role="author", expected_revision=1, command_key="self-approve")

    def test_guard_failures_do_not_consume_command_key(self):
        self.engine.apply(self.run["id"], "$edit", patch={"amount": 2000}, actor="author", role="author", expected_revision=0, command_key="edit")
        self.submit(expected_revision=1)
        for _ in range(2):
            with self.assertRaises(ValueError):
                self.engine.apply(self.run["id"], "approve", actor="reviewer", role="reviewer", expected_revision=2, command_key="approve")
        self.assertEqual(self.engine.get(self.run["id"])["revision"], 2)

    def test_edits_are_limited_to_configured_states_and_roles(self):
        with self.assertRaises(PermissionError):
            self.engine.apply(self.run["id"], "$edit", patch={"amount": 1}, actor="guest", role="guest", expected_revision=0, command_key="bad")
        self.submit()
        with self.assertRaises(PermissionError):
            self.engine.apply(self.run["id"], "$edit", patch={"amount": 1}, actor="author", role="author", expected_revision=1, command_key="late")

    def test_idempotent_retries_and_conflicting_command_reuse(self):
        first = self.submit()
        self.assertEqual(self.submit(), first)
        self.assertEqual(len(self.engine.history(self.run["id"])), 2)
        with self.assertRaises(Conflict):
            self.submit(actor="different-author")

    def test_reopened_execution_preserves_definition_version(self):
        changed = copy.deepcopy(SPEC)
        changed["transitions"]["submit"]["roles"] = ["new-role"]
        new_version = self.engine.register("approval", changed)
        self.assertNotEqual(new_version, self.version)
        again = Engine(self.path)
        result = again.apply(self.run["id"], "submit", actor="author", role="author", expected_revision=0, command_key="submit")
        self.assertEqual(result["version"], self.version)

    def test_racing_revisions_have_one_winner(self):
        def attempt(number):
            try:
                return self.submit(command_key="submit-" + str(number))
            except Conflict:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(10)))
        self.assertEqual(sum(r is not None for r in results), 1)

    def test_concurrent_identical_commands_have_one_effect(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.submit(), range(10)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(len(self.engine.history(self.run["id"])), 2)

    def test_guards_fail_closed_on_missing_or_wrong_types(self):
        self.assertFalse(passes({}, {"path": "amount", "op": "gte", "value": 1}))
        self.assertFalse(passes({"amount": True}, {"path": "amount", "op": "gte", "value": 1}))
        self.assertFalse(passes({"flag": True}, {"path": "flag", "op": "eq", "value": 1}))
        self.assertTrue(passes({"item": {"kind": "a"}}, {"path": "item.kind", "op": "in", "value": ["a", "b"]}))

    def test_bad_definitions_are_rejected_before_registration(self):
        for field, value in (("initial", "missing"), ("start_roles", []), ("states", ["draft", "draft"])):
            spec = copy.deepcopy(SPEC)
            spec[field] = value
            with self.assertRaises(ValueError):
                self.engine.register("bad", spec)

    def test_transition_cannot_smuggle_arbitrary_data_patch(self):
        with self.assertRaises(ValueError):
            self.submit(patch={"amount": 1})
        self.assertEqual(self.engine.get(self.run["id"])["data"]["amount"], 100)

    def test_nonfinite_data_and_bad_revisions_are_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.start("approval", self.version, {"amount": float("nan")}, actor="author", role="author")
        with self.assertRaises(ValueError):
            self.submit(expected_revision=True)

    def test_successful_old_receipt_is_not_current_state(self):
        first = self.submit()
        self.engine.apply(self.run["id"], "approve", actor="reviewer", role="reviewer", expected_revision=1, command_key="approve")
        self.assertEqual(self.submit(), first)
        self.assertEqual(self.engine.get(self.run["id"])["state"], "approved")
