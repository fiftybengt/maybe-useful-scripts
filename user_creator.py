#!/usr/bin/env python3
from __future__ import annotations  # let `str | None` / `list[str]` run on older 3.x too

import csv
import os
import platform
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

NAME_SEPARATORS = r"[-._\s]"  # characters that divide one name from the next

# Letters that Unicode NFKD cannot split into base + accent, so we map them by hand.
SPECIAL_CHARS = {
    "ø": "o", "Ø": "o",
    "æ": "ae", "Æ": "ae",
    "œ": "oe", "Œ": "oe",
    "ß": "ss",
    "đ": "d", "Đ": "d",
    "ð": "d", "Ð": "d",
    "þ": "th", "Þ": "th",
    "ł": "l", "Ł": "l",
}


def to_ascii(text: str) -> str:
    for char, replacement in SPECIAL_CHARS.items():
        text = text.replace(char, replacement)            # handle non-decomposing letters first
    decomposed = unicodedata.normalize("NFKD", text)      # ö -> o + accent, é -> e + accent, ...
    return "".join(c for c in decomposed if not unicodedata.combining(c))  # drop the accents


def name_tokens(name: str) -> list[str]:
    local_part = name.split("@", 1)[0]                    # ignore any email domain
    tokens = []
    for piece in re.split(NAME_SEPARATORS, local_part):
        letters = "".join(c for c in piece if c.isalpha())  # strip digits and symbols
        if letters:
            tokens.append(letters)
    return tokens


def name_split(name: str) -> str:
    return " ".join(token.capitalize() for token in name_tokens(name))  # "pEter_storm" -> "Peter Storm"


def email_local_part(name: str) -> str:
    tokens = name_tokens(to_ascii(name))                  # transliterate before splitting
    return ".".join(token.lower() for token in tokens)    # "Björk Djur" -> "bjork.djur"


def email_address(name: str, domain: str) -> str:
    return f"{email_local_part(name)}@{domain}"           # "...@redning.no"


# --- Session CSV --------------------------------------------------------------
# Every processed name is logged with all known forms. The file lives next to the
# script, is blanked at startup (fresh per run), and stays in sync after each name.
# Rows are keyed by the email local-part, so any spelling/format of the same name
# ("Göran Persson", "goran.persson", "GORAN PERSSON") updates one shared row.

CSV_PATH = Path(__file__).resolve().parent / "names.csv"
CSV_HEADER = ["Name", "Email-short", "Email", "Department", "Role", "Language"]
SESSION = {"domain": ""}                                  # last domain entered, reused across modes
_records: dict[str, list[str]] = {}                       # local-part -> full CSV row (6 fields)


def _flush_csv() -> None:
    # Rewrite the whole file from memory so it always matches _records.
    with CSV_PATH.open("w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig: Excel reads accents right
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        writer.writerows(_records.values())


def start_session() -> None:
    _records.clear()                                      # drop anything from a previous run
    SESSION["domain"] = ""
    _flush_csv()                                          # create or blank the file (header only)


def record(name: str, domain: str = "", department: str = "",
           role: str = "", language: str = "") -> None:
    key = email_local_part(name)                          # canonical id: format/accent-insensitive
    if not key:                                           # empty / unusable input -> nothing to log
        return
    domain = domain or SESSION["domain"]                  # fall back to the session domain
    email = f"{key}@{domain}" if domain else ""
    row = _records.get(key)
    if row is None:                                       # first time we see this person
        row = [name_split(name), key, "", "", "", ""]     # Name keeps its first-seen spelling
        _records[key] = row
    for index, value in ((2, email), (3, department), (4, role), (5, language)):
        if value:                                         # only fill in real values, never blank out
            row[index] = value
    _flush_csv()


# --- Clipboard ----------------------------------------------------------------
def _clipboard_command() -> list[str] | None:
    system = platform.system()
    if system == "Windows":
        return ["clip"]                                   # built into Windows
    if system == "Darwin":
        return ["pbcopy"]                                 # built into macOS
    # Linux/BSD: prefer the Wayland tool, then fall back to X11 tools.
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        return ["wl-copy"]
    if shutil.which("xclip"):
        return ["xclip", "-selection", "clipboard"]
    if shutil.which("xsel"):
        return ["xsel", "--clipboard", "--input"]
    return None                                           # no usable tool found


def copy_to_clipboard(text: str) -> bool:
    try:
        import pyperclip                                  # handles Windows/macOS Unicode cleanly
        pyperclip.copy(text)
        return True
    except Exception:
        pass                                              # not installed, or no backend it can use
    command = _clipboard_command()                        # fall back to a native CLI tool
    if command is None:
        return False
    try:
        subprocess.run(command, input=text.encode("utf-8"), check=True)
        return True
    except Exception:
        return False


def _clipboard_hint() -> str:
    if platform.system() != "Linux":
        return "(clipboard unavailable — try: pip install pyperclip)"
    if os.environ.get("WAYLAND_DISPLAY"):
        return "(clipboard unavailable — run: sudo apt install wl-clipboard)"
    return "(clipboard unavailable — run: sudo apt install xclip)"


_clipboard_hint_shown = False  # so the install tip prints once, not on every result


def present(text: str) -> None:
    print(text)                                           # every result is shown...
    if copy_to_clipboard(text):
        print(f"copied: {text}")                          # ...and copied, with a confirmation
        return
    global _clipboard_hint_shown
    if not _clipboard_hint_shown:                         # surface the fix once per run
        print(_clipboard_hint())
        _clipboard_hint_shown = True


# --- CLI ----------------------------------------------------------------------
def prompt(message: str) -> str | None:
    answer = input(message).strip()
    return None if answer.lower() == "q" else answer      # None means "go back / quit"


def prompt_default(message: str, default: str) -> str | None:
    # Like prompt(), but Enter on an empty line reuses `default` (shown in brackets).
    suffix = f" [{default}]" if default else ""
    answer = input(f"{message}{suffix}: ").strip()
    if answer.lower() == "q":
        return None
    return answer or default


def run_name_split() -> None:
    while True:
        name = prompt("Name or email (q to go back): ")
        if name is None:
            return
        present(name_split(name))
        record(name)                                      # log all known forms in the background


def name_email_no_at() -> None:
    while True:
        name = prompt("Name (q to go back): ")
        if name is None:
            return
        present(email_local_part(name))
        record(name)


def name_email_yes_at() -> None:
    domain = prompt("Domain (e.g. redning.no): ")
    if domain is None:
        return
    SESSION["domain"] = domain                            # remember it for the CSV + other modes
    while True:                                           # domain asked once, names repeat
        name = prompt("Name: ")
        if name is None:
            return
        present(email_address(name, domain))
        record(name, domain=domain)


def full_profile() -> None:
    domain = SESSION["domain"]                            # seed defaults from anything set earlier
    department = role = language = ""
    while True:
        name = prompt("Name (q to go back): ")
        if name is None:
            return
        if not name_split(name):                          # guard against letter-less input
            print("Need at least one letter in the name.")
            continue
        domain = prompt_default("Domain (e.g. redning.no)", domain)
        if domain is None:
            return
        department = prompt_default("Department (TC / RSA / other)", department)
        if department is None:
            return
        role = prompt_default("Role (Agent / TL / other)", role)
        if role is None:
            return
        language = prompt_default("Language (Norwegian / other)", language)
        if language is None:
            return
        SESSION["domain"] = domain                        # share domain with the other modes
        record(name, domain=domain, department=department, role=role, language=language)
        print(f"saved: {name_split(name)} → {email_local_part(name)}@{domain}")  # no clipboard here


def list_users() -> None:
    if not _records:
        print("No users yet.")
        return
    print(f"\n{len(_records)} user(s) this session:")
    for name, short, email, department, role, language in _records.values():
        contact = email or short                          # show full email if known, else the short form
        extras = ", ".join(v for v in (department, role, language) if v)
        line = f"  - {name}  ({contact})"
        if extras:
            line += f"  [{extras}]"
        print(line)


def main() -> None:
    start_session()                                       # fresh CSV for this run
    print(f"Session CSV: {CSV_PATH}")
    actions = {
        "1": ("To title    ", "Fifty Bengt           ", run_name_split),
        "2": ("To mail     ", "fifty.bengt           ", name_email_no_at),
        "3": ("To mail(@)  ", "fifty.bengt@redning.no", name_email_yes_at),
        "4": ("Full profile", "name + dept/role/lang ", full_profile),
        "5": ("List        ", "show users made       ", list_users),
    }
    while True:
        print("\n=== Name tools ===")
        for key, (label, example, _) in actions.items():
            print(f"  {key}.  {label}  →  {example}")
        print("  q.  Quit")
        choice = input("Select: ").strip().lower()
        if choice == "q":
            break
        action = actions.get(choice)
        if action is None:
            print("Unknown option.")
            continue
        action[2]()                                       # run the chosen sub-loop


if __name__ == "__main__":
    main()