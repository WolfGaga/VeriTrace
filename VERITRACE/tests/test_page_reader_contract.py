from contextlib import nullcontext
from io import BytesIO
import logging
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pdfplumber
import pytest
import requests

from veritrace.agent.core import page_reader
from veritrace.agent.core.page_reader import PageSelectTool
from veritrace.agent.core.tools import LRUCache


def response(content, encoding="utf-8"):
    value = requests.Response()
    value.status_code = 200
    value._content = content.encode(encoding) if isinstance(content, str) else content
    value.encoding = encoding
    return value


@pytest.mark.parametrize("first_page", ["Alpha", ""])
def test_browser_pdf_summary_matches_page_limit_output_and_cleanup(monkeypatch, caplog, first_page):
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("PDF_MAX_PAGES", "1")
    paths, cleanup = [], []

    def render(path, **kwargs):
        paths.append(Path(path))
        paths[-1].write_bytes(b"controlled PDF")

    page = SimpleNamespace(goto=lambda *args, **kwargs: None,
                           wait_for_load_state=lambda *args, **kwargs: None,
                           wait_for_timeout=lambda *args, **kwargs: None,
                           pdf=render, close=lambda: cleanup.append("page"))
    monkeypatch.setattr(page_reader, "_get_playwright_browser", lambda:
                        SimpleNamespace(new_page=lambda **kwargs: page))
    monkeypatch.setattr(page_reader, "_cleanup_thread_playwright", lambda: cleanup.append("browser"))
    pages = [SimpleNamespace(extract_text=lambda: first_page),
             SimpleNamespace(extract_text=lambda: "Not selected")]
    monkeypatch.setattr(pdfplumber, "open", lambda path: nullcontext(SimpleNamespace(pages=pages)))
    result = PageSelectTool(indices=[])._fetch_with_playwright_pdf("https://source.example/report")
    assert result == first_page
    assert f"pages=2 read=1 chars={len(result)}" in caplog.text
    assert "Browser PDF started; url=https://source.example/report" in caplog.text
    assert ("Browser PDF extraction returned no text" in caplog.text) == (not first_page)
    assert paths and all(not path.exists() for path in paths)
    assert cleanup == ["page", "browser"]


@pytest.fixture(autouse=True)
def page_environment(monkeypatch):
    monkeypatch.setenv("ENABLE_PAGE_OCR", "false")
    monkeypatch.setenv("READER_MAX_CONTENT_LENGTH", "15000")
    monkeypatch.delenv("JINA_API_KEY", raising=False)


def test_select_pages_keeps_requested_indices_urls_metadata_and_order(monkeypatch):
    cache = LRUCache()
    cache["fixture"] = [
        {"index": 4, "url": "https://a.example", "title": "A", "domain": "a.example", "author": "Author A"},
        {"index": 9, "url": "https://b.example", "title": "B", "date_published": "2026-01-01"},
    ]
    monkeypatch.setattr(page_reader, "_SEARCH_RESULTS_CACHE", cache)
    monkeypatch.setattr(PageSelectTool, "_fetch_page", lambda self, url: f"Body for {url}")

    result = PageSelectTool(indices=[9, 4, 404], cache_key="fixture").execute()

    assert result.success
    assert result.output["num_pages"] == 2
    pages = result.output["pages_raw"]
    assert [(p["index"], p["url"], p["content"]) for p in pages] == [
        (9, "https://b.example", "Body for https://b.example"),
        (4, "https://a.example", "Body for https://a.example"),
    ]
    assert pages[0]["date_published"] == "2026-01-01"
    assert pages[1]["author"] == "Author A"
    assert pages[1]["domain"] == "a.example"


def test_html_acquisition_strips_navigation_and_preserves_image_position(monkeypatch):
    html = "<html><header>HEADER_DROP</header><nav>NAV_DROP</nav><script>SCRIPT_DROP</script><body>"
    html += "<p>Start of report. " + "Research provides detailed evidence about company revenue. " * 5 + "</p>"
    html += '<img src="/charts/revenue.png" alt="Revenue chart"><p>End of report.</p><footer>FOOTER_DROP</footer></body></html>'
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return response(html)

    monkeypatch.setattr(requests, "get", get)
    content = PageSelectTool(indices=[])._fetch_page("https://source.example/report")
    assert calls == ["https://source.example/report"]
    assert not any(text in content for text in ("HEADER_DROP", "NAV_DROP", "SCRIPT_DROP", "FOOTER_DROP"))
    assert (
        content.index("Start of report")
        < content.index("![Revenue chart](https://source.example/charts/revenue.png)")
        < content.index("End of report")
    )


def test_javascript_shell_falls_back_to_jina_with_original_source_url(monkeypatch):
    url = "https://source.example/dynamic"
    markdown = "# Extracted report\n" + "Reliable source text with complete details. " * 6
    calls = []

    def get(request_url, **kwargs):
        calls.append(request_url)
        return response("<html>Please enable JavaScript</html>" if request_url == url else markdown)

    monkeypatch.setattr(requests, "get", get)
    assert PageSelectTool(indices=[])._fetch_page(url) == markdown
    assert calls == [url, f"https://r.jina.ai/{url}"]


def test_pdf_route_parses_text_tables_and_respects_page_limit(monkeypatch):
    calls = []
    monkeypatch.setenv("PDF_MAX_PAGES", "1")
    monkeypatch.setattr(requests, "get", lambda url, **kwargs: calls.append(url) or response(b"PDF fixture"))
    pages = [
        SimpleNamespace(
            extract_text=lambda: "First page text", extract_tables=lambda: [[["Name", "Value"], ["A|B", "12\nunits"]]]
        ),
        SimpleNamespace(extract_text=lambda: "SECOND_PAGE_SHOULD_NOT_APPEAR", extract_tables=lambda: []),
    ]
    monkeypatch.setattr(pdfplumber, "open", lambda stream: nullcontext(SimpleNamespace(pages=pages)))
    content = PageSelectTool(indices=[])._fetch_page("https://source.example/REPORT.PDF?download=1")
    assert calls == ["https://source.example/REPORT.PDF?download=1"]
    assert "[Page 1]\nFirst page text" in content
    assert "| A\\|B | 12 units |" in content
    assert "SECOND_PAGE" not in content


def test_pdf_parse_failure_uses_jina_and_truncates(monkeypatch):
    url = "https://source.example/broken.pdf"
    calls = []
    monkeypatch.setenv("READER_MAX_CONTENT_LENGTH", "110")

    def get(request_url, **kwargs):
        calls.append(request_url)
        return response(b"broken" if request_url == url else b"R" * 180)

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(pdfplumber, "open", lambda stream: (_ for _ in ()).throw(ValueError("malformed PDF")))
    content = PageSelectTool(indices=[])._fetch_page(url)
    assert content == "R" * 110 + "...[truncated]"
    assert calls == [url, f"https://r.jina.ai/{url}"]


def test_excel_route_reads_workbook_and_limits_sheets(monkeypatch):
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as workbook:
        pd.DataFrame({"Company": ["FixtureCo"], "Revenue": [12]}).to_excel(workbook, sheet_name="Revenue", index=False)
        pd.DataFrame({"Private": ["SECOND_SHEET"]}).to_excel(workbook, sheet_name="Extra", index=False)
    monkeypatch.setenv("EXCEL_MAX_SHEETS", "1")
    monkeypatch.setattr(requests, "get", lambda url, **kwargs: response(buffer.getvalue()))
    content = PageSelectTool(indices=[])._fetch_page("https://source.example/data.xlsx?download=1")
    assert "## Sheet: Revenue" in content
    assert "FixtureCo" in content
    assert "12" in content
    assert "SECOND_SHEET" not in content


def test_csv_route_decodes_utf8_and_keeps_table_cells(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, **kwargs: response("公司,收入\n研究公司,12.5\n"))
    content = PageSelectTool(indices=[])._fetch_page("https://source.example/report.csv")
    assert "公司" in content
    assert "研究公司" in content
    assert "12.5" in content


@pytest.mark.parametrize("failure", ["navigation", "export", "parse", None])
def test_playwright_closes_page_and_removes_temporary_pdf(monkeypatch, tmp_path, failure):
    pdf_path = tmp_path / "rendered.pdf"
    calls = []

    class Page:
        def goto(self, *args, **kwargs):
            if failure == "navigation":
                raise RuntimeError("navigation failed")

        def wait_for_load_state(self, *args, **kwargs):
            pass

        def wait_for_timeout(self, *args, **kwargs):
            pass

        def pdf(self, **kwargs):
            if failure == "export":
                raise RuntimeError("PDF export failed")

        def close(self):
            calls.append("page_closed")

    monkeypatch.setattr(
        page_reader, "_get_playwright_browser", lambda: SimpleNamespace(new_page=lambda **kwargs: Page())
    )
    monkeypatch.setattr(page_reader, "_cleanup_thread_playwright", lambda: calls.append("browser_closed"))
    monkeypatch.setattr("tempfile.NamedTemporaryFile", lambda **kwargs: pdf_path.open("wb"))

    def open_pdf(path):
        if failure == "parse":
            raise ValueError("PDF parse failed")
        return nullcontext(SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: "Rendered report")]))

    monkeypatch.setattr(pdfplumber, "open", open_pdf)
    result = PageSelectTool(indices=[])._fetch_with_playwright_pdf("https://source.example/dynamic")
    assert result == ("" if failure else "Rendered report")
    assert calls == ["page_closed", "browser_closed"]
    assert not pdf_path.exists()


def test_excel_parse_failure_closes_workbook_and_reports_parser_failure(monkeypatch):
    calls = []

    class Workbook:
        sheet_names = ["Revenue"]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            calls.append("workbook_closed")

    monkeypatch.setattr(requests, "get", lambda url, **kwargs: response(b"workbook"))
    monkeypatch.setattr(pd, "ExcelFile", lambda *args, **kwargs: Workbook())

    def parse(*args, **kwargs):
        raise ValueError("invalid worksheet")

    monkeypatch.setattr(pd, "read_excel", parse)
    content = PageSelectTool(indices=[])._fetch_page("https://source.example/broken.xlsx")
    assert content == "[Error: Excel parsing failed]"
    assert calls == ["workbook_closed"]
