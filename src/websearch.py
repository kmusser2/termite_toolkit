#!/usr/bin/env python3
# TYPE: TOOLKIT
"""websearch.py - Search the web and print results (DuckDuckGo HTML).
# keywords: search|web|internet|query|duckduckgo|bing|mojeek|results|snippet|google|lookup|find
# examples: import websearch; websearch.search("quantum computing", num=5)

Built on top of webfetch.py (same directory). Queries DuckDuckGo's
JS-free HTML endpoint and extracts titles, real URLs, and snippets.

CLI:
  ./websearch.py <query words...> [options]

Options:
  -n, --num N           number of results to show (default 5, max 30)
  -t, --timeout SECONDS timeout in seconds (default 30, max 120)
  -j, --json            print results as JSON
  -q, --quiet           suppress status messages
  -v, --verbose         verbose messages
      --selftest        run built-in offline self tests and exit

Python API:
  import websearch
  results = websearch.search("python http server", num=5)
  for r in results:
      print(r["title"], r["url"])
      print(r["snippet"])
"""

import argparse
import base64
import hashlib
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

import webfetch

SEARCH_URL = "https://html.duckduckgo.com/html/?q=%s"
BING_URL = "https://www.bing.com/search?q=%s&setlang=en&count=%d"
MOJEEK_URL = "https://www.mojeek.com/search?q=%s"

ENGINE_LIST = ["ddg", "bing", "mojeek"]

# Local cache + DDG cooldown (be sparse with the web!).
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          ".websearch_cache.json")
COOLDOWN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             ".ddg_cooldown")
RR_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             ".engine_rr")
CACHE_TTL = 3600          # 1 hour
DDG_COOLDOWN = 300        # 5 min after a 202 bot-block


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def decode_ddg_url(href: str) -> str:
    """Decode a DuckDuckGo redirect URL to the real URL.

    href is like: //duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com&rut=...
    If it's already a plain URL (no redirect), return as-is.
    """
    if not href:
        return ""
    if "uddg=" in href:
        q = urllib.parse.urlsplit(href).query
        params = urllib.parse.parse_qs(q)
        if "uddg" in params and params["uddg"]:
            return params["uddg"][0]
    return href


def decode_bing_url(href: str) -> str:
    """Decode a Bing /ck/a redirect URL to the real URL.

    href is like: https://www.bing.com/ck/a?!&&p=...&u=a1<base64>&ntb=1
    The u= param is 'a1' + base64 of the real URL.
    """
    if not href or "bing.com/ck/a" not in href:
        return href
    try:
        q = urllib.parse.urlsplit(href).query
        params = urllib.parse.parse_qs(q)
        u = params.get("u", [""])[0]
        if u.startswith("a1"):
            b64 = u[2:].replace("-", "+").replace("_", "/")
            b64 += "=" * (-len(b64) % 4)
            return base64.b64decode(b64).decode("utf-8", errors="replace")
    except Exception:
        pass
    return href


def _class_has(node, token):
    cls = node.attrs.get("class", "")
    return token in cls.split()


def _find_all(node, tag=None, class_token=None):
    """Depth-first search for nodes by tag and/or class token."""
    found = []
    for child in node.children:
        if child.tag is not None:
            if (tag is None or child.tag == tag) and (class_token is None or _class_has(child, class_token)):
                found.append(child)
            found.extend(_find_all(child, tag, class_token))
    return found


def _text(node):
    """All text under a node (no markdown)."""
    parts = []
    if node.tag is None:
        return node.text
    for child in node.children:
        parts.append(_text(child))
    return "".join(parts)


def _cache_key(query, num, engine):
    h = hashlib.sha256(("%s|%d|%s" % (query, num, engine)).encode("utf-8"))
    return h.hexdigest()[:24]


def _atomic_write(path, text):
    """Write text atomically: write a .new file, then os.replace().

    The sandbox denies plain 'w' overwrites of existing files (Errno 13),
    so we always write a fresh file and swap it in (per AGENTS.md).
    """
    try:
        tmp = path + ".new"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
        return True
    except Exception as e:
        print("websearch: warning: could not write %s: %s" % (path, e),
              file=sys.stderr)
        return False


def _cache_load():
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _cache_save(cache):
    try:
        tmp = CACHE_PATH + ".new"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        os.replace(tmp, CACHE_PATH)
    except Exception as e:
        print("websearch: warning: could not write cache: %s" % e, file=sys.stderr)


def _cache_get(cache, key, ttl):
    entry = cache.get(key)
    if not entry:
        return None
    if time.time() - entry.get("ts", 0) > ttl:
        return None
    return entry.get("results")


def _ddg_in_cooldown():
    try:
        return time.time() - os.path.getmtime(COOLDOWN_PATH) < DDG_COOLDOWN
    except Exception:
        return False


def _ddg_set_cooldown():
    _atomic_write(COOLDOWN_PATH, str(time.time()))


def search_ddg(query: str, num: int = 5, timeout: float = 30, verbose: bool = False) -> list[dict[str, str]]:
    """Search DuckDuckGo's HTML endpoint. Raises RuntimeError on bot-block."""
    url = SEARCH_URL % urllib.parse.quote_plus(query.strip())
    if verbose:
        print("websearch: ddg fetching %s" % url, file=sys.stderr)

    result = webfetch.webfetch(url, format="html", timeout=timeout, verbose=verbose)
    if result["status"] != 200:
        raise RuntimeError("DuckDuckGo returned HTTP %d" % result["status"])
    html = result["output"]

    if "No results found" in html or "result--no-result" in html:
        return []

    tree = webfetch.parse_html(html)

    # Each result is a <div class="result ..."> containing a.result__a (title)
    # and a.result__snippet (snippet). Group by the result container.
    results = []
    seen = set()
    for container in _find_all(tree, tag="div", class_token="result"):
        a_link = None
        snippet = None
        for a in _find_all(container, tag="a"):
            if a_link is None and _class_has(a, "result__a"):
                a_link = a
            if snippet is None and _class_has(a, "result__snippet"):
                snippet = a
            if a_link and snippet:
                break
        if a_link is None:
            continue
        title = _text(a_link).strip()
        href = a_link.attrs.get("href", "")
        real_url = decode_ddg_url(href)
        snip = _text(snippet).strip() if snippet else ""
        if real_url in seen:
            continue
        seen.add(real_url)
        results.append({"title": title, "url": real_url, "snippet": snip})
        if len(results) >= num:
            break

    return results


def search_bing(query: str, num: int = 5, timeout: float = 30, verbose: bool = False) -> list[dict[str, str]]:
    """Search Bing. Returns a list of dicts (same shape as search_ddg)."""
    import re
    url = BING_URL % (urllib.parse.quote_plus(query.strip()), min(max(num, 1), 30))
    if verbose:
        print("websearch: bing fetching %s" % url, file=sys.stderr)

    result = webfetch.webfetch(url, format="html", timeout=timeout, verbose=verbose)
    if result["status"] != 200:
        raise RuntimeError("Bing returned HTTP %d" % result["status"])
    html = result["output"]

    results = []
    seen = set()
    # Each result is <li class="b_algo"> ... <h2><a href="...">Title</a></h2>
    # ... <p>snippet</p> ... </li>
    for block in re.split(r'<li class="b_algo"', html)[1:]:
        m = re.search(r'<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        href = m.group(1).replace("&amp;", "&")
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        real_url = decode_bing_url(href)
        if not real_url.startswith("http"):
            continue
        ms = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        snip = re.sub(r"<[^>]+>", "", ms.group(1)).strip() if ms else ""
        snip = re.sub(r"&nbsp;|&#0183;|&#32;", " ", snip)
        if real_url in seen:
            continue
        seen.add(real_url)
        results.append({"title": title, "url": real_url, "snippet": snip})
        if len(results) >= num:
            break

    return results


def search_mojeek(query: str, num: int = 5, timeout: float = 30, verbose: bool = False) -> list[dict[str, str]]:
    """Search Mojeek. Direct result URLs, no redirect decoding needed."""
    import re
    url = MOJEEK_URL % urllib.parse.quote_plus(query.strip())
    if verbose:
        print("websearch: mojeek fetching %s" % url, file=sys.stderr)

    result = webfetch.webfetch(url, format="html", timeout=timeout, verbose=verbose)
    if result["status"] != 200:
        raise RuntimeError("Mojeek returned HTTP %d" % result["status"])
    html = result["output"]

    results = []
    seen = set()
    # Each result: <li class="r1"> ... <h2><a class="title" href="URL">Title</a></h2>
    #                       ... <p class="s">snippet</p> ...
    for block in re.split(r'<li class="r', html)[1:]:
        m = re.search(r'<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a></h2>', block, re.S)
        if not m:
            continue
        href = m.group(1).replace("&amp;", "&")
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if not href.startswith("http"):
            continue
        ms = re.search(r'<p class="s"[^>]*>(.*?)</p>', block, re.S)
        snip = re.sub(r"<[^>]+>", "", ms.group(1)).strip() if ms else ""
        snip = re.sub(r"<[^>]+>", "", snip)
        if href in seen:
            continue
        seen.add(href)
        results.append({"title": title, "url": href, "snippet": snip})
        if len(results) >= num:
            break

    return results


def _search_one(engine, query, num, timeout, verbose):
    if engine == "ddg":
        return search_ddg(query, num=num, timeout=timeout, verbose=verbose)
    if engine == "bing":
        return search_bing(query, num=num, timeout=timeout, verbose=verbose)
    if engine == "mojeek":
        return search_mojeek(query, num=num, timeout=timeout, verbose=verbose)
    raise ValueError("unknown engine: %r" % engine)


def _rr_next():
    """Return the next engine index for round-robin, advancing the state."""
    try:
        with open(RR_STATE_PATH, "r") as f:
            idx = int(f.read().strip())
    except Exception:
        idx = -1
    nxt = (idx + 1) % len(ENGINE_LIST)
    _atomic_write(RR_STATE_PATH, str(nxt))
    return nxt


def search(query: str, num: int = 5, timeout: float = 30, verbose: bool = False, engine: str = "auto", use_cache: bool = True, cache_ttl: int = CACHE_TTL) -> list[dict[str, str]]:
    """Search the web and return a list of dicts:
    [{"title": str, "url": str, "snippet": str}, ...]

    engine: "auto" (round-robin across ddg -> bing -> mojeek, rotating
            the start engine each call), or force one: "ddg", "bing",
            "mojeek".
    Results are cached on disk (TTL cache_ttl seconds) so repeated searches
    don't hit the network. After a DDG 202 bot-block, auto mode skips DDG
    for a cooldown period.
    """
    if not query or not query.strip():
        raise ValueError("query must not be empty")
    num = max(1, min(int(num), 30))

    key = _cache_key(query, num, engine)
    cache = _cache_load() if use_cache else {}
    if use_cache:
        cached = _cache_get(cache, key, cache_ttl)
        if cached is not None:
            if verbose:
                print("websearch: cache hit for %r" % query, file=sys.stderr)
            return cached

    if engine != "auto":
        # Explicit engine: no round-robin.
        results = _search_one(engine, query, num, timeout, verbose)
    else:
        # Round-robin: rotate start engine each call, try the rest in order.
        start = _rr_next()
        order = ENGINE_LIST[start:] + ENGINE_LIST[:start]
        last_err = None
        for eng in order:
            if eng == "ddg" and _ddg_in_cooldown():
                if verbose:
                    print("websearch: ddg in cooldown; skipping", file=sys.stderr)
                continue
            try:
                results = _search_one(eng, query, num, timeout, verbose)
                break
            except RuntimeError as e:
                last_err = e
                if eng == "ddg":
                    _ddg_set_cooldown()
                if verbose:
                    print("websearch: %s failed (%s); trying next" % (eng, e),
                          file=sys.stderr)
        else:
            raise last_err or RuntimeError("all search engines failed")

    if use_cache:
        cache[key] = {"ts": time.time(), "results": results}
        _cache_save(cache)
    return results


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

    # URL decoding
    check("decode uddg redirect",
          decode_ddg_url("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fb%3D1&rut=abc")
          == "https://example.com/a?b=1")
    check("decode plain url", decode_ddg_url("https://example.com") == "https://example.com")
    check("decode bing redirect",
          decode_bing_url("https://www.bing.com/ck/a?!&&p=x&u=a1aHR0cHM6Ly93d3cucHl0aG9uLm9yZy8&ntb=1")
          == "https://www.python.org/")
    check("decode bing plain", decode_bing_url("https://example.com") == "https://example.com")
    check("decode empty", decode_ddg_url("") == "")

    # Query URL building
    u = SEARCH_URL % urllib.parse.quote_plus("hello world")
    check("query encoding", "q=hello+world" in u and u.startswith("https://html.duckduckgo.com/html/"))

    # Mojeek parsing (offline): sample result block
    moj = ('<li class="r1"><a class="ob" href="https://x.io"><p class="i">'
           '<span class="url">https://x.io</span></p></a>'
           '<h2><a class="title" title="https://x.io" href="https://x.io">X Site</a></h2>'
           '<p class="s">A snippet here.</p></li>')
    results = []
    import re as _re
    for block in _re.split(r'<li class="r', moj)[1:]:
        m = _re.search(r'<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a></h2>', block, _re.S)
        if m:
            results.append((m.group(1), _re.sub(r"<[^>]+>", "", m.group(2)).strip()))
    check("mojeek parse", results == [("https://x.io", "X Site")])

    # Round-robin rotation advances
    r1 = _rr_next()
    r2 = _rr_next()
    check("round-robin rotates", (r2 - r1) % len(ENGINE_LIST) == 1)

    # DOM search helper: parse tiny html with a result div
    tiny = ('<html><body>'
            '<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fx.io">X</a>'
            '<a class="result__snippet" href="#">snip here</a></div>'
            '</body></html>')
    tree = webfetch.parse_html(tiny)
    divs = _find_all(tree, tag="div", class_token="result")
    check("find result divs", len(divs) == 1)
    links = _find_all(tree, tag="a", class_token="result__a")
    check("find result__a links", len(links) == 1 and _text(links[0]) == "X")

    print("\n%s" % ("ALL TESTS PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="websearch.py",
        description="Search the web via DuckDuckGo HTML endpoint and print results.",
        epilog=("Examples:\n"
                "  ./websearch.py terminal tetris c++ ansi\n"
                "  ./websearch.py -n 10 'python http server'\n"
                "  ./websearch.py -j -n 3 opencode webfetch\n"
                "  ./websearch.py --engine bing python http server\n"
                "  ./websearch.py --selftest"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("query", nargs="*", help="search query (multiple words OK)")
    parser.add_argument("-n", "--num", type=int, default=5, help="number of results (default 5, max 30)")
    parser.add_argument("-t", "--timeout", type=int, default=30,
                        help="timeout in seconds (default 30, max 120)")
    parser.add_argument("--engine", choices=["auto", "ddg", "bing", "mojeek"],
                        default="auto",
                        help="search engine: auto (default, round-robin ddg/bing/mojeek), "
                             "ddg, bing, or mojeek")
    parser.add_argument("--no-cache", action="store_true",
                        help="disable result caching (default: cache 1 hour)")
    parser.add_argument("--cache-ttl", type=int, default=CACHE_TTL,
                        help="cache TTL in seconds (default %d)" % CACHE_TTL)
    parser.add_argument("-j", "--json", action="store_true", help="print results as JSON")
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress status messages")
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose messages")
    parser.add_argument("--selftest", action="store_true", help="run offline self tests and exit")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()

    if not args.query:
        parser.error("the following arguments are required: query")

    query = " ".join(args.query)

    try:
        results = search(query, num=args.num, timeout=args.timeout,
                         verbose=args.verbose, engine=args.engine,
                         use_cache=not args.no_cache, cache_ttl=args.cache_ttl)
    except Exception as e:
        print("websearch: error: %s" % e, file=sys.stderr)
        return 1

    if not args.quiet:
        print("# %d result(s) for: %s" % (len(results), query), file=sys.stderr)

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        for i, r in enumerate(results, 1):
            print("%d. %s" % (i, r["title"]))
            print("   %s" % r["url"])
            if r["snippet"]:
                print("   %s" % r["snippet"])
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
