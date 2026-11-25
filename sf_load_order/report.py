"""The report (called by cli.run after every check): report.txt, report.html and report.json in ctx.args.out.

  write(ctx) -> {"txt": path, "html": path, "json": path}   (a file that can't be written is left out and logged)

report.txt   plain text under 120 columns: a header (the byline: tool, version, author, license, project page; then
             tool version, source, profile, Data folder, files read, date), the top 10 (problems, then cautions, then
             notes; findings with a fix before those without), every section in model.SECTIONS order with its status
             and findings, the suggested order, and the files written.
report.html  the same in one self-contained file: inline CSS only, no scripts, no web fonts, and no links except
             LINKS (the project page, its issues page and sf-mod-check's page). The byline sits under the title; the
             footer gives the license, source and issues links, credits and the privacy line. Light and dark follow
             the system setting. Sections fold with <details>, long lists (archive pairs, stray files, lost records)
             fold with their counts, and every string from a file is HTML-escaped.
report.json  Report.to_dict() plus "generated", "results" (a summary per section: counts, not the raw data) and
             "left_out" (what the summary leaves out, and where to find it). load_order.mods[].files is replaced by
             files_count. Keys are listed in the README ("report.json").
Nothing here reads a game or mod file; it only formats what the checks found.
"""
import datetime
import html
import json
import os
import textwrap
import traceback

from . import BYLINE, LICENSE, URL, __version__, log, recommend, sfmodcheck
from .model import LEVELS, SECTIONS

PROJECT_URL = URL
ISSUES_URL = URL + "/issues"
SF_MOD_CHECK_URL = sfmodcheck.RELEASES.rsplit("/releases", 1)[0]  # https://github.com/taosecurity/sf-mod-check
LINKS = (PROJECT_URL, ISSUES_URL, SF_MOD_CHECK_URL)  # the only links report.html holds
PRIVACY = "It only reads your game and mod manager files and never uploads anything."
TXT, HTML, JSON = "report.txt", "report.html", "report.json"
WIDTH = 118
TOP = 10
LIST_MAX = 200  # items shown in one folded list in report.html

TITLES = {
    "inputs": "What was read",
    "order": "Load order and masters",
    "patches": "Patches (built for an older master, merged patches)",
    "clashes": "Known clashes (LOOT and your own files)",
    "archives": "Archives (.ba2 files)",
    "loose": "Loose files",
    "rules": "Vortex mod rules",
    "records": "Record conflicts (sf-mod-check)",
    "recommend": "Rating",
    "sort": "Sorting",
    "candidate": "The new build against the installed mods",
}
LEVEL_WORD = {"problem": "Problem", "caution": "Caution", "note": "Note"}
SOURCE_WORD = {"plugins.txt": "the game's Plugins.txt", "vortex": "a Vortex load order export",
               "mo2": "a Mod Organizer 2 profile"}
ARCHIVE_ADVICE = {"built": "built on it", "samemod": "same mod", "same": "identical copies",
                  "overlap": "order decides"}
KIND_WORD = {"F": "full", "M": "medium", "L": "light"}
# {role: words} for input files and files written; a role not listed is shown as it is
ROLE_WORD = {"txt": "this report, as text", "html": "this report, as a web page", "json": "this report, as data",
             "plugins_txt": "the order as a Plugins.txt", "loadorder_txt": "the order as MO2's loadorder.txt",
             "vortex_json": "the order for Vortex's Import button",
             "how_to": "how to apply the order", "rules_proposed": "the proposed rules (rules accept)"}
INPUT_WORD = {"plugins_txt": "the plugin list", "vortex_loadorder": "Vortex's load order export",
              "vortex_staging": "Vortex's staging folder", "vortex_deployment": "Vortex's deployment record",
              "modorganizer_ini": "MO2's settings", "modlist": "MO2's mod list", "loadorder_txt": "MO2's load order"}


# ---------------------------------------------------------------- small helpers

def _e(s):
    return html.escape("" if s is None else str(s), quote=True)


def _n(x):
    return f"{x:,}" if isinstance(x, int) and not isinstance(x, bool) else str(x)


def _plural(n, one, many=None):
    return f"{_n(n)} {one if n == 1 else (many or one + 's')}"


def _sentence(s):
    s = str(s or "").strip()
    if not s:
        return ""
    s = s[:1].upper() + s[1:]
    return s if s.endswith((".", "!", "?", ")")) else s + "."


def top_findings(report):
    """All findings in the report's order of importance: level (problem, caution, note), then the ones with a fix
    before the ones without, then section order. The first TOP of them are the report's top 10."""
    return sorted(report.findings, key=lambda f: (LEVELS.index(f.level), 0 if f.fix else 1,
                                                  SECTIONS.index(f.section)))


def _shown(ctx):
    """The sections this run's report shows: every check, or inputs and candidate for `candidate`."""
    return getattr(ctx, "sections", None) or tuple(s for s in SECTIONS if s not in ("candidate", "sort"))


def _section_findings(report, section):
    return [f for f in top_findings(report) if f.section == section]


def _status(report, section):
    return report.sections.get(section) or {"status": "missing"}


def status_text(st):
    """One or two plain sentences for a section's status."""
    s = st.get("status")
    why = st.get("why")
    if s == "done":
        if why and why.lower().startswith("couldn't use: "):
            why = why[len("couldn't use: "):]
        out = "Done." + (f" Part of it couldn't be used: {_sentence(why)}" if why else "")
    elif s == "skipped":
        out = f"Skipped. Why: {_sentence(why or 'no reason was given')}"
    elif s == "failed":
        out = f"This check failed, and the other checks still ran. Why: {_sentence(why or 'unknown')}"
    else:
        out = "This check didn't run."
    return out


def _summary_lines(ctx, section):
    """Plain sentences summing up a section from its status dict and ctx.results (counts only)."""
    st = _status(ctx.report, section)
    res = ctx.results.get(section) or {}
    lo = ctx.load_order
    out = []
    if section == "inputs" and lo is not None:
        loading = lo.enabled()
        official = sum(1 for p in lo.plugins if p.official and p.enabled and not p.dropped)
        disabled = sum(1 for p in lo.plugins if not p.enabled)
        out.append(f"{_plural(len(loading), 'plugin')} load ({_n(official)} official), and "
                   f"{_n(disabled)} listed plugin(s) are disabled.")
        if lo.mods is not None:
            out.append(f"{_plural(len(lo.mods), 'mod')} were read from the mod manager.")
        elif lo.mods_note:
            out.append(_sentence(lo.mods_note))
    elif section == "order" and st.get("counts"):
        c = st["counts"]
        lim = st.get("limits") or {}
        out.append("Plugins that load, by kind (base game included): " + ", ".join(
            f"{KIND_WORD[k]} {_n(c.get(k, 0))}" + (f" of {_n(lim[k])}" if k in lim else "") for k in "FML") + ".")
    elif section == "patches":
        if st.get("kind_changes") is not None:
            out.append(f"{_plural(st['kind_changes'], 'master')} changed kind since the last run.")
        if st.get("built_for_old"):
            n = st["built_for_old"]
            out.append(f"{_plural(n, 'plugin')} {'is' if n == 1 else 'are'} still built for an older kind of a "
                       "master (full, medium or light); this is reported on every run until it's rebuilt, and the problems "
                       "below name "
                       f"{'it' if n == 1 else 'them'}.")
        if st.get("state") == "off (--no-state)":
            out.append("The snapshot for \"changed since the last run\" is off (--no-state).")
        elif st.get("state") and st.get("previous") is False:
            out.append("This is the first run for this load order, so there is nothing to compare with yet.")
        if st.get("state_warning"):
            out.append(_sentence(st["state_warning"]))
    elif section == "clashes":
        for part, label in (("loot", "LOOT's masterlist"), ("clashes", "your --clashes file"),
                            ("accept", "your --accept file"), ("picks", "your --picks file")):
            p = (st.get("parts") or {}).get(part)
            if not p:
                continue
            if p.get("status") == "done":
                extra = f" ({_plural(p['entries'], 'entry', 'entries')} read)" if isinstance(p.get("entries"), int) \
                    else ""
                out.append(f"{label[:1].upper() + label[1:]} was read{extra}.")
            else:
                verb = "couldn't be used" if p.get("status") == "failed" else f"was {p.get('status', 'not read')}"
                out.append(f"{label[:1].upper() + label[1:]} {verb}: "
                           f"{_sentence(p.get('why') or 'no reason given')}")
    elif section == "archives" and st.get("status") == "done":
        out.append(f"{_plural(st.get('archives', 0), 'archive')} load with mod plugins; "
                   f"{_plural(st.get('shared_paths', 0), 'path')} "
                   f"{'appears' if st.get('shared_paths') == 1 else 'appear'} in more than one; order decides for "
                   f"{_plural(st.get('order_decides', 0), 'plugin')}.")
        if res.get("pairs"):
            out.append(f"{_plural(len(res['pairs']), 'pair')} of plugins share archive paths "
                       f"({', '.join(f'{ARCHIVE_ADVICE.get(k, k)} {v}' for k, v in _count(res['pairs'], 'advice').items())}).")
    elif section == "loose" and st.get("status") == "done":
        n_mods, n_pairs = st.get("mods", 0), st.get("pairs", 0)
        out.append(f"{_plural(n_mods, 'mod')} {'ships' if n_mods == 1 else 'ship'} loose files; "
                   f"{_plural(n_pairs, 'pair')} of mods {'ships' if n_pairs == 1 else 'ship'} the same paths.")
        if st.get("stray") is not None:
            out.append(f"{_plural(st['stray'], 'loose file')} in Data come from no enabled mod.")
    elif section == "rules" and st.get("status") == "done":
        out.append(("The modlist export has mod rules; " if st.get("has_rules") else
                    "The modlist export has no mod rules; ")
                   + f"{_plural(st.get('pairs', 0), 'pair')} of mods ship the same loose paths, "
                     f"{_n(st.get('differ', 0))} with different copies.")
    elif section == "records" and st.get("status") == "done":
        src = "a conflicts.json you gave (--conflicts)" if st.get("source") == "file" else "an sf-mod-check run"
        navi = (f" (and {_plural(st['navi'], 'navmesh info map entry', 'navmesh info map entries')}, which aren't "
                "lost changes)") if st.get("navi") else ""
        out.append(f"Read from {src}: {_plural(st.get('lost', 0), 'lost change entry', 'lost change entries')}{navi}, "
                   f"{_plural(st.get('pairs', 0), 'plugin pair')} ({_n(st.get('action_pairs', 0))} to act on), "
                   f"{_n(st.get('official_undone', 0))} official change(s) undone, {_n(st.get('itms', 0))} ITM(s) and "
                   f"{_n(st.get('type_changes', 0))} record type change(s).")
        ratings = _count((res.get("plugins") or {}).values(), "label")
        if ratings:
            out.append("Mod plugins by rating: " + ", ".join(f"{k} {_n(v)}" for k, v in ratings.items()) + ".")
        if st.get("heavy"):
            out.append(f"Heavy checks: {_sentence(st['heavy'])}")
        if st.get("result_line"):
            out.append(f"sf-mod-check said: {st['result_line']}")
    cand = getattr(ctx.report, "candidate", None)
    if section == "candidate" and cand:
        out.append(f"Checked {_plural(len(cand.get('zips') or []), 'zip')} ({', '.join(cand.get('plugins') or []) or 'no plugin'}) "
                   f"at depth {cand.get('depth')}: {_plural(cand.get('build_paths', 0), 'path')}, "
                   f"{_n(cand.get('shared', 0))} also shipped by the {_plural(cand.get('mods_checked', 0), 'enabled mod')} checked.")
        if cand.get("replaced"):
            out.append(f"Left out as the installed copy of this build: {', '.join(cand['replaced'])}.")
        rec = cand.get("records")
        if isinstance(rec, dict):
            if rec.get("status") == "done":
                mods = rec.get("mods") or []
                out.append(f"Record check (sf-mod-check conflict): {_plural(rec.get('shared_records', 0), 'record')} "
                           f"shared with {_plural(len(mods), 'installed plugin')}.")
                if rec.get("result_line"):
                    out.append(f"sf-mod-check said: {rec['result_line']}")
            elif rec.get("why"):
                out.append(f"Record check {'failed' if rec.get('status') == 'failed' else 'skipped'}: "
                           f"{str(rec['why']).rstrip('.')}.")
        elif cand.get("depth") == "light":
            out.append("Record check: not run at depth light.")
    if st.get("note"):
        out.append(_sentence(st["note"]))
    return out


def _count(items, key):
    out = {}
    for x in items:
        k = x.get(key) if isinstance(x, dict) else None
        out[k] = out.get(k, 0) + 1
    return out


def _fields_words(fields):
    return recommend.plain(fields or [])


def _item_text(x):
    """One line for an item of a folded list (finding data or section results). Never raises: an item of an
    unexpected shape is shown as it is."""
    try:
        return _item_text_(x)
    except Exception:  # a list item of a shape this doesn't know must not cost the whole report
        return str(x)


def _item_text_(x):
    if isinstance(x, str):
        return x
    if isinstance(x, (list, tuple)):
        return " / ".join(str(v) for v in x)
    if not isinstance(x, dict):
        return str(x)
    if "path" in x and "hint" in x:
        return f"{x['path']}: {x['hint']}"
    if "path" in x and "before" in x and "after" in x:
        return f"{x['path']}: {x['before']} wins today, {x['after']} would win"
    if "loser" in x and "winner" in x:
        what = f"{x.get('type', '')} {x.get('edid') or x.get('fk') or ''}".strip()
        s = (f"{what}: " if what else "") + f"{x['loser']} loses to {x['winner']}"
        bits = []
        if x.get("label"):
            bits.append(str(x["label"]))
        elif x.get("advice") in ARCHIVE_ADVICE:
            bits.append(ARCHIVE_ADVICE[x["advice"]])
        if isinstance(x.get("files"), int):
            bits.append(_plural(x["files"], "file"))
        if isinstance(x.get("records"), list) and (x["records"] or not x.get("navi")):
            bits.append(_plural(len(x["records"]), "record"))
        elif isinstance(x.get("records"), int):
            bits.append(_plural(x["records"], "record"))
        if isinstance(x.get("navi"), list) and x["navi"]:
            bits.append(_plural(len(x["navi"]), "navmesh info map record"))
        f = x.get("fields")
        if isinstance(f, dict):  # records.py pair: {label: count of fields}
            counts = ", ".join(f"{k} {_n(v)}" for k, v in f.items() if v)
            if counts:
                bits.append(f"fields by label: {counts}")
        elif f and isinstance(f, (list, tuple)):
            words = f if all(isinstance(v, str) for v in f) and "fk" not in x else _fields_words(f)
            if words:
                bits.append("lost: " + ", ".join(words[:6]) + (f" and {len(words) - 6} more" if len(words) > 6
                                                                 else ""))
                if recommend.QUEST in words:
                    bits.append("this can stop a quest or an NPC from appearing there")
        if x.get("why"):
            bits.append(str(x["why"]))
        return s + (f" ({'; '.join(bits)})" if bits else "")
    if "pick" in x and "why" in x:
        p = x["pick"] if isinstance(x["pick"], dict) else {}
        return f"{p.get('plugin', '?')} after {p.get('after', '?')}: {x['why']}"
    return "; ".join(f"{k}: {_item_text(v) if isinstance(v, (dict, list, tuple)) else v}" for k, v in x.items())


DATA_LABELS = {"pairs": "Pairs", "files": "Files", "archives": "Archives", "records": "Records", "flips": "Files",
               "sample": "Examples", "groups": "Groups"}


def _data_lists(f):
    """[(label, items)] for the lists in a finding's data worth showing folded."""
    out = []
    for k, v in (f.data or {}).items():
        if isinstance(v, (list, tuple)) and v and k in DATA_LABELS:
            out.append((DATA_LABELS[k], list(v)))
    return out


def _detail_lists(ctx, section):
    """[(title, items)] of long lists from a section's results, shown folded in report.html."""
    res = ctx.results.get(section) or {}
    out = []
    if section == "order" and res.get("effective"):
        out.append(("The order the game loads", list(res["effective"])))
    elif section == "archives" and res.get("pairs"):
        out.append(("Plugin pairs sharing archive paths", res["pairs"]))
    elif section == "loose":
        if res.get("pairs"):
            out.append(("Mod pairs shipping the same loose files",
                        [f"{' / '.join(p.get('mods') or [])} ({_plural(p.get('files', 0), 'file')}, "
                         f"{'identical' if p.get('advice') == 'same' else _n(p.get('differ', 0)) + ' differ'})"
                         for p in res["pairs"]]))
        stray = res.get("stray") or {}
        if stray.get("files"):
            out.append(("Stray files in Data", stray["files"]))
    elif section == "records":
        if res.get("pairs"):
            out.append(("Plugin pairs with lost changes", res["pairs"]))
        if res.get("lost"):
            out.append(("Lost records", res["lost"]))
    return out


# ---------------------------------------------------------------- the suggested order

def _suggested(ctx):
    """(paragraphs, moves, folds) for the "Suggested order" part. moves: [str]; folds: [(title, items)].
    paragraphs is None when the run has no such part (`candidate`)."""
    if "recommend" not in _shown(ctx):
        return None, [], []
    st = _status(ctx.report, "recommend")
    res = ctx.results.get("recommend")
    if not res:
        if st.get("status") in ("skipped", "failed"):
            return [f"No order was worked out. {status_text(st)}"], [], []
        return ["No order was worked out."], [], []
    paras, folds = [], []
    if res.get("rated"):
        paras.append(f"Loss score (lower is better): {res.get('label') or recommend.LABEL_CURRENT} "
                     f"{_n(res.get('score_current'))}, recommended {_n(res.get('score_recommended'))}.")
        if res.get("estimate"):
            paras.append(f"The recommended score is {recommend.ESTIMATE_WHY}.")
    else:
        paras.append("The order wasn't rated, because there is no record-conflict data (run at --depth medium with "
                     "sf-mod-check 0.8.0 or later, or pass --conflicts). Only master order and your picks were "
                     "applied.")
    if res.get("same_as_current"):
        paras.append("The suggested order is the same as your current order, so there is nothing to change.")
    moves = []
    for m in res.get("moves") or []:
        where = f"after {m['after']}" if m.get("after") else "at the top"
        moves.append(f"Load {m['plugin']} {where}. Why: {_sentence(m.get('reason'))}")
    picks = res.get("picks") or {}
    if picks.get("applied"):
        paras.append(f"{_plural(len(picks['applied']), 'pick')} from your --picks file were applied.")
    if picks.get("not_applied"):
        folds.append(("Picks that weren't applied", picks["not_applied"]))
    if res.get("archive_flips"):
        paras.append(f"The suggested order changes which mod's archived copy the game uses for "
                     f"{_plural(len(res['archive_flips']), 'file')}; the folded list \"Archived files whose winner "
                     f"changes\" below names them, and a caution under {TITLES[recommend.SECTION]} sums them up.")
        folds.append(("Archived files whose winner changes", res["archive_flips"]))
    if res.get("rejected"):
        folds.append(("Pairs kept in their current order", res["rejected"]))
    if res.get("remaining"):
        paras.append(f"{_plural(len(res['remaining']), 'record')} still lose changes under the suggested order; only "
                     "a merged patch keeps both sides.")
        folds.append(("Records only a merged patch can fix", res["remaining"]))
    vl = res.get("vs_loot")
    if vl is None:
        paras.append("No LOOT order was compared (pass --loot-order with a Plugins.txt that LOOT sorted to "
                     "compare).")
    elif vl.get("error"):
        paras.append(f"LOOT's order in {vl.get('file')} couldn't be read: {_sentence(vl['error'])}")
    else:
        s = (f"Compared with LOOT's order ({vl.get('file')}): "
             f"{_plural(vl.get('moved_count', 0), 'plugin')} placed differently")
        if vl.get("score") is not None:
            s += f", and LOOT's order has a loss score of {_n(vl['score'])}"
        paras.append(s + ".")
        if vl.get("missing"):
            paras.append(f"LOOT's file doesn't list {_plural(len(vl['missing']), 'plugin')} that load here: "
                         f"{', '.join(vl['missing'][:10])}" + (f" and {len(vl['missing']) - 10} more." if
                                                                len(vl['missing']) > 10 else "."))
        if vl.get("extra"):
            paras.append(f"LOOT's file lists {_plural(len(vl['extra']), 'plugin')} that don't load here.")
    files = res.get("files") or {}
    if files:
        how = files.get("how_to")
        paras.append("Files to apply the order: " + ", ".join(os.path.basename(p) for p in files.values()) + "."
                     + (f" Follow {how} to apply them, after backing up your current order." if how else ""))
    return paras, moves, folds


SOURCE_LABEL = {"game": "the game (masters, master plugins first)", "mod": "mods' own plugin order",
                "picks": "your picks", "user": "your rules file", "patch": "patch targets",
                "community": "the community rules file", "loot_userlist": "LOOT's userlist",
                "loot_masterlist": "LOOT's masterlist", "loot_groups": "LOOT's groups", "vortex": "Vortex's mod rules",
                "default_group": "the default group (plugins given no group)"}


def _sorted(ctx):
    """[(part title or None, paragraphs, items)] for the "Sorted order" part of a `sort` run (DESIGN 1.10), or None
    when the run has no such part."""
    if "sort" not in _shown(ctx):
        return None
    res = ctx.results.get("sort")
    st = _status(ctx.report, "sort")
    if not res:
        return [(None, [f"No order was sorted. {status_text(st)}"], [])]
    parts = []
    if res.get("order") is None:
        head = ["No valid order exists: the plugins' own masters form a loop (see the problem under Sorting), so "
                "no order files were written."]
    else:
        head = [f"Sorting moves {_plural(len(res.get('moves') or []), 'plugin')}"
                + (f"; loss score current {_n(res.get('score_current'))}, sorted {_n(res.get('score_sorted'))}"
                   if res.get("rated") else "; the order wasn't rated (no record-conflict data)") + "."]
        if res.get("estimate"):
            head.append(f"The sorted score is {recommend.ESTIMATE_WHY}.")
        if res.get("tie_break") == "name":
            head.append("--tie-break name: plugins are in file-name order wherever the rules allow.")
        if res.get("same_as_current"):
            head.append("The sorted order is the same as your current order, so there is nothing to change.")
    parts.append((None, head, []))
    mv = []
    for m in res.get("moves") or []:
        where = f"after {m['after']}" if m.get("after") else "at the top"
        mv.append(f"Load {m['plugin']} {where}. Why: {_sentence(m.get('reason'))}")
    if mv:
        parts.append(("Moves", [], mv))
    rej = [r.get("text") for r in res.get("rejected") or []]
    parts.append(("Rules not met", [] if rej else ["Every rule that applies to this load order was met."], rej))
    checks = ([f"{x['plugin']} needs {x['needs']}, which isn't enabled." for x in res.get("requires_missing") or []]
              + [f"{x['plugin']} and {x['with']} are both enabled, but they don't work together."
                 for x in res.get("incompatible") or []])
    parts.append(("Rules checks (requires and incompatible)", [] if checks else ["Nothing to fix."], checks))
    used = [f"{SOURCE_LABEL.get(k, k)}: {_n(v)}" for k, v in (res.get("sources") or {}).items() if v]
    paras = ["Rules used, by source: " + (", ".join(used) if used else "none") + "."]
    if res.get("inactive_rules"):
        paras.append(f"{_plural(res['inactive_rules'], 'rule')} name a plugin that isn't enabled, so they don't "
                     "apply here (sf-load-order rules list shows them).")
    lf = res.get("loot_files") or {}
    paras.append("LOOT files read: " + (", ".join(lf.values()) if lf else "none") + ".")
    parts.append(("Rules used", paras, []))
    prop = (res.get("files") or {}).get("rules_proposed")
    n = res.get("proposed") or 0
    parts.append(("Proposed rules", [f"{_plural(n, 'proposed rule')} in {prop or 'rules-proposed.json'}."
                                     + (f" To keep them, so later sorts make the same choice: sf-load-order rules "
                                        f"accept \"{prop}\" --all (or --id <id> for one)." if n and prop else "")], []))
    stale = res.get("stale") or []
    if stale:
        parts.append(("Stale rules", ["These rules are still used, but a plugin they name changed since they were "
                                      "pinned. Run sf-load-order sort again, look at the new proposals, and accept "
                                      "or delete the old rule."],
                      [f"{x['plugin']} ({os.path.basename(x['file'])} entry {x['entry']}): "
                       f"{', '.join(x['changed'])} changed" for x in stale]))
    files = {k: v for k, v in (res.get("files") or {}).items() if k != "rules_proposed"}
    if files:
        how = files.get("how_to")
        parts.append((None, ["Files to apply the order: " + ", ".join(os.path.basename(p) for p in files.values())
                             + "." + (f" Follow {how} to apply them, after backing up your current order." if how
                                      else "")], []))
    return parts


# ---------------------------------------------------------------- report.txt

_WRAP = {}


def _wrap(text, indent="", first=None):
    key = (indent, first)
    w = _WRAP.get(key)
    if w is None:
        w = _WRAP[key] = textwrap.TextWrapper(width=WIDTH, initial_indent=first if first is not None else indent,
                                              subsequent_indent=indent, break_long_words=False,
                                              break_on_hyphens=False)
    return w.fill(str(text)) if text else (first or indent).rstrip()


def _header_facts(ctx, paths):
    lo = ctx.load_order
    facts = [("Tool", f"sf-load-order {ctx.report.tool_version or __version__}"),
             ("Date", datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))]
    if lo is not None:
        facts.append(("Source", SOURCE_WORD.get(lo.source, lo.source)))
        if lo.profile:
            facts.append(("Profile", lo.profile))
        facts.append(("Data folder", lo.data or "not found"))
        for role, p in (lo.files or {}).items():
            facts.append(("File read", f"{p} ({INPUT_WORD.get(role, role)})"))
    depth = getattr(ctx.args, "depth", None) or "medium"
    facts.append(("Depth", depth))
    return facts


def _counts_line(report):
    c = report.counts()
    return (f"{_plural(c['problem'], 'problem')}, {_plural(c['caution'], 'caution')} and {_plural(c['note'], 'note')}"
            " were found.")


def render_text(ctx, paths):
    rep = ctx.report
    L = ["sf-load-order report", "=" * 20, _wrap(BYLINE), ""]
    for k, v in _header_facts(ctx, paths):
        L.append(_wrap(v, " " * 16, f"{k + ':':<16}"))
    L += ["", _wrap(_counts_line(rep))]
    failed = [TITLES[s] for s in _shown(ctx) if _status(rep, s).get("status") == "failed"]
    if failed:
        L.append(_wrap(f"These checks failed (see their sections): {', '.join(failed)}."))
    L += ["", "Top 10", "-" * 6]
    ordered = top_findings(rep)
    if not ordered:
        L.append("Nothing was found to fix or look at.")
    for i, f in enumerate(ordered[:TOP], 1):
        L.append(_wrap(f"[{LEVEL_WORD[f.level]}] {f.text}", "    ", f"{i:>2}. "))
        if f.fix:
            L.append(_wrap(f"Fix: {f.fix}", "    "))
        L.append(_wrap(f"(Section: {TITLES[f.section]}.)", "    "))
    if len(ordered) > TOP:
        L.append(_wrap(f"{_plural(len(ordered) - TOP, 'more finding')} are listed in the sections below."))
    for i, s in enumerate(_shown(ctx), 1):
        st = _status(rep, s)
        title = f"{i}. {TITLES[s]}"
        L += ["", title, "-" * len(title), _wrap(f"Status: {status_text(st)}")]
        for line in _summary_lines(ctx, s):
            L.append(_wrap(line))
        fs = _section_findings(rep, s)
        if st.get("status") == "done" and not fs:
            L.append("Nothing was found here.")
        for f in fs:
            L.append(_wrap(f"[{LEVEL_WORD[f.level]}] {f.text}", "    ", "  - "))
            if f.fix:
                L.append(_wrap(f"Fix: {f.fix}", "    "))
        for title_, items in _detail_lists(ctx, s):
            L.append(_wrap(f"{title_}: {_n(len(items))} (the full list is in report.html).", "    ", "  * "))
    paras, moves, folds = _suggested(ctx)
    if paras is not None:
        L += ["", "Suggested order", "-" * 15]
    for p in paras or ():
        L.append(_wrap(p))
    if moves:
        L.append(f"Moves ({_n(len(moves))}):")
        for i, m in enumerate(moves, 1):
            L.append(_wrap(m, "      ", f"  {i:>2}. "))
    for title_, items in folds:
        L.append(_wrap(f"{title_}: {_n(len(items))}.", "    ", "  * "))
        for x in items[:20]:
            L.append(_wrap(_item_text(x), "        ", "      - "))
        if len(items) > 20:
            L.append(_wrap(f"and {_n(len(items) - 20)} more in report.html.", "        ", "      "))
    srt = _sorted(ctx)
    if srt is not None:
        L += ["", "Sorted order", "-" * 12]
        for title_, ps, items in srt:
            if title_:
                L.append(f"{title_}" + (f" ({_n(len(items))}):" if items else ":"))
            for p in ps:
                L.append(_wrap(p, "    " if title_ else "", "  " if title_ else None))
            for i, x in enumerate(items[:200], 1):
                L.append(_wrap(x, "      ", f"  {i:>2}. "))
            if len(items) > 200:
                L.append(_wrap(f"and {_n(len(items) - 200)} more in report.json.", "      ", "      "))
    L += ["", "Files written", "-" * 13]
    for role, p in _outputs(ctx, paths).items():
        L.append(_wrap(f"{p} ({ROLE_WORD.get(role, role)})", "    ", "  - "))
    return "\n".join(L) + "\n"


def _outputs(ctx, paths):
    out = dict(ctx.report.outputs or {})
    out.update(paths)
    return out


# ---------------------------------------------------------------- report.html

CSS = """
:root{--bg:#f7f7f5;--panel:#ffffff;--text:#1d1f23;--muted:#5d6470;--line:#d9dce1;--accent:#2f5d9e;
--prob-bg:#fbe3e1;--prob:#9b1c13;--caut-bg:#fcefd2;--caut:#7a4d00;--note-bg:#e4ecf7;--note:#24456f;
--ok-bg:#e1f2e5;--ok:#1f6431;--skip-bg:#ececec;--skip:#4b4f56;color-scheme:light dark}
@media (prefers-color-scheme: dark){:root{--bg:#16181c;--panel:#1f2227;--text:#e6e7ea;--muted:#a3a9b3;
--line:#363a42;--accent:#8db4ec;--prob-bg:#4a1d1a;--prob:#ffb4ab;--caut-bg:#45340f;--caut:#f6cf7c;
--note-bg:#1d2f47;--note:#a9c8f2;--ok-bg:#183a22;--ok:#9fdcad;--skip-bg:#2c2f35;--skip:#c3c7ce}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,
"Helvetica Neue",Arial,sans-serif;overflow-wrap:anywhere}
main{padding:16px;max-width:1200px;margin:0 auto}
h1{font-size:1.5rem;margin:.2rem 0 .6rem}h2{font-size:1.2rem;margin:0 0 .5rem}
section,details.sec{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;
margin:0 0 12px}
details.sec>summary{cursor:pointer;font-weight:600;font-size:1.05rem;list-style-position:outside}
details.sec>summary .pill{font-weight:600}
dl.facts{display:grid;grid-template-columns:max-content 1fr;gap:2px 12px;margin:0}
dl.facts dt{color:var(--muted)}dl.facts dd{margin:0}
@media (max-width:560px){dl.facts{grid-template-columns:1fr}dl.facts dd{margin-bottom:6px}}
.pill{display:inline-block;padding:0 8px;border-radius:999px;font-size:.8rem;font-weight:600;line-height:1.6;
vertical-align:1px;white-space:nowrap}
.problem{background:var(--prob-bg);color:var(--prob)}.caution{background:var(--caut-bg);color:var(--caut)}
.note{background:var(--note-bg);color:var(--note)}.done{background:var(--ok-bg);color:var(--ok)}
.skipped,.missing{background:var(--skip-bg);color:var(--skip)}.failed{background:var(--prob-bg);color:var(--prob)}
ol.top,ul.findings{padding-left:1.4rem;margin:.3rem 0}ul.findings{list-style:none;padding-left:0}
ol.top li,ul.findings li{margin:0 0 .7rem}
.fix{margin:.15rem 0 0;color:var(--text)}.fix b{color:var(--muted);font-weight:600}
.status,.muted,.where{color:var(--muted)}
details.list{margin:.3rem 0}details.list>summary{cursor:pointer;color:var(--accent)}
details.list ul{margin:.3rem 0;padding-left:1.2rem;font-size:.92rem}
code{font-family:ui-monospace,Consolas,monospace;font-size:.9em}
footer{color:var(--muted);font-size:.9rem;padding:4px 16px 24px;max-width:1200px;margin:0 auto}
footer p{margin:.3rem 0}.byline{color:var(--muted);margin:0 0 .6rem}
a{color:var(--accent)}
"""


def _link(url, text=None):
    """A link to one of LINKS (never to anything a file named)."""
    if url not in LINKS:
        raise ValueError(f"report.html links only to {LINKS}, not {url}")
    return f'<a href="{_e(url)}">{_e(text or url)}</a>'


def _footer_html():
    return ("<footer>"
            f"<p>{_e(BYLINE).replace(_e(URL), _link(URL))}</p>"
            f"<p>License: {_e(LICENSE)}. This is free software: you may share and change it under the "
            f"{_e(LICENSE)}. It comes with no warranty. Source: {_link(PROJECT_URL)}. Problems and ideas: "
            f"{_link(ISSUES_URL)}.</p>"
            "<p>Credits: the LOOT messages come from LOOT's Starfield masterlist (CC0 1.0), read from your own LOOT "
            f"install when it's there. Record conflicts come from {_link(SF_MOD_CHECK_URL, 'sf-mod-check')}, when "
            "it's installed.</p>"
            f"<p>Privacy: {_e(PRIVACY)}</p>"
            "</footer>")


def _pill(cls, text):
    return f'<span class="pill {_e(cls)}">{_e(text)}</span>'


def _fold(title, items, open_=False):
    shown = items[:LIST_MAX]
    lis = "".join(f"<li>{_e(_item_text(x))}</li>" for x in shown)
    more = (f'<li class="muted">{_e(f"and {_n(len(items) - LIST_MAX)} more in report.json or the files the check read.")}'
            "</li>" if len(items) > LIST_MAX else "")
    return (f'<details class="list"{" open" if open_ else ""}><summary>{_e(title)} ({_e(_n(len(items)))})</summary>'
            f"<ul>{lis}{more}</ul></details>")


def _finding_html(f, where=False):
    h = f"{_pill(f.level, LEVEL_WORD[f.level])} {_e(f.text)}"
    if f.fix:
        h += f'<p class="fix"><b>Fix:</b> {_e(f.fix)}</p>'
    if where:
        h += f'<p class="where">Section: {_e(TITLES[f.section])}.</p>'
    for label, items in _data_lists(f):
        h += _fold(label, items)
    return h


def _counts_pills(fs):
    c = {lv: sum(1 for f in fs if f.level == lv) for lv in LEVELS}
    return " ".join(_pill(lv, f"{c[lv]} {lv}{'s' if c[lv] != 1 else ''}") for lv in LEVELS if c[lv])


def render_html(ctx, paths):
    rep = ctx.report
    P = []
    P.append("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
             "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
             "<meta name=\"color-scheme\" content=\"light dark\">"
             f"<title>Load order report</title><style>{CSS}</style></head><body><main>")
    P.append("<section><h1>sf-load-order report</h1>")
    P.append(f'<p class="byline">{_e(BYLINE).replace(_e(URL), _link(URL))}</p><dl class="facts">')
    for k, v in _header_facts(ctx, paths):
        P.append(f"<dt>{_e(k)}</dt><dd>{_e(v)}</dd>")
    P.append(f"</dl><p>{_counts_pills(rep.findings) or _pill('done', 'nothing found')} {_e(_counts_line(rep))}</p>")
    failed = [TITLES[s] for s in _shown(ctx) if _status(rep, s).get("status") == "failed"]
    if failed:
        P.append(f"<p>{_pill('failed', 'failed')} {_e('These checks failed (see their sections): ' + ', '.join(failed) + '.')}</p>")
    P.append("</section>")

    ordered = top_findings(rep)
    P.append('<section id="top"><h2>Top 10</h2>')
    if ordered:
        P.append('<ol class="top">' + "".join(f"<li>{_finding_html(f, True)}</li>" for f in ordered[:TOP]) + "</ol>")
        if len(ordered) > TOP:
            P.append(f'<p class="muted">{_e(_plural(len(ordered) - TOP, "more finding") + " are listed in the sections below.")}</p>')
    else:
        P.append("<p>Nothing was found to fix or look at.</p>")
    P.append("</section>")

    for i, s in enumerate(_shown(ctx), 1):
        st = _status(rep, s)
        fs = _section_findings(rep, s)
        open_ = st.get("status") == "failed" or any(f.level != "note" for f in fs)
        P.append(f'<details class="sec" id="{_e(s)}"{" open" if open_ else ""}><summary>{_e(f"{i}. {TITLES[s]}")} '
                 f'{_pill(st.get("status", "missing"), st.get("status", "not run"))} {_counts_pills(fs)}</summary>')
        P.append(f'<p class="status">{_e(status_text(st))}</p>')
        for line in _summary_lines(ctx, s):
            P.append(f"<p>{_e(line)}</p>")
        if st.get("status") == "done" and not fs:
            P.append('<p class="muted">Nothing was found here.</p>')
        if fs:
            P.append('<ul class="findings">' + "".join(f"<li>{_finding_html(f)}</li>" for f in fs) + "</ul>")
        for title, items in _detail_lists(ctx, s):
            P.append(_fold(title, items))
        P.append("</details>")

    paras, moves, folds = _suggested(ctx)
    if paras is not None:
        P.append('<section id="suggested"><h2>Suggested order</h2>')
    P += [f"<p>{_e(p)}</p>" for p in paras or ()]
    if moves:
        P.append(f"<p>Moves ({_e(_n(len(moves)))}):</p><ol>" + "".join(f"<li>{_e(m)}</li>" for m in moves) + "</ol>")
    for title, items in folds:
        P.append(_fold(title, items))
    if paras is not None:
        P.append("</section>")
    srt = _sorted(ctx)
    if srt is not None:
        P.append('<section id="sorted"><h2>Sorted order</h2>')
        for title, ps, items in srt:
            if title:
                P.append(f"<h3>{_e(title)}</h3>")
            P += [f"<p>{_e(p)}</p>" for p in ps]
            if items:
                shown = items[:LIST_MAX]
                P.append("<ol>" + "".join(f"<li>{_e(x)}</li>" for x in shown) + "</ol>")
                if len(items) > LIST_MAX:
                    P.append(f'<p class="muted">{_e(f"and {_n(len(items) - LIST_MAX)} more in report.json.")}</p>')
        P.append("</section>")

    P.append('<section id="files"><h2>Files written</h2><dl class="facts">')
    for role, p in _outputs(ctx, paths).items():
        P.append(f"<dt>{_e(ROLE_WORD.get(role, role))}</dt><dd><code>{_e(p)}</code></dd>")
    P.append("</dl></section></main>")
    P.append(_footer_html())
    P.append("</body></html>\n")
    return "\n".join(P)


# ---------------------------------------------------------------- report.json

LEFT_OUT = {
    "load_order": "Each mod's file list (load_order.mods[].files) is replaced by files_count.",
    "order": "The kind of every plugin is in load_order.plugins[].kind.",
    "archives": "The shared paths, their content digests and the archives each plugin loads are left out; the "
                "findings name the pairs to act on, with examples.",
    "loose": "Every loose path and its owners, and the stray file list beyond the 200 the stray-file finding "
             "holds, are left out.",
    "records": "The lost records with their field values are left out; they are in sf-mod-check's conflicts.json "
               "(results.records.file) and in report.html.",
    "clashes": "The LOOT messages and clash entries are the findings themselves; the raw masterlist is left out.",
    "recommend": "The full recommended order is the top-level \"recommended\" key; the records only a merged patch "
                 "can fix are counted here and listed (up to 100) in the recommend finding's data.",
}


def _results_summary(ctx):
    r = ctx.results
    out = {}
    o = r.get("order")
    if o:
        kinds = {}
        for k in (o.get("kinds") or {}).values():
            kinds[k or "unread"] = kinds.get(k or "unread", 0) + 1
        out["order"] = {"loading": len(o.get("effective") or []), "kinds": kinds}
    c = r.get("clashes")
    if c is not None:
        loot = c.get("loot") or {}
        out["clashes"] = {"accepted": len(c.get("accepted") or []), "known": len(c.get("known") or []),
                          "loot_file": loot.get("masterlist"), "picks": len(r.get("picks") or [])}
    a = r.get("archives")
    if a:
        out["archives"] = {"archives_loaded": len(a.get("loaded") or []), "plugins": len(a.get("plugins") or {}),
                           "shared_paths": len(a.get("paths") or {}), "pairs": len(a.get("pairs") or []),
                           "pairs_by_advice": _count(a.get("pairs") or [], "advice"),
                           "order_decides": list(a.get("order_decides") or []),
                           "misnamed": len(a.get("misnamed") or []), "orphans": len(a.get("orphans") or []),
                           "unreadable": len(a.get("unreadable") or []),
                           "lz4_missing": len(a.get("lz4_missing") or []), "data_checked": a.get("data_checked")}
    lr = r.get("loose")
    if lr:
        stray = lr.get("stray")
        out["loose"] = {"mods": lr.get("mods"), "paths": len(lr.get("paths") or {}),
                        "pairs": len(lr.get("pairs") or []), "pairs_by_advice": _count(lr.get("pairs") or [], "advice"),
                        "hides": len(lr.get("hides") or []), "stray": stray.get("count") if stray else None,
                        "stray_by_source": (stray or {}).get("by_source"), "data_checked": lr.get("data_checked")}
    rc = r.get("records")
    if rc:
        prog = rc.get("program") or {}
        out["records"] = {"source": rc.get("source"), "file": rc.get("file"), "generated": rc.get("generated"),
                          "program_version": prog.get("version") if isinstance(prog, dict) else None,
                          "exit": rc.get("exit"),
                          "lost": sum(1 for x in rc.get("lost") or [] if x.get("outcome") != "navi"),
                          "navi": sum(1 for x in rc.get("lost") or [] if x.get("outcome") == "navi"),
                          "pairs": len(rc.get("pairs") or []),
                          "pairs_by_advice": _count(rc.get("pairs") or [], "advice"),
                          "ratings": _count((rc.get("plugins") or {}).values(), "rating"),
                          "official_undone": len(rc.get("official_undone") or []), "itms": len(rc.get("itms") or []),
                          "type_changes": len(rc.get("type_changes") or []),
                          "problems": len(rc.get("problems") or []), "differs": bool(rc.get("differs"))}
    rr = r.get("recommend")
    if rr:
        vl = rr.get("vs_loot")
        picks = rr.get("picks") or {}
        out["recommend"] = {"rated": rr.get("rated"), "score_current": rr.get("score_current"),
                            "score_recommended": rr.get("score_recommended"), "estimate": rr.get("estimate"),
                            "same_as_current": rr.get("same_as_current"), "label": rr.get("label"),
                            "moves": list(rr.get("moves") or []), "flipped": len(rr.get("flipped") or []),
                            "rejected": len(rr.get("rejected") or []),
                            "picks_applied": len(picks.get("applied") or []),
                            "picks_not_applied": len(picks.get("not_applied") or []),
                            "archive_flips": len(rr.get("archive_flips") or []),
                            "remaining": len(rr.get("remaining") or []),
                            "vs_loot": None if vl is None else {k: vl.get(k) for k in
                                                                ("file", "moved_count", "score", "error")},
                            "files": dict(rr.get("files") or {})}
    return out


def to_json(ctx, paths):
    d = ctx.report.to_dict()
    d["generated"] = datetime.datetime.now().replace(microsecond=0).isoformat()
    d["outputs"] = _outputs(ctx, paths)
    lo = d.get("load_order") or {}
    for m in lo.get("mods") or []:
        files = m.pop("files", None)
        m["files_count"] = None if files is None else len(files)
    for s in _shown(ctx):
        d["sections"].setdefault(s, {"status": "missing", "why": "this check didn't run"})
    d["results"] = _results_summary(ctx)
    d["left_out"] = dict(LEFT_OUT)
    return json.dumps(d, indent=2, ensure_ascii=False, default=_json_default, sort_keys=False) + "\n"


def _json_default(o):
    if isinstance(o, (set, frozenset)):
        return sorted(o, key=str)
    return str(o)


# ---------------------------------------------------------------- write

def write(ctx):
    out = ctx.args.out
    paths = {"txt": os.path.join(out, TXT), "html": os.path.join(out, HTML), "json": os.path.join(out, JSON)}
    render = {"json": to_json, "txt": render_text, "html": render_html}
    written = {}
    disk_errors = {}  # {path: why} of files the disk refused, so a run that also hit a bug can name both causes
    bug = []          # set by this call only, so an older "failed" section can't make disk errors look like a bug

    def attempt(role, listed):
        """Write one file whose "Files written" part lists `listed`; True when it was written."""
        try:
            body = render[role](ctx, listed)  # any error while rendering is a bug, even an OSError
        except Exception as e:  # one broken part of the report must not cost the other two files
            log.info("report", f"couldn't make {paths[role]}:\n" + traceback.format_exc())
            # a bug, not a disk problem: cli.main() names it as an internal error when no file was written
            ctx.report.sections["report"] = {"status": "failed", "why": f"{type(e).__name__}: {e}"}
            bug.append(role)
            written.pop(role, None)
            return False
        try:
            with open(paths[role], "w", encoding="utf-8", newline="\n") as f:
                f.write(body)
            written[role] = paths[role]
            return True
        except OSError as e:
            log.info("report", f"can't write {paths[role]}: {e}")
            disk_errors[paths[role]] = getattr(e, "strerror", None) or str(e)
        written.pop(role, None)
        return False

    # Each file lists only the files really written: the text and web page first (listing all three), then the
    # JSON, listing what exists (itself included). If any one failed, the others are written again without it.
    for role in ("html", "txt"):
        attempt(role, paths)
    attempt("json", dict(written, json=paths["json"]))
    if len(written) < len(paths):
        for role in [r for r in ("html", "txt", "json") if r in written]:
            if not attempt(role, dict(written)):
                try:  # a file that first worked but now failed may be half written: remove it
                    os.remove(paths[role])
                except OSError:
                    pass
    sec = ctx.report.sections.get("report")
    if disk_errors and bug and sec and sec.get("status") == "failed":  # added last: every file is rendered
        sec["disk_errors"] = disk_errors
    log.info("report", f"wrote {sorted(written)} to {out}")
    return written
