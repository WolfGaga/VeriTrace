"""Web page acquisition, document parsing and optional image OCR."""

import logging
import re
import time
from concurrent.futures import as_completed
from typing import Dict, List, Optional

from pydantic import Field

from ..concurrency import ThreadPoolExecutor, check_cancelled
from .base_tool import BaseTool, ToolResult, register_tool
from .tools import _SEARCH_RESULTS_CACHE, _get_playwright_browser, _cleanup_thread_playwright


@register_tool
class PageSelectTool(BaseTool):
    """
    Select and read content from previously searched web pages.

    Use this after WebSearchTool to read full content of specific pages.
    """

    indices: List[int] = Field(description="List of page indices to read (from search results).")
    cache_key: Optional[str] = Field(
        default=None,
        description="Cache key from search results. If not provided, uses most recent search.",
    )

    @property
    def name(self) -> str:
        return "select_pages"

    @property
    def description(self) -> str:
        return "Read the full content of selected web pages from search results. Provide the indices of pages you want to read."

    def execute(self) -> ToolResult:
        """Fetch and read selected pages."""
        try:
            # Get cached search results
            if self.cache_key:
                results = _SEARCH_RESULTS_CACHE.get(self.cache_key)
            else:
                # Use most recent cache
                if _SEARCH_RESULTS_CACHE:
                    cache_key = list(_SEARCH_RESULTS_CACHE.keys())[-1]
                    results = _SEARCH_RESULTS_CACHE[cache_key]
                else:
                    results = None

            if not results:
                return ToolResult(
                    success=False,
                    error="No search results found. Run web_search first.",
                )

            # Fetch selected pages in parallel
            # Build index lookup map
            index_to_result = {r.get("index", i): r for i, r in enumerate(results)}

            valid_indices = [i for i in self.indices if i in index_to_result]
            total = len(valid_indices)

            # Log invalid indices
            for idx in self.indices:
                if idx not in index_to_result:
                    logging.warning(
                        f"   ⚠️ Index {idx} not found in cache (available: {list(index_to_result.keys())[:5]}...)"
                    )

            # Parallel fetch pages
            pages = []
            if valid_indices:
                logging.info(f"   🚀 并行获取 {total} 个页面...")

                def fetch_one(idx: int) -> dict:
                    page_info = index_to_result[idx]
                    logging.info(f"   📄 正在获取: {page_info['title'][:50]}...")
                    content = self._fetch_page(page_info["url"])
                    return {
                        "index": idx,
                        "title": page_info["title"],
                        "url": page_info["url"],
                        "content": content,
                        "domain": page_info.get("domain", ""),
                        "date_published": page_info.get("date_published"),
                        "author": page_info.get("author"),
                        "site_name": page_info.get("site_name"),
                    }

                with ThreadPoolExecutor(max_workers=min(30, total)) as executor:
                    futures = {executor.submit(fetch_one, idx): idx for idx in valid_indices}
                    for future in as_completed(futures):
                        try:
                            page = future.result()
                            pages.append(page)
                        except Exception as e:
                            idx = futures[future]
                            logging.error(f"   ❌ 获取页面 {idx} 失败: {e}")

                # Sort by original index order
                pages.sort(key=lambda p: valid_indices.index(p["index"]))

            return ToolResult(
                success=True,
                output={
                    "num_pages": len(pages),
                    "pages": self._format_pages(pages),
                    "pages_raw": pages,  # Raw page data for quality evaluation
                },
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def _fetch_page(self, url: str) -> str:
        """Fetch page content with smart file type detection and multi-level fallback.

        Flow:
        1. Detect file type (PDF/Excel/CSV) → use specialized parser
        2. For HTML: direct fetch → collect images
        3. Check if meaningful → if not, try Jina
        4. Check for scattered table data → Playwright PDF (text only, keep images)
        5. If Jina failed and still not meaningful → Playwright fallback
        6. OCR images (append to end if Playwright was used)
        7. Truncate to max length
        """
        from ..runtime.progress import report_progress

        report_progress("reading")
        import os

        # Step 1: Detect file type and use specialized handler
        file_type = self._detect_file_type(url)

        if file_type == "pdf":
            content = self._fetch_pdf(url)
            if content:
                return self._truncate_content(content)
            logging.info("   🔄 PDF parsing failed, trying Jina Reader...")
            jina_content = self._fetch_with_jina(url)
            return self._truncate_content(jina_content or "[Error: PDF parsing failed]")

        if file_type == "excel":
            content = self._fetch_excel(url)
            if content:
                return self._truncate_content(content)
            return "[Error: Excel parsing failed]"

        if file_type == "csv":
            content = self._fetch_csv(url)
            if content:
                return self._truncate_content(content)
            return "[Error: CSV parsing failed]"

        # Step 2: For HTML/unknown, try direct fetch (always collect images)
        content, images = self._fetch_direct(url)
        is_meaningful = self._is_meaningful_content(content)
        used_playwright = False

        # Step 3: If not meaningful, try Jina
        if not is_meaningful:
            logging.info("   🔄 Content not meaningful (likely JS-rendered), trying Jina Reader...")
            jina_content = self._fetch_with_jina(url)
            if jina_content:
                content = jina_content
                jina_images = self._parse_images_from_markdown(jina_content)
                if jina_images:
                    images = jina_images  # Update images from Jina
                    logging.info(f"   📷 Found {len(images)} images in Jina markdown")
                is_meaningful = self._is_meaningful_content(content)

        # Step 4: Check for scattered table data → Playwright PDF (text only)
        if is_meaningful and self._has_scattered_table_data(content):
            logging.info("   🔄 Scattered table detected, trying Playwright PDF...")
            playwright_content = self._fetch_with_playwright_pdf(url)
            if playwright_content and len(playwright_content) > 100:
                if not self._has_scattered_table_data(playwright_content):
                    logging.info("   ✅ Playwright PDF has better table structure")
                    content = playwright_content
                    used_playwright = True
                    # Keep images! Don't clear them - they'll be OCR'd separately

        # Step 5: If still not meaningful (Jina failed), try Playwright as last resort
        if not is_meaningful:
            logging.info("   🔄 Content still not meaningful, trying Playwright PDF as fallback...")
            playwright_content = self._fetch_with_playwright_pdf(url)
            if playwright_content and len(playwright_content) > 100:
                content = playwright_content
                used_playwright = True
                # Keep original images for OCR

        # Step 6: OCR images if enabled
        if images and os.getenv("ENABLE_PAGE_OCR", "").lower() in ("true", "1", "yes"):
            if used_playwright:
                # Playwright replaced content, append OCR to end
                ocr_section = self._ocr_images_to_section(images[:3])
                if ocr_section:
                    content += ocr_section
            else:
                # Direct/Jina content, replace images in-place
                content = self._replace_images_with_ocr(content, images[:3])

        return self._truncate_content(content)

    def _truncate_content(self, content: str) -> str:
        """Truncate content to max length."""
        import os

        max_length = int(os.getenv("READER_MAX_CONTENT_LENGTH", "15000"))
        if len(content) > max_length:
            return content[:max_length] + "...[truncated]"
        return content

    def _detect_file_type(self, url: str) -> str:
        """Detect file type from URL extension."""
        lower_url = url.lower().split("?")[0]  # Remove query params

        if lower_url.endswith(".pdf"):
            return "pdf"
        if lower_url.endswith((".xlsx", ".xls")):
            return "excel"
        if lower_url.endswith(".csv"):
            return "csv"
        return "html"

    def _is_meaningful_content(self, content: str) -> bool:
        """Check if content is meaningful (not JS empty shell).

        JS-rendered pages often return:
        - Lots of script/config code but little actual text
        - Loading indicators
        - "Enable JavaScript" messages
        """
        # Condition 1: Content too short
        if len(content.strip()) < 100:
            return False

        # Condition 2: Error message
        if content.startswith("[Error"):
            return False

        # Condition 3: If content is very long, it's likely meaningful
        # (Jina/other sources returning 5000+ chars of content is usually good)
        if len(content.strip()) > 5000:
            return True

        # Condition 4: Detect JS empty shell characteristics
        # Remove HTML tags to get pure text
        text_only = re.sub(r"<[^>]+>", "", content)
        text_ratio = len(text_only.strip()) / max(len(content), 1)

        # JS/config code patterns
        js_patterns = [
            r"function\s*\(",
            r"window\.",
            r"document\.",
            r"\bvar\s+",
            r"\bconst\s+",
            r"\blet\s+",
            r"module\.exports",
            r"require\(",
            r"import\s+.*from",
            r"__webpack",
            r"_next/static",
            r"bundled",
        ]
        js_count = sum(1 for p in js_patterns if re.search(p, content))

        # If text ratio is low (<30%) and JS features are high (>=3), likely empty shell
        if text_ratio < 0.3 and js_count >= 3:
            logging.debug(f"   JS shell detected: text_ratio={text_ratio:.2f}, js_count={js_count}")
            return False

        # Condition 5: Common empty page indicators (only for short content)
        empty_indicators = [
            "loading...",
            "please wait",
            "javascript is required",
            "enable javascript",
            "please enable javascript",
            "this page requires javascript",
        ]
        content_lower = content.lower()
        if any(ind in content_lower for ind in empty_indicators):
            return False

        return True

    def _fetch_pdf(self, url: str) -> str:
        """Download and parse PDF content."""
        import os

        try:
            import pdfplumber
            from io import BytesIO
            import requests

            logging.info(f"   📄 Fetching PDF: {url[:60]}...")

            check_cancelled()
            response = requests.get(
                url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            response.raise_for_status()

            text_parts = []
            max_pages = int(os.getenv("PDF_MAX_PAGES", "20"))

            with pdfplumber.open(BytesIO(response.content)) as pdf:
                total_pages = len(pdf.pages)
                pages_to_read = min(total_pages, max_pages)
                logging.info(f"   📄 PDF has {total_pages} pages, reading {pages_to_read}")

                for i, page in enumerate(pdf.pages[:pages_to_read]):
                    # Extract text
                    text = page.extract_text()
                    if text:
                        text_parts.append(f"[Page {i + 1}]\n{text}")

                    # Extract tables
                    tables = page.extract_tables()
                    for table in tables:
                        formatted_table = self._format_table(table)
                        if formatted_table:
                            text_parts.append(formatted_table)

            if text_parts:
                logging.info(f"   📄 PDF extracted: {len(text_parts)} sections")
                return "\n\n".join(text_parts)
            else:
                logging.warning("   ⚠️ PDF extracted but no text found")
                return ""

        except ImportError:
            logging.warning("   ⚠️ pdfplumber not installed. Run: pip install pdfplumber")
            return ""
        except Exception as e:
            logging.warning(f"   ⚠️ PDF extraction failed: {e}")
            return ""

    def _fetch_with_playwright_pdf(self, url: str) -> str:
        """Render a page to PDF and extract text, releasing temporary resources."""
        import os
        import tempfile

        pdf_path = None
        started = time.monotonic()
        logging.info("[PageReader] Browser PDF started; url=%s", url[:120])
        try:
            import pdfplumber

            check_cancelled()
            browser = _get_playwright_browser()
            page = browser.new_page(viewport={"width": 2160, "height": 3840})
            try:
                page.goto(url, timeout=60000)
                page.wait_for_load_state("domcontentloaded", timeout=30000)
                page.wait_for_timeout(5000)
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    pdf_path = tmp.name
                page.pdf(path=pdf_path, format="A3", landscape=True, print_background=True, scale=0.8)
            finally:
                page.close()
            text_parts = []
            max_pages = int(os.getenv("PDF_MAX_PAGES", "20"))
            with pdfplumber.open(pdf_path) as pdf:
                total_pages = len(pdf.pages)
                selected_pages = pdf.pages[:max_pages]
                for pg in selected_pages:
                    text = pg.extract_text()
                    if text:
                        text_parts.append(text)
            if not text_parts:
                logging.warning("Browser PDF extraction returned no text")
            result = "\n\n".join(text_parts)
            logging.info("[PageReader] Browser PDF finished; pages=%d read=%d chars=%d elapsed=%.2fs",
                         total_pages, len(selected_pages), len(result), time.monotonic() - started)
            return result
        except ImportError as exc:
            logging.warning("Browser PDF dependencies unavailable: %s", exc)
            return ""
        except Exception as exc:
            logging.warning("Browser PDF extraction failed: %s", exc)
            return ""
        finally:
            if pdf_path is not None:
                try:
                    os.unlink(pdf_path)
                except OSError:
                    logging.warning("Could not remove temporary PDF: %s", pdf_path)
            _cleanup_thread_playwright()

    def _has_scattered_table_data(self, content: str) -> bool:
        """Detect if content looks like scattered table data (headers and values separated).

        This happens when Jina/direct fetch gets a JS-rendered table page but
        loses the row-column alignment. Signs include:
        - Many numeric values on separate lines
        - Country/entity names on separate lines (not with their values)
        - Repeating patterns of data that should be in columns
        """
        if not content or len(content) < 500:
            return False

        lines = content.split("\n")

        # Count lines that are mostly numbers (data rows)
        numeric_lines = 0
        # Count lines that look like headers/labels (text only)
        label_lines = 0

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Count digits and letters in the line
            digit_count = sum(1 for c in stripped if c.isdigit())
            alpha_count = sum(1 for c in stripped if c.isalpha())

            # Numeric line: has digits and digits dominate (> 2x letters)
            # This correctly handles "123,456" and "45.6 (ep)" but NOT "Germany"
            if digit_count > 0 and digit_count > alpha_count * 2:
                numeric_lines += 1
            # Label line: no digits, short text (country names, headers)
            elif digit_count == 0 and alpha_count > 0 and len(stripped) < 50:
                label_lines += 1

        # If we have many separate label lines and numeric lines, it's scattered
        # A proper table would have labels and numbers on the same line
        # Additional check: labels and numbers should be roughly balanced (within 3:1 ratio)
        # If labels >> numbers, it's likely a normal article with some numbers, not a table
        if label_lines > 15 and numeric_lines > 15:
            # Check ratio - scattered tables have roughly balanced labels:numbers
            # Real tables like Eurostat have similar counts of labels and data rows
            ratio = max(label_lines, numeric_lines) / max(min(label_lines, numeric_lines), 1)
            if ratio > 3:
                # Too imbalanced, likely not a table (e.g., Wikipedia article with 417 labels but only 94 numbers)
                logging.debug(
                    f"   📊 Skipping scattered check: imbalanced ratio {ratio:.1f} (labels:{label_lines}, numbers:{numeric_lines})"
                )
                return False

            # Check if labels and numbers are NOT on same lines (scattered)
            lines_with_both = sum(
                1
                for line in lines
                if any(c.isdigit() for c in line) and any(c.isalpha() for c in line) and len(line.strip()) > 20
            )
            # If most content is separated, it's scattered
            if lines_with_both < (label_lines + numeric_lines) * 0.3:
                logging.info(
                    f"   📊 Detected scattered table data (labels:{label_lines}, numbers:{numeric_lines}, combined:{lines_with_both})"
                )
                return True

        return False

    def _fetch_excel(self, url: str) -> str:
        """Download and parse Excel file."""
        import os

        try:
            import pandas as pd
            from io import BytesIO
            import requests

            logging.info(f"   📊 Fetching Excel: {url[:60]}...")

            check_cancelled()
            response = requests.get(
                url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            response.raise_for_status()

            with pd.ExcelFile(BytesIO(response.content)) as xlsx:
                parts = []
                max_sheets = int(os.getenv("EXCEL_MAX_SHEETS", "5"))

                for sheet_name in xlsx.sheet_names[:max_sheets]:
                    df = pd.read_excel(xlsx, sheet_name=sheet_name)
                    parts.append(f"## Sheet: {sheet_name}\n")
                    parts.append(df.to_markdown(index=False))

                logging.info(f"   📊 Excel extracted: {len(xlsx.sheet_names)} sheets")
                return "\n\n".join(parts)

        except ImportError:
            logging.warning("   ⚠️ pandas/openpyxl not installed. Run: pip install pandas openpyxl")
            return ""
        except Exception as e:
            logging.warning(f"   ⚠️ Excel extraction failed: {e}")
            return ""

    def _fetch_csv(self, url: str) -> str:
        """Download and parse CSV file."""
        try:
            import pandas as pd
            from io import BytesIO
            import requests

            logging.info(f"   📊 Fetching CSV: {url[:60]}...")

            check_cancelled()
            response = requests.get(
                url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            response.raise_for_status()

            # Auto-detect encoding
            encoding = self._detect_encoding(response.content)

            df = pd.read_csv(BytesIO(response.content), encoding=encoding)
            content = df.to_markdown(index=False)

            logging.info(f"   📊 CSV extracted: {len(df)} rows, encoding={encoding}")
            return content

        except ImportError:
            logging.warning("   ⚠️ pandas not installed. Run: pip install pandas")
            return ""
        except Exception as e:
            logging.warning(f"   ⚠️ CSV extraction failed: {e}")
            return ""

    def _detect_encoding(self, content: bytes) -> str:
        """Auto-detect content encoding using chardet."""
        try:
            import chardet

            result = chardet.detect(content)
            encoding = result.get("encoding", "utf-8")
            confidence = result.get("confidence", 0)

            if confidence > 0.7 and encoding:
                logging.debug(f"   Detected encoding: {encoding} ({confidence:.0%})")
                return encoding
        except ImportError:
            pass
        except Exception:
            pass

        return "utf-8"

    def _format_table(self, table: List) -> str:
        """Convert table data to Markdown format."""
        if not table or not table[0]:
            return ""

        try:
            # Clean up cells
            def clean_cell(cell):
                if cell is None:
                    return ""
                return str(cell).replace("|", "\\|").replace("\n", " ")

            # Header
            header = "| " + " | ".join(clean_cell(cell) for cell in table[0]) + " |"
            separator = "|" + "|".join("---" for _ in table[0]) + "|"

            # Data rows
            rows = []
            for row in table[1:]:
                rows.append("| " + " | ".join(clean_cell(cell) for cell in row) + " |")

            return "\n".join([header, separator] + rows)
        except Exception:
            return ""

    def _fetch_direct(self, url: str) -> tuple:
        """Direct fetch with requests + BeautifulSoup. Returns (text, image_urls).

        Images are replaced with markdown-style tags to preserve their position
        for later OCR replacement.
        """
        try:
            import requests
            from bs4 import BeautifulSoup
            from urllib.parse import urljoin

            check_cancelled()
            response = requests.get(
                url, timeout=10, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            response.raise_for_status()

            # Auto-detect encoding if not provided or confidence is low
            if response.encoding is None or response.encoding.lower() == "iso-8859-1":
                detected_encoding = self._detect_encoding(response.content)
                response.encoding = detected_encoding
                logging.debug(f"   Using detected encoding: {detected_encoding}")

            soup = BeautifulSoup(response.text, "html.parser")

            # Remove non-content elements first
            for el in soup(["script", "style", "nav", "footer", "header"]):
                el.decompose()

            # Replace meaningful images with markdown tags to preserve position
            images = []
            for img in soup.find_all("img", src=True):
                src = img.get("src", "")
                alt = img.get("alt", "")
                if self._is_meaningful_image(src, alt):
                    full_url = urljoin(url, src)
                    images.append({"url": full_url, "alt": alt})
                    # Replace img tag with markdown-style placeholder
                    img.replace_with(f"\n![{alt}]({full_url})\n")
                else:
                    # Remove non-meaningful images
                    img.decompose()

            # Get text content (now includes markdown image tags)
            text = soup.get_text(separator="\n", strip=True)
            text = re.sub(r"\n{3,}", "\n\n", text)  # Clean excessive newlines

            return text, images

        except ImportError:
            raise ImportError("beautifulsoup4 not installed. Run: pip install beautifulsoup4")
        except Exception as e:
            return f"[Error fetching page: {str(e)}]", []

    def _fetch_with_jina(self, url: str) -> Optional[str]:
        """Fallback: use Jina Reader. Returns None on failure (graceful degradation)."""
        import os
        import requests

        try:
            jina_url = f"https://r.jina.ai/{url}"
            headers = {"Accept": "text/markdown"}

            # Use API key if available
            jina_key = os.getenv("JINA_API_KEY")
            if jina_key:
                headers["Authorization"] = f"Bearer {jina_key}"

            check_cancelled()
            response = requests.get(jina_url, timeout=30, headers=headers)
            response.raise_for_status()

            content = response.text
            if content and len(content.strip()) > 100:
                logging.info(f"   ✅ Jina Reader: {len(content)} chars")
                return content
            return None

        except Exception as e:
            logging.warning(f"   ⚠️ Jina Reader failed (degrading gracefully): {e}")
            return None  # Graceful degradation

    def _is_meaningful_image(self, src: str, alt: str) -> bool:
        """Filter out icons, tracking pixels, and decorative images."""
        src_lower = src.lower()

        # Skip common non-content images
        skip_patterns = [
            "icon",
            "logo",
            "avatar",
            "tracking",
            "pixel",
            "badge",
            "button",
            "sprite",
            "data:image",
            "1x1",
            "spacer",
            ".svg",
            ".ico",
            "emoji",
            "loading",
            "spinner",
        ]
        if any(p in src_lower for p in skip_patterns):
            return False

        # Skip common non-content paths
        skip_paths = ["/static/", "/assets/icons/", "/ui/", "/emoji/"]
        if any(p in src_lower for p in skip_paths):
            return False

        # Only OCR images with meaningful alt text (likely content images)
        return bool(alt and len(alt) > 5)

    def _parse_images_from_markdown(self, markdown: str) -> List[Dict]:
        """Parse image URLs from Jina Reader's markdown output.

        Jina returns images as: ![Image 1](https://example.com/image.png)
        We extract URLs and filter out non-meaningful images.
        """
        images = []
        # Match markdown image syntax: ![alt](url)
        pattern = r"!\[([^\]]*)\]\((https?://[^\)]+)\)"
        matches = re.findall(pattern, markdown)

        for alt, url in matches:
            # Reuse the same filtering logic
            if self._is_meaningful_image(url, alt):
                images.append({"url": url, "alt": alt})

        return images

    def _ocr_single_image(self, img: Dict) -> str:
        """OCR a single image using DeepSeek-OCR. Returns OCR text or empty string on failure."""
        import os
        import requests

        api_key = os.getenv("SILICONFLOW_API_KEY")
        if not api_key:
            return ""

        try:
            check_cancelled()
            response = requests.post(
                "https://api.siliconflow.cn/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek-ai/DeepSeek-OCR",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": img["url"]}},
                                {
                                    "type": "text",
                                    "text": "Extract all text from this image. Return only the extracted text.",
                                },
                            ],
                        }
                    ],
                    "max_tokens": 1000,
                },
                timeout=30,
            )
            response.raise_for_status()

            result = response.json()
            text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            if text and len(text.strip()) > 10:
                logging.info(f"   📷 OCR: {len(text)} chars")
                return text.strip()

        except Exception as e:
            logging.debug(f"   OCR skipped: {e}")

        return ""

    def _replace_images_with_ocr(self, content: str, images: List[Dict]) -> str:
        """Replace image markdown tags with OCR text in-place.

        This preserves the position of images in the document, so OCR content
        appears exactly where the original image was located.
        """
        for img in images:
            ocr_text = self._ocr_single_image(img)
            if ocr_text:
                # Build the original markdown image pattern
                # Handle both exact match and slight variations
                alt = img.get("alt", "")
                url = img["url"]

                # Escape special regex characters in alt and url
                alt_escaped = re.escape(alt)
                url_escaped = re.escape(url)

                # Pattern to match: ![alt](url) with possible whitespace variations
                pattern = rf"!\[{alt_escaped}\]\({url_escaped}\)"

                # Replacement: OCR text block with context marker
                alt_info = f" ({alt})" if alt else ""
                replacement = f"\n\n[📷 OCR{alt_info}]:\n{ocr_text}\n"

                content = re.sub(pattern, replacement, content)
                logging.info(f"   ✓ Replaced image{alt_info} with OCR text at original position")
            else:
                # Keep original image tag if OCR failed
                logging.debug("   ⚠ OCR failed for image, keeping original tag")

        return content

    def _ocr_images_to_section(self, images: List[Dict]) -> str:
        """OCR images and return as a section to append (for Playwright path).

        When Playwright PDF is used, we can't replace images in-place because
        the content is completely replaced. Instead, we append OCR results as
        a separate section at the end.
        """
        ocr_results = []

        for img in images:
            ocr_text = self._ocr_single_image(img)
            if ocr_text:
                alt = img.get("alt", "")
                alt_info = f" ({alt})" if alt else ""
                ocr_results.append(f"[📷 OCR{alt_info}]:\n{ocr_text}")
                logging.info(f"   ✓ OCR extracted{alt_info}: {len(ocr_text)} chars")

        if ocr_results:
            return "\n\n--- 图表OCR内容 ---\n\n" + "\n\n".join(ocr_results)
        return ""

    def _format_pages(self, pages: List[Dict]) -> str:
        """Format page contents for LLM."""
        sections = []
        for page in pages:
            section = f"## [{page['index']}] {page['title']}\n"
            section += f"URL: {page['url']}\n\n"
            section += page["content"]
            sections.append(section)

        return "\n\n---\n\n".join(sections)
