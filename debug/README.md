# Debug `.tex` fixtures

Standalone documents for troubleshooting and debugging resume-TeX-fit. Each has the `\rs` knob wired (except `no-knob.tex`, on purpose) and a header comment describing what it covers. Copy the script next to one, or pass the path, and run it.

The main shrink-to-one-page case is `demo.tex` at the repo root; these focus on format variety and edge inputs.

| File | What it covers | Expected result |
|---|---|---|
| `two-column.tex` | Two-column body via `multicol` | `--pages 1` fits one page |
| `academic-cv.tex` | CV with academic `\section` headings, two pages | Recognized as a CV; `--pages 0` fits its natural two pages |
| `fontspec.tex` | Explicit main font via `fontspec` | `--pages 1` fits one page |
| `spaced-knob.tex` | Knob written with spaces: `\newcommand {\rs} {1.000}` | Knob still detected and rewritten |
| `no-knob.tex` | A resume with no `\rs` knob | Refused: nothing to fit |
| `broken.tex` | Knob present, deliberate LaTeX error | Compile fails; the first error line is reported |

Example:

```bash
python3 resume-tex-fit.py debug/two-column.tex --pages 1
```

Running a fixture writes a `.tex.bak` and LaTeX build files next to it; both are covered by `.gitignore`.
