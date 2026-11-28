"""The troubleshooting log: --verbose writes it to stderr, --log <file> to a file (both may be given).

Every part logs through log.info("section", "what was read or decided"). Nothing is logged unless asked for.
"""
import datetime
import os
import sys

_sinks = []

# A troubleshooting log is text: it must never be written over a plugin, an archive or a mod's files.
MOD_FILE_EXTENSIONS = {".esm", ".esp", ".esl", ".ba2", ".zip", ".7z", ".rar", ".strings", ".dlstrings", ".ilstrings",
                       ".pex", ".psc", ".dll"}


def _input_files(args):
    """Every existing file the command line names (any option's value, or a list of them), except --log itself."""
    found = []
    for key, value in (vars(args) if args is not None else {}).items():
        if key == "log":
            continue
        for v in value if isinstance(value, (list, tuple)) else [value]:
            try:
                if isinstance(v, str) and v and os.path.isfile(v):
                    found.append(v)
            except (OSError, ValueError):
                pass
    return found


def refusal(log_file, args=None):
    """Why --log must not write to `log_file` (it names an input file, a plugin or archive name, a file stream or a
    link), else None. Checked before the file is opened for writing, so nothing is truncated."""
    try:
        full = os.path.abspath(log_file)
        name = os.path.basename(full)
        if os.name == "nt":
            name = name.rstrip(". ")
            full = os.path.join(os.path.dirname(full), name)
        ext = os.path.splitext(name.split(":")[0])[1].lower()
        if ext in MOD_FILE_EXTENSIONS:
            return f"it names a {ext} file, and the log is plain text"
        if os.name == "nt" and ":" in os.path.splitdrive(name)[1]:
            return "its name holds a colon (a Windows file stream)"
        if os.path.islink(log_file):
            return "it is a link"
        for inp in _input_files(args):
            if os.path.exists(full) and os.path.samefile(full, inp):
                return f"it is the same file as the input {inp}"
    except (OSError, ValueError):
        return None  # a path the system can't open: the open below reports it
    return None


def setup(verbose=False, log_file=None, args=None):
    """Open the sinks. Returns an error message (str) when the log file can't be opened, else None. args: the parsed
    command line, so --log can be refused when it names one of the run's input files."""
    close()
    if verbose:
        _sinks.append(sys.stderr)
    if log_file:
        why = refusal(log_file, args)
        if why:
            return f"can't use {log_file} for --log: {why}. Fix: give --log a new file name such as sf-load-order.log."
        try:
            _sinks.append(open(log_file, "w", encoding="utf-8"))
        except (OSError, ValueError) as e:  # ValueError: a NUL in the path
            why = getattr(e, "strerror", None) or e
            return f"can't write the log file {log_file}: {why}. Fix: give --log a folder you can write to."
    return None


def info(section, msg):
    if not _sinks:
        return
    line = f"{datetime.datetime.now().strftime('%H:%M:%S')} [{section}] {msg}\n"
    for s in _sinks:
        s.write(line)
        s.flush()


def enabled():
    return bool(_sinks)


def close():
    for s in _sinks:
        if s is not sys.stderr:
            s.close()
    _sinks.clear()
