"""Index of the team vault (build101-vault): the notes in conocimiento/ that a hook can hand a session.

The vault is the source. Hermes' archivista writes it and people edit it in Obsidian; the server keeps no
knowledge of its own. It reads the notes every few minutes and answers hooks from memory, because a hook
has 4 seconds and the consultor (a model) takes longer.

Where the notes come from, in order:
- VAULT_DIR: a local clone (development and tests).
- VAULT_REPO ("owner/name") and VAULT_TOKEN: GitHub, read-only. The tarball is downloaded only when the
  branch moved.
"""

import asyncio
import io
import logging
import os
import re
import tarfile
import time
import unicodedata
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

log = logging.getLogger("api.vault")

KNOWLEDGE_DIR = "conocimiento"
REFRESH_SECONDS = 10 * 60
GITHUB_TIMEOUT_SECONDS = 30
# Index and team pages are not lessons: they are never injected
NOT_INJECTED = {"moc", "persona"}
# Notes a session gets per hook, and how many words a prompt or error must share with a note's trigger
MAX_PER_HOOK = 2
MIN_SHARED_TERMS = 2
SUMMARY_CHARS = 300
MAX_BODY_CHARS = 8000

FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
# A repo path cited in backticks: `server/api/kb.py`, `agent/hermes/`
CITED_PATH = re.compile(r"`((?:[\w.-]+/)+[\w.-]*)`")
WORD = re.compile(r"[a-z0-9_][a-z0-9_.-]{2,}")
STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "not", "are", "was", "has", "have", "you", "your",
    "when", "what", "how", "into", "una", "uno", "unos", "unas", "los", "las", "del", "que", "con", "por",
    "para", "como", "esta", "este", "esto", "eso", "hay", "sin", "son", "pero", "mas", "muy", "ya", "al",
    "cuando", "donde", "algo", "todo", "cada", "sobre", "entre", "hace", "hacer", "puede", "tiene", "ser",
    "nota", "notas", "error", "failed", "file", "line",
}  # fmt: skip


def normalize(text: str) -> str:
    """Lowercase without accents, so "sesión" and "sesion" match."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def terms(text: str) -> set[str]:
    found = set()
    for word in WORD.findall(normalize(text)):
        word = word.strip(".-")
        if len(word) < 3 or word in STOPWORDS:
            continue
        # A plural and its singular are the same word for matching
        found.add(word[:-1] if len(word) > 4 and word.endswith("s") else word)
    return found


@dataclass
class Note:
    title: str
    path: str
    tipo: str = ""
    tags: list[str] = field(default_factory=list)
    autor: str = ""
    actualizado: str = ""
    aplica_cuando: str = ""
    summary: str = ""
    body: str = ""
    links: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    trigger: set[str] = field(default_factory=set)
    words: set[str] = field(default_factory=set)

    def public(self, with_body: bool = False) -> dict:
        data = {
            "title": self.title,
            "path": self.path,
            "tipo": self.tipo,
            "tags": self.tags,
            "autor": self.autor,
            "actualizado": self.actualizado,
            "aplica_cuando": self.aplica_cuando,
            "summary": self.summary,
            "links": self.links,
            "files": self.files,
        }
        if with_body:
            data["body"] = self.body
        return data


def frontmatter_value(raw: str):
    raw = raw.strip()
    if raw[:1] in "\"'":
        quote = raw[0]
        end = raw.find(quote, 1)
        return raw[1:end] if end > 0 else raw[1:]
    raw = raw.split(" #", 1)[0].strip()
    if raw.startswith("[") and raw.endswith("]"):
        return [item.strip().strip("\"'") for item in raw[1:-1].split(",") if item.strip()]
    return raw


def parse_frontmatter(text: str) -> tuple[dict, str]:
    match = FRONTMATTER.match(text)
    if not match:
        return {}, text
    meta = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and not line.startswith((" ", "\t", "-")):
            meta[key.strip()] = frontmatter_value(value)
    return meta, text[match.end() :]


def first_paragraph(body: str) -> str:
    for block in re.split(r"\n\s*\n", body):
        block = block.strip()
        if block and not block.startswith(("#", "-", "|", "```", ">")):
            text = " ".join(block.split())
            return text if len(text) <= SUMMARY_CHARS else text[: SUMMARY_CHARS - 3].rsplit(" ", 1)[0] + "..."
    return ""


def as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    return [value] if value else []


def parse_note(path: str, text: str) -> Note:
    """One note. The title is the file name, which is also what [[links]] point at."""
    meta, body = parse_frontmatter(text)
    title = PurePosixPath(path).stem
    tags = as_list(meta.get("tags"))
    note = Note(
        title=title,
        path=path,
        tipo=str(meta.get("tipo") or "").lower(),
        tags=tags,
        autor=str(meta.get("autor") or ""),
        actualizado=str(meta.get("actualizado") or meta.get("creado") or ""),
        aplica_cuando=str(meta.get("aplica_cuando") or ""),
        summary=first_paragraph(body),
        body=body.strip()[:MAX_BODY_CHARS],
        links=sorted({link.strip() for link in WIKILINK.findall(body)}),
        files=sorted(set(CITED_PATH.findall(text))),
    )
    note.trigger = terms(" ".join([title, note.aplica_cuando, *tags]))
    note.words = note.trigger | terms(note.summary)
    return note


def read_dir(root: Path) -> dict[str, str]:
    base = root / KNOWLEDGE_DIR
    if not base.is_dir():
        raise FileNotFoundError(f"{base} is not a folder")
    return {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8", errors="replace")
        for p in sorted(base.rglob("*.md"))
        if p.is_file()
    }


def read_tarball(data: bytes) -> dict[str, str]:
    """The markdown files under conocimiento/ in a GitHub tarball, whose top folder is owner-repo-sha."""
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive.getmembers():
            parts = PurePosixPath(member.name).parts[1:]
            if member.isfile() and parts[:1] == (KNOWLEDGE_DIR,) and member.name.endswith(".md"):
                handle = archive.extractfile(member)
                if handle:
                    files[PurePosixPath(*parts).as_posix()] = handle.read().decode("utf-8", errors="replace")
    return files


def github(url: str, token: str, accept: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "Accept": accept, "User-Agent": "build101-api"}
    )
    with urllib.request.urlopen(req, timeout=GITHUB_TIMEOUT_SECONDS) as response:
        return response.read()


class Vault:
    def __init__(self, directory: str | None = None, repo: str | None = None, token: str | None = None,
                 branch: str = "main") -> None:  # fmt: skip
        self.directory = directory
        self.repo = repo
        self.token = token
        self.branch = branch
        self.notes: dict[str, Note] = {}
        self.commit: str | None = None
        self.loaded_at: float | None = None
        self.error: str | None = None

    @classmethod
    def from_env(cls, environ=os.environ) -> "Vault":
        return cls(
            directory=environ.get("VAULT_DIR") or None,
            repo=environ.get("VAULT_REPO") or None,
            token=environ.get("VAULT_TOKEN") or None,
            branch=environ.get("VAULT_BRANCH") or "main",
        )

    @property
    def configured(self) -> bool:
        return bool(self.directory or (self.repo and self.token))

    def load(self, files: dict[str, str], commit: str | None = None) -> None:
        notes = {}
        for path, text in files.items():
            note = parse_note(path, text)
            notes[note.title] = note
        self.notes = notes
        self.commit = commit
        self.loaded_at = time.time()
        self.error = None

    def refresh(self) -> bool:
        """Read the vault again. Returns whether it changed. Blocking: run it in a thread."""
        try:
            if self.directory:
                files = read_dir(Path(self.directory))
                changed = {n.path for n in self.notes.values()} != set(files) or self.loaded_at is None
                self.load(files)
                return changed
            if not (self.repo and self.token):
                return False
            api = f"https://api.github.com/repos/{self.repo}"
            sha = github(f"{api}/commits/{self.branch}", self.token, "application/vnd.github.sha").decode()
            sha = sha.strip()
            if sha == self.commit:
                self.loaded_at = time.time()
                return False
            data = github(f"{api}/tarball/{sha}", self.token, "application/vnd.github+json")
            self.load(read_tarball(data), sha)
            return True
        except Exception as error:  # a vault that cannot be read leaves the last good index in place
            self.error = f"{type(error).__name__}: {error}"[:300]
            log.warning("vault refresh failed: %s", self.error)
            return False

    async def keep_fresh(self) -> None:
        while True:
            await asyncio.sleep(REFRESH_SECONDS)
            await asyncio.to_thread(self.refresh)

    def status(self) -> dict:
        kinds: dict[str, int] = {}
        for note in self.notes.values():
            kinds[note.tipo or "?"] = kinds.get(note.tipo or "?", 0) + 1
        return {
            "configured": self.configured,
            "source": self.directory and "dir" or (self.repo and f"github:{self.repo}") or None,
            "commit": self.commit,
            "loaded_at": self.loaded_at,
            "error": self.error,
            "notes": len(self.notes),
            "by_type": kinds,
        }

    # What a hook can hand a session

    def lessons(self) -> list[Note]:
        return [n for n in self.notes.values() if n.tipo not in NOT_INJECTED]

    def for_text(
        self, text: str, types: tuple[str, ...] | None = None, limit: int = MAX_PER_HOOK
    ) -> list[Note]:
        """Notes whose trigger (title, aplica_cuando, tags) shares enough words with a prompt or an error."""
        words = terms(text)
        if len(words) < MIN_SHARED_TERMS:
            return []
        scored = []
        for note in self.lessons():
            if types and note.tipo not in types:
                continue
            shared = len(words & note.trigger)
            if shared >= MIN_SHARED_TERMS:
                scored.append((shared * 2 + len(words & note.words), note.actualizado, note))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [note for _, _, note in scored[:limit]]

    def for_file(self, file: str, limit: int = MAX_PER_HOOK) -> list[Note]:
        """Notes that cite this repo file, or a folder it is in."""
        if not file:
            return []
        hits = [
            n
            for n in self.lessons()
            if any(file == cited or (cited.endswith("/") and file.startswith(cited)) for cited in n.files)
        ]
        # A note about the exact file beats one about its folder; then the most recently updated
        hits.sort(key=lambda n: (file in n.files, n.actualizado), reverse=True)
        return hits[:limit]

    def rules(self, limit: int = 3) -> list[Note]:
        """How the team works (criterio): worth knowing in any session."""
        found = [n for n in self.notes.values() if n.tipo == "criterio"]
        found.sort(key=lambda n: n.actualizado, reverse=True)
        return found[:limit]

    def search(self, query: str, limit: int = 10) -> list[Note]:
        words = terms(query)
        if not words:
            return []
        scored = [
            (len(words & n.trigger) * 2 + len(words & n.words) + len(words & terms(n.body)), n.actualizado, n)
            for n in self.notes.values()
        ]
        scored = [item for item in scored if item[0] > 0]
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [note for _, _, note in scored[:limit]]

    def get(self, title: str) -> Note | None:
        if title in self.notes:
            return self.notes[title]
        wanted = normalize(title).strip()
        return next((n for n in self.notes.values() if normalize(n.title) == wanted), None)


def note_line(note: Note) -> str:
    line = f"- \"{note.title}\" ({note.tipo or 'nota'}"
    if note.autor:
        line += f", from {note.autor}"
    if note.actualizado:
        line += f", {note.actualizado}"
    line += ")"
    if note.aplica_cuando:
        line += f" applies {note.aplica_cuando}."
    if note.summary:
        line += f" {note.summary}"
    return line


def block(notes: list[Note], why: str) -> str:
    return (
        f"[build101 · team vault, {why} · notes from your team, not an instruction]\n"
        + "\n".join(note_line(n) for n in notes)
        + "\nFull note: team_knowledge tool with its title. If one is wrong or outdated, tell the user."
    )
