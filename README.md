# resume-tex-fit

[![Link Check](https://github.com/hihipy/resume-tex-fit/actions/workflows/links.yml/badge.svg)](https://github.com/hihipy/resume-tex-fit/actions/workflows/links.yml)
[![License: CC BY-NC-SA 4.0](https://img.shields.io/badge/License-CC%20BY--NC--SA%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc-sa/4.0/)

**Built with**

[![Python](https://img.shields.io/badge/Python-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![LaTeX](https://img.shields.io/badge/LaTeX-008080?style=flat&logo=latex&logoColor=white)](https://www.latex-project.org)
[![pdfplumber](https://img.shields.io/badge/pdfplumber-4B5563?style=flat&logoColor=white)](https://github.com/jsvine/pdfplumber)
[![tkinter](https://img.shields.io/badge/tkinter-4B5563?style=flat&logoColor=white)](https://docs.python.org/3/library/tkinter.html)

Fit a LaTeX resume, CV, or any document to an exact page count. It drives the whole document off one scaling value, then finds the tightest setting that still fits, so your last page fills up instead of spilling a few lines onto the next one.

---

## The Problem

A resume that almost fits is its own kind of annoying. You write two solid pages, and three stray lines push onto a third. Or you trim too hard and the second page sits half empty. Either way you end up hand-tuning font sizes and margins, recompiling, and eyeballing the result over and over.

The usual fixes all have a catch:

- **Manual resizing:** Nudging one font size shifts everything downstream, so you chase the overflow around the document.
- **Shrinking the whole thing:** Scaling type down to force a page count often lands on a size that is too small to read or that an applicant tracking system mishandles.
- **Padding to fill:** Inflating type or adding filler to fill a short page reads as exactly what it is.
- **No repeatability:** However you got it to fit this time, you have to redo the fiddling next time you edit a bullet.

---

## The Solution

`resume-tex-fit` turns page fitting into one search. Your document exposes a single scaling command, `\newcommand{\rs}{1.000}`, and every font size, line spacing, and gap is computed from it. The tool edits that number, compiles with `xelatex`, reads the page count from the log, and searches for the largest scale that still fits your target. Fitting to the largest scale means the last page fills as much as it can without spilling.

It checks feasibility against the document's natural size, not against the scale ceiling, so a longer target does not pad a short resume by inflating type unless you turn on Force Fit. Three outcomes, [covered in detail below](#the-three-outcomes): it fits, it is too long, or it is too short.

### Why a Single Script?

- **No setup:** The core fit uses only the Python standard library. Run it with one command. Nothing to install past a working LaTeX distribution.
- **CLI or GUI:** Drive it from the terminal for scripting, or open a small window with a file picker if you would rather click.
- **One job:** It fits a document to a page count and reports honestly when it cannot. It does not try to be a full build system.

---

## New to LaTeX?

If you already know LaTeX, skip to the next section. If you have only used Word or Google Docs, here is the whole idea.

**[LaTeX](https://www.latex-project.org/)** (say "lay-tek") is a typesetting system built on [TeX](https://tug.org/), the engine Donald Knuth wrote in the late 1970s to set mathematics well. Instead of formatting by clicking buttons, you write plain text mixed with commands that describe structure, then a program turns that into a finished PDF. You write `\section{Experience}` and `\textbf{Senior Analyst}`, and LaTeX decides the exact spacing, alignment, and line breaks.

Why bother with it for a resume? A resume is exactly the kind of short, precise document where the everyday tools fight you.

In Word or Google Docs you format by hand, so the layout drifts as you edit. Fix one bullet and the spacing shifts, a line slips onto the next page, and your clean two pages become two and a half. You claw it back by nudging margins and font sizes, and the next edit undoes it.

LaTeX flips that around. You mark what each piece of text is (a heading, a job title, a bullet) and let the system handle the spacing, alignment, and line breaks. It keeps that consistent across the whole document, so an edit in one place does not quietly break the layout somewhere else. The output looks typeset, the way a book or a journal is set, rather than like a word-processor default.

Two things that matter specifically for a resume:

- **It is plain text.** Your resume is one small file you can back up, compare against an old version line by line, and rebuild into the exact same PDF a year from now. No folder of near-identical `resume_final_v3.docx` copies.
- **It reads cleanly to applicant tracking systems** when built right, because the words are real selectable text, not baked into a picture the way many design-tool exports are.

The honest trade-off against the usual options:

| Tool | Good at | The catch for a resume |
| --- | --- | --- |
| Word, Google Docs | Fast to start, everyone has it, you see the page as you type | Layout drifts as you edit, and holding an exact page count is manual and fragile |
| Canva, design tools | Polished visuals with little effort | Exports often bury the text in an image that applicant tracking systems cannot read |
| LaTeX | Consistent typesetting, precise control, plain-text source you can reuse | A steeper start, and you edit markup instead of a live page |

The steeper start is the real cost, and it is why this repo hands the writing of the LaTeX to an AI (further down) so you mostly skip it.

The pieces you actually touch:

- **The `.tex` file** is your document, plain text you can open in any editor.
- **A compiler** reads the `.tex` and produces a **`.pdf`**, plus side files (`.aux`, `.log`) you can ignore or gitignore.
- **[`xelatex`](https://tug.org/xetex/)** is the compiler flavor this tool uses. It handles modern system fonts and Unicode cleanly. It ships with [TeX Live](https://tug.org/texlive/) (Windows, Linux) and [MacTeX](https://tug.org/mactex/) (Mac). Check it with `xelatex --version`.

**The scaling knob, in plain terms.** In a normal LaTeX resume each size is a fixed number: body text is 10 point, a heading is 12, a gap is 6. In a document built for this tool, each of those is written as "a base number times `\rs`." So `\rs` at 1.0 gives the normal sizes, 0.95 shrinks everything 5 percent, 1.03 grows it 3. That one number scales the whole document, and it is the only thing the tool turns.

---

## The One Requirement: the `\rs` Knob

This is the hard dependency. The tool does not parse your layout or resize elements one by one. It turns `\rs` and recompiles. If your `.tex` does not route its sizing through `\rs`, there is nothing to turn and the tool refuses to run.

Wiring it up is a few lines in the preamble. You need the [`xfp`](https://ctan.org/pkg/xfp) package for the arithmetic, the knob itself, and two small helpers so the rest of the document never writes a raw size:

```latex
\usepackage{xfp}                         % \fpeval for inline arithmetic

% The one knob resume-tex-fit turns. Keep it at 1.000 in source; the tool sets it.
\newcommand{\rs}{1.000}

% \fs{size}{leading} selects a font size and line spacing, both scaled by \rs.
\newcommand{\fs}[2]{\fontsize{\fpeval{#1*\rs}}{\fpeval{#2*\rs}}\selectfont}

% \sv{pt} inserts vertical space scaled by \rs.
\newcommand{\sv}[1]{\vspace{\fpeval{#1*\rs}pt}}

% Scaled length registers for package options that need a real length.
\newlength{\Lsecabove}\setlength{\Lsecabove}{\fpeval{7.5*\rs}pt}
\newlength{\Litemsep}\setlength{\Litemsep}{\fpeval{1.6*\rs}pt}
```

From there, body text uses `\fs{10}{11.7}`, a section gap uses `\sv{6}`, list spacing pulls from `\Litemsep`, and every size traces back to `\rs`. The repo ships [`demo.tex`](demo.tex), a minimal knob-wired resume that compiles with the default fonts (no `fonts/` folder), so you can try the tool right after cloning:

```bash
python3 resume-tex-fit.py demo.tex --pages 1
```

That compresses it onto one clean page (it runs onto a second page at normal size). Try `--pages 2` to keep its natural two pages instead. The full before and after is in the [next section](#what-a-run-looks-like).

If you would rather not hand-write any of this, the [next section](#bring-your-own-resume-convert-it-with-ai) has an AI do it for you.

---

## What a Run Looks Like

The shipped `demo.tex` is deliberately overloaded: at normal size it runs onto a second page. Target one page:

```bash
python3 resume-tex-fit.py demo.tex --pages 1
```

![Before and after: a two-page resume with a barely-used second page, compressed onto one clean page](assets/before-after.png)

It binary-searches the density and locks the largest scale that still holds one page:

```
Fitting demo.tex to 1 page(s) (scale range 0.90-1.05):
  scale 1.0000 -> 2 page(s)
  scale 0.9000 -> 1 page(s)
  scale 0.9500 -> 2 page(s)
  scale 0.9250 -> 2 page(s)
  scale 0.9125 -> 2 page(s)
  scale 0.9062 -> 1 page(s)
  scale 0.9094 -> 1 page(s)
  scale 0.9064 -> 1 page(s)

Locked scale 0.9064 -> 1 page(s). Backup saved as demo.tex.bak.
```

The only edit it makes to your `.tex` is the one knob:

```diff
-\newcommand{\rs}{1.000}
+\newcommand{\rs}{0.9064}
```

Every size, line height, and gap recomputes from that value, so the whole document tightens by the same proportion instead of one part getting cramped. The original is saved as `demo.tex.bak` next to it, and [`debug/`](debug/) holds more documents to try, including a two-column layout, an academic CV, and a few edge cases.

---

## Bring Your Own Resume: Convert It With AI

If you have a resume in Word, Docs, or a PDF, or an open-source `.tex` template you want to reuse, you do not need to learn LaTeX or wire the knob by hand. A general model ([Claude](https://claude.ai), [ChatGPT](https://chatgpt.com), [Gemini](https://gemini.google.com)) converts it reliably, but only if you hand it the knob machinery and tell it to route everything through it. A plain "convert my resume to LaTeX" request produces hardcoded sizes the tool cannot touch. The prompt below forces every size through `\rs`, whether you paste text, attach your current resume file, or start from an existing template.

### Why Convert at All

**What you get:**

- **Exact page control:** You hit two pages, not two pages plus three orphaned lines, and the last page looks full.
- **Retarget without rewriting:** Change the `--pages` number and rerun. The knob retunes density; you do not touch a word.
- **Typeset quality:** Consistent spacing and alignment that word processors do not match.
- **Plain-text source:** Version control, clean diffs, and a PDF you can regenerate identically.
- **ATS-friendly when built right:** Selectable text and standard section names parse cleanly through [applicant tracking systems](https://en.wikipedia.org/wiki/Applicant_tracking_system).

**What it costs:**

- **A real dependency:** You need `xelatex` installed to compile.
- **Proofreading is on you:** AI conversion drops bullets, mangles special characters, and occasionally invents a detail. Read every line against your original.
- **Not every template converts cleanly:** Single-file `article`-class templates rewire through one knob without a fight. Class-based templates (the ones that load a `.cls`) keep their sizing inside the class; the prompt inlines that into one file, but the conversion is less reliable.

### Step 1: Pick a Look

Browse a few real, open-source `.tex` templates, decide on a style, then have the AI reproduce that look in the knob-wired structure. Everything below is a repository you can clone and read, not a hosted editor.

For this tool, favor the **article-class, single-file** templates (Jake's, sb2nov, latexcv, rover-resume). Their sizing lives in the preamble where the knob can drive it directly. The **class-based** ones (AltaCV, Awesome-CV, moderncv, Deedy) keep their sizing inside a `.cls`, so the prompt has to inline that into one file first. It can, but the conversion is less reliable, so treat them as a visual reference and let the AI reproduce the look in the simpler single-file structure.

| Template | What it is | License | Source |
| --- | --- | --- | --- |
| Jake's Resume | Single-file, single-column, dense, ATS-friendly. The most convertible of the popular ones. | MIT | [github.com/jakegut/resume](https://github.com/jakegut/resume) |
| sb2nov/resume | Clean single-column, a de facto standard in tech, easy to start from. | MIT | [github.com/sb2nov/resume](https://github.com/sb2nov/resume) |
| latexcv | A collection of several self-contained styles; needs only a minimal TeX Live. | MIT | [github.com/jankapunkt/latexcv](https://github.com/jankapunkt/latexcv) |
| rover-resume | Minimal `article`-class, roughly ten lines to start, no custom class to learn. | CC BY 4.0 | [github.com/subidit/rover-resume](https://github.com/subidit/rover-resume) |
| AltaCV | Two-column designed CV, class-based. Strong visual reference. | LPPL 1.3+ | [github.com/liantze/AltaCV](https://github.com/liantze/AltaCV) |
| Awesome-CV | Polished, icon fonts, matching cover letter. Class-based. | Class LPPL 1.3c; template CC BY-SA 4.0 | [github.com/posquit0/Awesome-CV](https://github.com/posquit0/Awesome-CV) |
| moderncv | Five built-in styles, class-based, distributed on CTAN. | LPPL 1.3c | [ctan.org/pkg/moderncv](https://ctan.org/pkg/moderncv) |
| Deedy-Resume | Dense two-column, one page, Lato and Raleway fonts. Opinionated. | Apache 2.0 | [github.com/deedy/Deedy-Resume](https://github.com/deedy/Deedy-Resume) |

For a wider hunt:

- **Curated list:** [github.com/smortezah/awesome-cv](https://github.com/smortezah/awesome-cv) collects templates and generators across LaTeX, Typst, and others.
- **GitHub topics:** browse hundreds and filter by license at [github.com/topics/latex-resume-template](https://github.com/topics/latex-resume-template) and [github.com/topics/latex-cv-template](https://github.com/topics/latex-cv-template).

Licenses vary and can change, and forks often differ from the original. Confirm the license in the repo before you reuse or redistribute a template.

### Step 2: Convert With This Prompt

Paste this into your AI of choice. It handles three inputs: **A**, paste the plain text of your resume and it builds a clean layout; **B**, attach your current resume as a PDF or DOCX and it reproduces the look as closely as it can; **C**, hand it an existing template `.tex` plus your content and it rewires the template through the knob while keeping its look. Fill the bracketed fields and mark your input A, B, or C at the bottom.

```text
You are producing ONE self-contained LaTeX file that compiles with xelatex. It will be fed to a tool called resume-tex-fit, which fits the document to a target page count by turning a single scaling knob, so every font size, line spacing, and vertical space MUST be computed from that knob. Follow every rule exactly. Do not deviate, and do not explain your work.

INPUT MODE. I am giving you exactly one of the following, and I mark which at the very bottom:

  (A) The plain text of my resume, pasted below.
      Build a clean, single-column layout from scratch, using the font and accent color from rule 7.

  (B) My existing resume as an attached file (PDF or DOCX).
      Read it, extract its content exactly, then reproduce its appearance (fonts, colors, section styling, sizes, spacing) in LaTeX as closely as you can. Match what it looks like; do not redesign it. Flatten it to a single column even if the original has columns (rule 6). Change no wording, add nothing, drop nothing.

  (C) An existing .tex file (a template or my own draft), followed by my resume content.
      Keep the file's visual design and only swap its sample content for mine. Do not redesign it. If it relies on a custom class or style file (a .cls or .sty that is not a standard package), inline the parts you need into this one file, because the tool edits only this single .tex and cannot reach sizing locked inside a class.

Rules 1 to 8 apply to ALL modes.

1. Knob machinery. Include this verbatim in the preamble and route ALL sizing through it. Leave no font size, leading, or vertical space anywhere that bypasses \rs, including sizes carried over from a file or template.

   \usepackage{xfp}
   \newcommand{\rs}{1.000}
   \newcommand{\fs}[2]{\fontsize{\fpeval{#1*\rs}}{\fpeval{#2*\rs}}\selectfont}
   \newcommand{\sv}[1]{\vspace{\fpeval{#1*\rs}pt}}

   Use \fs{size}{leading} for every size, \sv{pt} for every manual vertical space, and \fpeval{VALUE*\rs} inside any package option or length that takes a measurement (\titlespacing, list itemsep and topsep, \setlength, \\[...] line-break spacing, and so on). When you carry a size over from a file or template, keep the same number so the look is identical at \rs = 1.000. Leave \rs at exactly 1.000; the tool sets it, not you. Never hand-tune sizes to hit a page count.

   BANNED, because each sets a fixed size the knob cannot move: a bare \fontsize outside the \fs macro; the named size commands \tiny \scriptsize \footnotesize \small \normalsize \large \Large \LARGE \huge \Huge; a raw \vspace, \vskip, \hspace, or \\[Npt] with a literal measurement; and any literal pt, em, or cm value in a package option or length. Replace every one with \fs, \sv, or \fpeval{VALUE*\rs}.

2. Content integrity. Use only the content I provide or that appears in the file I attach. Do not invent employers, titles, dates, metrics, or achievements, and drop any sample or placeholder content. If something is unreadable, ambiguous, or missing, leave a % TODO comment instead of guessing.

3. Compiles with xelatex. The file must build with a plain: xelatex file.tex . If a template or reproduction would need a pdflatex-only setup (for example \usepackage[T1]{fontenc} with a Type 1 font package), replace it with a fontspec equivalent or the default Latin Modern.

4. Self-contained. One file only. Do not \input or \include external files, and do not depend on a separate .cls or .sty beyond standard packages. Inline anything you need.

5. Escaping. Escape LaTeX specials in any text taken from plain input or a file: & % $ # _ { } ~ ^ and backslash. Keep real dollar amounts as \$ (for example, \$540M). Do not double-escape content that is already valid LaTeX in a template.

6. ATS-safe. Single column, real selectable text, standard section headings via \section, no text rendered as an image, no content laid out in tables. Standard section names (Summary, Experience, Education, Skills, Projects) help both parsers and the tool's checks. This holds even when reproducing a multi-column original: keep the styling, drop the columns.

7. Look. Mode A: set the main font with fontspec to [FONT NAME, or "the default" to use Latin Modern with no extra files], and use one accent color [ACCENT HEX, for example 1A365D] for the name, headings, and rules. Mode B: match the file's fonts and colors; if you cannot identify a font, use the closest common one and note the substitution in a % comment. Mode C: keep the template's fonts and colors unless I override here: [OPTIONAL overrides, or leave blank].

8. Target. Aim the layout at roughly [TARGET PAGES] page(s) at normal size, but do not force it; resume-tex-fit will tighten or relax the fit.

If a rule is impossible for a given input, do not break it silently: put one % NOTE line at the very top of the file saying what you could not do, then follow every other rule.

Before you output, check each of these: \rs is exactly 1.000; every size and gap goes through \fs, \sv, or \fpeval; none of the banned commands above appear; the layout is single column; and the file would compile with xelatex. Fix anything that fails before you send it.

Output ONLY the complete .tex file in one code block, beginning with \documentclass and ending with \end{document}. No prose, label, or note before or after it.

Here is my input (marked A, B, or C):
[FOR A: PASTE RESUME TEXT. FOR B: ATTACH THE FILE AND WRITE "B" HERE. FOR C: PASTE YOUR .tex THEN YOUR RESUME CONTENT.]
```

Save the result as `myresume.tex` next to [`resume-tex-fit.py`](resume-tex-fit.py). If you told the model to use a local font, drop the font files in a `fonts/` folder beside the `.tex` and point [`fontspec`](https://ctan.org/pkg/fontspec) at them; the default font needs nothing extra.

### Step 3: Check the Output

The model gets you most of the way. The rest is on you, because this is a resume.

- **Compile it and read the PDF against your original.** Watch for dropped bullets, reordered dates, and numbers that do not match.
- **Scan for `% TODO` comments.** Those mark where the model was unsure.
- **Check special characters.** Percentages, dollar signs, ampersands in company names, and underscores in emails are the usual breakage points.
- **Confirm the knob is used.** The tool refuses a file with no `\rs`, but it cannot catch a stray `\fontsize{11pt}` that slipped past the knob. A search for raw `pt` sizes finds those.

---

## Customizing the Look

You almost never need to write this by hand. The AI prompt above wires the styling for you, so the easiest path is to ask for what you want in plain words: "use the Charter font with a dark green accent," "tighter spacing," "wider margins." The AI applies it and keeps the one rule that matters, that every size runs through the knob so the fitter still works.

Here is the menu of what you can change. Each item shows the plain-English version to ask for, and the single line that controls it if you ever want to peek.

**Font, meaning the typeface.** Ask for any font by name. A few work with no setup because they come with LaTeX: Latin Modern (the default), and the TeX Gyre family (Termes looks like Times, Heros like Arial, Pagella like Palatino). Any font installed on your own computer works too. Stick to clean, professional faces and skip handwriting or display styles, which read badly and can confuse resume scanners.

```latex
\setmainfont{TeX Gyre Termes}
```

If you plan to send the resume to someone who might not have your font, ask the AI to set the font up from a `fonts/` folder, then drop the font files into that folder next to your `.tex`. That way it looks the same on any computer.

**Color.** One accent color, used on your name, the section titles, and the lines under them. Give the AI a color by name, or a hex code like `1A365D` for navy. One accent looks sharp; more than one looks busy.

```latex
\definecolor{accent}{HTML}{1A365D}
```

**Spacing and margins.** How much white space sits around the sections, the bullets, and the edges of the page. Ask for "tighter" or "more open," or a specific margin like "three quarters of an inch." The tool changes how dense the text is, not the margins, so pick margins you like and let it handle the fit.

```latex
\usepackage[margin=0.9in]{geometry}
```

**Section headings.** The look of "Experience," "Education," and the rest: the font, whether a line sits under each one, and the space around them. Describe the style in words ("small caps, thin underline") and the AI builds it.

**Keep it one column.** You can make two columns, but do not for a resume you upload to a job site. Many [applicant tracking systems](https://en.wikipedia.org/wiki/Applicant_tracking_system) read straight across the page and jumble two-column text. The tool and the prompt both stay single column for that reason.

---

## Features

- **Exact page fitting:** Binary-searches the scale range for the largest setting that still fits your target page count.
- **Feasibility check:** Judges the target against the document's natural size, so it does not pad a short document by default.
- **Document-type targets (GUI):** Pick Junior (1 page), Senior or Executive (2 pages), or Academic CV (fits its natural length) instead of a raw page number.
- **Force Fit (opt-in):** When the content does not fit the target, grow past the normal range or shrink below the readable floor to hit it anyway, with a plain warning about the cost.
- **Three honest outcomes:** Fits, too long, or too short, each with a clear report and next step.
- **Cut guidance when too long:** Estimates the overflow and suggests where to trim, ordered easiest to hardest.
- **Choose where the PDF goes:** The GUI asks for the output folder and name before running; the CLI takes `--out`.
- **Backup and restore:** Writes a `.tex.bak` before any change and restores it if the target is not reachable.
- **Follows your OS theme:** The GUI reads your light or dark setting on launch.
- **Responsive GUI:** `xelatex` runs on a background thread so the window does not freeze.

---

## Getting Started

Put the script, your `.tex`, and (if the document loads local fonts) a `fonts/` folder in the same directory.

### Requirements

- **`xelatex` on your PATH.** The one hard requirement. Ships with [TeX Live](https://tug.org/texlive/) and [MacTeX](https://tug.org/mactex/). Check with `xelatex --version`.
- **[Python](https://www.python.org/) 3.8 or newer.** Standard library only for the core fit.
- **[`pdfplumber`](https://github.com/jsvine/pdfplumber) (optional).** If installed, the "too long" advice gives a line-level overflow estimate instead of a coarse page-based one. Install with `pip install pdfplumber`.
- **[`tkinter`](https://docs.python.org/3/library/tkinter.html) (optional, GUI only).** Bundled with most Python installs. On some Linux builds it is a separate `python3-tk` package.

### CLI

```bash
python3 resume-tex-fit.py resume.tex --pages 2
python3 resume-tex-fit.py resume.tex --pages 1 --force --out ~/Desktop/resume.pdf
python3 resume-tex-fit.py cv.tex --pages 0            # 0 = fit to natural length
```

`--pages` is the target (default 2); `0` fits to the document's natural length, for a CV. `--force` grows or shrinks past the normal range to hit the target even when the content does not fit, and warns about the cost. `--out PATH` copies the fitted PDF to PATH after a successful run. `--min` and `--max` set the scale range (defaults 0.90 and 1.05); lower `--min` for smaller type, raise `--max` for larger.

### GUI

```bash
python3 resume-tex-fit.py           # opens the GUI
python3 resume-tex-fit.py --gui
```

Pick a `.tex`, choose a document type, and hit Fit. On Fit it asks where to save the fitted PDF, then runs and writes it there on success. Turn on **Force Fit** first if you want it to hit the target even when the content does not fit. Every message, the knob check, fit progress, outcomes, and errors, prints to the output pane so you can select and copy it; there is no separate status bar. The window follows your OS light or dark theme, and `xelatex` runs on a background thread so it stays responsive.

---

## The Three Outcomes

- **Fits.** The tool searches the scale range, backs off the boundary slightly so a later reflow cannot push you over, locks that scale, and reports it. A `.tex.bak` is saved first so you can revert.
- **Too long.** If the document still overflows at the smallest normal density, the tool estimates the overflow (in lines, if `pdfplumber` is installed) and gives options ordered easiest to hardest, ending with Force Fit. Left alone, it restores your file and changes nothing. With Force Fit on, it shrinks below the readable floor to hit the target, or reverts and tells you the content is too dense if even that cannot reach it.
- **Too short.** If the content does not reach the target at normal size, the tool reports what it fills and declines to pad. With Force Fit on, it grows the type to reach the target (which reads as padding), or reverts and tells you the content is too sparse if even the ceiling cannot reach it.

---

## Limitations

- **The `\rs` knob is mandatory.** Pointing the tool at an arbitrary `.tex` without it will not work, by design.
- **The search assumes page count rises with scale.** It almost always does, but LaTeX reflow can occasionally shuffle a widow or float across a page boundary and break that assumption. On a pathological document the fit can be off by a page; a rerun after a small edit usually resolves it.
- **Each fit runs `xelatex` several times.** A search compiles repeatedly. The tool caches page counts per scale to avoid recompiling the same setting twice, but expect a run to take as long as several `xelatex` passes on your document.
- **The GUI render is not verified on every platform.** The logic and thread handling are tested; the exact window appearance varies by OS and theme.

---

## License

This project is licensed under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/).

You are free to:

- Use, share, and adapt this work
- Use it at your job

Under these terms:

- **Attribution:** Credit the original author
- **NonCommercial:** No selling or commercial products
- **ShareAlike:** Derivatives must use the same license
