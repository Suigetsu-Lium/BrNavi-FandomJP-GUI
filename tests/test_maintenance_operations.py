import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modules import downloader
from modules import network
from modules.extractor import extract_levels
from modules.operation_context import OperationCancelled, OperationContext
from modules.operations import (
    get_level_list_languages,
    is_supported_level_list_url,
    load_level_list_pages,
    run_maintenance_operation,
)


class MaintenanceOperationTests(unittest.TestCase):
    def test_level_list_pages_parse_metadata_and_exclude_unsupported_urls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            list_path = Path(temp_dir) / "JP.txt"
            list_path.write_text(
                "[通常階層] (normal)\n"
                "[0001] Level 0\n"
                "URL: https://backrooms.fandom.com/wiki/Level_0\n"
                "SOURCE: official\n"
                "INDEX: 0\n"
                "TRANSLATION_SECTION: level_0\n"
                "[0002] Unsafe page\n"
                "URL: https://example.com/wiki/Unsafe\n"
                "[0003] Insecure page\n"
                "URL: http://backrooms.fandom.com/wiki/Insecure\n",
                encoding="utf-8",
            )

            with patch("modules.operations.LEVEL_LIST_DIR", Path(temp_dir)):
                pages = load_level_list_pages("JP")

        self.assertEqual(len(pages), 1)
        self.assertEqual(
            pages[0],
            {
                "title": "Level 0",
                "url": "https://backrooms.fandom.com/wiki/Level_0",
                "category": "normal",
                "category_label": "通常階層",
                "language": "JP",
                "source_type": "official",
                "indexes": "0",
                "translation_section": "level_0",
            },
        )

    def test_level_list_language_discovery_ignores_summary_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "JP.txt").touch()
            (root / "en.txt").touch()
            (root / "_summary.txt").touch()

            with patch("modules.operations.LEVEL_LIST_DIR", root):
                languages = get_level_list_languages()

        self.assertEqual(languages, ["en", "JP"])

    def test_level_list_loader_rejects_invalid_language_path(self):
        with self.assertRaises(ValueError):
            load_level_list_pages("../secrets")

    def test_level_list_download_reports_download_skip_and_failure_counts(self):
        pages = [
            {"title": "Existing", "url": "https://backrooms.fandom.com/wiki/Existing"},
            {"title": "New", "url": "https://backrooms.fandom.com/wiki/New"},
            {"title": "Failed", "url": "https://backrooms.fandom.com/wiki/Failed"},
        ]

        with (
            patch(
                "modules.operations.find_existing_html",
                side_effect=["existing.html", None, None],
            ),
            patch(
                "modules.operations.download_page",
                side_effect=[Path("existing.html"), Path("new.html"), None],
            ) as download,
        ):
            result = run_maintenance_operation(
                "download_level_list_html",
                options={"pages": pages, "overwrite": False},
            )

        self.assertFalse(result.success)
        self.assertEqual(
            result.details,
            {"downloaded": 1, "skipped": 1, "failed": 1},
        )
        self.assertEqual(download.call_count, 3)

    def test_html_download_cancellation_before_replace_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            existing_file = output_dir / "Level_0.html"
            existing_file.write_text("existing HTML", encoding="utf-8")
            cancel_event = threading.Event()
            context = OperationContext(cancel_event=cancel_event)

            def cancel_while_formatting(_html, _page):
                cancel_event.set()
                return "new HTML"

            with (
                patch.object(downloader, "OUTPUT_DIR", output_dir),
                patch.object(
                    downloader,
                    "find_existing_html",
                    return_value=existing_file,
                ),
                patch.object(
                    downloader.requests,
                    "get",
                    return_value=SimpleNamespace(
                        status_code=200,
                        text="<html>new</html>",
                    ),
                ),
                patch.object(
                    downloader,
                    "modify_html",
                    side_effect=cancel_while_formatting,
                ),
            ):
                with self.assertRaises(OperationCancelled):
                    downloader.download_url(
                        "https://backrooms.fandom.com/wiki/Level_0",
                        page_info={
                            "title": "Level 0",
                            "url": "https://backrooms.fandom.com/wiki/Level_0",
                        },
                        overwrite=True,
                        show_detail=False,
                        context=context,
                    )

            self.assertEqual(
                existing_file.read_text(encoding="utf-8"),
                "existing HTML",
            )
            self.assertEqual(list(output_dir.glob(".Level_0-*")), [])

    def test_supported_level_list_url_is_limited_to_wiki_pages(self):
        self.assertTrue(
            is_supported_level_list_url(
                "https://backrooms.fandom.com/wiki/Level_0"
            )
        )
        self.assertFalse(
            is_supported_level_list_url(
                "https://backrooms.fandom.com/wiki"
            )
        )
        self.assertFalse(
            is_supported_level_list_url(
                "https://backrooms.fandom.com.evil.example/wiki/Level_0"
            )
        )

    def test_update_master_routes_explicit_mode_to_noninteractive_service(self):
        context = OperationContext()

        with patch("modules.operations.execute_update", return_value=True) as update:
            result = run_maintenance_operation(
                "update_master",
                options={"mode": 2},
                context=context,
            )

        self.assertTrue(result.success)
        update.assert_called_once_with(2, context=context)

    def test_extract_cancellation_preserves_existing_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            html_dir = root / "html"
            html_dir.mkdir()
            (html_dir / "Level_0.html").write_text("<html></html>", encoding="utf-8")
            output_dir = root / "levels"
            output_dir.mkdir()
            existing_txt = output_dir / "Level 0.txt"
            existing_txt.write_text("existing article", encoding="utf-8")
            summary_csv = root / "summary.csv"
            summary_csv.write_text("existing summary", encoding="utf-8")
            context = OperationContext()
            context.cancel_event.set()

            with self.assertRaises(OperationCancelled):
                extract_levels(
                    html_dir=html_dir,
                    output_dir=output_dir,
                    summary_csv=summary_csv,
                    context=context,
                )

            self.assertEqual(existing_txt.read_text(encoding="utf-8"), "existing article")
            self.assertEqual(summary_csv.read_text(encoding="utf-8"), "existing summary")

    def test_network_cancellation_preserves_existing_csv_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.txt"
            input_path.write_text("Level 0: Example\n", encoding="utf-8")
            edges_path = root / "edges.csv"
            nodes_path = root / "nodes.csv"
            edges_path.write_text("existing edges", encoding="utf-8")
            nodes_path.write_text("existing nodes", encoding="utf-8")
            cancel_event = threading.Event()
            context = OperationContext(
                cancel_event=cancel_event,
                on_progress=lambda *_: cancel_event.set(),
            )

            with patch.multiple(
                network,
                INPUT_FILE=str(input_path),
                EDGES_OUTPUT_FILE=str(edges_path),
                NODES_OUTPUT_FILE=str(nodes_path),
            ):
                with self.assertRaises(OperationCancelled):
                    network.process_backrooms_data(context=context)

            self.assertEqual(edges_path.read_text(encoding="utf-8"), "existing edges")
            self.assertEqual(nodes_path.read_text(encoding="utf-8"), "existing nodes")
            self.assertEqual(list(root.glob(".brnavi-network-*")), [])

    def test_network_replaces_both_csv_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.txt"
            input_path.write_text(
                "Level 0: Example\n[出口]\nLevel 1\n",
                encoding="utf-8",
            )
            edges_path = root / "edges.csv"
            nodes_path = root / "nodes.csv"

            with patch.multiple(
                network,
                INPUT_FILE=str(input_path),
                EDGES_OUTPUT_FILE=str(edges_path),
                NODES_OUTPUT_FILE=str(nodes_path),
            ):
                self.assertTrue(network.process_backrooms_data())

            self.assertIn("Level 0", nodes_path.read_text(encoding="utf-8"))
            self.assertIn("Level 1", nodes_path.read_text(encoding="utf-8"))
            self.assertIn("Level 1", edges_path.read_text(encoding="utf-8"))
            self.assertEqual(list(root.glob(".brnavi-network-*")), [])

    def test_network_rolls_back_first_csv_if_second_replace_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            edges_path = root / "edges.csv"
            nodes_path = root / "nodes.csv"
            edges_path.write_text("old edges", encoding="utf-8")
            nodes_path.write_text("old nodes", encoding="utf-8")
            edges_staging = root / "edges.tmp"
            nodes_staging = root / "nodes.tmp"
            edges_staging.write_text("new edges", encoding="utf-8")
            nodes_staging.write_text("new nodes", encoding="utf-8")
            replace = network.os.replace

            def replace_with_failure(source, target):
                if Path(target) == nodes_path and Path(source) == nodes_staging:
                    raise PermissionError("test replacement failure")
                return replace(source, target)

            with patch(
                "modules.network.os.replace",
                side_effect=replace_with_failure,
            ):
                with self.assertRaises(PermissionError):
                    network._replace_csv_outputs(
                        [
                            (str(edges_path), str(edges_staging)),
                            (str(nodes_path), str(nodes_staging)),
                        ]
                    )

            self.assertEqual(edges_path.read_text(encoding="utf-8"), "old edges")
            self.assertEqual(nodes_path.read_text(encoding="utf-8"), "old nodes")
            self.assertEqual(list(root.glob(".brnavi-network-backup-*")), [])


if __name__ == "__main__":
    unittest.main()
