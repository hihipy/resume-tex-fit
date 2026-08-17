#!/usr/bin/env python3
r"""
resume-tex-fit: fit a LaTeX resume, CV, or any knob-wired document to an
exact page count.

The target .tex must expose one density knob:  \newcommand{\rs}{1.000}
Font sizes, leading, and spacing all scale from it. This tool edits the knob,
compiles with xelatex, reads the page count, and binary-searches for the
LARGEST scale that still fits the target, so the last page fills as much as
possible without spilling over.

When the target is not reachable at a sensible density, it says so instead of
shrinking the type into unreadability:
  - too long  -> estimates how much content to cut, with concrete options.
  - too short -> reports the page count it actually reaches; won't pad.

Two ways to run it:

  GUI (pick any .tex with a file dialog, choose the page target, watch it run):
      python3 resume-tex-fit.py
      python3 resume-tex-fit.py --gui

  CLI:
      python3 resume-tex-fit.py resume.tex --pages 2
      python3 resume-tex-fit.py mydoc.tex --pages 1 --min 0.85

The .tex, this script, and the fonts/ folder should sit in the same directory,
since the document loads its icon fonts from ./fonts/. xelatex must be on PATH.

After a successful fit the PDF is re-emitted so weaker text extractors can
find word boundaries. XeLaTeX writes word gaps as glyph positioning rather than
space characters, so parsers that do not reconstruct them read "Power BI" as
"PowerBI" and miss the keyword. Verified by counting extractable words before
and after; the rewrite is reverted if it does not improve them. This needs pdftocairo (poppler-utils) and pypdf;
without either it is skipped and the fit is unaffected. Disable with
--no-space-fix.

Core fit needs no third-party packages. If pdfplumber is installed, the
"too long" advice gets a finer, line-level estimate of the overflow. The GUI
needs tkinter (bundled with most Python installs; on some Linux builds it is a
separate python3-tk package).

Exit status (CLI): 0 when the document was fitted to the target, 1 when it was
not, so a calling script can tell the two apart.
"""  # noqa: EXE001

import argparse
import pathlib
import re
import shutil
import subprocess
import sys

MIN_SCALE_DEFAULT = 0.90    # legibility / ATS floor
MAX_SCALE_DEFAULT = 1.05    # ceiling so type never balloons into padding
FORCE_MAX = 2.0             # grow ceiling: ~2x type is about the largest that
                            # still reads as a document; only used under force
FORCE_MIN = 0.75            # shrink floor: below ~0.75 body type drops under
                            # ~7.5pt and legibility/ATS suffer; only under force
TOLERANCE = 0.004
MAX_ITERS = 12
SAFETY = 0.003              # back off the boundary so a reflow can't spill
COMPILE_TIMEOUT = 120       # seconds; one xelatex pass should be quick

# These calibrate the "too long" / "too short" advice text only. The fit itself
# reads the real page count from the log, so imprecise values here change the size
# of the suggested trim, never the correctness of the fit.
BASE_LEADING_PT = 11.7      # body leading at scale 1.0
BULLET_LINES = 1.8          # rough wrapped lines per bullet, for cut estimates
LINES_PER_PAGE = 46         # coarse fallback when pdfplumber is unavailable

KNOB_RE = re.compile(r"(\\newcommand\s*\{\s*\\rs\s*\}\s*\{)\s*([0-9.]+)\s*(\})")
# xelatex's driver writes "(2 pages)."; the pdftex family writes
# "(2 pages, 48213 bytes)." Accept either terminator.
PAGES_RE = re.compile(r"Output written on .*?\((\d+)\s+pages?[,)]")
SECTION_RE = re.compile(r"\\section\*?\{([^}]*)\}")
# Recognized resume and CV headings; two or more suggests a real resume or CV.
DOC_SECTIONS = {
    "SUMMARY", "EXPERIENCE", "WORK EXPERIENCE", "PROFESSIONAL EXPERIENCE",
    "EDUCATION", "SKILLS", "PROJECT", "PROJECTS", "CERTIFICATIONS",
    "PUBLICATIONS", "GRANTS", "TEACHING", "TEACHING EXPERIENCE", "RESEARCH",
    "RESEARCH EXPERIENCE", "AWARDS", "HONORS", "PRESENTATIONS", "SERVICE",
}


class FitError(Exception):
    """Anything that stops a fit: missing file, missing knob, compile failure."""


# --------------------------------------------------------------------------- #
# Core (UI-agnostic). Every routine takes a `log` callback so the CLI can print
# and the GUI can stream the same lines into its output pane.
# --------------------------------------------------------------------------- #

def check_tex(path):
    """Inspect a .tex without compiling. Returns a dict describing fitness."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return {"ok": False, "has_knob": False, "looks_resume": False,
                "sections": [], "message": f"Cannot read file: {exc}"}
    has_knob = bool(KNOB_RE.search(text))
    sections = sorted({m.group(1).strip().upper() for m in SECTION_RE.finditer(text)}
                      & DOC_SECTIONS)
    looks_resume = len(sections) >= 2
    if not has_knob:
        msg = (r"No \rs knob found. This tool needs \newcommand{\rs}{1.000} in "
               "the preamble, with the document's sizes and spacing scaled from "
               "it. It cannot fit this file as-is.")
    elif not looks_resume:
        msg = ("Has the density knob but does not look like a resume or CV "
               f"(found {sections or 'no standard sections'}). The fitter still "
               "works on any .tex with the knob; continue if that's intended.")
    else:
        msg = f"Looks good: density knob present, sections {sections}."
    return {"ok": has_knob, "has_knob": has_knob, "looks_resume": looks_resume,
            "sections": sections, "message": msg}


def set_scale(tex, scale):
    # Read strictly, not with errors="ignore": this is a read-modify-write of
    # the user's source, and dropping undecodable bytes would silently rewrite
    # the document as well as the knob.
    try:
        text = tex.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise FitError(
            f"{tex.name} is not valid UTF-8, so the knob cannot be rewritten "
            "without risking the rest of the file. Re-save it as UTF-8 and "
            "rerun.") from exc
    except OSError as exc:
        raise FitError(f"Cannot read {tex.name}: {exc}") from exc
    new_text, n = KNOB_RE.subn(
        lambda m: f"{m.group(1)}{scale:.4f}{m.group(3)}", text, count=1)
    if n == 0:
        raise FitError(r"No \rs knob to set in " + tex.name)
    try:
        tex.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        raise FitError(f"Cannot write {tex.name}: {exc}") from exc


def compile_pdf(tex, log):
    """Compile once; return the page count. Raise FitError on failure."""
    try:
        proc = subprocess.run(  # noqa: PLW1510
            ["xelatex", "-interaction=nonstopmode", "-halt-on-error", tex.name],
            cwd=tex.parent, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=COMPILE_TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise FitError("xelatex not found on PATH. Install TeX Live or MacTeX, "
                       "then confirm with: xelatex --version") from exc
    except subprocess.TimeoutExpired as exc:
        raise FitError(f"xelatex timed out after {COMPILE_TIMEOUT}s on {tex.name}. "
                       "The document may have an infinite loop or a stuck "
                       "package.") from exc
    logf = tex.with_suffix(".log")
    if proc.returncode != 0 or not logf.exists():
        detail = ""
        if logf.exists():
            # LaTeX error lines start with "!"; surface the first one so the
            # user does not have to open the log to see what broke.
            for ln in logf.read_text(encoding="utf-8", errors="ignore").splitlines():
                if ln.startswith("!"):
                    detail = f" First error: {ln.strip()}"
                    break
        raise FitError(f"xelatex failed.{detail} See {logf.name} (often a missing "
                       "font, or the fonts/ folder not sitting next to the .tex).")
    matches = PAGES_RE.findall(logf.read_text(encoding="utf-8", errors="ignore"))
    if not matches:
        raise FitError("Could not read a page count from the LaTeX log.")
    return int(matches[-1])


def normalize_spaces(pdf, log):
    r"""Re-emit `pdf` in place so weaker text extractors find word boundaries.

    XeLaTeX's driver writes word gaps as glyph positioning rather than space
    characters. Poppler reconstructs them; pypdf and a number of applicant
    tracking parsers do not, so a multi-word keyword like "Power BI" extracts
    as "PowerBI" and fails an exact match. pdftocairo re-encodes the text stream
    so those boundaries survive extraction, but it drops document metadata and
    link annotations, so both are copied back from the original.

    Skips quietly when pdftocairo or pypdf is missing, and restores the original
    if the rewrite does not actually improve word separation. Returns True when the
    file was rewritten.
    """
    if shutil.which("pdftocairo") is None:
        log("  space fix skipped: pdftocairo not found (install poppler-utils).")
        return False
    try:
        # pypdf's optional crypto backend emits a DeprecationWarning on import
        # in some environments; it is noise in the log pane, not a problem here.
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import pypdf
    except ImportError:
        log("  space fix skipped: pypdf not installed (pip install pypdf).")
        return False

    def words(path):
        # Whitespace-separated words as a weak parser would see them. This is
        # plain str.split() on the extracted text: no network, no API, no model.
        # More words after the rewrite means word boundaries became visible.
        reader = pypdf.PdfReader(str(path))
        return len("".join(p.extract_text() or "" for p in reader.pages).split())

    backup = pdf.with_suffix(".pdf.orig")
    rebuilt = pdf.with_suffix(".pdf.spaced")
    try:
        before = words(pdf)
        shutil.copy2(pdf, backup)
        subprocess.run(  # noqa: PLW1510
            ["pdftocairo", "-pdf", str(backup), str(rebuilt)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=COMPILE_TIMEOUT, check=True,
        )
        orig, fixed = pypdf.PdfReader(str(backup)), pypdf.PdfReader(str(rebuilt))
        writer = pypdf.PdfWriter()
        for i, page in enumerate(fixed.pages):
            if i < len(orig.pages):
                annots = orig.pages[i].get("/Annots")   # clickable links
                if annots is not None:
                    page[pypdf.generic.NameObject("/Annots")] = annots
            writer.add_page(page)
        if orig.metadata:                                # pdftitle, pdfkeywords
            writer.add_metadata(
                {k: v for k, v in orig.metadata.items() if isinstance(v, str)})
        with open(rebuilt, "wb") as fh:
            writer.write(fh)

        after = words(rebuilt)
        if after <= before:
            log(f"  space fix reverted: no improvement ({before} -> {after} words).")
            return False
        shutil.move(str(rebuilt), str(pdf))
        log(f"  space fix applied: {before} -> {after} extractable words.")
        return True
    except Exception as exc:  # noqa: BLE001
        # Never let this cost a good fit. Put the original back and move on.
        log(f"  space fix skipped: {exc}")
        if backup.exists():
            shutil.copy2(backup, pdf)
        return False
    finally:
        for tmp in (backup, rebuilt):
            tmp.unlink(missing_ok=True)


def overflow_lines(tex, target, scale):
    """Estimate text lines spilling past `target` pages in the current PDF.
    Uses pdfplumber if importable; returns a float, or None if unavailable."""
    try:
        import pdfplumber
    except ImportError:
        return None
    try:
        with pdfplumber.open(str(tex.with_suffix(".pdf"))) as pdf:
            if len(pdf.pages) <= target:
                return 0.0
            spilled = 0.0
            for pg in pdf.pages[target:]:
                ws = pg.extract_words()
                if ws:
                    spilled += max(w["bottom"] for w in ws) - min(w["top"] for w in ws)
        return spilled / (BASE_LEADING_PT * scale)
    except Exception:  # noqa: BLE001
        return None


def advise_too_long(tex, target, pages_at_min, min_scale, tool, log):
    lines = overflow_lines(tex, target, min_scale)
    if lines is None:
        lines = (pages_at_min - target) * LINES_PER_PAGE
        coarse = " (coarse; install pdfplumber for a line-level figure)"
    else:
        coarse = ""
    page_frac = lines / LINES_PER_PAGE
    log(f"Cannot fit {target} page(s). At minimum density (scale {min_scale:.2f}) "
        f"it is still {pages_at_min} page(s), about {lines:.0f} line(s) over{coarse}.")
    if page_frac >= 0.6:
        log(f"That is roughly {page_frac:.1f} of a page. This much content does "
            f"not compress to {target} page(s) without gutting it; {pages_at_min} "
            "page(s) is the honest size for it.")
        log("To get down to fewer pages the right way, cut whole roles or "
            "sections, not single bullets. Otherwise fit to what it holds:")
        log(f"  python3 {tool} {tex.name} --pages {pages_at_min}")
        log(f"To force {target} page(s) anyway, turn on Force Fit (GUI) or add "
            "--force (CLI). It shrinks the type below the readable floor, so it "
            "can be hard to read and applicant tracking systems may struggle.")
    else:
        bullets = max(1, round(lines / BULLET_LINES))
        log("Cut about that much and rerun. Options, easiest first:")
        log(f"  1. Trim ~{bullets} of the weakest bullets (least visible).")
        log("  2. Compress a section: fold a short list onto one line, or cut a "
            "list to its strongest few items.")
        log("  3. Tighten the longest paragraphs and drop low-value detail.")
        log("  4. Last resort: turn on Force Fit (GUI) or add --force (CLI), "
            "which shrinks below the readable floor and can hurt readability "
            "and applicant tracking; or widen the .tex margins.")


def advise_too_short(tex, target, natural_pages, tool, log):
    log(f"At natural density (scale 1.0) the content fills {natural_pages} "
        f"page(s). It will not reach {target} page(s) without padding it out "
        "(inflated type or filler), which weakens a document.")
    log("Options:")
    log(f"  1. If {target} was a ceiling, you're fine: it fits in {natural_pages}.")
    log(f"  2. To genuinely fill {target} page(s), add real content.")
    log(f"  3. Or target what it holds: python3 {tool} {tex.name} "
        f"--pages {natural_pages}")
    log(f"  4. To force {target} page(s) anyway, turn on Force Fit (GUI) or add "
        "--force (CLI). It enlarges the type to fill the space, which usually "
        "reads as padding.")


def run_fit(tex, target, min_scale, max_scale, log, tool="resume-tex-fit.py",
            force=False, space_fix=True):
    """Orchestrate a fit. Returns a result dict:
       {status: ok|too_long|too_short, pages: int, scale: float|None}.
    target=None fits to the document's natural page count (fills the last page).
    With force=True, a target larger than the natural size grows the type to
    reach it, and a target smaller shrinks below the legible floor. Both are
    opt-in and tend to look padded or become hard to read.
    With space_fix=True the locked PDF is re-emitted so weaker text extractors
    can find word boundaries; see normalize_spaces.
    Raises FitError on missing file, missing knob, or compile failure."""
    if not tex.exists():
        raise FitError(f"{tex} not found.")
    if not check_tex(tex)["has_knob"]:
        raise FitError(r"No \rs density knob in " + tex.name + "; nothing to fit.")

    backup = tex.with_suffix(".tex.bak")
    shutil.copy2(tex, backup)

    # Memoize page counts per scale (keyed at the 4dp resolution set_scale writes)
    # so no scale is compiled twice, and track which scale is currently rendered.
    # page_count() reads the memo for search decisions; render() guarantees the
    # PDF on disk matches a given scale, for steps that read it or lock the file.
    counts = {}
    disk = {"scale": None}

    def _render(scale):
        # Set the knob, compile, cache the count, and record what is on disk.
        set_scale(tex, scale)
        pages = compile_pdf(tex, log)
        key = round(scale, 4)
        counts[key] = pages
        disk["scale"] = key
        log(f"  scale {scale:.4f} -> {pages} page(s)")
        return pages

    def page_count(scale):
        # Memoized count for search decisions. May leave a different scale
        # rendered on disk; use only when the number is all that matters.
        key = round(scale, 4)
        return counts[key] if key in counts else _render(scale)

    def render(scale):
        # Guarantee this scale is the one currently on disk, then return its
        # count. Use when a later step reads the PDF or to lock the file.
        key = round(scale, 4)
        return counts[key] if disk["scale"] == key else _render(scale)

    try:
        goal = "its natural length" if target is None else f"{target} page(s)"
        log(f"Fitting {tex.name} to {goal} "
            f"(scale range {min_scale:.2f}-{max_scale:.2f}):")

        # Judge feasibility against the natural size at neutral density, not the
        # ceiling, so a larger target never pads the doc by inflating type.
        p0 = page_count(1.0)
        if target is None:              # CV / auto: fit to the natural length
            target = p0
        if p0 < target and not force:
            actual = render(1.0)                  # leave the file locked at 1.0
            advise_too_short(tex, target, p0, tool, log)
            log(f"\nSet scale 1.0000 -> {actual} page(s). "
                f"Backup saved as {backup.name}.")
            return {"status": "too_short", "pages": actual, "scale": 1.0}

        if p0 > target:
            p_min = render(min_scale)             # min-scale PDF must be on disk
            if p_min > target and not force:
                advise_too_long(tex, target, p_min, min_scale, tool, log)
                shutil.copy2(backup, tex)          # restore prior working state
                compile_pdf(tex, log)              # PDF back in step with the .tex
                log(f"\nLeft {tex.name} unchanged. Trim per the above, then rerun.")
                return {"status": "too_long", "pages": p_min, "scale": None}
            if p_min > target:
                # force path: shrink below the legible floor to drop a page.
                log("Forcing fewer pages by shrinking type below the readable "
                    "floor. This can be hard to read and applicant tracking "
                    "systems may struggle.")
                lo, hi, floor = FORCE_MIN, min_scale, FORCE_MIN
                shrank = True
                if page_count(lo) > target:
                    # The hard floor still overflows, so no scale in this range
                    # fits. Collapse the interval instead of compiling a search
                    # that cannot succeed; the guard after the loop reports it.
                    hi = lo
            else:
                lo, hi, floor = min_scale, 1.0, min_scale   # shrink below neutral
                shrank = False
            grew = False
        elif p0 < target:
            # force path: grow past natural size to reach the target page count.
            # Give the search real headroom so it can actually add a page, even
            # though enlarging type this much usually reads as padding.
            log("Forcing more pages by enlarging type and spacing. This can look "
                "padded and leave the last page sparse.")
            lo, hi, floor = 1.0, max(max_scale, FORCE_MAX), 1.0
            grew, shrank = True, False
            if page_count(hi) < target:
                # Even the grow ceiling stays under the target, so no scale in
                # this range reaches it. Collapse the interval rather than
                # compile a search that cannot succeed; the guard after the
                # loop reports it.
                lo = hi
        else:
            lo, hi, floor = 1.0, max_scale, 1.0         # p0 == target: fill last page
            grew, shrank = False, False

        best = lo
        for _ in range(MAX_ITERS):
            if hi - lo < TOLERANCE:
                break
            mid = (lo + hi) / 2.0
            if page_count(mid) <= target:
                best, lo = mid, mid
            else:
                hi = mid

        # Back off the boundary so a reflow can't spill into an extra page. Skip
        # it when growing: there best sits at the top of the target-page window,
        # and backing off could drop below target and trip a false "too sparse".
        if not grew:
            best = max(floor, best - SAFETY)
        final = render(best)
        if grew and final < target:
            # Even at the grow ceiling the content stays under the target. Huge
            # type that still falls short is worse than leaving it alone.
            render(1.0)
            log(f"\nEven enlarged to scale {best:.4f} the content only reaches "
                f"{final} page(s), short of {target}. It is too sparse to fill "
                f"{target} page(s); add content. Reverted to normal size.")
            return {"status": "too_short", "pages": p0, "scale": 1.0}
        if shrank and final > target:
            # Even below the legible floor it still overflows the target.
            shutil.copy2(backup, tex)
            compile_pdf(tex, log)
            log(f"\nEven shrunk to scale {best:.4f} it still needs {final} "
                f"page(s), over {target}. There is too much content to reach "
                f"{target} page(s) even below the readable floor; cut some. "
                f"Reverted to normal size.")
            return {"status": "too_long", "pages": final, "scale": None}
        if space_fix:
            normalize_spaces(tex.with_suffix(".pdf"), log)
        log(f"\nLocked scale {best:.4f} -> {final} page(s). "
            f"Backup saved as {backup.name}.")
        return {"status": "ok", "pages": final, "scale": best}
    except FitError:
        # On a hard failure mid-fit, leave the file as we found it.
        shutil.copy2(backup, tex)
        raise


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main_cli(args, tool):
    tex = args.tex
    if not tex.exists():
        sys.exit(
            f"{tex} not found in {pathlib.Path.cwd()}.\n"
            "cd into the folder that holds your .tex (alongside the fonts/ "
            "directory), or pass the path:\n"
            f"  python {tool} path/to/resume.tex --pages {args.pages}")
    try:
        target = None if args.pages == 0 else args.pages   # 0 = fit to natural
        res = run_fit(tex, target, args.min_scale, args.max_scale, print, tool,
                      force=args.force, space_fix=not args.no_space_fix)
    except FitError as exc:
        sys.exit(str(exc))
    if args.out:
        # Only a real fit is worth exporting. On too_long the .tex and PDF have
        # been restored to their pre-run state, so copying that out would hand
        # back an unfitted document under the name of a fitted one.
        if res["status"] != "ok":
            print(f"Not saved to {args.out}: the fit did not reach the target.")
        else:
            pdf = tex.with_suffix(".pdf")
            if pdf.exists():
                shutil.copy2(pdf, args.out)
                print(f"Saved PDF to {args.out}")
            else:
                print("No PDF to save (the fit did not produce one).")
    if res["status"] != "ok":
        sys.exit(1)      # so a calling script can tell a failed fit from a good one
    return res


# --------------------------------------------------------------------------- #
# GUI (tkinter). Compact window: pick file, choose pages, fit, watch output.
# --------------------------------------------------------------------------- #

LIGHT_THEME = {
    "bg": "#f2f2f2", "fg": "#1a1a1a", "muted": "#6b6b6b",
    "field_bg": "#ffffff", "field_fg": "#1a1a1a",
    "ok": "#1a7f37", "warn": "#8a6d00", "err": "#b00020",
}
DARK_THEME = {
    "bg": "#1e1e1e", "fg": "#e6e6e6", "muted": "#9a9a9a",
    "field_bg": "#2a2a2a", "field_fg": "#e6e6e6",
    "ok": "#4ec9a8", "warn": "#d7ba7d", "err": "#f26d6d",
}

# Document type -> target page count. None fits to the natural length, since an
# academic CV has no fixed page count. Senior and Executive both target 2: a
# two-page resume is standard for both; only academic CVs run longer.
ROLE_PAGES = {
    "Junior / Early Career (1 Page)": 1,
    "Senior / Experienced (2 Pages)": 2,
    "Executive (2 Pages)": 2,
    "Academic CV (3+ Pages)": None,
}


def detect_dark_mode():
    """Best-effort OS dark-mode check. Returns False (light) on any doubt."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],  # noqa: PLW1510
                                 capture_output=True, text=True, timeout=2)
            return "dark" in out.stdout.lower()
        if sys.platform.startswith("win"):
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        # Linux and the rest: GNOME/freedesktop color-scheme setting.
        out = subprocess.run(  # noqa: PLW1510
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True, text=True, timeout=2)
        return "dark" in out.stdout.lower()
    except Exception:  # noqa: BLE001
        return False


def launch_gui(initial, min_scale, max_scale, tool):
    try:
        import threading
        import tkinter as tk
        from tkinter import filedialog, scrolledtext, ttk
    except Exception:  # noqa: BLE001
        sys.exit("GUI needs tkinter (install python3-tk), or use the CLI:\n"
                 f"  python {tool} file.tex --pages 2")

    root = tk.Tk()
    root.title("resume-TeX-fit")
    root.minsize(680, 460)
    root.geometry("780x560")

    # Follow the OS light/dark setting. aqua (mac) and vista (win) already track
    # it natively; on Linux the default theme does not, so switch to clam, which
    # honors the palette we set below.
    pal = DARK_THEME if detect_dark_mode() else LIGHT_THEME
    root.configure(bg=pal["bg"])
    style = ttk.Style()
    if not (sys.platform == "darwin" or sys.platform.startswith("win")):
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
    style.configure(".", background=pal["bg"], foreground=pal["fg"])
    style.configure("TButton", foreground=pal["fg"])
    for el in ("TEntry", "TSpinbox", "TCombobox"):
        style.configure(el, fieldbackground=pal["field_bg"], foreground=pal["field_fg"])
    # The combobox dropdown is a classic Listbox; theme it via the option DB so it
    # is not a light popup over a dark window on Linux (aqua/vista ignore this).
    root.option_add("*TCombobox*Listbox.background", pal["field_bg"])
    root.option_add("*TCombobox*Listbox.foreground", pal["field_fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", pal["muted"])

    main = ttk.Frame(root, padding=12)
    main.pack(fill="both", expand=True)

    selected = {"path": pathlib.Path(initial) if initial else None}
    running = {"on": False}
    dest = {"path": None}                # where to write the fitted PDF, set on Fit
    last = {"inspected": None}           # so typing does not re-inspect per keystroke

    # --- file row ---
    ttk.Label(main, text=".tex File:").grid(row=0, column=0, sticky="w")
    path_var = tk.StringVar(value=str(selected["path"] or ""))
    ttk.Entry(main, textvariable=path_var, width=48).grid(
        row=0, column=1, sticky="ew", padx=(4, 4))
    main.columnconfigure(1, weight=1)

    def current_path():
        # The entry is editable, so the text in it is what a run acts on, not
        # whatever the file dialog last returned.
        raw = path_var.get().strip()
        return pathlib.Path(raw).expanduser() if raw else None

    def inspect(path):
        # No colored status label: write the knob/resume check into the output
        # pane so it can be selected and copied like everything else.
        info = check_tex(path)
        fit_btn.state(["!disabled"] if info["has_knob"] else ["disabled"])
        show(info["message"])

    def on_path_edit(*_):
        # A typed or pasted path goes through the same check as a chosen one.
        # Only a path that resolves to a file is inspected, so partial typing
        # does not flood the pane.
        if running["on"]:
            return
        p = current_path()
        if p and p.is_file():
            if p != last["inspected"]:
                last["inspected"] = p
                selected["path"] = p
                inspect(p)
        else:
            last["inspected"] = None
            fit_btn.state(["disabled"])

    path_var.trace_add("write", on_path_edit)

    def choose():
        p = filedialog.askopenfilename(
            title="Choose a .tex File",
            filetypes=[("LaTeX Files", "*.tex"), ("All Files", "*.*")])
        if p:
            path_var.set(p)              # the trace inspects it and enables Fit

    browse_btn = ttk.Button(main, text="Browse...", command=choose)
    browse_btn.grid(row=0, column=2)

    # --- document type row ---
    ttk.Label(main, text="Document Type:").grid(row=2, column=0, sticky="w")
    role_var = tk.StringVar(value="")    # no default; the user must choose a type
    role_box = ttk.Combobox(main, textvariable=role_var, state="readonly", width=28,
                            values=list(ROLE_PAGES))
    role_box.grid(row=2, column=1, sticky="w", padx=(4, 0))
    fit_btn = ttk.Button(main, text="Fit")               # command wired below
    fit_btn.grid(row=2, column=2, sticky="e")
    force_var = tk.BooleanVar(value=False)
    force_box = ttk.Checkbutton(
        main, text="Force Fit (Shrink or Grow Past the Readable Range)",
        variable=force_var)
    force_box.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

    controls = (fit_btn, browse_btn, role_box, force_box)

    # --- output pane ---
    out = scrolledtext.ScrolledText(main, height=14, wrap="word",
                                    state="disabled", font="TkFixedFont",
                                    background=pal["field_bg"], foreground=pal["field_fg"],
                                    insertbackground=pal["fg"],
                                    selectbackground=pal["muted"])
    out.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=(10, 0))
    main.rowconfigure(4, weight=1)

    def append(line):
        out.configure(state="normal")
        out.insert("end", line + "\n")
        out.see("end")
        out.configure(state="disabled")

    def show(text):                      # replace pane contents with one message
        out.configure(state="normal")
        out.delete("1.0", "end")
        out.insert("end", text + "\n")
        out.configure(state="disabled")

    def log(line):                       # thread-safe: marshal to the UI thread
        root.after(0, append, line)

    def finish():
        running["on"] = False
        for w in controls:
            w.state(["!disabled"])       # readonly on the combobox survives this

    def save_result(path):
        # Copy the freshly fitted PDF to the destination chosen before the run.
        # `path` is the file the run actually used, not whatever is in the entry
        # now, so a mid-run edit cannot redirect the copy.
        target_path = dest["path"]
        src = path.with_suffix(".pdf")
        if not target_path or not src.exists():
            return
        try:
            if pathlib.Path(target_path).resolve() != src.resolve():
                shutil.copy2(src, target_path)
            log(f"\nSaved fitted PDF to {target_path}")
        except OSError as exc:
            log(f"\nCould not save PDF: {exc}")

    def start_fit(path, target, force):
        running["on"] = True
        for w in controls:               # freeze the inputs the run depends on
            w.state(["disabled"])
        out.configure(state="normal")            # fresh pane for each run
        out.delete("1.0", "end")
        out.configure(state="disabled")
        # xelatex runs in a thread so the window stays responsive.
        threading.Thread(target=worker, args=(path, target, force),
                         daemon=True).start()

    def on_result(path, res):
        try:
            if res["status"] == "ok":            # only a real fit gets written out
                save_result(path)
            else:
                log("\nNot saved.")
            finish()
        except Exception as exc:                 # never wedge the window  # noqa: BLE001
            log(f"\nUnexpected error: {exc!r}")
            finish()

    def worker(path, target, force):
        try:
            res = run_fit(path, target, min_scale, max_scale, log, tool, force=force)
            root.after(0, on_result, path, res)
        except FitError as exc:
            log(f"\nError: {exc}")
            root.after(0, finish)
        except Exception as exc:                 # surface, don't crash silently  # noqa: BLE001
            log(f"\nUnexpected error: {exc!r}")
            root.after(0, finish)

    def on_fit():
        if running["on"]:
            return
        # Re-resolve and re-check here rather than trusting the button state,
        # since the entry can be edited without the button ever losing focus.
        path = current_path()
        if not path or not path.is_file():
            show("Choose an existing .tex file first.")
            return
        selected["path"] = path
        info = check_tex(path)
        if not info["has_knob"]:
            show(info["message"])
            return
        if role_var.get() not in ROLE_PAGES:
            show("Choose a document type first.")
            return
        # Ask where the fitted PDF should go before running. Cancel aborts.
        # No filetypes list: on macOS that renders a redundant "Format" popup
        # and widens the panel. defaultextension still forces .pdf.
        # Default the save location to Downloads (where people expect output),
        # falling back to the .tex folder if there is no Downloads directory.
        downloads = pathlib.Path.home() / "Downloads"
        start_dir = downloads if downloads.is_dir() else path.parent
        chosen = filedialog.asksaveasfilename(
            title="Save Fitted PDF As", defaultextension=".pdf",
            initialdir=str(start_dir), initialfile=path.stem + ".pdf")
        if not chosen:
            return
        dest["path"] = chosen
        start_fit(path, ROLE_PAGES[role_var.get()], force_var.get())

    fit_btn.configure(command=on_fit)

    if selected["path"] and selected["path"].is_file():
        last["inspected"] = selected["path"]
        inspect(selected["path"])
    else:
        fit_btn.state(["disabled"])
        show("Pick a .tex file to begin.")

    root.mainloop()


# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(
        description="Fit a LaTeX document to an exact page count.")
    ap.add_argument("tex", nargs="?", default=None, type=pathlib.Path,
                    help="path to the .tex (omit to open the GUI)")
    ap.add_argument("--pages", type=int, default=2,
                    help="target page count, or 0 to fit to natural length (CV)")
    ap.add_argument("--min", dest="min_scale", type=float, default=MIN_SCALE_DEFAULT)
    ap.add_argument("--max", dest="max_scale", type=float, default=MAX_SCALE_DEFAULT)
    ap.add_argument("--gui", action="store_true", help="force the GUI")
    ap.add_argument("--force", action="store_true",
                    help="grow or shrink type to reach --pages even when the "
                         "content does not fit (may look padded or become small)")
    ap.add_argument("--no-space-fix", action="store_true",
                    help="skip re-emitting the PDF with real space characters "
                         "(see normalize_spaces); leaves raw xelatex output")
    ap.add_argument("--out", type=pathlib.Path, default=None,
                    help="copy the fitted PDF to this path after a successful fit")
    args = ap.parse_args()
    tool = pathlib.Path(sys.argv[0]).name

    if args.pages < 0:
        sys.exit("--pages must be 0 (natural length) or more.")
    if not 0 < args.min_scale < args.max_scale:
        sys.exit("--min must be greater than 0 and less than --max.")

    if args.gui or args.tex is None:
        launch_gui(args.tex, args.min_scale, args.max_scale, tool)
    else:
        main_cli(args, tool)


if __name__ == "__main__":
    main()