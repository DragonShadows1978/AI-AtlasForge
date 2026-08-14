# WP-E1 — WebProxy: extraction quality (trafilatura, honest truncation, binary guard, DDG unwrap, JS-render heuristics)

YOUR WRITABLE TARGET is `/mnt/ForgeRealm/wt-webproxy-e1` — edits and test
runs AUTHORIZED. This is a git worktree of AI-AtlasForge on branch
`webproxy/upgrade-2026-08`. Work ONLY inside it.

## Boundaries

- WRITABLE: `/mnt/ForgeRealm/wt-webproxy-e1/WebProxy/**`. Temp files under
  `/mnt/ForgeRealm/wt-webproxy-e1/tmp_wp_e1/` (create it).
- READ-ONLY: everything else. Your sandbox has NO network — all verification
  is offline (pytest with mocked HTTP / fixture HTML). Do not attempt live
  fetches; the lead runs live smoke after merge.
- NO git operations except read-only `git diff --stat`. The lead commits.
- NO subagents.
- DO NOT touch the live proxy on port 8765 or any systemd unit.
- Two sibling seats are editing OTHER regions of `WebProxy/service.py` and
  `WebProxy/mcp_server.py` in parallel worktrees (redirect/status handling in
  the HTTP layer; cache eviction/stats). Keep your diff surgical: no
  reformatting, no renames of existing symbols, no function reordering, no
  drive-by cleanups outside this order's scope.

## Context

`WebProxy/service.py` fetches pages and extracts content in
`extract_page_content()` (~line 1450): BeautifulSoup + lxml, strip
script/style/noscript/svg/iframe/canvas, then a naive container pick
(`main` → `article` → `body` → soup) and h1/h2/h3/p/li/blockquote text
assembly. Pages without semantic HTML return nav/footer/cookie soup with no
quality signal. Truncation (`_apply_max_chars`, ~line 675) is a raw
`text[:max_chars]` slice — mid-word, no marker, no flag the model can see.
Non-text binaries fall through to text decoding with `errors="replace"`
garbage. The DuckDuckGo HTML scrape takes `a.result__a` hrefs verbatim, which
are sometimes DDG redirector URLs (`duckduckgo.com/l/?uddg=...`).
`js_render.py` heuristics (`should_render`) miss SPAs that server-render
>200 chars of boilerplate.

The output dict of `extract_page_content` (`title`, `meta_description`,
`headings`, `text`, `links`, `text_length`) is a STABLE CONTRACT consumed by
the cache layer and `mcp_server.py`'s `_format_fetch_results`. Keep every
existing key; you may ADD keys.

**trafilatura 2.2.0 is already installed** in the user site-packages
(`python3 -c "import trafilatura"` works). Guard the import anyway — if it's
missing at runtime, fall back to the current pipeline with a single warning
log, never a crash.

## Deliverables

### D1 — trafilatura as primary extractor

In `extract_page_content()`:
- New env knob `ATLASFORGE_WEB_PROXY_EXTRACTOR` = `auto` (default) |
  `trafilatura` | `bs4`.
- `auto`/`trafilatura`: run `trafilatura.extract()` on the HTML (with
  `url=`, comments off; pick settings for max fidelity, favor recall) for the
  main `text`. If trafilatura returns None or < 200 chars in `auto` mode,
  fall back to the existing BS4 assembly.
- `title`/`meta_description`: prefer trafilatura metadata when present, else
  the existing BS4 extraction. `headings` and `links` stay BS4-derived as
  today (trafilatura doesn't give you the link list shape you need).
- ADD `extraction_method` key: `"trafilatura"` | `"bs4"` | `"bs4_fallback"`.
- `mcp_server.py` `_format_fetch_results`: include the extraction method in
  the header block.

### D2 — Honest truncation

Rework `_apply_max_chars` truncation:
- Cut at the last whitespace within the final 200 chars of the window when
  one exists (never mid-word if avoidable); total text stays ≤ max_chars.
- Append a visible marker on its own line:
  `…[truncated: showing N of M chars]`.
- ADD `truncated: true/false` and `full_text_length` keys to the payload.
- `_format_fetch_results` surfaces truncation to the model (the marker line
  already does this if it's inside `text`; make sure it survives formatting).
- Existing tests that assert exact `text[:max_chars]` slicing will need
  updating to the new contract — that is in scope and expected.

### D3 — Binary content guard

In `fetch_page`: when the response Content-Type is none of
text/html/xml/json/plain families, `image/*` (existing branch), or PDF:
- Do NOT decode the body as text. Return a structured result with the normal
  contract keys where `text` is a one-line description like
  `Binary content (application/zip, 1234567 bytes) — not fetchable as text.`,
  plus ADD `content_kind: "binary"`, `content_type`, `content_length`.
- `application/pdf` on plain `/fetch`: same structured shape with a hint
  text directing the caller to the PaperFetch tool / `/paper/fetch`.

### D4 — DDG redirector unwrap

In the DuckDuckGo HTML-scrape parser: when a result href is a DDG redirector
(`duckduckgo.com/l/?uddg=<encoded>` or scheme-relative `//duckduckgo.com/l/`),
unwrap to the real destination via `urllib.parse` (unquote `uddg`). Non-
redirector hrefs pass through untouched. Malformed redirector → keep the raw
href (never drop a result).

### D5 — JS-render heuristic upgrade + visibility

In `js_render.py` `should_render()`:
- Add a text-ratio signal: strip tags (cheap regex or BS4) and compare
  visible-text length against HTML size and script count; a page with an SPA
  framework signal whose visible text is small relative to its markup
  (pick and document thresholds) should render even if it exceeds the old
  flat 200-char floor. Keep all existing positive signals.
- Document the thresholds in the module docstring.
In `service.py`/`mcp_server.py`: the fetch JSON already carries a
`js_rendered`/rendered flag internally — make sure it's set consistently and
`_format_fetch_results` shows `JS-rendered: yes` in the header when a page
went through Playwright.

## Gates (registered; run all, report verbatim)

1. Baseline first: `cd /mnt/ForgeRealm/wt-webproxy-e1 && python3 -m pytest WebProxy/tests/ -q`
   must be green BEFORE your edits; if RED, STOP and report.
2. Full suite green after your edits (updated truncation-contract tests
   included).
3. New tests covering at minimum:
   - trafilatura path returns article text on a fixture page whose content
     lives in a bare `<div>` (the case BS4's container pick fails);
   - `auto` fallback to BS4 when trafilatura yields nothing;
   - `ATLASFORGE_WEB_PROXY_EXTRACTOR=bs4` forces the old path;
   - truncation: marker present, `truncated` flag set, no mid-word cut on a
     normal-prose fixture, `max_chars` respected;
   - binary guard: `application/zip` body returns the structured binary
     shape with no mojibake text;
   - PDF-on-/fetch hint shape;
   - DDG unwrap: wrapped href → real URL; malformed wrapper → raw href kept;
   - `should_render` text-ratio: SPA shell with 500 chars of boilerplate
     over 100KB of markup → True; a normal article page → False.

## Honesty rails

- RED is a result. Report failed gates with receipts; never adjust a
  threshold after seeing results.
- No monitor-idling: run gates synchronously to completion.
- Every claim in your report must be reproducible from the worktree.

## Done

Your final message MUST contain, verbatim:
- `git diff --stat` output.
- Full pytest summary line(s): baseline AND final.
- New-test names and pass status.
- The chosen trafilatura settings and the D5 thresholds, stated explicitly.
- A **Residuals** section: anything not covered, known weaknesses, follow-ups.
