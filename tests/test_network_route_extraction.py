import csv

from modules import network


def jp(*codes):
    return "".join(chr(code) for code in codes)


def test_network_csv_keeps_certainty_mixed_endpoints_and_explicit_direction(tmp_path, monkeypatch):
    html_dir = tmp_path / "html"
    html_dir.mkdir()
    title = "Level 8"
    uncertain_heading = "[" + jp(0x4e0d, 0x78ba, 0x5b9a, 0x306a, 0x51fa, 0x53e3) + "]"
    confirmed_heading = "[" + jp(0x78ba, 0x5b9a, 0x3057, 0x305f, 0x5165, 0x53e3) + "]"
    uncertain_line = (
        "Level 8" + jp(0x3067, 0x4e00, 0x822c, 0x7684, 0x306b) +
        "Frontrooms" + jp(0x3067, 0x898b, 0x3089, 0x308c, 0x308b) +
        " phone booth" + jp(0x306b, 0x5165, 0x308b, 0x3068) +
        "Level 9" + jp(0x3078, 0x79fb, 0x52d5, 0x3059, 0x308b, 0x3002)
    )
    confirmed_line = (
        "Level 7" + jp(0x304b, 0x3089, 0x5165, 0x308b, 0x3053, 0x3068, 0x304c, 0x3067, 0x304d, 0x308b)
    )
    html = (
        '<html><h1 id="firstHeading"><span class="mw-page-title-main">Level 8</span></h1>'
        '<div class="mw-parser-output">'
        f'<h2>{uncertain_heading}</h2><p>{uncertain_line}</p>'
        f'<h2>{confirmed_heading}</h2><p>{confirmed_line}</p>'
        '</div></html>'
    )
    (html_dir / "Level_8.html").write_text(html, encoding="utf-8")

    monkeypatch.setattr(network, "HTML_DIR", str(html_dir))
    monkeypatch.setattr(network, "EDGES_OUTPUT_FILE", str(tmp_path / "backrooms_edges.csv"))
    monkeypatch.setattr(network, "NODES_OUTPUT_FILE", str(tmp_path / "backrooms_nodes.csv"))
    monkeypatch.setattr(network, "SUMMARY_CSV", str(tmp_path / "summary.csv"))

    assert network.process_backrooms_data() is True
    with (tmp_path / "backrooms_edges.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))

    explicit = next(row for row in rows if row["Source"] == "Level 8" and row["Target"] == "Level 9")
    assert explicit["Certainty"] == "Uncertain"
    assert explicit["Evidence"] == "ExplicitTransition"
    assert explicit["SourceKind"] == "Level"
    assert explicit["TargetKind"] == "Level"

    incoming = next(row for row in rows if row["Source"] == "Level 7" and row["Target"] == "Level 8")
    assert incoming["Category"] == "Entrance"
    assert incoming["Certainty"] == "Confirmed"
