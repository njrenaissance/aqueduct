"""webenum row mapping + CSV provenance."""

from __future__ import annotations

import csv

from onedrive_enum import webenum


def test_created_iso_parses_sharepoint_format():
    assert webenum._created_iso("0;#2026-08-12 14:41:42") == "2026-08-12T14:41:42"


def test_created_iso_passthrough_when_no_marker():
    assert webenum._created_iso(None) is None
    assert webenum._created_iso("plain") == "plain"


def test_row_to_item_file():
    row = {
        "FileRef": "/personal/u/Documents/root/sub/a.txt",
        "FSObjType": "0",
        "File_x0020_Size": "10",
        "UniqueId": "{U}",
        "_UIVersionString": "2.0",
    }
    rec = webenum._row_to_item(row, "/personal/u/Documents/root/", "https://host")
    assert rec["path"] == "sub/a.txt"
    assert rec["type"] == "file"
    assert rec["size"] == 10
    assert rec["quickXorHash"] is None
    assert rec["webUrl"] == "https://host/personal/u/Documents/root/sub/a.txt"


def test_row_to_item_folder():
    row = {"FileRef": "/root/sub", "FSObjType": "1", "ItemChildCount": "3"}
    rec = webenum._row_to_item(row, "/root/", "https://host")
    assert rec["type"] == "folder"
    assert rec["childCount"] == 3


def test_write_csv_has_provenance_comments_and_rows(tmp_path):
    manifest = {
        "tool": "webenum",
        "tool_version": "0.1.0",
        "source": "web-session",
        "share_url": "https://host/:f:/r/share",
        "enumerated_at_utc": "2026-09-04T03:37:59+00:00",
        "enumerated_by": "user@example.com",
        "counts": {"files": 1, "folders": 1, "total_bytes": 10},
        "items": [
            {"path": "sub", "type": "folder", "size": 0},
            {
                "path": "sub/a.txt",
                "type": "file",
                "size": 10,
                "modified": "2026-08-25T18:27:05Z",
                "created": "2026-08-25T11:27:05",
                "version": "2.0",
                "id": "{U}",
                "guid": "{G}",
                "webUrl": "https://host/sub/a.txt",
                "fileRef": "/personal/u/Documents/sub/a.txt",
            },
        ],
    }
    out = tmp_path / "m.csv"
    n = webenum.write_csv(manifest, out)
    assert n == 2

    lines = out.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("# onedrive-enum manifest")
    assert any(line.startswith("# share_url: https://host/:f:/r/share") for line in lines)

    # After the comment lines, a csv reader still sees the header + 2 data rows.
    rows = list(csv.reader(line for line in lines if not line.startswith("#")))
    assert rows[0][0] == "path"
    assert len(rows) == 1 + 2
