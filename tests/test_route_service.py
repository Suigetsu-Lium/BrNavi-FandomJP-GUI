import csv
import tempfile
import unittest
from pathlib import Path

from modules.route_service import build_route_context, load_route_graph


class RouteServiceTests(unittest.TestCase):
    def test_network_node_values_take_priority_over_older_summary_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (root / "backrooms_nodes.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["Id", "Label", "Comprehension", "Danger"])
                writer.writeheader()
                writer.writerow({"Id": "Level 0", "Label": "Level 0", "Comprehension": "60%", "Danger": "条件付き（最新値）"})
            with (root / "extracted_fandom_levels_summary.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["階層コード", "危険度", "理解度"])
                writer.writeheader()
                writer.writerow({"階層コード": "Level 0", "理解度": "20%", "危険度": "0/5"})

            nodes, _, _ = load_route_graph(root)

        self.assertEqual(nodes["Level 0"]["danger"], "条件付き（最新値）")
        self.assertEqual(nodes["Level 0"]["comprehension"], "60%")

    def test_context_finds_exact_conditional_route_for_advanced_roamer_query(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (root / "backrooms_nodes.csv").open(
                "w", encoding="utf-8", newline=""
            ) as stream:
                writer = csv.DictWriter(
                    stream, fieldnames=["Id", "Label", "Comprehension", "Danger"]
                )
                writer.writeheader()
                writer.writerows([
                    {"Id": "Level 0", "Label": "Level 0"},
                    {"Id": "Level 200 η", "Label": "The Perfect World"},
                ])
            with (root / "backrooms_edges.csv").open(
                "w", encoding="utf-8", newline=""
            ) as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=["Source", "Target", "Type", "Category", "Condition"],
                )
                writer.writeheader()
                writer.writerow({
                    "Source": "Level 0",
                    "Target": "Level 200 η",
                    "Type": "Directed",
                    "Category": "Exit",
                    "Condition": "特異点の力を召喚する",
                })

            context = build_route_context(
                "Level 0からLevel 200 ηへ行く条件の『特異点の力を召喚する』とは？",
                base_dir=root,
            )

        self.assertIn("接続条件と一致", context)
        self.assertIn("Level 0 → Level 200 η", context)
        self.assertIn("特異点の力を召喚する", context)


if __name__ == "__main__":
    unittest.main()
