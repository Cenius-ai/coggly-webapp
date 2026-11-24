"""Smoke tests for Coggly, run with: python3 -m unittest -v

Every store test points --data (or HOME) at a temporary directory, so the real
~/.coggly.json and ~/.coggly.session are never read, created or modified.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import coggly

ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "coggly.py"
DEMO = coggly.DEMO_EMAIL
DEMO_PASSWORD = coggly.DEMO_PASSWORD


class CliTestCase(unittest.TestCase):
    """Runs the real command line in a throwaway HOME with a throwaway data file."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.data = self.tmp / "store.json"
        self.env = {**os.environ, "HOME": str(self.home), "PYTHONPATH": str(ROOT)}

    def run_cli(
        self, *args: str, expect: int = 0, use_data_flag: bool = True, stdin: str | None = None
    ) -> subprocess.CompletedProcess:
        command = [sys.executable, str(SCRIPT)]
        if use_data_flag:
            command += ["--data", str(self.data)]
        command += list(args)
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            input=stdin,
            env=self.env,
            cwd=str(ROOT),
            timeout=120,
        )
        self.assertEqual(
            result.returncode,
            expect,
            msg=(
                f"{' '.join(args)}\nexit={result.returncode}\n"
                f"stdout={result.stdout}\nstderr={result.stderr}"
            ),
        )
        return result

    def login(self, email: str = DEMO, password: str = DEMO_PASSWORD) -> subprocess.CompletedProcess:
        return self.run_cli("auth", "login", "--email", email, "--password", password)

    def store(self) -> dict:
        return json.loads(self.data.read_text(encoding="utf-8"))


class SignedInTestCase(CliTestCase):
    """A fresh store with the seeded demo watchmaker already signed in."""

    def setUp(self) -> None:
        super().setUp()
        self.login()


class PendulumMathsTest(unittest.TestCase):
    def test_fast_clock_shortens_by_expected_amount(self) -> None:
        self.assertAlmostEqual(coggly.pendulum_change_mm(1000, 10), -0.2314814, places=6)

    def test_slow_clock_lengthens(self) -> None:
        self.assertAlmostEqual(coggly.pendulum_change_mm(1000, -10), 0.2314814, places=6)

    def test_rate_is_linear_in_length_and_error(self) -> None:
        self.assertAlmostEqual(
            coggly.pendulum_change_mm(2000, 10), 2 * coggly.pendulum_change_mm(1000, 10)
        )
        self.assertAlmostEqual(
            coggly.pendulum_change_mm(1000, 20), 2 * coggly.pendulum_change_mm(1000, 10)
        )


class PendulumCommandTest(CliTestCase):
    def test_fast_error_reports_shorten_with_inches(self) -> None:
        out = self.run_cli("pendulum", "correct", "--length", "1000", "--error", "10").stdout
        self.assertIn("shorten by 0.23 mm (0.009 in)", out)
        self.assertIn("999.77 mm (39.361 in)", out)
        self.assertIn("1000.00 mm (39.370 in)", out)

    def test_slow_error_reports_lengthen_with_inches(self) -> None:
        out = self.run_cli("pendulum", "correct", "--length", "1000", "--error", "-10").stdout
        self.assertIn("lengthen by 0.23 mm (0.009 in)", out)
        self.assertIn("1000.23 mm (39.379 in)", out)

    def test_every_run_explains_the_regulating_nut(self) -> None:
        out = self.run_cli("pendulum", "correct", "--length", "750", "--error", "-4").stdout
        self.assertIn("DOWN to lengthen", out)
        self.assertIn("UP to shorten", out)

    def test_zero_error_is_in_rate_not_an_empty_direction(self) -> None:
        out = self.run_cli("pendulum", "correct", "--length", "1000", "--error", "0").stdout
        self.assertIn("in rate", out)
        self.assertIn("0.00 mm (0.000 in)", out)
        self.assertNotIn("shorten by", out)
        self.assertNotIn("lengthen by", out)

    def test_no_flags_prints_usage_and_worked_example(self) -> None:
        result = self.run_cli("pendulum", "correct", expect=1)
        self.assertEqual(result.stdout, "")
        self.assertIn("coggly pendulum correct --length MM --error S_PER_DAY", result.stderr)
        self.assertIn("coggly pendulum correct --length 1000 --error 10", result.stderr)
        self.assertIn("999.77 mm", result.stderr)

    def test_zero_length_is_rejected(self) -> None:
        result = self.run_cli("pendulum", "correct", "--length", "0", "--error", "5", expect=1)
        self.assertIn("length must be greater than zero millimetres", result.stderr)

    def test_negative_length_is_rejected(self) -> None:
        result = self.run_cli("pendulum", "correct", "--length", "-900", "--error", "5", expect=1)
        self.assertIn("greater than zero millimetres", result.stderr)

    def test_non_numeric_error_is_rejected(self) -> None:
        result = self.run_cli("pendulum", "correct", "--length", "1000", "--error", "fast", expect=1)
        self.assertIn("between -600 and 600 seconds per day", result.stderr)

    def test_absurd_error_is_rejected(self) -> None:
        result = self.run_cli("pendulum", "correct", "--length", "1000", "--error", "5000", expect=1)
        self.assertIn("between -600 and 600 seconds per day", result.stderr)

    def test_pendulum_needs_no_account(self) -> None:
        # Nothing is read or written, so the calculator stays open while signed out.
        self.assertFalse(self.data.exists())
        out = self.run_cli("pendulum", "correct", "--length", "1000", "--error", "10").stdout
        self.assertIn("shorten by 0.23 mm", out)
        self.assertFalse(self.data.exists())


class AuthTest(CliTestCase):
    def test_seeded_demo_account_signs_in(self) -> None:
        result = self.login()
        self.assertIn("Signed in as", result.stdout)
        self.assertIn(DEMO, result.stdout)
        whoami = self.run_cli("auth", "whoami").stdout
        self.assertIn(DEMO, whoami)
        self.assertIn("owner", whoami)

    def test_demo_username_also_signs_in(self) -> None:
        out = self.run_cli("auth", "login", "--email", coggly.DEMO_USERNAME, "--password", DEMO_PASSWORD)
        self.assertIn(DEMO, out.stdout)

    def test_session_token_lives_in_the_store_as_a_hash(self) -> None:
        self.login()
        data = self.store()
        token = data.get("client_token", "")
        self.assertTrue(token)
        self.assertEqual(coggly.token_digest(token), data["sessions"][0]["token_hash"])
        self.assertEqual(len(data["sessions"][0]["token_hash"]), 64)  # sha256 hex
        # everything is in one file: nothing beside it in the directory
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name != "home"], ["store.json"])

    def test_password_is_never_stored_in_plaintext(self) -> None:
        secret = "***REDACTED-SECRET***"
        self.run_cli(
            "auth", "register", "--email", "m.okafor@example.com", "--username", "okafor",
            "--password-stdin", stdin=secret + "\n",
        )
        raw = self.data.read_text(encoding="utf-8")
        self.assertNotIn(secret, raw)
        user = next(u for u in self.store()["users"] if u["email"] == "m.okafor@example.com")
        self.assertTrue(user["password_hash"])
        self.assertTrue(user["password_salt"])
        self.assertTrue(coggly.verify_password(secret, user["password_salt"], user["password_hash"]))
        self.assertFalse(coggly.verify_password("wrong", user["password_salt"], user["password_hash"]))

    def test_register_signs_the_new_user_in(self) -> None:
        out = self.run_cli(
            "auth", "register", "--email", "a.bell@example.com", "--name", "A. Bell",
            "--password-stdin", stdin="bell-1901\n",
        ).stdout
        self.assertIn("Registered", out)
        self.assertIn("A. Bell", out)
        whoami = self.run_cli("auth", "whoami").stdout
        self.assertIn("a.bell@example.com", whoami)
        self.assertIn("watchmaker", whoami)

    def test_register_rejects_a_short_password(self) -> None:
        result = self.run_cli(
            "auth", "register", "--email", "short@example.com", "--password", "abc", expect=1
        )
        self.assertIn("at least 6 characters", result.stderr)
        self.assertFalse(self.data.exists())

    def test_register_rejects_a_bad_email(self) -> None:
        result = self.run_cli(
            "auth", "register", "--email", "not-an-email", "--password", "longenough", expect=1
        )
        self.assertIn("email address is not valid", result.stderr)

    def test_register_rejects_a_duplicate_email(self) -> None:
        self.login()
        result = self.run_cli(
            "auth", "register", "--email", DEMO, "--password", "longenough", expect=1
        )
        self.assertIn("already exists", result.stderr)

    def test_wrong_password_and_unknown_account_look_identical(self) -> None:
        wrong = self.run_cli("auth", "login", "--email", DEMO, "--password", "nope", expect=1)
        unknown = self.run_cli(
            "auth", "login", "--email", "nobody@example.com", "--password", "nope", expect=1
        )
        self.assertEqual(wrong.stderr, unknown.stderr)
        self.assertIn("email or password is not correct", wrong.stderr)
        self.assertNotIn("sign in", wrong.stdout)

    def test_logout_revokes_the_session_server_side(self) -> None:
        self.login()
        self.assertEqual(len(self.store()["sessions"]), 1)
        out = self.run_cli("auth", "logout").stdout
        self.assertIn("Signed out", out)
        self.assertEqual(self.store()["sessions"], [])
        self.assertNotIn("client_token", self.store())
        self.run_cli("auth", "whoami", expect=1)

    def test_logout_without_a_session_is_an_error(self) -> None:
        result = self.run_cli("auth", "logout", expect=1)
        self.assertIn("not signed in", result.stderr)

    def test_signed_out_visitor_is_sent_to_the_login(self) -> None:
        for command in (
            ("beat", "list"),
            ("beat", "add", "--movement", "P-42", "--error", "3"),
            ("beat", "summary"),
            ("beat", "remove", "0001"),
            ("service", "add", "--movement", "P-42", "--work", "oil", "--watchmaker", "A. Bell"),
            ("service", "list", "--movement", "P-42"),
            ("service", "movements"),
            ("auth", "whoami"),
        ):
            result = self.run_cli(*command, expect=1)
            self.assertIn("not signed in", result.stderr, msg=" ".join(command))
            self.assertIn("coggly auth login", result.stderr, msg=" ".join(command))
        self.assertFalse(self.data.exists())

    def test_signed_out_write_does_not_create_the_store(self) -> None:
        self.run_cli("beat", "add", "--movement", "P-42", "--error", "3", expect=1)
        self.assertFalse(self.data.exists())

    def test_a_stale_token_is_not_a_session(self) -> None:
        self.login()
        data = self.store()
        data["client_token"] = "not-a-real-token"
        self.data.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli("beat", "list", expect=1)
        self.assertIn("not signed in", result.stderr)


class BeatLogTest(SignedInTestCase):
    def test_first_reading_is_created_in_one_json_file(self) -> None:
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name != "home"], ["store.json"])
        self.run_cli("beat", "add", "--movement", "P-42", "--error", "4.5")
        store = self.store()
        self.assertEqual(store["schema_version"], coggly.SCHEMA_VERSION)
        self.assertEqual(len(store["beat_entries"]), 1)
        entry = store["beat_entries"][0]
        self.assertEqual(entry["movement"], "P-42")
        self.assertEqual(entry["error_spd"], 4.5)
        self.assertEqual(entry["recorded_on"], date.today().isoformat())
        self.assertEqual(entry["owner"], store["users"][0]["id"])

    def test_add_prints_id_movement_date_and_rate(self) -> None:
        out = self.run_cli("beat", "add", "--movement", "P-42", "--error", "4.5").stdout
        self.assertIn("0001", out)
        self.assertIn("P-42", out)
        self.assertIn(date.today().isoformat(), out)
        self.assertIn("4.5 s/day", out)
        self.assertIn("fast", out)

    def test_explicit_date_is_stored_and_listed(self) -> None:
        self.run_cli("beat", "add", "--movement", "P-42", "--error", "-9", "--date", "2024-03-01")
        out = self.run_cli("beat", "list").stdout
        self.assertIn("2024-03-01", out)
        self.assertIn("-9.0", out)
        self.assertEqual(self.store()["beat_entries"][0]["error_spd"], -9.0)

    def test_non_numeric_error_stores_nothing(self) -> None:
        result = self.run_cli("beat", "add", "--movement", "P-42", "--error", "abc", expect=1)
        self.assertIn("must be a number between -600 and 600 seconds per day", result.stderr)
        self.assertEqual(self.store()["beat_entries"], [])

    def test_out_of_range_error_stores_nothing(self) -> None:
        result = self.run_cli("beat", "add", "--movement", "P-42", "--error", "601", expect=1)
        self.assertIn("between -600 and 600", result.stderr)
        self.assertEqual(self.store()["beat_entries"], [])

    def test_missing_movement_is_rejected(self) -> None:
        result = self.run_cli("beat", "add", "--error", "3", expect=1)
        self.assertIn("movement number is required", result.stderr)

    def test_list_is_newest_first_and_filtered(self) -> None:
        self.run_cli("beat", "add", "--movement", "P-42", "--error", "2", "--date", "2024-01-05")
        self.run_cli("beat", "add", "--movement", "P-42", "--error", "6", "--date", "2024-02-05")
        self.run_cli("beat", "add", "--movement", "K-17", "--error", "1.1", "--date", "2024-03-05")
        out = self.run_cli("beat", "list", "--movement", "P-42").stdout
        self.assertNotIn("K-17", out)
        self.assertLess(out.index("2024-02-05"), out.index("2024-01-05"))
        self.assertIn("2 readings for P-42", out)

    def test_summary_reports_latest_count_mean_min_max(self) -> None:
        for error in ("2", "4", "6"):
            self.run_cli("beat", "add", "--movement", "P-42", "--error", error)
        out = self.run_cli("beat", "summary", "--movement", "P-42").stdout
        self.assertIn("Readings      3", out)
        self.assertIn("6.0 s/day", out)
        self.assertIn("4.0 s/day", out)
        self.assertIn("2.0 s/day", out)

    def test_empty_log_invites_the_exact_next_command(self) -> None:
        out = self.run_cli("beat", "list").stdout
        self.assertEqual(len([line for line in out.splitlines() if line.strip()]), 1)
        self.assertIn("No readings yet", out)
        self.assertIn("coggly beat add --movement P-42 --error 3.2", out)

    def test_remove_by_id_confirms_and_deletes(self) -> None:
        self.run_cli("beat", "add", "--movement", "P-42", "--error", "2")
        self.run_cli("beat", "add", "--movement", "P-42", "--error", "6")
        out = self.run_cli("beat", "remove", "0001").stdout
        self.assertIn("Removed reading 0001", out)
        self.assertIn("P-42", out)
        listing = self.run_cli("beat", "list").stdout
        self.assertNotIn("0001", listing)
        self.assertIn("0002", listing)

    def test_remove_unknown_id_leaves_other_readings(self) -> None:
        self.run_cli("beat", "add", "--movement", "P-42", "--error", "2")
        result = self.run_cli("beat", "remove", "9999", expect=1)
        self.assertIn("no reading has that id", result.stderr)
        self.assertEqual(len(self.store()["beat_entries"]), 1)

    def test_ids_are_never_reused(self) -> None:
        self.run_cli("beat", "add", "--movement", "P-42", "--error", "1")
        self.run_cli("beat", "add", "--movement", "P-42", "--error", "2")
        self.run_cli("beat", "remove", "0002")
        out = self.run_cli("beat", "add", "--movement", "P-42", "--error", "3").stdout
        self.assertIn("0003", out)
        ids = sorted(entry["id"] for entry in self.store()["beat_entries"])
        self.assertEqual(ids, ["0001", "0003"])

    def test_columns_stay_aligned_and_within_eighty_columns(self) -> None:
        self.run_cli("beat", "add", "--movement", "P-42", "--error", "2")
        self.run_cli("beat", "add", "--movement", "LONG-MOVEMENT-NUMBER-9931", "--error", "5.5")
        lines = self.run_cli("beat", "list").stdout.splitlines()
        for line in lines:
            self.assertLessEqual(len(line), 80, msg=line)
        header = next(line for line in lines if line.startswith("ID"))
        for row in (line for line in lines if line[:4].isdigit()):
            self.assertEqual(row.index("P-42") if "P-42" in row else row.index("LONG"), header.index("MOVEMENT"))


class ServiceHistoryTest(SignedInTestCase):
    def test_add_and_list_oldest_first_with_elapsed_days(self) -> None:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        long_ago = (date.today() - timedelta(days=400)).isoformat()
        self.run_cli(
            "service", "add", "--movement", "P-42", "--date", long_ago,
            "--work", "full service", "--watchmaker", "A. Bell",
        )
        self.run_cli(
            "service", "add", "--movement", "P-42", "--date", yesterday,
            "--work", "regulate", "--watchmaker", "A. Bell",
        )
        out = self.run_cli("service", "list", "--movement", "P-42").stdout
        self.assertLess(out.index(long_ago), out.index(yesterday))
        self.assertIn("400 days", out)
        self.assertIn("1 day", out)
        self.assertIn("full service", out)
        self.assertIn("A. Bell", out)
        self.assertIn("2 records for P-42", out)

    def test_add_prints_id_movement_date_and_work(self) -> None:
        out = self.run_cli(
            "service", "add", "--movement", "P-42", "--date", "2024-03-01",
            "--work", "full service", "--watchmaker", "A. Bell",
        ).stdout
        self.assertIn("0001", out)
        self.assertIn("P-42", out)
        self.assertIn("2024-03-01", out)
        self.assertIn("full service", out)

    def test_optional_cost_and_note_are_stored(self) -> None:
        self.run_cli(
            "service", "add", "--movement", "K-17", "--work", "oil",
            "--watchmaker", "M. Okafor", "--cost", "18.5", "--note", "worn bush",
        )
        record = self.store()["service_records"][0]
        self.assertEqual(record["cost"], 18.5)
        self.assertEqual(record["note"], "worn bush")

    def test_movements_index_lists_counts_and_last_service(self) -> None:
        self.run_cli(
            "service", "add", "--movement", "P-42", "--date", "2023-01-10",
            "--work", "full service", "--watchmaker", "A. Bell",
        )
        self.run_cli(
            "service", "add", "--movement", "P-42", "--date", "2024-03-01",
            "--work", "mainspring", "--watchmaker", "A. Bell",
        )
        self.run_cli(
            "service", "add", "--movement", "K-17", "--date", "2024-06-14",
            "--work", "oil", "--watchmaker", "M. Okafor",
        )
        out = self.run_cli("service", "movements").stdout
        self.assertIn("P-42", out)
        self.assertIn("K-17", out)
        self.assertIn("2024-03-01", out)
        self.assertIn("2 movements, 3 records", out)

    def test_missing_movement_is_rejected(self) -> None:
        result = self.run_cli("service", "add", "--work", "oil", expect=1)
        self.assertIn("movement number is required", result.stderr)
        self.assertEqual(self.store()["service_records"], [])

    def test_impossible_date_is_rejected(self) -> None:
        result = self.run_cli(
            "service", "add", "--movement", "P-42", "--date", "2024-13-40", "--work", "oil", expect=1
        )
        self.assertIn("real date in YYYY-MM-DD form", result.stderr)
        self.assertEqual(self.store()["service_records"], [])

    def test_movement_without_records_gets_an_invitation(self) -> None:
        out = self.run_cli("service", "list", "--movement", "ZZZ").stdout
        self.assertIn("No service records for ZZZ", out)
        self.assertIn("coggly service add --movement ZZZ --work", out)

    def test_empty_index_gets_an_invitation(self) -> None:
        out = self.run_cli("service", "movements").stdout
        self.assertIn("No service records yet", out)


class MovementIsolationTest(CliTestCase):
    def test_a_second_watchmaker_cannot_see_or_remove_other_records(self) -> None:
        self.login()
        self.run_cli("beat", "add", "--movement", "P-42", "--error", "4.5")
        self.run_cli("service", "add", "--movement", "P-42", "--work", "oil", "--watchmaker", "A. Bell")
        self.run_cli("auth", "logout")
        self.run_cli(
            "auth", "register", "--email", "other@example.com", "--password-stdin", stdin="secret-pass\n"
        )
        self.assertIn("No readings yet", self.run_cli("beat", "list").stdout)
        self.assertIn("No service records yet", self.run_cli("service", "movements").stdout)
        result = self.run_cli("beat", "remove", "0001", expect=1)
        self.assertIn("no reading has that id", result.stderr)
        owners = {entry["owner"] for entry in self.store()["beat_entries"]}
        self.assertEqual(len(owners), 1)
        self.assertEqual(len(self.store()["beat_entries"]), 1)

    def test_both_accounts_keep_their_own_rows(self) -> None:
        self.login()
        self.run_cli("beat", "add", "--movement", "P-42", "--error", "4.5")
        self.run_cli("auth", "logout")
        self.run_cli(
            "auth", "register", "--email", "other@example.com", "--password-stdin", stdin="secret-pass\n"
        )
        self.run_cli("beat", "add", "--movement", "K-17", "--error", "-2")
        listing = self.run_cli("beat", "list").stdout
        self.assertIn("K-17", listing)
        self.assertNotIn("P-42", listing)
        self.assertEqual(len(self.store()["beat_entries"]), 2)


class StoreTest(SignedInTestCase):
    def test_round_trip_between_invocations(self) -> None:
        self.run_cli("beat", "add", "--movement", "P-42", "--error", "3.2", "--note", "cold morning")
        self.run_cli("service", "add", "--movement", "P-42", "--work", "oil", "--watchmaker", "A. Bell")
        store = coggly.load_store(self.data)
        self.assertEqual(len(store.beat_entries), 1)
        self.assertEqual(store.beat_entries[0].note, "cold morning")
        self.assertEqual(len(store.service_records), 1)
        coggly.save_store(self.data, store)
        self.assertEqual(len(coggly.load_store(self.data).beat_entries), 1)

    def test_save_is_atomic_and_leaves_no_temp_files(self) -> None:
        self.run_cli("beat", "add", "--movement", "P-42", "--error", "1")
        self.run_cli("beat", "add", "--movement", "P-42", "--error", "2")
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name != "home"], ["store.json"])

    def test_corrupt_file_is_reported_and_left_untouched(self) -> None:
        self.data.write_text("{ this is not json", encoding="utf-8")
        before = self.data.read_bytes()
        result = self.run_cli("beat", "list", expect=1)
        self.assertIn(str(self.data), result.stderr)
        self.assertIn("not valid JSON", result.stderr)
        self.assertIn("will not overwrite it", result.stderr)
        self.assertEqual(self.data.read_bytes(), before)

    def test_file_without_schema_version_is_refused(self) -> None:
        self.data.write_text('{"beat_entries": []}', encoding="utf-8")
        before = self.data.read_bytes()
        result = self.run_cli("beat", "list", expect=1)
        self.assertIn("schema_version", result.stderr)
        self.assertEqual(self.data.read_bytes(), before)

    def test_data_override_keeps_the_home_file_untouched(self) -> None:
        self.run_cli("beat", "add", "--movement", "P-42", "--error", "5")
        self.assertFalse((self.home / ".coggly.json").exists())
        self.assertTrue(self.data.exists())

    def test_default_path_follows_home(self) -> None:
        self.run_cli(
            "auth", "login", "--email", DEMO, "--password", DEMO_PASSWORD, use_data_flag=False
        )
        self.run_cli(
            "beat", "add", "--movement", "P-42", "--error", "5", use_data_flag=False
        )
        home_file = self.home / ".coggly.json"
        self.assertTrue(home_file.exists())
        store = json.loads(home_file.read_text(encoding="utf-8"))
        self.assertEqual(store["schema_version"], coggly.SCHEMA_VERSION)
        self.assertEqual(len(store["beat_entries"]), 1)
        self.assertEqual([p.name for p in self.home.iterdir()], [".coggly.json"])

    def test_legacy_v1_file_is_migrated_to_the_demo_account(self) -> None:
        legacy = {
            "schema_version": 1,
            "beat_entries": [
                {"id": "0001", "movement": "P-42", "recorded_on": "2024-03-01", "error_spd": 4.5}
            ],
            "service_records": [],
            "next_ids": {"beat": 2, "service": 1},
        }
        self.data.write_text(json.dumps(legacy), encoding="utf-8")
        self.login()
        store = self.store()
        self.assertEqual(store["schema_version"], coggly.SCHEMA_VERSION)
        self.assertEqual(store["beat_entries"][0]["owner"], store["users"][0]["id"])
        self.assertIn("P-42", self.run_cli("beat", "list").stdout)


class CommandSurfaceTest(CliTestCase):
    def test_help_lists_the_groups(self) -> None:
        result = self.run_cli("--help")
        for group in ("auth", "beat", "pendulum", "service"):
            self.assertIn(group, result.stdout)
        self.assertIn("--data", result.stdout)

    def test_group_help_lists_its_commands(self) -> None:
        groups = {
            "auth": ("register", "login", "logout", "whoami"),
            "beat": ("add", "list", "summary", "remove"),
            "pendulum": ("correct",),
            "service": ("add", "list", "movements"),
        }
        for group, commands in groups.items():
            out = self.run_cli(group, "--help").stdout
            for command in commands:
                self.assertIn(command, out, msg=group)

    def test_login_help_shows_the_seeded_demo_account(self) -> None:
        out = self.run_cli("auth", "login", "--help").stdout
        self.assertIn(DEMO, out)
        self.assertIn(DEMO_PASSWORD, out)

    def test_unknown_command_prints_usage_and_exits_two(self) -> None:
        result = self.run_cli("frobnicate", expect=2)
        self.assertIn("usage: coggly", result.stderr)
        self.assertIn("invalid choice", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_bare_invocation_prints_help(self) -> None:
        out = self.run_cli().stdout
        self.assertIn("usage: coggly", out)

    def test_module_entry_point_works(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "coggly", "--version"],
            capture_output=True, text=True, env=self.env, cwd=str(ROOT), timeout=60,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn(coggly.VERSION, result.stdout)

    def test_errors_go_to_stderr_not_stdout(self) -> None:
        result = self.run_cli("auth", "login", "--email", DEMO, "--password", "wrong", expect=1)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.startswith("error: "))


if __name__ == "__main__":
    unittest.main()
