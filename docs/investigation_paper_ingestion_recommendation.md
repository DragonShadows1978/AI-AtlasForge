# Investigation Paper Ingestion and Function Transparency Recommendation

Date: 2026-05-07

## Executive Summary

There are state-of-the-art patterns for this problem, but not a drop-in feature that makes an agent's paper quotes trustworthy by prompt alone. The reliable pattern is:

1. Resolve the cited paper to a canonical identifier and full-text artifact.
2. Download and persist the full artifact when licensing allows it.
3. Extract the full document into structured text with page/section provenance.
4. Validate every quoted passage against exact spans in the extracted document.
5. Feed final synthesis only validated evidence blocks, not unsupported subagent prose.
6. Persist tool/function traces so the investigation can show exactly how each source was found, fetched, and used.

AtlasForge should implement this as deterministic infrastructure around the subagents. Do not rely on Haiku to decide whether it used WebProxy "enough."

## Current AtlasForge Reality

The investigation runner currently receives each subagent result as one completed response string. In `investigation_engine.py`, `_run_single_subagent()` calls `invoke_claude(...)` and stores the returned response in `SubagentResult.findings`. The Python layer does not consume the subagent's WebSearch/WebFetch steps as structured evidence.

The current subagent prompt asks agents to use `WebSearch` and `WebFetch`, cross-reference sources, and document URLs, but it does not require full-paper ingestion before quoting a paper.

The validator fetch path is better than no validation, but it is not sufficient for full-paper quote enforcement:

- `investigation_validator/source_fetcher.py` tries WebProxy first, then falls back to direct HTTP.
- It calls WebProxy `/fetch` with `max_chars: 0`.
- It extracts PDF text only from the first 20 pages.
- It truncates source content at `ValidationConfig.max_source_chars`, currently 50,000 characters.

The WebProxy has another important mismatch:

- `WebProxy/mcp_server.py` advertises `WebFetch` as returning title, headings, full text, and links.
- The MCP handler currently calls `/fetch` with `{"url": url, "max_chars": 0}`.
- `WebProxy/service.py` documents and implements `max_chars == 0` as empty text, while `max_chars < 0` means unlimited internally. The HTTP endpoint rejects negative `max_chars`, but it caches by internally calling `fetch_page(..., max_chars=-1)` and then truncates per caller.

That means Haiku may be using the WebFetch tool, but the tool wrapper can still hand it an empty text body. This should be treated as an immediate WebProxy/MCP contract bug.

Tool transparency partly exists already. `agent_stream_manager.py` parses Claude stream-json `tool_use` and `tool_result` blocks into `tool_call` and `tool_result` events. That trace is not yet promoted into an investigation evidence ledger, and the runner does not use it to gate citations.

## Direct Answer: Why Not Fetch The Papers Directly?

Yes. For paper quotes, AtlasForge should fetch papers directly in their entirety when the paper is open-access or otherwise legally accessible to the local user. The current "website view" path is useful for landing pages, docs, blogs, and search results, but it is not enough for scholarly claims that quote a paper.

The right implementation is not just `WebFetch(arxiv page)`. It is a dedicated paper acquisition path:

1. Detect paper-like sources: arXiv IDs, DOI URLs, PDF URLs, PubMed/PMC IDs, ACL Anthology, OpenReview, Semantic Scholar paper IDs, and publisher landing pages.
2. Resolve to canonical metadata and a full-text URL:
   - arXiv API/abstract page/PDF URL for arXiv papers.
   - Unpaywall for DOI-to-open-access locations and PDF URLs.
   - Semantic Scholar `openAccessPdf` metadata as an additional resolver.
   - PubMed Central OA paths for biomedical open-access full text.
3. Download the PDF or source package as bytes, not HTML-extracted text.
4. Store the artifact locally with SHA-256, content type, byte length, resolver chain, fetch time, and license/terms metadata where available.
5. Extract all pages, not the first 20 pages.
6. Preserve page markers and, ideally, PDF coordinates.
7. Require exact quote-span validation before any synthesis can quote the paper.

arXiv allows personal/research use of e-print content through its APIs, but warns that e-prints retain copyright and redistribution requires permission from the copyright holder. arXiv also says legacy API users should make no more than one request every three seconds and use one connection at a time. For bulk full-text work, arXiv provides requester-pays S3 PDF/source buckets rather than encouraging heavy live downloads.

## SOTA References And Implications

ALCE frames citation generation as an end-to-end retrieve-and-cite task and evaluates citation quality, not just answer quality. Its key implication for AtlasForge is that citation support must be evaluated independently from fluency and correctness.

RAGAS, RAGChecker, RefChecker, and FActScore all point in the same direction: long-form answers need fine-grained faithfulness checks. The system should atomize claims or claim-triplets, then verify them against retrieved context. For direct quotations, the verifier should be stricter than claim support: it should require exact or normalized-exact text span matches.

GROBID is the practical SOTA-style tool to consider for scholarly PDFs. It is designed to extract, parse, and restructure scientific PDFs into structured TEI/XML, including full text, section structure, references, citation contexts, and PDF coordinates. PyPDF/pypdf can remain a fallback, but GROBID is the better default when the system needs robust paper-level provenance.

For function transparency, OpenTelemetry's trace/span model maps cleanly onto investigations: one root span per investigation, child spans per decomposition, subagent, tool call, fetch, download, validation, and synthesis. W3C PROV is also a good conceptual model for evidence lineage: entity = source artifact, activity = fetch/extract/validate/synthesize, agent = subagent/model/tool.

## Recommended Course Of Action

### Phase 0: Fix The Immediate WebProxy Contract

Fix `WebProxy/mcp_server.py` so MCP `WebFetch` returns actual text.

Recommended options:

- Add a WebProxy HTTP-level `full_text: true` or `max_chars: null` contract and keep `max_chars: 0` as empty text.
- Or have MCP `WebFetch` call `/fetch` with a large explicit cap, such as the current `MAX_MAX_CHARS`, until a true full-text flag exists.
- Update validator `_fetch_via_proxy()` so it does not ask for `max_chars: 0`.

Acceptance check: a subagent calling WebFetch on a normal article receives non-empty article text.

### Phase 1: Add A Paper Fetch Primitive

Add a deterministic paper-specific WebProxy tool and HTTP endpoint:

- Tool: `PaperFetch`
- Endpoint: `POST /paper/fetch`
- Inputs: `url`, `doi`, `arxiv_id`, `pmcid`, optional `expected_title`
- Outputs:
  - `canonical_id`
  - `canonical_url`
  - `landing_url`
  - `pdf_url`
  - `local_pdf_path`
  - `local_text_path`
  - `sha256`
  - `content_type`
  - `byte_length`
  - `page_count`
  - `pages_extracted`
  - `extractor`
  - `license`
  - `truncated`
  - `resolver_chain`
  - `errors`

Use direct byte downloads for PDFs. Do not route PDFs through the HTML extraction path.

### Phase 2: Full-Document Extraction

Replace the validator's "first 20 pages" PDF behavior for paper sources.

Recommended extraction order:

1. GROBID full-text TEI extraction for scholarly PDFs.
2. pypdf/PyPDF2 full-page extraction fallback.
3. OCR fallback for scanned PDFs, marked as lower confidence.

Use a separate paper limit such as `max_paper_source_chars` or chunk storage, not the general `max_source_chars = 50000`. Long papers should be chunked and searched, not silently truncated before quote validation.

### Phase 3: Evidence Ledger

Create a durable investigation evidence ledger:

```text
investigations/<investigation_id>/artifacts/evidence/
  evidence_ledger.jsonl
  tool_trace.jsonl
  sources/
    <source_id>.json
  papers/
    <source_id>.pdf
    <source_id>.tei.xml
    <source_id>.txt
```

Each evidence entry should include:

- investigation id
- subagent id
- model
- tool call id/session id
- resolver used
- source URL and canonical URL
- local artifact paths
- hashes
- extraction method
- page count and extracted page count
- whether any truncation occurred
- quotes claimed by the subagent
- quote span match status
- validation result

This is the piece that turns "Haiku said it found this" into "AtlasForge can prove where this evidence came from."

### Phase 4: Quote Gate

Add a strict validation rule:

> If a final report quotes a paper, the full paper must have been ingested and the quote must match an extracted span.

Rules:

- If the source is a paper and only the landing page was fetched, quotes fail closed.
- If the PDF download fails, quotes fail closed.
- If the PDF is truncated, paper quotes fail closed unless the matching span is in the retained chunk and the ledger explicitly records that limitation.
- If the quote is paraphrased, it should be validated as a claim, not rendered as a direct quote.
- If an exact quote cannot be located after normalized whitespace/hyphenation matching, it is unsupported.

### Phase 5: Synthesis Input Control

The Opus final synthesis should receive a curated evidence packet, not raw subagent findings alone.

Recommended synthesis inputs:

- validated claims
- exact quote spans
- source metadata
- contradiction notes
- unsupported claims excluded or clearly labeled
- subagent raw findings available only as background, not as citable evidence

This keeps synthesis from accidentally laundering unsupported Haiku output into the final answer.

### Phase 6: Function Transparency UI

Expose investigation function use in the Dashboard:

- per-subagent tool call timeline
- WebSearch queries
- WebFetch/WebResearch/PaperFetch URLs
- cache hits
- download byte sizes
- local artifact paths
- validation status per source
- final synthesis evidence packet

This can start from the existing stream-json parsing in `agent_stream_manager.py`, then add deterministic events from WebProxy, SourceFetcher, validator, and synthesis.

## Proposed Acceptance Criteria

1. A paper quote from arXiv passes only when the PDF/source artifact is downloaded, hashed, fully extracted, and the quote span is found.
2. A quote from page 25 of a 30-page PDF can pass. This specifically proves the old first-20-pages behavior is gone.
3. A quote from a PDF larger than the configured ingest limit fails with a clear reason instead of silently passing from a truncated source.
4. The investigation artifact folder contains the paper PDF, extracted text/TEI, source metadata, and evidence ledger.
5. The Dashboard can show which subagent called which search/fetch/download tools.
6. The final synthesis can cite only validated evidence packet entries.

## Implementation Priority

1. Fix MCP `WebFetch` returning empty text due to `max_chars: 0`.
2. Add `PaperFetch` and direct PDF byte download.
3. Remove the validator's first-20-pages paper extraction limit.
4. Add exact quote-span matching and fail-closed paper quote rules.
5. Add evidence ledger files.
6. Feed Opus synthesis only validated evidence.
7. Build the transparency UI from stored tool/evidence traces.

## Source Notes

- arXiv API access and terms:
  - https://info.arxiv.org/help/api/index.html
  - https://info.arxiv.org/help/api/tou.html
  - https://info.arxiv.org/help/api/user-manual.html
  - https://info.arxiv.org/help/bulk_data_s3.html
- Citation and faithfulness evaluation:
  - ALCE: https://arxiv.org/abs/2305.14627
  - RAGAS: https://arxiv.org/abs/2309.15217
  - RAGChecker: https://arxiv.org/abs/2408.08067
  - RefChecker: https://arxiv.org/abs/2405.14486
  - FActScore: https://arxiv.org/abs/2305.14251
- Paper acquisition and extraction:
  - GROBID: https://grobid.readthedocs.io/en/latest/Introduction/
  - Semantic Scholar API tutorial: https://www.semanticscholar.org/product/api/tutorial
  - Unpaywall schema notes: https://unpywall.readthedocs.io/en/latest/dataformat.html
- Provenance and trace transparency:
  - OpenTelemetry tracing API: https://opentelemetry.io/docs/specs/otel/trace/api/
  - W3C PROV overview: https://www.w3.org/TR/prov-overview/
