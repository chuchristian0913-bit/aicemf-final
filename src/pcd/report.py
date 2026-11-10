"""Render the note from agent-authored claims plus the verified metric registry.

Division of labour, and it is the point of the module:

* the **agent** decides what to say and which metric supports each sentence;
* **this code** formats numbers, builds tables, lists accessions, escapes LaTeX and
  refuses to render a placeholder that has no metric behind it.

A claim carries `metric_id` and `quoted_value`. The renderer re-reads the value from
`metrics.json` and raises if they disagree beyond tolerance, so a note cannot drift
from the model even if the claims file is stale.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .util import read_json, rel, repo_root, write_text, write_json

LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def esc(text: str) -> str:
    return "".join(LATEX_ESCAPES.get(ch, ch) for ch in text)


# Footnote markers are written `[[key]]` in the claims file and rendered as a dagger
# superscript that points at a limitation in the footnotes block.
MARKER = re.compile(r"\[\[([a-zA-Z0-9_\-]+)\]\]")


def _segments(text: str, known: set[str]) -> list[tuple[str, str]]:
    """Split raw prose into ('text'|'marker', payload) runs, checking marker keys.

    A marker that points at no limitation is a dangling reference in a published
    note, so it raises here rather than rendering a dagger with nothing after it.
    """
    runs: list[tuple[str, str]] = []
    pos = 0
    for m in MARKER.finditer(text):
        key = m.group(1)
        if key not in known:
            raise KeyError(f"footnote marker [[{key}]] has no limitation text")
        if m.start() > pos:
            runs.append(("text", text[pos:m.start()]))
        runs.append(("marker", key))
        pos = m.end()
    if pos < len(text):
        runs.append(("text", text[pos:]))
    return runs


def esc_marked(text: str, known: set[str]) -> str:
    """Escape LaTeX but keep footnote markers alive.

    The order matters and this is the bug it fixes: escaping first turns `[[key]]`
    into `{[}{[}key{]}{]}` and no later `replace` can find the marker. An earlier
    version escaped first and shipped a note with a literal `[[period_alignment]]`
    mid-sentence. Substitution happens on raw text, each prose fragment is escaped
    on its own, and the pieces are joined afterwards.
    """
    return "".join(
        r"\textsuperscript{\dag}" if kind == "marker" else esc(payload)
        for kind, payload in _segments(text, known)
    )


def html_marked(text: str, known: set[str]) -> str:
    """Same contract as `esc_marked`, for HTML."""
    import html as h

    return "".join(
        "<sup>&dagger;</sup>" if kind == "marker" else h.escape(payload)
        for kind, payload in _segments(text, known)
    )


# ---------------------------------------------------------------------- formatting


def fmt(value: float | None, kind: str) -> str:
    if value is None:
        return "n/a"
    if kind == "usd_b":
        return f"{value / 1e9:,.1f}"
    if kind == "usd_bn2":
        return f"{value / 1e9:,.2f}"
    if kind == "usd_ps":
        return f"{value:,.2f}"
    if kind == "pct1":
        return f"{value * 100:,.1f}%"
    if kind == "pct2":
        return f"{value * 100:,.2f}%"
    if kind == "num2":
        return f"{value:,.2f}"
    if kind == "usd_m":
        return f"{value / 1e6:,.0f}"
    raise ValueError(f"unknown format {kind}")


DISPLAY = {
    "usd_b": lambda v: f"{v / 1e9:,.1f}",
    "usd_bn2": lambda v: f"{v / 1e9:,.2f}",
    "usd_ps": lambda v: f"{v:,.2f}",
    "pct1": lambda v: f"{v * 100:,.1f}%",
    "pct2": lambda v: f"{v * 100:,.2f}%",
    "num2": lambda v: f"{v:,.2f}",
    "usd_m": lambda v: f"{v / 1e6:,.0f}",
}


# ------------------------------------------------------------------------ resolve


def resolve_claims(claims: list[dict], registry: dict, tol_rel: float = 5e-4, tol_abs: float = 0.5) -> tuple[dict, list[dict]]:
    """Attach the registry value to each claim and check the quoted number."""
    out: dict[str, dict] = {}
    problems: list[dict] = []
    for c in claims:
        cid = c["claim_id"]
        mid = c.get("metric_id")
        rec = registry.get(mid) if mid else None
        if rec is None:
            problems.append({"claim_id": cid, "kind": "unknown_metric", "metric_id": mid})
            out[cid] = {"claim": c, "value": None, "display": "n/a", "record": None}
            continue
        value = rec["value"]
        quoted = c.get("quoted_value")
        if value is None:
            out[cid] = {"claim": c, "value": None, "display": "n/a", "record": rec}
            continue
        if quoted is not None and abs(quoted - value) > max(tol_abs, abs(value) * tol_rel):
            problems.append({"claim_id": cid, "kind": "quoted_value_drift", "metric_id": mid,
                             "quoted": quoted, "registry": value})
        out[cid] = {"claim": c, "value": value, "display": DISPLAY[c["fmt"]](value), "record": rec}
    return out, problems


PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_\-]+)\}")


def fill(text: str, resolved: dict) -> str:
    def sub(m: re.Match) -> str:
        cid = m.group(1)
        if cid not in resolved:
            raise KeyError(f"placeholder {{{cid}}} has no claim: a number in the note must be traceable")
        return resolved[cid]["display"]

    return PLACEHOLDER.sub(sub, text)


# ------------------------------------------------------------------------- tables


def cited_sources(resolved: dict, sources: dict[str, dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for item in resolved.values():
        for sid in (item["record"] or {}).get("sources", []):
            s = sources.get(sid)
            if s and s["accn"] not in seen:
                seen[s["accn"]] = s
    return sorted(seen.values(), key=lambda s: (s["ticker"], s["filed"]))


# -------------------------------------------------------------------------- latex


PREAMBLE = r"""\documentclass[10pt]{article}
\usepackage[a4paper,margin=19mm,top=16mm,bottom=16mm]{geometry}
\usepackage{booktabs}
\usepackage{array}
\usepackage{tabularx}
\usepackage{xcolor}
\usepackage{titlesec}
\usepackage{fancyhdr}
\usepackage[hidelinks]{hyperref}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\definecolor{desk}{HTML}{1F3A5F}
\definecolor{rule}{HTML}{9FB3C8}
\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0.4pt}
\fancyhead[L]{\footnotesize\color{desk}PCD --- Payments Coverage Desk}
\fancyhead[R]{\footnotesize\color{desk}\thepage}
\fancyfoot[C]{\scriptsize\color{black!55}Generated by the PCD controller; every figure is bound to a metric\_id and an SEC accession.}
\titleformat{\section}{\normalfont\bfseries\color{desk}\large}{}{0pt}{}
\titlespacing*{\section}{0pt}{7pt}{3pt}
\setlength{\parindent}{0pt}
\setlength{\parskip}{3.2pt}
\renewcommand{\arraystretch}{1.12}
\newcommand{\smallnote}[1]{\footnotesize\color{black!65}#1}
"""


def render_tex(note: dict, resolved: dict, model: dict, sources: dict[str, dict], meta: dict) -> str:
    L: list[str] = [PREAMBLE]
    L.append(r"\begin{document}")
    L.append(r"\begin{center}")
    L.append(r"{\color{desk}\rule{\textwidth}{0.9pt}}\\[3pt]")
    L.append(r"{\LARGE\bfseries\color{desk} " + esc(note["title"]) + r"}\\[2pt]")
    L.append(r"{\small " + esc(meta["desk"]) + r" \quad|\quad " + esc(meta["universe"]) + r"}\\[1pt]")
    L.append(
        r"{\footnotesize " + esc(meta["date_line"]) + r" \quad|\quad Analyst of record: " + esc(meta["analyst"]) + r"}"
    )
    L.append(r"\\[1pt]{\color{desk}\rule{\textwidth}{0.4pt}}")
    L.append(r"\end{center}")
    L.append(r"\vspace{-4pt}")
    L.append(r"{\small\itshape " + esc(fill(note["standfirst"], resolved)) + r"}")
    L.append(r"\vspace{2pt}")

    footnote_text: dict[str, str] = {}
    for lim in note.get("limitations", []):
        footnote_text[lim["key"]] = lim["text"]

    for block in note["blocks"]:
        kind = block["kind"]
        if kind == "heading":
            L.append(r"\section*{" + esc(block["text"]) + "}")
        elif kind == "para":
            L.append(esc_marked(fill(block["text"], resolved), set(footnote_text)) + r"\par")
        elif kind == "footnotes":
            items = []
            for key in block["keys"]:
                text = footnote_text.get(key)
                if text is None:
                    raise KeyError(f"footnote key {key} has no limitation text")
                items.append(r"\dag\ " + esc(text))
            L.append(r"\vspace{1pt}{\footnotesize\color{black!70}" + r"\\".join(items) + r"}\par")
        elif kind == "table":
            cols = block["columns"]
            spec = block.get("spec") or ("l" + "r" * (len(cols) - 1))
            L.append(r"\begin{center}\footnotesize")
            L.append(r"\begin{tabular}{" + spec + "}")
            L.append(r"\toprule")
            L.append(r"\textbf{" + r"} & \textbf{".join(esc(c) for c in cols) + r"} \\")
            L.append(r"\midrule")
            for row in block["rows"]:
                if row.get("rule"):
                    L.append(r"\midrule")
                cells = [esc(fill(c, resolved)) for c in row["cells"]]
                L.append(esc(row["label"]) + " & " + " & ".join(cells) + r" \\")
            L.append(r"\bottomrule")
            L.append(r"\end{tabular}")
            if block.get("caption"):
                L.append(r"\\[1pt]{\scriptsize\color{black!65}" + esc(block["caption"]) + r"}")
            L.append(r"\end{center}")
        elif kind == "sources":
            used = cited_sources(resolved, sources)
            L.append(r"\section*{Sources and tie-out}")
            L.append(
                r"{\scriptsize Every figure above is generated from the frozen snapshot of "
                + esc(meta["date_line"])
                + r" and is bound to a metric identifier that resolves to one of the filings below. "
                + r"The controller recomputed each ratio from unrounded inputs (gate G09) and "
                + r"re-derived the valuation from the assumption file (gate G10).}\par"
            )
            L.append(r"{\scriptsize\begin{tabular}{@{}lll@{}}\toprule Ticker & Form & Accession (filed) \\\midrule")
            for s in used:
                L.append(f"{esc(s['ticker'])} & {esc(s['form'])} & {esc(s['accn'])} ({esc(s['filed'])}) \\\\")
            L.append(r"\bottomrule\end{tabular}}\par")
        elif kind == "footer_note":
            L.append(r"\vspace{2pt}{\scriptsize\color{black!65}" + esc(block["text"]) + r"}\par")

    L.append(r"\end{document}")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- html


HTML_HEAD = """<!doctype html>
<meta charset="utf-8">
<title>{title}</title>
<style>
:root{{--ink:#12212f;--muted:#5b6b7b;--acc:#1f3a5f;--rule:#c9d6e2;--bg:#fbfcfd}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 "Charter","Georgia",serif;}}
.wrap{{max-width:760px;margin:38px auto;padding:0 26px 70px}}
.kicker{{font:600 11px/1.4 ui-sans-serif,system-ui;letter-spacing:.14em;text-transform:uppercase;color:var(--acc)}}
h1{{font-size:29px;line-height:1.15;margin:6px 0 4px;letter-spacing:-.01em}}
.meta{{font:12px/1.5 ui-sans-serif,system-ui;color:var(--muted);border-bottom:2px solid var(--acc);padding-bottom:8px}}
.stand{{font-style:italic;margin:14px 0 18px;color:#2b3d4f}}
h2{{font:600 12px/1.4 ui-sans-serif,system-ui;letter-spacing:.1em;text-transform:uppercase;color:var(--acc);margin:24px 0 6px}}
p{{margin:0 0 9px}}
table{{border-collapse:collapse;width:100%;font:13px/1.45 ui-sans-serif,system-ui;margin:6px 0 4px}}
th{{text-align:right;font-weight:600;border-bottom:1.5px solid var(--acc);padding:5px 7px;color:var(--acc)}}
th:first-child{{text-align:left}}
td{{text-align:right;padding:4px 7px;border-bottom:1px solid #e6edf3;font-variant-numeric:tabular-nums}}
td:first-child{{text-align:left;font-weight:600}}
tr.total td{{border-top:1px solid var(--acc);font-weight:600}}
.cap{{font:11.5px/1.45 ui-sans-serif,system-ui;color:var(--muted);margin:2px 0 14px}}
.fn{{font-size:12px;color:var(--muted);border-left:2px solid var(--rule);padding-left:10px;margin:12px 0}}
.src{{font:11.5px/1.5 ui-sans-serif,system-ui;color:var(--muted)}}
.src code{{background:#eef3f7;padding:1px 4px;border-radius:3px}}
footer{{margin-top:26px;border-top:1px solid var(--rule);padding-top:8px;font:11px/1.5 ui-sans-serif,system-ui;color:var(--muted)}}
</style>
<div class="wrap">
"""


def render_html(note: dict, resolved: dict, model: dict, sources: dict[str, dict], meta: dict) -> str:
    import html as h

    out = [HTML_HEAD.format(title=h.escape(note["title"]))]
    out.append(f'<div class="kicker">{h.escape(meta["desk"])}</div>')
    out.append(f'<h1>{h.escape(note["title"])}</h1>')
    out.append(f'<div class="meta">{h.escape(meta["date_line"])} &nbsp;·&nbsp; {h.escape(meta["universe"])} &nbsp;·&nbsp; Analyst of record: {h.escape(meta["analyst"])}</div>')
    out.append(f'<p class="stand">{h.escape(fill(note["standfirst"], resolved))}</p>')

    fn = {l["key"]: l["text"] for l in note.get("limitations", [])}
    for block in note["blocks"]:
        k = block["kind"]
        if k == "heading":
            out.append(f"<h2>{h.escape(block['text'])}</h2>")
        elif k == "para":
            out.append(f"<p>{html_marked(fill(block['text'], resolved), set(fn))}</p>")
        elif k == "footnotes":
            for key in block["keys"]:
                out.append(f'<div class="fn">&dagger; {h.escape(fn[key])}</div>')
        elif k == "table":
            out.append("<table><thead><tr>" + "".join(f"<th>{h.escape(c)}</th>" for c in block["columns"]) + "</tr></thead><tbody>")
            for row in block["rows"]:
                cls = ' class="total"' if row.get("rule") else ""
                cells = "".join(f"<td>{h.escape(fill(c, resolved))}</td>" for c in row["cells"])
                out.append(f"<tr{cls}><td>{h.escape(row['label'])}</td>{cells}</tr>")
            out.append("</tbody></table>")
            if block.get("caption"):
                out.append(f'<div class="cap">{h.escape(block["caption"])}</div>')
        elif k == "sources":
            used = cited_sources(resolved, sources)
            out.append("<h2>Sources and tie-out</h2>")
            out.append(
                '<div class="src">Every figure is bound to a metric identifier that resolves to a filing below. '
                "Ratios were recomputed from unrounded inputs (G09) and the valuation re-derived from the "
                "assumption file (G10).<br><br>"
                + "<br>".join(f'{h.escape(s["ticker"])} &nbsp; {h.escape(s["form"])} &nbsp; <code>{h.escape(s["accn"])}</code> filed {h.escape(s["filed"])}' for s in used)
                + "</div>"
            )
        elif k == "footer_note":
            out.append(f'<footer>{h.escape(block["text"])}</footer>')
    out.append("</div>")
    return "\n".join(out)


# ------------------------------------------------------------------------- compose


def _meta(case_id: str) -> dict:
    import tomllib

    root = repo_root()
    with open(root / "config" / "run.toml", "rb") as fh:
        run = tomllib.load(fh)
    man = read_json(root / "data" / "frozen" / case_id / "MANIFEST.json", {})
    return {
        "desk": run["case"]["desk"],
        "analyst": run["case"]["analyst"],
        "universe": ", ".join(run["case"]["universe"]),
        "date_line": f"Information cutoff {run['case']['cutoff']}",
    }


def render(case_id: str, claims_path: Path, out_dir: Path, *, compile_pdf: bool = True) -> dict:
    root = repo_root()
    note = read_json(claims_path)
    if note is None:
        raise FileNotFoundError(rel(claims_path))
    model = read_json(root / "cases" / case_id / "work" / "metrics.json")
    sources = {r["source_id"]: r for r in _read_jsonl(root / "cases" / case_id / "work" / "sources.jsonl")}

    resolved, problems = resolve_claims(note["claims"], model["registry"])
    if problems:
        raise ValueError(f"claims do not match the model: {problems[:4]}")

    meta = _meta(case_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    tex = render_tex(note, resolved, model, sources, meta)
    (out_dir / "note.tex").write_text(tex, encoding="utf-8", newline="\n")
    (out_dir / "note.html").write_text(render_html(note, resolved, model, sources, meta), encoding="utf-8", newline="\n")

    # auditable companion: the resolved claim table
    write_json(
        out_dir / "claim_resolution.json",
        {
            "case_id": case_id,
            "claims": [
                {
                    "claim_id": cid,
                    "metric_id": it["record"]["metric_id"] if it["record"] else None,
                    "value": it["value"],
                    "display": it["display"],
                    "comparability": (it["record"] or {}).get("comparability"),
                    "sources": (it["record"] or {}).get("sources", []),
                }
                for cid, it in resolved.items()
            ],
        },
    )

    pdf_ok = False
    if compile_pdf and shutil.which("latexmk"):
        proc = subprocess.run(
            ["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error", "note.tex"],
            cwd=out_dir,
            capture_output=True,
            text=True,
            timeout=240,
        )
        pdf_ok = (out_dir / "note.pdf").exists() and proc.returncode == 0
        if not pdf_ok:
            (out_dir / "latex_error.log").write_text(proc.stdout[-8000:], encoding="utf-8")

    return {"tex": str(out_dir / "note.tex"), "html": str(out_dir / "note.html"),
            "pdf": str(out_dir / "note.pdf") if pdf_ok else None,
            "claims": len(resolved), "pdf_ok": pdf_ok}


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


if __name__ == "__main__":
    import sys

    case = "case-01"
    rd = repo_root() / "cases" / case / "rounds" / "round-001"
    res = render(case, rd / "note" / "claims.json", rd / "note", compile_pdf=True)
    print(json.dumps(res, indent=2))
