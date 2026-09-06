"""filecopy URL construction: downloads must go through download.aspx (which
honors Range) with the server-relative path percent-encoded."""

from __future__ import annotations

from aqueduct import filecopy


def test_download_url_uses_download_aspx_and_encodes_path():
    url = filecopy._download_url("https://host/personal/u", "/personal/u/Documents/a b.txt")
    assert url.startswith("https://host/personal/u/_layouts/15/download.aspx?SourceUrl=")
    # spaces and slashes in the source path are percent-encoded
    assert "%2FDocuments%2Fa%20b.txt" in url
