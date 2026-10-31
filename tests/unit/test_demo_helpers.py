"""The parts of scripts/demo.py that need no server or database."""

import importlib.util
import re
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "demo_script", Path(__file__).resolve().parents[2] / "scripts" / "demo.py"
)
demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo)


def test_generated_pdf_is_structurally_valid():
    pdf = demo.make_pdf("abc123")
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    assert demo.MARKER.encode() in pdf and b"abc123" in pdf
    # every xref entry must point exactly at its "N 0 obj" header
    xref_start = int(re.search(rb"startxref\n(\d+)\n", pdf).group(1))
    assert pdf[xref_start:].startswith(b"xref")
    offsets = [int(m) for m in re.findall(rb"(\d{10}) 00000 n ", pdf)]
    assert len(offsets) == 5
    for number, offset in enumerate(offsets, start=1):
        assert pdf[offset:].startswith(f"{number} 0 obj".encode())


def test_each_run_makes_different_bytes_and_the_same_run_makes_identical_bytes():
    assert demo.make_pdf("one") != demo.make_pdf("two")
    assert demo.sha(demo.make_pdf("same")) == demo.sha(demo.make_pdf("same"))  # duplicate upload


def test_table_prints_headers_rows_and_an_empty_marker(capsys):
    demo.table(["file", "n"], [["a.pdf", 1], ["b.pdf", None]])
    demo.table(["x"], [])
    out = capsys.readouterr().out
    assert "a.pdf" in out and "b.pdf" in out and "(no rows)" in out
    assert out.splitlines()[1].strip().startswith("----")


def test_check_records_pass_and_fail(capsys):
    before = len(demo.CHECKS)
    demo.check("good thing", True)
    demo.check("bad thing", False, "detail")
    out = capsys.readouterr().out
    assert "[PASS] good thing" in out and "[FAIL] bad thing  (detail)" in out
    assert demo.CHECKS[before:] == [("good thing", True), ("bad thing", False)]
    del demo.CHECKS[before:]


def test_only_demo_accounts_can_have_their_role_changed():
    class Conn:
        def cursor(self):
            raise AssertionError("must not reach the database")

    db = demo.Db.__new__(demo.Db)
    db.conn = Conn()
    with pytest.raises(AssertionError):
        db.set_role("admin@example.com", "ADMIN")


def test_api_client_refuses_non_http_urls():
    with pytest.raises(ValueError):
        demo.Api("file:///etc").call("GET", "/x")
