"""Frozen pilot membership, logical originals and scoped audit regression tests."""

import copy
import json
import subprocess
import sys
from unittest.mock import patch

import test_archive as fixtures
from explorer_ingest.archive_catalog import Archive, plan
from explorer_ingest.archive_limits import ValidationBudgetError, run_limited
from explorer_ingest.archive_selection import create_pilot
from explorer_ingest.archive_transfer import Options, fetch, verify


class PilotTests(fixtures.HTTPFixture):
    def prepare_three(self):
        self.archive.close()
        self.root = self.base / "pilot"
        self.root.mkdir()
        self.archive = Archive(self.root, create=True)
        data = fixtures.inventory(f"http://127.0.0.1:{self.server.server_port}/data/one.pdf")
        template = data["assets"]["asset-1"]
        files, assets = [], {}
        for index, identity in enumerate(("asset-z", "asset-a", "asset-m"), 1):
            node = {
                "id": f"file-{index}",
                "kind": "file",
                "name": f"{index}.pdf",
                "asset_id": identity,
            }
            files.append(node)
            asset = copy.deepcopy(template)
            asset.update(
                asset_id=identity,
                url=f"http://127.0.0.1:{self.server.server_port}/data/{index}.pdf",
                original_name=f"{index}.pdf",
            )
            asset["download_url"] = asset["url"]
            asset["occurrences"] = [{**template["occurrences"][0], "node_id": node["id"]}]
            assets[identity] = asset
        data["tree"]["children"][0]["children"] = files
        data["assets"] = assets
        data["validation"].update(
            unique_files_count=3,
            file_placements_count=3,
            known_unique_size_bytes=3 * len(fixtures.PDF),
        )
        self.source.write_text(json.dumps(data))
        plan(self.archive, self.source, layout="logical")

    def test_tree_order_membership_resume_and_scoped_audit(self):
        self.prepare_three()
        selected = create_pilot(self.archive, 2)
        self.assertEqual(selected["pilot"]["total"], 2)
        self.assertEqual(
            [
                r[0]
                for r in self.archive.db.execute("SELECT asset_id FROM pilot_assets ORDER BY rank")
            ],
            ["asset-z", "asset-a"],
        )
        partial = fetch(self.archive, self.options, limit=1, pilot=True)
        self.assertFalse(partial["pilot"]["download_complete"])
        finished = fetch(self.archive, self.options, pilot=True)
        self.assertTrue(finished["pilot"]["download_complete"])
        self.assertFalse(finished["download_complete"])
        self.assertEqual(len(self.requests), 2)
        fetch(self.archive, self.options, pilot=True)
        self.assertEqual(len(self.requests), 2)
        audited = verify(self.archive, self.options, pilot=True)
        self.assertTrue(audited["pilot"]["verified"])
        self.assertFalse(audited["complete_at_last_audit"])
        self.assertEqual(audited["pilot"]["audit"]["checked"], 2)
        self.assertEqual(audited["statuses"]["pending"], 1)
        manifest = self.root / ".pw010-download/pilot-verified-files.jsonl"
        self.assertEqual(len(manifest.read_text().splitlines()), 2)
        rows = self.archive.db.execute("SELECT relative_path FROM assets WHERE status='downloaded'")
        for row in rows:
            self.assertTrue(row[0].startswith("originals/DOJ--"))
            self.assertEqual((self.root / row[0]).read_bytes(), fixtures.PDF)

    def test_membership_cannot_expand_on_rerun_and_bounded_audit_stays_incomplete(self):
        self.prepare_three()
        create_pilot(self.archive, 2)
        create_pilot(self.archive, 2)
        with self.assertRaisesRegex(ValueError, "different fixed pilot"):
            create_pilot(self.archive, 3)
        fetch(self.archive, self.options, pilot=True)
        self.assertFalse(
            verify(self.archive, self.options, limit=1, pilot=True)["pilot"]["verified"]
        )
        self.assertTrue(verify(self.archive, self.options, pilot=True)["pilot"]["verified"])

    def test_corruption_fails_pilot_audit(self):
        self.prepare_three()
        create_pilot(self.archive, 1)
        fetch(self.archive, self.options, pilot=True)
        row = self.archive.db.execute(
            "SELECT a.* FROM pilot_assets p JOIN assets a ON a.id=p.asset_id"
        ).fetchone()
        path = self.root / row["relative_path"]
        path.write_bytes(b"X" * path.stat().st_size)
        result = verify(self.archive, self.options, pilot=True)
        self.assertFalse(result["pilot"]["verified"])
        self.assertEqual(result["pilot"]["statuses"]["failed"], 1)

    def test_monitor_timeout_recovers_or_returns_bounded_validation_error(self):
        calls = 0

        def recovered_monitor(pid):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise subprocess.TimeoutExpired("ps", 1)
            return 0

        with patch("explorer_ingest.archive_limits.resident_bytes", side_effect=recovered_monitor):
            self.assertEqual(run_limited([sys.executable, "-c", "print('{}')"], {}, Options()), {})
        with patch(
            "explorer_ingest.archive_limits.resident_bytes",
            side_effect=subprocess.TimeoutExpired("ps", 1),
        ):
            with self.assertRaisesRegex(ValidationBudgetError, "three attempts"):
                run_limited([sys.executable, "-c", "import time; time.sleep(10)"], {}, Options())

    def test_cookie_session_is_used_without_logging_values(self):
        import http.cookiejar
        from dataclasses import replace

        self.prepare_three()
        create_pilot(self.archive, 1)
        filename = str(self.base / "session.cookies.txt")
        jar = http.cookiejar.MozillaCookieJar(filename)
        jar.set_cookie(
            http.cookiejar.Cookie(
                0,
                "age-session",
                "synthetic-private-value",
                None,
                False,
                "127.0.0.1",
                False,
                False,
                "/",
                True,
                False,
                None,
                True,
                None,
                None,
                {},
                False,
            )
        )
        jar.save(ignore_discard=True)
        result = fetch(self.archive, replace(self.options, cookie_file=filename), pilot=True)
        self.assertTrue(result["pilot"]["download_complete"])
        self.assertIn("age-session=synthetic-private-value", self.requests[-1]["Cookie"])
        self.assertNotIn(
            "synthetic-private-value", (self.root / ".pw010-download/events.jsonl").read_text()
        )

    def test_cli_pilot_reports_success_independently_of_full_archive(self):
        from pathlib import Path

        self.prepare_three()
        self.archive.close()
        config = self.base / "options.json"
        config.write_text(
            json.dumps(
                {
                    "allow_hosts": ["127.0.0.1"],
                    "allow_http": True,
                    "request_delay": 0,
                    "reserve_bytes": 0,
                }
            )
        )
        command = [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "scripts/download_archive.py"),
            "--destination",
            str(self.root),
            "--config",
            str(config),
        ]
        for arguments, expected in [
            (["pilot", "--first", "2"], 0),
            (["fetch", "--pilot", "--limit", "1"], 2),
            (["fetch", "--pilot"], 0),
            (["verify", "--pilot"], 0),
        ]:
            result = subprocess.run(command + arguments, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, expected, result.stderr + result.stdout)
        self.assertTrue(json.loads(result.stdout)["pilot"]["verified"])
        self.archive = Archive(self.root)

    def test_logical_names_are_safe_and_same_name_assets_remain_distinct(self):
        self.archive.close()
        self.root = self.base / "collision"
        self.root.mkdir()
        self.archive = Archive(self.root, create=True)
        data = fixtures.inventory()
        second = copy.deepcopy(data["assets"]["asset-1"])
        second.update(
            asset_id="asset-2",
            url="https://www.justice.gov/second.pdf",
            download_url="https://www.justice.gov/second.pdf",
        )
        second["occurrences"] = [second["occurrences"].pop()]
        data["assets"]["asset-1"]["occurrences"] = data["assets"]["asset-1"]["occurrences"][:1]
        data["assets"]["asset-2"] = second
        data["tree"]["children"][0]["name"] = "../unsafe/group"
        data["tree"]["children"][0]["children"][1]["asset_id"] = "asset-2"
        data["validation"].update(
            unique_files_count=2, known_unique_size_bytes=2 * len(fixtures.PDF)
        )
        self.source.write_text(json.dumps(data))
        plan(self.archive, self.source, layout="logical")
        paths = [row[0] for row in self.archive.db.execute("SELECT relative_path FROM assets")]
        self.assertEqual(len(set(paths)), 2)
        self.assertTrue(all(".." not in path.split("/") for path in paths))
        with self.assertRaisesRegex(ValueError, "original layout"):
            plan(self.archive, self.source, layout="url")

    def test_source_etag_change_is_rejected(self):
        self.prepare_three()
        create_pilot(self.archive, 1)
        row = self.archive.db.execute(
            "SELECT a.* FROM pilot_assets p JOIN assets a ON a.id=p.asset_id"
        ).fetchone()
        metadata = json.loads(row["metadata"])
        metadata["etag"] = '"another-source-version"'
        with self.archive.db:
            self.archive.db.execute(
                "UPDATE assets SET metadata=? WHERE id=?", (json.dumps(metadata), row["id"])
            )
        result = fetch(self.archive, self.options, pilot=True)
        self.assertEqual(result["pilot"]["statuses"]["failed"], 1)
        self.assertFalse((self.root / row["relative_path"]).exists())
        self.assertEqual(len(self.requests), 1)

    def test_http_date_retry_after_is_honored(self):
        from datetime import datetime, timedelta, timezone
        from email.utils import format_datetime

        self.mode = "rate-limit"
        self.retry_after = format_datetime(
            datetime.now(timezone.utc) + timedelta(seconds=90), usegmt=True
        )
        with patch("explorer_ingest.archive_transfer.time.sleep") as sleep:
            self.assertTrue(fetch(self.archive, self.options)["download_complete"])
        self.assertTrue(any(80 < call.args[0] <= 90 for call in sleep.call_args_list))

    def test_new_ledger_has_bounded_external_disk_cache_and_page_size(self):
        self.assertEqual(self.archive.db.execute("PRAGMA page_size").fetchone()[0], 65536)
        self.assertEqual(self.archive.db.execute("PRAGMA cache_size").fetchone()[0], -262144)

    def test_pilot_summary_scans_membership_before_large_registry(self):
        from explorer_ingest.archive_selection import pilot_status

        self.prepare_three()
        create_pilot(self.archive, 2)
        queries = []
        self.archive.db.set_trace_callback(queries.append)
        try:
            pilot_status(self.archive)
        finally:
            self.archive.db.set_trace_callback(None)
        for query in queries:
            if "JOIN assets a" in query:
                steps = [row[3] for row in self.archive.db.execute("EXPLAIN QUERY PLAN " + query)]
                self.assertFalse(any(step.startswith("SCAN a") for step in steps), steps)
                self.assertTrue(steps[0].startswith("SCAN p"), steps)

    def test_cli_signal_interrupts_sql_and_releases_writer(self):
        import os
        import signal
        import time
        from pathlib import Path

        self.prepare_three()
        self.archive.close()
        ready = self.base / "ready"
        code = """
import sys
from pathlib import Path
import explorer_ingest.archive_cli as cli
def slow(archive,*args,**kwargs):
 Path(sys.argv[3]).write_text('ready')
 archive.db.execute('WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<1000000000) SELECT sum(x) FROM n').fetchone()
cli.plan=slow
raise SystemExit(cli.main(['--destination',sys.argv[1],'plan','--dump',sys.argv[2],'--layout','logical']))
"""
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(self.root), str(self.source), str(ready)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join(
                    [str(Path(__file__).resolve().parents[1]), os.environ.get("PYTHONPATH", "")]
                ),
            },
        )
        try:
            deadline = time.monotonic() + 5
            while not ready.exists():
                if process.poll() is not None or time.monotonic() > deadline:
                    self.fail("SQL cancellation worker did not become ready")
                time.sleep(0.02)
            process.send_signal(signal.SIGTERM)
            output, error = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 2, error + output)
            self.assertEqual(json.loads(output)["status"], "paused")
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
            self.archive = Archive(self.root)

    def test_selection_manifest_recovers_after_interrupted_publication(self):
        self.prepare_three()
        create_pilot(self.archive, 2)
        path = self.root / ".pw010-download/pilot.json"
        original = json.loads(path.read_text())
        path.unlink()
        create_pilot(self.archive, 2)
        self.assertEqual(json.loads(path.read_text()), original)

    def test_cli_restores_callers_temporary_directory_settings(self):
        import contextlib
        import io
        import os
        import tempfile

        from explorer_ingest.archive_cli import main

        self.prepare_three()
        self.archive.close()
        before = tempfile.tempdir, {key: os.environ.get(key) for key in ("TMPDIR", "SQLITE_TMPDIR")}
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(["--destination", str(self.root), "pilot", "--first", "1"]), 0
                )
            self.assertEqual(
                (
                    tempfile.tempdir,
                    {key: os.environ.get(key) for key in ("TMPDIR", "SQLITE_TMPDIR")},
                ),
                before,
            )
        finally:
            self.archive = Archive(self.root)

    def test_interrupted_reaudit_clears_previous_pilot_certificate(self):
        from explorer_ingest.archive_selection import pilot_status

        self.prepare_three()
        create_pilot(self.archive, 1)
        fetch(self.archive, self.options, pilot=True)
        self.assertTrue(verify(self.archive, self.options, pilot=True)["pilot"]["verified"])
        with patch(
            "explorer_ingest.archive_transfer.validate_content", side_effect=KeyboardInterrupt
        ):
            with self.assertRaises(KeyboardInterrupt):
                verify(self.archive, self.options, pilot=True)
        self.assertFalse(pilot_status(self.archive)["verified"])
        self.assertFalse((self.root / ".pw010-download/pilot-verified-files.jsonl").exists())

    def test_pilot_integrity_checks_selected_membership_and_reports_scope(self):
        self.prepare_three()
        create_pilot(self.archive, 1)
        fetch(self.archive, self.options, pilot=True)
        queries = []
        self.archive.db.set_trace_callback(queries.append)
        try:
            result = verify(self.archive, self.options, pilot=True)
        finally:
            self.archive.db.set_trace_callback(None)
        self.assertTrue(result["pilot"]["verified"])
        self.assertEqual(result["pilot"]["audit"]["scope"], "pilot")
        self.assertIn("PRAGMA integrity_check('pilot_assets')", queries)
        self.assertIn("PRAGMA foreign_key_check('pilot_assets')", queries)
        self.assertNotIn("PRAGMA quick_check", queries)

    def test_older_sqlite_retains_full_integrity_fallback(self):
        self.prepare_three()
        create_pilot(self.archive, 1)
        fetch(self.archive, self.options, pilot=True)
        with patch("explorer_ingest.archive_transfer.sqlite3.sqlite_version_info", (3, 32, 0)):
            result = verify(self.archive, self.options, pilot=True)
        self.assertTrue(result["pilot"]["verified"])
        self.assertEqual(result["pilot"]["audit"]["ledger_integrity_scope"], "archive")
