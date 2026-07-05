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

Core fit needs no third-party packages. If pdfplumber is installed, the
"too long" advice gets a finer, line-level estimate of the overflow. The GUI
needs tkinter (bundled with most Python installs; on some Linux builds it is a
separate python3-tk package).
"""

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
PAGES_RE = re.compile(r"Output written on .*?\((\d+)\s+pages?\)")
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
    text = tex.read_text(encoding="utf-8")
    new_text, n = KNOB_RE.subn(
        lambda m: f"{m.group(1)}{scale:.4f}{m.group(3)}", text, count=1)
    if n == 0:
        raise FitError(r"No \rs knob to set in " + tex.name)
    tex.write_text(new_text, encoding="utf-8")


def compile_pdf(tex, log):
    """Compile once; return the page count. Raise FitError on failure."""
    try:
        proc = subprocess.run(
            ["xelatex", "-interaction=nonstopmode", "-halt-on-error", tex.name],
            cwd=tex.parent, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=COMPILE_TIMEOUT,
        )
    except FileNotFoundError:
        raise FitError("xelatex not found on PATH. Install TeX Live or MacTeX, "
                       "then confirm with: xelatex --version")
    except subprocess.TimeoutExpired:
        raise FitError(f"xelatex timed out after {COMPILE_TIMEOUT}s on {tex.name}. "
                       "The document may have an infinite loop or a stuck package.")
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
    except Exception:
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
            force=False):
    """Orchestrate a fit. Returns a result dict:
       {status: ok|too_long|too_short, pages: int, scale: float|None}.
    target=None fits to the document's natural page count (fills the last page).
    With force=True, a target larger than the natural size grows the type to
    reach it, and a target smaller shrinks below the legible floor. Both are
    opt-in and tend to look padded or become hard to read.
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
                      force=args.force)
    except FitError as exc:
        sys.exit(str(exc))
    if args.out:
        pdf = tex.with_suffix(".pdf")
        if pdf.exists():
            shutil.copy2(pdf, args.out)
            print(f"Saved PDF to {args.out}")
        else:
            print("No PDF to save (the fit did not produce one).")
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
            out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                                 capture_output=True, text=True, timeout=2)
            return "dark" in out.stdout.lower()
        if sys.platform.startswith("win"):
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        # Linux and the rest: GNOME/freedesktop color-scheme setting.
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True, text=True, timeout=2)
        return "dark" in out.stdout.lower()
    except Exception:
        return False


def launch_gui(initial, min_scale, max_scale, tool):
    try:
        import threading
        import tkinter as tk
        from tkinter import filedialog, ttk, scrolledtext
    except Exception:
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

    # --- file row ---
    ttk.Label(main, text=".tex File:").grid(row=0, column=0, sticky="w")
    path_var = tk.StringVar(value=str(selected["path"] or ""))
    ttk.Entry(main, textvariable=path_var, width=48).grid(
        row=0, column=1, sticky="ew", padx=(4, 4))
    main.columnconfigure(1, weight=1)

    def inspect(path):
        # No colored status label: write the knob/resume check into the output
        # pane so it can be selected and copied like everything else.
        info = check_tex(path)
        fit_btn.state(["!disabled"] if info["has_knob"] else ["disabled"])
        show(info["message"])

    def choose():
        p = filedialog.askopenfilename(
            title="Choose a .tex File",
            filetypes=[("LaTeX Files", "*.tex"), ("All Files", "*.*")])
        if p:
            selected["path"] = pathlib.Path(p)
            path_var.set(p)
            inspect(selected["path"])

    ttk.Button(main, text="Browse...", command=choose).grid(row=0, column=2)

    # --- document type row ---
    ttk.Label(main, text="Document Type:").grid(row=2, column=0, sticky="w")
    role_var = tk.StringVar(value="")    # no default; the user must choose a type
    ttk.Combobox(main, textvariable=role_var, state="readonly", width=28,
                 values=list(ROLE_PAGES)).grid(row=2, column=1, sticky="w",
                                                padx=(4, 0))
    fit_btn = ttk.Button(main, text="Fit")               # command wired below
    fit_btn.grid(row=2, column=2, sticky="e")
    force_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(main, text="Force Fit (Shrink or Grow Past the Readable Range)",
                    variable=force_var).grid(row=3, column=0, columnspan=2,
                                             sticky="w", pady=(6, 0))

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
        fit_btn.state(["!disabled"])

    def save_result():
        # Copy the freshly fitted PDF to the destination chosen before the run.
        target_path = dest["path"]
        src = selected["path"].with_suffix(".pdf")
        if not target_path or not src.exists():
            return
        try:
            if pathlib.Path(target_path).resolve() != src.resolve():
                shutil.copy2(src, target_path)
            log(f"\nSaved fitted PDF to {target_path}")
        except OSError as exc:
            log(f"\nCould not save PDF: {exc}")

    def start_fit(target, force):
        running["on"] = True
        fit_btn.state(["disabled"])
        out.configure(state="normal")            # fresh pane for each run
        out.delete("1.0", "end")
        out.configure(state="disabled")
        # xelatex runs in a thread so the window stays responsive.
        threading.Thread(target=worker, args=(selected["path"], target, force),
                         daemon=True).start()

    def on_result(target, res):
        try:
            if res["status"] == "ok":            # only a real fit gets written out
                save_result()
            else:
                log("\nNot saved.")
            finish()
        except Exception as exc:                 # never wedge the window
            log(f"\nUnexpected error: {exc!r}")
            finish()

    def worker(path, target, force):
        try:
            res = run_fit(path, target, min_scale, max_scale, log, tool, force=force)
            root.after(0, on_result, target, res)
        except FitError as exc:
            log(f"\nError: {exc}")
            root.after(0, finish)
        except Exception as exc:                 # surface, don't crash silently
            log(f"\nUnexpected error: {exc!r}")
            root.after(0, finish)

    def on_fit():
        if running["on"]:
            return
        path = selected["path"]
        if not path or not path.exists():
            show("Choose an existing .tex file first.")
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
        start_fit(ROLE_PAGES[role_var.get()], force_var.get())

    fit_btn.configure(command=on_fit)

    if selected["path"] and selected["path"].exists():
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
