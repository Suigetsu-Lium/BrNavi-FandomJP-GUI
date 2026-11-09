import csv
import tempfile
import unittest
from pathlib import Path

from modules.route_service import build_route_context


class RouteServiceTests(unittest.TestCase):
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
