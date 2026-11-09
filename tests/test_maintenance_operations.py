import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from modules import network
from modules.extractor import extract_levels
from modules.operation_context import OperationCancelled, OperationContext
from modules.operations import run_maintenance_operation


class MaintenanceOperationTests(unittest.TestCase):
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
