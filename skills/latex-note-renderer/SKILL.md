---
name: latex-note-renderer
description: Render a claims-driven research note to PDF/HTML where every number is bound to a metric_id, on Windows with TeX Live.
when_to_use: Publishing a generated note whose figures must be traceable and the build must not silently drift from the data.
---

# Render a claims-driven note (LaTeX + HTML)

1. **Claims are the only source of numbers.** Each claim: `{claim_id, metric_id,
   quoted_value, fmt}`. The renderer re-reads the registry and raises on drift beyond
   tolerance — a stale claims file must fail loudly, not render old numbers.
2. **Placeholders** `{claim_id}` resolve to pre-formatted display strings. A placeholder
   with no claim raises: a number in the note must be traceable.
3. **Footnote markers** `[[key]]` become dagger superscripts. ORDER MATTERS: substitute
   markers on RAW text, then escape each fragment. Escaping first turns `[[key]]` into
   `{[}{[}key{]}{]}` and the published PDF carries a literal marker (this exact bug
   shipped once; see `pcd/report.py::esc_marked`). A marker without a matching
   limitation raises.
4. **LaTeX escaping** covers `\ & % $ # _ { } ~ ^`. Percent signs in prose numbers
   (`57.6%`) must be escaped or pdflatex dies.
5. **Table specs**: `spec` length MUST equal the column count. Gate G01 now validates
   this because a 6-column table under a 5-column spec passed every gate and failed at
   publish time with `! Extra alignment tab has been changed to \cr`.
6. **Compile** with `latexmk -pdf -interaction=nonstopmode -halt-on-error note.tex`
   (TeX Live at `/c/texlive/2026/bin/windows/` on this machine). On failure write
   `latex_error.log` and report `pdf_ok: false` — never publish a note without its PDF
   or with a silent failure.
7. **Verify visually**: render page 1 to PNG (`fitz`) and read it. Text-extraction
   success does not mean the layout is right.
