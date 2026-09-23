#!/usr/bin/env python3
# TYPE: TOOLKIT
"""webfetch.py - fetch pages; urlgrep() pulls the lines you need.
# keywords: url|grep|fetch|web|http|html|markdown|text|download|page|content|curl|scrape|retrieve|get|distill|extract|filter|lines|regex|pattern|snippet|lookup|find|specific|section|token|efficient|context
# examples: import webfetch; webfetch.urlgrep("https://openrouter.ai/docs/limits", ["limit_remaining"], max_lines=5) | import webfetch; webfetch.head("https://example.com") | import webfetch; webfetch.webfetch("https://example.com", format="markdown")

Token-efficiency first: when you need something SPECIFIC from a page, call
urlgrep() (fetch + filter in one call -- only matching lines enter context);
call head() for a quick outline of the top of a page; call webfetch() only when
you want the whole page.

  import webfetch
  webfetch.urlgrep(url, patterns, max_lines=10, width=150, ctx_before=0,
                   ctx_after=0, case=True, format='text')
  webfetch.head(url, n=20, width=150, format='markdown')

Fetches a URL and returns the content as Markdown (default), plain text, or
raw HTML. Detects images and can save them. Stdlib-only (no pip deps).

CLI:
  ./webfetch.py <url> [options]

Options:
  -f, --format text|markdown|html  output format (default: markdown; text for --grep)
  -g, --grep PATTERN               print only lines matching PATTERN (regex, repeatable)
      --lines N                    max matching lines for --grep (default 10)
      --head N                     print first N non-empty lines instead of the page
      --width W                    truncate each printed line to W chars (default 150)
      --before B / --after A       context lines around each --grep match
  -i, --ignore-case                case-insensitive --grep
  -t, --timeout SECONDS            timeout in seconds (default 30, max 120)
  -o, --output FILE                write output to FILE instead of stdout
      --save-image FILE            when fetching an image, save bytes to FILE
  -H, --header NAME:VALUE          extra request header (repeatable)
      --max-size BYTES             max response size (default 5242880 = 5MB)
      --show-meta                  print status/content-type/size to stderr
  -q, --quiet                      suppress status messages
  -v, --verbose                    verbose messages
      --selftest                   run built-in self tests and exit

Python API:
  import webfetch
  result = webfetch.webfetch("https://example.com", format="markdown")
  print(result["output"])
  webfetch.urlgrep("https://example.com/docs", ["timeout"], max_lines=5)
  webfetch.head("https://example.com")   # outline of the top (markdown)

Notes / behavior (mirrors OpenCode webfetch):
  * URL must start with http:// or https://
  * Format-specific Accept header so servers may return markdown directly.
  * Browser User-Agent; retries with honest UA "opencode" if Cloudflare
    returns 403 + "cf-mitigated: challenge".
  * 5MB response cap (Content-Length or actual read).
  * Images are returned as base64 data-URL attachments and can be saved.
"""

import argparse
import base64
import gzip
import re
import socket
import sys
import urllib.error
import urllib.request
import zlib
from contextlib import contextmanager
from html.parser import HTMLParser
from typing import Any, Sequence

DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 120
MAX_RESPONSE_SIZE = 5 * 1024 * 1024  # 5 MB

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/143.0.0.0 Safari/537.36")
HONEST_UA = "opencode"

ACCEPT_HEADERS = {
    "markdown": "text/markdown;q=1.0, text/x-markdown;q=0.9, "
                "text/plain;q=0.8, text/html;q=0.7, */*;q=0.1",
    "text": "text/plain;q=1.0, text/markdown;q=0.9, text/html;q=0.8, */*;q=0.1",
    "html": "text/html;q=1.0, application/xhtml+xml;q=0.9, text/plain;q=0.8, "
            "text/markdown;q=0.7, */*;q=0.1",
}
DEFAULT_ACCEPT = ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,image/apng,*/*;q=0.8")

SKIP_TAGS = {"script", "style", "noscript", "iframe", "object", "embed",
             "meta", "link", "head", "title"}
BLOCK_TAGS = {"p", "div", "section", "article", "header", "footer", "nav",
              "main", "aside", "h1", "h2", "h3", "h4", "h5", "h6", "ul",
              "ol", "li", "pre", "blockquote", "hr", "table", "tr", "figure",
              "figcaption", "form", "fieldset"}

VOID_ELEMENTS = {"area", "base", "br", "col", "embed", "hr", "img", "input",
                 "link", "meta", "param", "source", "track", "wbr"}


# --------------------------------------------------------------------------
# Token-efficient helpers: distill a page instead of dumping it
# --------------------------------------------------------------------------
#
# python_eval output lands in LLM context, so these fetch and filter in one
# call and return only the lines you asked for. They are defined first so they
# appear at the top of this tool's function list (list_tools / get_tool).

def _fetch(url: str, format: str = "text") -> str:
    """Fetch url and return its content as a string in the given format."""
    return webfetch(url, format=format)["output"]


def urlgrep(url: str, patterns: Sequence[str], max_lines: int = 10, width: int = 150, ctx_before: int = 0, ctx_after: int = 0, case: bool = True, format: str = "text") -> str:
    """Fetch url, return only the lines matching any pattern (regex), truncated.

    Token-efficiency helper: keep just the lines you need. patterns are
    regexes; set case=False for case-insensitive matching. ctx_before/ctx_after
    add neighbouring lines as context. format picks the source rendition:
    'text' (default) is the clean match surface; 'markdown' keeps headings and
    links; 'html' uses the raw markup.
    """
    rxs = [re.compile(p, 0 if case else re.IGNORECASE) for p in patterns]
    lines = _fetch(url, format).splitlines()
    seen, out = set(), []
    for i, ln in enumerate(lines):
        if not any(rx.search(ln) for rx in rxs):
            continue
        for j in range(max(0, i - ctx_before), min(len(lines), i + ctx_after + 1)):
            if j in seen or len(out) >= max_lines:
                continue
            seen.add(j)
            t = lines[j].strip()
            out.append(t if len(t) <= width else t[:width] + "...")
    return "\n".join(out)


def head(url: str, n: int = 20, width: int = 150, format: str = "markdown") -> str:
    """Fetch url, return the first n non-empty lines (an outline), truncated.

    A quick "what is on this page?" peek. Defaults to format='markdown' so the
    heading/bullet structure stays visible; pass format='text' for plain text.
    Thin wrapper over urlgrep (pattern \\S = any line with real content).
    """
    return urlgrep(url, [r"\S"], max_lines=n, width=width, format=format)


# --------------------------------------------------------------------------
# Tiny DOM builder on top of html.parser
# --------------------------------------------------------------------------

class _Node:
    __slots__ = ("tag", "attrs", "children", "text")

    def __init__(self, tag=None, attrs=None, text=""):
        self.tag = tag          # None for text nodes
        self.attrs = attrs or {}
        self.children = []
        self.text = text


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node(tag="#root")
        self.stack = [self.root]
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in VOID_ELEMENTS:
            # Void elements have no closing tag; never push, never count skip.
            if self.skip_depth > 0 or tag in SKIP_TAGS:
                return
            node = _Node(tag=tag, attrs=dict(attrs))
            self.stack[-1].children.append(node)
            return
        if self.skip_depth > 0 or tag in SKIP_TAGS:
            if tag in SKIP_TAGS:
                self.skip_depth += 1
            return
        node = _Node(tag=tag, attrs=dict(attrs))
        self.stack[-1].children.append(node)
        self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self.skip_depth > 0 or tag in SKIP_TAGS:
            return
        node = _Node(tag=tag, attrs=dict(attrs))
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.skip_depth > 0:
            if tag in SKIP_TAGS:
                self.skip_depth -= 1
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.skip_depth == 0 and self.stack:
            self.stack[-1].children.append(_Node(text=data))


def parse_html(html: str) -> _Node:
    """Parse an HTML string into the internal _Node tree used by the converters."""
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


# --------------------------------------------------------------------------
# HTML -> Markdown
# --------------------------------------------------------------------------

def _inline(node):
    """Render a node as inline markdown (for p, headings, list items...)."""
    if node.tag is None:
        return node.text
    tag = node.tag
    if tag in SKIP_TAGS:
        return ""
    if tag == "a":
        href = node.attrs.get("href", "")
        inner = "".join(_inline(c) for c in node.children).strip()
        if not inner:
            inner = href
        title = node.attrs.get("title")
        if title:
            return "[%s](%s \"%s\")" % (inner, href, title)
        return "[%s](%s)" % (inner, href)
    if tag in ("strong", "b"):
        return "**" + "".join(_inline(c) for c in node.children).strip() + "**"
    if tag in ("em", "i"):
        return "*" + "".join(_inline(c) for c in node.children).strip() + "*"
    if tag == "code":
        text = "".join(_inline(c) for c in node.children)
        return "`" + text.replace("`", "\\`") + "`"
    if tag == "img":
        src = node.attrs.get("src", "")
        alt = node.attrs.get("alt", "")
        title = node.attrs.get("title")
        if title:
            return "![%s](%s \"%s\")" % (alt, src, title)
        return "![%s](%s)" % (alt, src)
    if tag == "br":
        return "\n"
    # default: concatenate children inline
    return "".join(_inline(c) for c in node.children)


def _pre_text(node):
    if node.tag is None:
        return node.text
    return "".join(_pre_text(c) for c in node.children)


def _code_lang(node):
    for c in node.children:
        if c.tag == "code":
            cls = c.attrs.get("class", "")
            for part in cls.split():
                if part.startswith("language-"):
                    return part[len("language-"):]
                if part.startswith("lang-"):
                    return part[len("lang-"):]
            if c.attrs.get("data-lang"):
                return c.attrs["data-lang"]
    return ""


def _list_item(node, depth, ordered, index):
    marker = "%s%d. " % ("  " * depth, index) if ordered else "%s- " % ("  " * depth)
    inline_parts = []
    nested = []
    for child in node.children:
        if child.tag in ("ul", "ol"):
            nested.append(child)
        elif child.tag in ("p", "div", "span"):
            inline_parts.append("".join(_inline(c) for c in child.children))
        else:
            inline_parts.append(_inline(child))
    text = "".join(inline_parts).strip()
    result = marker + text if text else marker.rstrip()
    for n in nested:
        result += "\n" + _list(n, depth + 1, n.tag == "ol").rstrip("\n")
    return result


def _list(node, depth, ordered):
    lines = []
    index = 1
    for child in node.children:
        if child.tag == "li":
            lines.append(_list_item(child, depth, ordered, index))
            index += 1
        else:
            inner = _block(child, depth).strip()
            if inner:
                lines.append(inner)
    return "\n".join(lines) + "\n\n"


def _table(node):
    rows = []
    for tr in node.children:
        if tr.tag != "tr":
            continue
        cells = []
        for cell in tr.children:
            if cell.tag in ("td", "th"):
                cells.append("".join(_inline(c) for c in cell.children).strip())
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    widths = [0] * ncols
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(cell))
    out = []
    header = rows[0]
    out.append("| " + " | ".join(header[i].ljust(widths[i]) for i in range(ncols)) + " |")
    out.append("|" + "|".join("-" * (widths[i] + 2) for i in range(ncols)) + "|")
    for r in rows[1:]:
        out.append("| " + " | ".join(r[i].ljust(widths[i]) for i in range(ncols)) + " |")
    return "\n".join(out)


def _block(node, depth=0):
    if node.tag is None:
        return node.text
    tag = node.tag
    if tag in SKIP_TAGS:
        return ""
    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        level = int(tag[1])
        return "#" * level + " " + "".join(_inline(c) for c in node.children).strip() + "\n\n"
    if tag == "p":
        inner = "".join(_inline(c) for c in node.children).strip()
        return inner + "\n\n" if inner else ""
    if tag == "hr":
        return "---\n\n"
    if tag in ("ul", "ol"):
        return _list(node, depth, tag == "ol")
    if tag == "li":
        return _list_item(node, depth, False, 1) + "\n\n"
    if tag == "pre":
        lang = _code_lang(node)
        text = _pre_text(node).strip("\n")
        return "```%s\n%s\n```\n\n" % (lang, text)
    if tag == "blockquote":
        inner = "".join(_block(c, depth + 1) for c in node.children).strip()
        lines = ["> " + line if line else ">" for line in inner.split("\n")]
        return "\n".join(lines) + "\n\n"
    if tag == "table":
        return _table(node) + "\n\n"
    if tag in ("div", "section", "article", "main", "header", "footer", "nav",
               "aside", "figure", "figcaption", "form", "fieldset", "body",
               "html", "span", "center", "details", "summary"):
        return "".join(_block(c, depth) for c in node.children)
    if tag == "br":
        return "\n"
    # Default: treat as a container and render children as blocks.
    # (This is what makes the root/html/unknown tags preserve structure.)
    return "".join(_block(c, depth) for c in node.children)


def html_to_markdown(html: str) -> str:
    """Convert an HTML string to Markdown text."""
    return _block(parse_html(html)).strip() + "\n"


# --------------------------------------------------------------------------
# HTML -> plain text
# --------------------------------------------------------------------------

def _extract_text(node):
    if node.tag is None:
        return node.text
    if node.tag in SKIP_TAGS:
        return ""
    parts = []
    for child in node.children:
        if child.tag is None:
            parts.append(child.text)
        else:
            parts.append(_extract_text(child))
            if child.tag in BLOCK_TAGS:
                parts.append("\n")
    return "".join(parts)


def html_to_text(html: str) -> str:
    """Convert an HTML string to plain text."""
    text = _extract_text(parse_html(html))
    lines = [ln.rstrip() for ln in text.split("\n")]
    out = []
    blank = 0
    for ln in lines:
        if not ln.strip():
            blank += 1
            if blank <= 1:
                out.append("")
        else:
            blank = 0
            out.append(ln.strip())
    return "\n".join(out).strip() + "\n"


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# IPv4-first name resolution (sandbox networking workaround)
# --------------------------------------------------------------------------
#
# CPython's socket.create_connection (used by urllib/http.client) has no
# Happy Eyeballs: it connects to addresses in getaddrinfo order, one at a
# time, burning the full socket timeout on every address that fails. When
# DNS returns AAAA (IPv6) records first - typical of Cloudflare-fronted
# hosts - and the host's IPv6 route is broken (outbound IPv6 SYN silently
# dropped, which is the case in the sandbox this toolkit runs in), every
# fetch to such a host stalls for (timeout x #v6-addresses) seconds before
# IPv4 is finally tried.
#
# Reordering to IPv4-first eliminates the stall. IPv6 addresses are still
# appended as a fallback, so IPv6-only hosts keep working; if a caller
# pins a specific address family, the original order is preserved.
#
_ORIG_GETADDRINFO = socket.getaddrinfo


def _ipv4_first_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if family not in (0, socket.AF_INET, socket.AF_INET6):
        family = 0
    results = _ORIG_GETADDRINFO(host, port, family, type, proto, flags)
    if family != 0 or not results:
        return results
    v4 = [r for r in results if r[0] == socket.AF_INET]
    v6 = [r for r in results if r[0] == socket.AF_INET6]
    if not v4:
        return results
    return v4 + v6


@contextmanager
def _ipv4_first():
    """Context manager: make socket resolution try IPv4 addresses first."""
    original = socket.getaddrinfo
    socket.getaddrinfo = _ipv4_first_getaddrinfo
    try:
        yield
    finally:
        socket.getaddrinfo = original


def webfetch(url: str, format: str = "markdown", timeout: float = DEFAULT_TIMEOUT, max_size: int = MAX_RESPONSE_SIZE, headers: dict[str, str] | None = None, verbose: bool = False) -> dict[str, Any]:
    """Fetch a URL and return a dict with 'output' (and metadata).

    Raises ValueError on bad args, RuntimeError on fetch/limit errors.
    """
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("URL must start with http:// or https://")
    if format not in ("text", "markdown", "html"):
        raise ValueError("format must be 'text', 'markdown', or 'html'")

    timeout = min(max(int(timeout or 0), 0), MAX_TIMEOUT)
    if timeout == 0:
        timeout = DEFAULT_TIMEOUT

    accept = ACCEPT_HEADERS.get(format, DEFAULT_ACCEPT)
    hdrs = {
        "User-Agent": BROWSER_UA,
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    }
    if headers:
        hdrs.update(headers)

    def _do(ua):
        h = dict(hdrs)
        h["User-Agent"] = ua
        req = urllib.request.Request(url, headers=h)
        with _ipv4_first():
            return urllib.request.urlopen(req, timeout=timeout)

    resp = None
    try:
        resp = _do(BROWSER_UA)
    except urllib.error.HTTPError as e:
        if (e.code == 403
                and str(e.headers.get("cf-mitigated", "")).lower() == "challenge"):
            if verbose:
                print("Cloudflare challenge detected; retrying with honest UA",
                      file=sys.stderr)
            try:
                resp = _do(HONEST_UA)
            except urllib.error.HTTPError as e2:
                raise RuntimeError("HTTP %d %s for %s" % (e2.code, e2.reason, url)) from e2
        else:
            raise RuntimeError("HTTP %d %s for %s" % (e.code, e.reason, url)) from e
    except urllib.error.URLError as e:
        raise RuntimeError("Failed to fetch %s: %s" % (url, e.reason)) from e
    except Exception as e:  # socket.timeout etc.
        raise RuntimeError("Request to %s failed: %s" % (url, e)) from e

    try:
        with resp:
            content_type = resp.headers.get("Content-Type", "")
            cl = resp.headers.get("Content-Length")
            if cl and cl.isdigit() and int(cl) > max_size:
                raise RuntimeError("Response too large (exceeds %d byte limit)" % max_size)
            data = resp.read(max_size + 1)
            if len(data) > max_size:
                raise RuntimeError("Response too large (exceeds %d byte limit)" % max_size)
            final_url = resp.geturl()
            status = getattr(resp, "status", 200)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError("Failed reading response: %s" % e) from e

    # Decompress Content-Encoding (gzip / deflate). Brotli needs the 'brotli'
    # package, which isn't stdlib; we give a clear error if a server sends it.
    encoding = (resp.headers.get("Content-Encoding") or "").strip().lower()
    if encoding in ("gzip", "x-gzip"):
        try:
            data = gzip.decompress(data)
        except OSError as e:
            raise RuntimeError("Failed to decompress gzip response: %s" % e) from e
    elif encoding == "deflate":
        try:
            data = zlib.decompress(data)
        except zlib.error:
            data = zlib.decompress(data, -zlib.MAX_WBITS)
    elif encoding == "br":
        raise RuntimeError("Content-Encoding 'br' (Brotli) is not supported "
                           "(stdlib has no brotli; install the 'brotli' package)")
    if len(data) > max_size:
        raise RuntimeError("Response too large (exceeds %d byte limit)" % max_size)

    mime = content_type.split(";")[0].strip().lower()
    title = "%s (%s)" % (url, content_type)

    if mime.startswith("image/"):
        b64 = base64.b64encode(data).decode("ascii")
        return {
            "url": url, "final_url": final_url, "status": status,
            "content_type": content_type, "format": format, "title": title,
            "output": "Image fetched successfully", "mime": mime,
            "size": len(data), "data": data, "base64": b64,
            "attachments": [{"type": "file", "mime": mime,
                             "url": "data:%s;base64,%s" % (mime, b64)}],
        }

    content = data.decode("utf-8", errors="replace")

    if format == "markdown" and "html" in content_type:
        output = html_to_markdown(content)
    elif format == "text" and "html" in content_type:
        output = html_to_text(content)
    else:
        output = content

    return {
        "url": url, "final_url": final_url, "status": status,
        "content_type": content_type, "format": format, "title": title,
        "output": output, "mime": mime, "size": len(data),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _selftest():
    ok = True
    def check(name, cond):
        nonlocal ok
        print(("PASS" if cond else "FAIL") + "  " + name)
        if not cond:
            ok = False

    # 1. URL validation
    try:
        webfetch("ftp://example.com", timeout=1)
        check("URL scheme validation", False)
    except ValueError:
        check("URL scheme validation", True)

    # 2. Markdown conversion
    html = ("<html><head><title>x</title><style>body{}</style></head><body>"
            "<h1>Hello</h1><p>This is <strong>bold</strong> and "
            "<a href=\"https://e.com\">a link</a>.</p>"
            "<ul><li>one</li><li>two</li></ul>"
            "<pre><code class=\"language-python\">print(1)</code></pre>"
            "<script>evil()</script>"
            "</body></html>")
    md = html_to_markdown(html)
    check("markdown has h1", "# Hello" in md)
    check("markdown has bold", "**bold**" in md)
    check("markdown has link", "[a link](https://e.com)" in md)
    check("markdown has list", "- one" in md and "- two" in md)
    check("markdown has fenced code", "```python" in md and "print(1)" in md)
    check("markdown skips script/style", "evil()" not in md and "body{}" not in md)

    # 3. Text extraction
    txt = html_to_text(html)
    check("text has content", "Hello" in txt and "bold" in txt)
    check("text skips script", "evil()" not in txt)

    # 4. Table conversion
    tbl = html_to_markdown("<table><tr><th>A</th><th>B</th></tr>"
                           "<tr><td>1</td><td>2</td></tr></table>")
    check("table has header sep", "| A | B |" in tbl and "---" in tbl)

    # 5. urlgrep / head (fetch is monkeypatched, no network)
    fake = ("line one credit\nline two other\nline three CREDIT again\n"
            "line four\n")
    orig_fetch = globals()["_fetch"]
    globals()["_fetch"] = lambda url, format="text": fake
    try:
        r = urlgrep("x", ["credit"])
        check("urlgrep returns only matching lines",
              "line one credit" in r and "line two" not in r)
        r2 = urlgrep("x", ["credit"], ctx_before=1, ctx_after=1)
        check("urlgrep adds context", "line two" in r2 and "line four" not in r2)
        r3 = urlgrep("x", ["credit"], case=False, max_lines=1)
        check("urlgrep case-insensitive + limit", r3 == "line one credit")
        r4 = head("x", 2)
        check("head returns first n non-empty lines",
              r4 == "line one credit\nline two other")
    finally:
        globals()["_fetch"] = orig_fetch

    print("\n%s" % ("ALL TESTS PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="webfetch.py",
        description="Fetch web content (clone of OpenCode's webfetch tool).",
        epilog=("Examples:\n"
                "  ./webfetch.py https://example.com\n"
                "  ./webfetch.py https://en.wikipedia.org/wiki/Tetris --format text\n"
                "  ./webfetch.py https://example.com --format html -o page.html\n"
                "  ./webfetch.py https://example.com/img.png --save-image img.png\n"
                "  ./webfetch.py https://example.com -g 'timeout' --lines 5\n"
                "  ./webfetch.py https://example.com --head 15"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", nargs="?", help="URL to fetch (must start with http:// or https://)")
    parser.add_argument("-f", "--format", choices=["text", "markdown", "html"],
                        default=None,
                        help="output format (default: markdown; text for --grep)")
    parser.add_argument("-t", "--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help="timeout in seconds (default %d, max %d)" % (DEFAULT_TIMEOUT, MAX_TIMEOUT))
    parser.add_argument("-o", "--output", metavar="FILE",
                        help="write output to FILE instead of stdout")
    parser.add_argument("--save-image", metavar="FILE",
                        help="when fetching an image, save raw bytes to FILE")
    parser.add_argument("-H", "--header", action="append", metavar="NAME:VALUE",
                        help="extra request header (repeatable)")
    parser.add_argument("--max-size", type=int, default=MAX_RESPONSE_SIZE,
                        help="max response size in bytes (default %d)" % MAX_RESPONSE_SIZE)
    parser.add_argument("--show-meta", action="store_true",
                        help="print status/content-type/size to stderr")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="suppress status messages")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="verbose messages")
    parser.add_argument("-g", "--grep", action="append", metavar="PATTERN",
                        help="print only lines matching PATTERN (regex, repeatable)")
    parser.add_argument("--lines", type=int, default=10,
                        help="max matching lines for --grep (default 10)")
    parser.add_argument("--head", type=int, default=0, metavar="N",
                        help="print first N non-empty lines instead of the whole page")
    parser.add_argument("--width", type=int, default=150,
                        help="truncate each printed line to W chars (default 150)")
    parser.add_argument("--before", type=int, default=0,
                        help="context lines before each --grep match")
    parser.add_argument("--after", type=int, default=0,
                        help="context lines after each --grep match")
    parser.add_argument("-i", "--ignore-case", action="store_true",
                        help="case-insensitive --grep")
    parser.add_argument("--selftest", action="store_true",
                        help="run built-in self tests and exit")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()

    if not args.url:
        parser.error("the following arguments are required: url")

    headers = {}
    for h in args.header or []:
        if ":" in h:
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()
        else:
            print("webfetch: warning: ignoring header without colon: %r" % h,
                  file=sys.stderr)

    # Distilled modes: only matching / leading lines reach the output.
    if args.head or args.grep:
        try:
            if args.head:
                text = head(args.url, args.head, args.width,
                            format=args.format or "markdown")
            else:
                text = urlgrep(args.url, args.grep, args.lines, args.width,
                               args.before, args.after,
                               case=not args.ignore_case,
                               format=args.format or "text")
        except Exception as e:
            print("webfetch: error: %s" % e, file=sys.stderr)
            return 1
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            if not args.quiet:
                print("webfetch: wrote %d chars to %s"
                      % (len(text), args.output), file=sys.stderr)
        else:
            print(text)
        return 0

    try:
        result = webfetch(args.url, format=args.format or "markdown",
                          timeout=args.timeout,
                          max_size=args.max_size, headers=headers,
                          verbose=args.verbose)
    except Exception as e:
        print("webfetch: error: %s" % e, file=sys.stderr)
        return 1

    if not args.quiet:
        meta = ("# %s\n# status=%s format=%s size=%d bytes final_url=%s"
                % (result["title"], result["status"], result["format"],
                   result["size"], result["final_url"]))
        print(meta, file=sys.stderr)

    if args.save_image and result.get("mime", "").startswith("image/"):
        with open(args.save_image, "wb") as f:
            f.write(result["data"])
        if not args.quiet:
            print("webfetch: saved image (%s, %d bytes) to %s"
                  % (result["mime"], result["size"], args.save_image),
                  file=sys.stderr)

    output = result["output"]
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        if not args.quiet:
            print("webfetch: wrote %d chars to %s" % (len(output), args.output),
                  file=sys.stderr)
    else:
        sys.stdout.write(output)
        if not output.endswith("\n"):
            sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
