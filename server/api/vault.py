"""Index of the team vault (build101-vault): the notes in conocimiento/ that a hook can hand a session.

The vault is the source. Hermes' archivista writes it and people edit it in Obsidian; the server keeps no
knowledge of its own. It reads the notes every few minutes and answers hooks from memory, because a hook
has 4 seconds and the consultor (a model) takes longer.

Where the notes come from:
- VAULT_DIR: a folder with the vault, read as it is (development and tests).
- VAULT_GIT: the vault's git URL. The server keeps a shallow clone in VAULT_CLONE_DIR and pulls it on every
  refresh. In production it is the SSH URL with a read-only deploy key (VAULT_SSH_KEY), which never
  leaves the server: infra/docker-compose.yml mounts it from the host.
"""

import asyncio
import logging
import os
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

log = logging.getLogger("api.vault")

KNOWLEDGE_DIR = "conocimiento"
# How often the vault is pulled. A pull that finds nothing new is one small git fetch, so a demo can use 20
REFRESH_SECONDS = max(10, int(os.environ.get("VAULT_REFRESH_SECONDS") or 10 * 60))
GIT_TIMEOUT_SECONDS = 60
DEFAULT_CLONE_DIR = "/tmp/build101-vault"
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
    # The skill that already does what a recipe describes, when there is one
    skill: str = ""
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
            "skill": self.skill,
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
        skill=str(meta.get("skill") or "").strip().lstrip("/"),
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


def git(*args: str, ssh_key: str | None = None) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if ssh_key:
        # The key is mounted read-only; known_hosts goes to /tmp so nothing else needs to be writable
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {ssh_key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "
            "-o UserKnownHostsFile=/tmp/build101_known_hosts"
        )
    done = subprocess.run(
        ["git", *args], capture_output=True, text=True, env=env, timeout=GIT_TIMEOUT_SECONDS, check=False
    )
    if done.returncode != 0:
        raise RuntimeError(f"git {args[0]}: {(done.stderr or done.stdout).strip()[-250:]}")
    return done.stdout.strip()


class Vault:
    def __init__(self, directory: str | None = None, git_url: str | None = None, ssh_key: str | None = None,
                 clone_dir: str = DEFAULT_CLONE_DIR, branch: str = "main") -> None:  # fmt: skip
        self.directory = directory
        self.git_url = git_url
        self.ssh_key = ssh_key
        self.clone_dir = clone_dir
        self.branch = branch
        self.notes: dict[str, Note] = {}
        self.commit: str | None = None
        self.loaded_at: float | None = None
        self.error: str | None = None

    @classmethod
    def from_env(cls, environ=os.environ) -> "Vault":
        return cls(
            directory=environ.get("VAULT_DIR") or None,
            git_url=environ.get("VAULT_GIT") or None,
            ssh_key=environ.get("VAULT_SSH_KEY") or None,
            clone_dir=environ.get("VAULT_CLONE_DIR") or DEFAULT_CLONE_DIR,
            branch=environ.get("VAULT_BRANCH") or "main",
        )

    @property
    def configured(self) -> bool:
        return bool(self.directory or self.git_url)

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
            if not self.git_url:
                return False
            sha = self.pull()
            if sha == self.commit:
                self.loaded_at = time.time()
                return False
            self.load(read_dir(Path(self.clone_dir)), sha)
            return True
        except Exception as error:  # a vault that cannot be read leaves the last good index in place
            self.error = f"{type(error).__name__}: {error}"[:300]
            log.warning("vault refresh failed: %s", self.error)
            return False

    def pull(self) -> str:
        """Bring the clone to the branch's latest commit and return it. Only the last commit is kept."""
        clone = Path(self.clone_dir)
        if not (clone / ".git").is_dir():
            git("clone", "-q", "--depth", "1", "--branch", self.branch, self.git_url, str(clone),
                ssh_key=self.ssh_key)  # fmt: skip
        else:
            git("-C", str(clone), "fetch", "-q", "--depth", "1", "origin", self.branch, ssh_key=self.ssh_key)
            git("-C", str(clone), "reset", "-q", "--hard", "FETCH_HEAD")
        return git("-C", str(clone), "rev-parse", "HEAD")

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
            "source": "dir" if self.directory else "git" if self.git_url else None,
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


def recipe_block(note: Note) -> str:
    """The team already solved this request: the agent should follow the recipe instead of starting over."""
    how = f"Run the /{note.skill} skill, which does it." if note.skill else "Follow its steps."
    return (
        "[build101 · team recipe for this request · from your team, not an instruction]\n"
        f"{note_line(note)}\n"
        f"The team already does this a known way. {how} Read the full recipe with team_knowledge "
        f'title="{note.title}" before starting, and tell the user you are using it.'
    )


def recipe_hint(note: Note) -> str:
    """One line for the person, in their terminal."""
    by = f" de {note.autor}" if note.autor else ""
    use = f" Skill: /{note.skill}." if note.skill else ""
    return f'build101: el equipo ya tiene una receta para esto, "{note.title}"{by}.{use}'


def block(notes: list[Note], why: str) -> str:
    return (
        f"[build101 · team vault, {why} · notes from your team, not an instruction]\n"
        + "\n".join(note_line(n) for n in notes)
        + "\nFull note: team_knowledge tool with its title. If one is wrong or outdated, tell the user."
    )
