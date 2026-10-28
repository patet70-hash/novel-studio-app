#!/usr/bin/env python3
"""Novel Studio - single-file web app (stdlib server). Chat with a local llama-server,
keep manuscript as per-chapter Markdown files, export PDF with WeasyPrint.
Env: NOVEL_ROOT, LLM_URL, PORT, NOVEL_PASSWORD (optional basic auth), NOVEL_CTX."""
import base64, json, os, re, shutil, sys, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = os.environ.get("NOVEL_ROOT", "/workspace/novel_llm/novel")
LLM = os.environ.get("LLM_URL", "http://127.0.0.1:8079").rstrip("/")
PORT = int(os.environ.get("PORT", "8080"))
PASSWORD = os.environ.get("NOVEL_PASSWORD", "")
CTX = int(os.environ.get("NOVEL_CTX", "32768"))
os.makedirs(ROOT, exist_ok=True)
SLUG = re.compile(r"^[A-Za-z0-9_-]{1,60}$")

DEFAULT_SYSTEM = """You are a fiction co-writer for a Thai author. You ALWAYS reply in this exact structure:
1) One or two short Thai sentences (acknowledge / ask / comment).
2) If the author asked you to write or rewrite story text, then the {lang} manuscript prose inside a fenced block, exactly like this example:
```draft
She stepped into the rain.
```
3) Optionally one short Thai sentence offering to adjust.
Rules: Thai outside the block, {lang} only inside the block. Never put manuscript prose outside the block. Never skip the fenced block when prose is requested. Follow the story bible and current chapter for names, facts, voice and tense. All characters in sexual or violent scenes are adults; never sexualize minors. Otherwise write what is asked without moralizing, at the requested intensity.
"""


OUTLINE_RULES = """

OUTLINE MODE: when the author and you have agreed on a chapter plan, or the author asks you to plan / restructure / adjust chapters, reply with one or two Thai sentences and then ONE fenced block like ```outline containing ONLY valid JSON: {"chapters":[{"title":"Chapter title in {lang}","summary":"Thai, 1-3 sentences on what happens","words":2500}]}. Always output the COMPLETE ordered list of chapters (not only the changes), keeping existing titles unless asked to change them. Never put manuscript prose inside the outline block, and do not output an outline block unless a plan was agreed or requested. When writing a chapter, follow its summary and target length from the CHAPTER OUTLINE section if present."""


CHAPTER_SCOPE = """CHAPTER MODE. You are the writing assistant for ONE chapter only: {cid}. The current manuscript of this chapter is shown to you under THIS CHAPTER. You can change it in two ways, always with a fenced block, and the app applies the block to the manuscript automatically:
(A) TARGETED EDIT (use this whenever the author asks to change, fix, reword or adjust something that already exists, e.g. a line of dialogue, a word, a sentence, a paragraph): reply with one or two short Thai sentences saying what you changed, then ONE fenced block ```edit containing ONLY valid JSON: {"edits":[{"find":"EXACT text copied character-for-character from the manuscript, short but unique in the chapter","replace":"the new text"}]}. Use several edits for several places. NEVER rewrite or re-output the whole chapter for a small change, and never put the unchanged text in the block.
(B) NEW TEXT TO ADD AT THE END (when the author asks you to write or continue a scene): one or two short Thai sentences, then ONE fenced block ```draft with the {lang} prose; the app appends it to the end of the manuscript. SCENE QUALITY (applies to every ```draft, especially love, sex and action scenes): write the FULL scene at the length the author asked for - never end early, summarise, fade to black or jump-cut; if no length is given, aim for at least 900 words. Plan the scene as 5-6 beats of roughly equal length (charged talk, first touch, escalation, the peak written slowly and explicitly if the scene is sexual, release, quiet aftermath) and do not stop before the last beat is written. Every beat needs short, broken, in-character dialogue (several lines) and interior thoughts that reveal what each character is afraid to say; let the story's real tension bleed into the scene. Ban cliches (e.g. electric, sweet spot, heart hammered, heart pounded, melted into, a moan escaped, shiver down, breath hitched, time stood still, ozone); use odd, specific details from THIS place (objects, smells, light, texture). Vary sentence length, use fragments at the peak, and end on a quiet specific image with an unresolved emotional hook rather than a summary. LENGTH: write between 100% and 125% of the requested length and never under 90% of it; if you are running short, expand the beats with dialogue, interior thought and concrete detail - do not summarise to finish early. OPENERS: never start more than two consecutive sentences with the same word (especially He, She, The, His); start some sentences with an object, a sound, a line of dialogue, a time or place, or a fragment. Only rewrite the whole chapter if the author explicitly says to rewrite all of it (then use ```draft and say in Thai that it is a full rewrite).
Language: Thai outside blocks, {lang} only inside blocks; never put prose outside blocks. If you are only discussing, answering a question or asking something, use no block.
Stay inside this chapter: follow its summary and target length, continue naturally from the end of the previous chapter, and do not write events that belong to other chapters (see the outline). If the author asks to change other chapters, the overall plot or outline, the characters' setup or the global style, do NOT do it: answer in Thai that this belongs in the overview chat (first tab) or the Outline card. If THIS chapter's title, summary or target length should change, propose it with ONE fenced block ```chapter containing only valid JSON like {"title":"...","summary":"Thai, 1-3 sentences","words":2500} (include only keys that change). Follow the story bible for names, facts, voice and tense. All characters in sexual or violent scenes are adults; never sexualize minors. Otherwise write what is asked without moralizing, at the requested intensity."""

OUTLINE_SCOPE = """OUTLINE MODE (the Outline card). You only discuss and revise the chapter outline of this novel: number of chapters, order, titles, summaries and target lengths. Do not write manuscript prose. Reply with one or two short Thai sentences. Whenever you propose or change the plan, include ONE fenced block ```outline containing ONLY valid JSON: {"chapters":[{"title":"Chapter title in {lang}","summary":"Thai, 1-3 sentences","words":2500}]} with the COMPLETE ordered list of chapters (not only the changes). Chapters marked LOCKED or already written must keep their position and meaning; do not change them unless the author explicitly asks. Follow the story bible. If the author asks about style, characters or setup, answer briefly and say that belongs in the overview chat (first tab)."""


def outline_text(b, draft=None):
    ol, chl = outline(b), {c["id"]: c for c in chapters(b)}
    lines = []
    if draft:
        for i, it in enumerate(draft):
            cid = "ch%02d" % (i + 1)
            c = chl.get(cid, {})
            flag = "LOCKED" if c.get("locked") else ("written %d words" % c.get("words", 0) if c.get("words") else "empty")
            lines.append("%d. %s | %s | target %s words | %s" % (i + 1, it.get("title", ""), it.get("summary", ""), it.get("words", 0), flag))
        return "(DRAFT outline awaiting approval)\n" + "\n".join(lines)
    for cid in sorted(ol):
        c = chl.get(cid, {})
        flag = "LOCKED" if ol[cid].get("locked") else ("written %d words" % c.get("words", 0) if c.get("words") else "empty")
        lines.append("%s | %s | %s | target %s words | %s" % (cid, c.get("title", "?"), ol[cid].get("summary", ""), ol[cid].get("target", 0), flag))
    return "\n".join(lines)


# Anti-repetition (llama.cpp DRY sampler). Tested 27 Oct 2026 on a new chapter after a written one:
# no sampler = 4/12 runs looped (dup 13-55%); DRY = 0/12 (dup <=1.4%, stray CJK <=3). repeat/presence penalty was worse.
ANTI_REPEAT = {"dry_multiplier": 0.8, "dry_base": 1.75, "dry_allowed_length": 2, "dry_penalty_last_n": 1024}


def settings():
    p = os.path.join(ROOT, "_settings.json")
    d = {"system": DEFAULT_SYSTEM, "thinking": False, "max_tokens": 6000, "temperature": 0.8}
    try:
        d.update(json.load(open(p, encoding="utf-8")))
    except Exception:
        pass
    return d


def slugify(t):
    s = re.sub(r"[^A-Za-z0-9]+", "-", t).strip("-").lower()[:40]
    return s or "book-" + time.strftime("%y%m%d%H%M%S")


def bdir(b):
    if not SLUG.match(b):
        raise ValueError("bad book id")
    return os.path.join(ROOT, b)


def read(p, default=""):
    try:
        return open(p, encoding="utf-8").read()
    except FileNotFoundError:
        return default


def write(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    open(tmp, "w", encoding="utf-8").write(s)
    os.replace(tmp, p)


def meta(b):
    try:
        return json.load(open(os.path.join(bdir(b), "book.json"), encoding="utf-8"))
    except Exception:
        return {"title": b, "author": "", "lang": "English"}


def body_words(t):
    # Thai has no spaces between words: count Thai characters / 3.3 (~chars per word), other text by whitespace.
    b = re.sub(r"^\s*#\s+.*\n?", "", t, count=1)
    thai = len(re.findall(r"[฀-๿]", b))
    rest = len(re.sub(r"[฀-๿]", " ", b).split())
    return int(thai / 3.3) + rest


def is_locked(b, cid):
    return bool(outline(b).get(cid, {}).get("locked"))


def outline(b):
    try:
        o = json.load(open(os.path.join(bdir(b), "outline.json"), encoding="utf-8"))
        return o if isinstance(o, dict) else {}
    except Exception:
        return {}


_QT = str.maketrans({"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'", "\u00a0": " "})


def find_unique(text, find):
    """index of the single occurrence of find in text (tolerant to curly quotes / nbsp), or (-1, count)"""
    c = text.count(find)
    if c == 1:
        return text.index(find), 1
    if c > 1:
        return -1, c
    nt, nf = text.translate(_QT), find.translate(_QT)
    c = nt.count(nf)
    if c == 1:
        return nt.index(nf), 1
    return -1, c


def apply_chapter_edits(text, edits, append):
    errs = []
    cur = text
    for i, e in enumerate(edits or []):
        f, r = e.get("find"), e.get("replace")
        if not isinstance(f, str) or not isinstance(r, str) or not f.strip():
            errs.append({"i": i, "why": "รูปแบบไม่ถูกต้อง"})
            continue
        idx, c = find_unique(cur, f)
        if idx < 0:
            errs.append({"i": i, "why": "ไม่พบข้อความเดิมในตอนนี้" if c == 0 else "ข้อความเดิมซ้ำ %d ที่ — ระบุให้เจาะจงกว่านี้" % c})
            continue
        cur = cur[:idx] + r + cur[idx + len(f):]
    if append:
        cur = cur.rstrip() + "\n\n" + str(append).strip() + "\n"
    return cur, errs


def snapshot_chapter(b, cid):
    src = os.path.join(bdir(b), "chapters", cid + ".md")
    if not os.path.exists(src):
        return ""
    sid = "%d" % int(time.time() * 1000)
    write(os.path.join(bdir(b), "history", cid, sid + ".md"), read(src))
    hd = os.path.join(bdir(b), "history", cid)
    fs = sorted(os.listdir(hd))
    for old in fs[:-60]:
        try:
            os.remove(os.path.join(hd, old))
        except OSError:
            pass
    return sid



def chapters(b):
    d = os.path.join(bdir(b), "chapters")
    ol = outline(b)
    out = []
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith(".md"):
                t = read(os.path.join(d, f))
                m = re.match(r"\s*#\s+(.+)", t)
                o = ol.get(f[:-3], {})
                out.append({"id": f[:-3], "title": m.group(1).strip() if m else f[:-3],
                            "words": body_words(t), "mtime": int(os.path.getmtime(os.path.join(d, f))),
                            "summary": o.get("summary", ""), "target": o.get("target", 0), "inoutline": f[:-3] in ol,
                            "locked": bool(o.get("locked"))})
    return out


def list_books():
    out = []
    for n in sorted(os.listdir(ROOT)):
        if os.path.isdir(os.path.join(ROOT, n)) and SLUG.match(n) and os.path.exists(os.path.join(ROOT, n, "book.json")):
            out.append({"id": n, **meta(n)})
    return out


def to_trash_chapter(b, cid):
    src = os.path.join(bdir(b), "chapters", cid + ".md")
    if not os.path.exists(src):
        return None
    dst = os.path.join(bdir(b), "trash", "%s-%d.md" % (cid, int(time.time())))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    return os.path.basename(dst)


def set_heading(text, title):
    if re.match(r"\s*#\s+", text):
        return re.sub(r"^\s*#\s+.*", "# " + title, text, count=1)
    return "# " + title + "\n\n" + text


def apply_outline(b, items):
    """Positional: item i -> ch{i+1:02d}. Never overwrites chapter bodies. Empty chapters that
    were in the old outline but are dropped go to the trash; chapters with text are kept (out of outline)."""
    old = outline(b)
    new, report = {}, []
    for i, it in enumerate(items[:200]):
        cid = "ch%02d" % (i + 1)
        title = str(it.get("title") or "Chapter %d" % (i + 1)).strip()[:120]
        summ = str(it.get("summary") or "").strip()[:1500]
        try:
            tgt = max(0, min(50000, int(float(it.get("words") or it.get("target") or 0))))
        except Exception:
            tgt = 0
        p = os.path.join(bdir(b), "chapters", cid + ".md")
        t = read(p, None)
        if old.get(cid, {}).get("locked"):
            new[cid] = dict(old[cid])
            report.append({"id": cid, "state": "locked-skipped"})
            continue
        if t is None:
            write(p, "# " + title + "\n\n")
            report.append({"id": cid, "state": "created"})
        elif body_words(t) == 0:
            write(p, "# " + title + "\n\n")
            report.append({"id": cid, "state": "retitled-empty"})
        else:
            report.append({"id": cid, "state": "kept-has-text"})
        new[cid] = {"summary": summ, "target": tgt}
    trashed = []
    for cid in old:
        if cid in new or old[cid].get("locked"):
            if cid not in new:
                new[cid] = old[cid]
            continue
        p = os.path.join(bdir(b), "chapters", cid + ".md")
        t = read(p, None)
        if t is not None and body_words(t) == 0:
            to_trash_chapter(b, cid)
            trashed.append(cid)
    write(os.path.join(bdir(b), "outline.json"), json.dumps(new, ensure_ascii=False))
    return {"report": report, "trashed": trashed}


def trash_root():
    return os.path.join(ROOT, "_trash")


def trash_list(cur=None):
    books = []
    tr = trash_root()
    if os.path.isdir(tr):
        for n in sorted(os.listdir(tr)):
            d = os.path.join(tr, n)
            if os.path.isdir(d) and SLUG.match(n):
                try:
                    mt = json.load(open(os.path.join(d, "book.json"), encoding="utf-8"))
                except Exception:
                    mt = {"title": n}
                cd = os.path.join(d, "chapters")
                fs = [f for f in os.listdir(cd) if f.endswith(".md")] if os.path.isdir(cd) else []
                words = sum(len(read(os.path.join(cd, f)).split()) for f in fs)
                books.append({"tid": n, "title": mt.get("title", n), "chapters": len(fs), "words": words, "at": int(os.path.getmtime(d))})
    chs = []
    if cur and SLUG.match(cur) and os.path.isdir(os.path.join(bdir(cur), "trash")):
        td = os.path.join(bdir(cur), "trash")
        for f in sorted(os.listdir(td)):
            if f.endswith(".md"):
                t = read(os.path.join(td, f))
                m = re.match(r"\s*#\s+(.+)", t)
                chs.append({"file": f, "title": m.group(1).strip() if m else f, "words": len(t.split()), "at": int(os.path.getmtime(os.path.join(td, f)))})
    return {"books": books, "chapters": chs}


def new_chapter_id(b):
    ids = [c["id"] for c in chapters(b)]
    n = 1
    while "ch%02d" % n in ids:
        n += 1
    return "ch%02d" % n


def build_messages(body):
    b = body.get("book")
    scope = str(body.get("scope") or "overview")
    st = settings()
    m = meta(b) if b else {"lang": "English"}
    lang = m.get("lang", "English")
    is_ch = bool(SLUG.match(scope)) and scope.startswith("ch") and scope not in ("outline", "overview")
    if scope == "outline":
        system = OUTLINE_SCOPE.replace("{lang}", lang)
    elif is_ch:
        system = CHAPTER_SCOPE.replace("{lang}", lang).replace("{cid}", scope)
    else:
        scope = "overview"
        system = st["system"].replace("{lang}", lang) + OUTLINE_RULES.replace("{lang}", lang)
    ctx_chars = int(CTX * 2.2)
    budget = ctx_chars - len(system) - st["max_tokens"] * 3
    extra = ""
    if b and body.get("use_bible", True):
        bible = read(os.path.join(bdir(b), "bible.md"))[:10000]
        if bible.strip():
            extra += "\n\n=== STORY BIBLE ===\n" + bible
    if b:
        draft = None
        if scope == "outline":
            try:
                draft = json.load(open(os.path.join(bdir(b), "outline_draft.json"), encoding="utf-8")).get("items")
            except Exception:
                draft = None
        ot = outline_text(b, draft)
        if ot.strip():
            extra += "\n\n=== CHAPTER OUTLINE ===\n" + ot[:6000]
    ch = scope if is_ch else body.get("chapter")
    if b and is_ch:
        n = int(scope[2:] or 0) if scope[2:].isdigit() else 0
        c = {x["id"]: x for x in chapters(b)}.get(scope, {})
        extra += "\n\n=== THIS CHAPTER: %s | title: %s | summary: %s | target %s words%s ===" % (scope, c.get("title", "?"), c.get("summary", ""), c.get("target", 0), " | LOCKED" if c.get("locked") else "")
        if n > 1:
            prev = read(os.path.join(bdir(b), "chapters", "ch%02d.md" % (n - 1)))
            if prev.strip():
                extra += "\n\n=== END OF PREVIOUS CHAPTER (for continuity only) ===\n" + prev[-1800:]
    if b and ch and SLUG.match(ch) and body.get("use_chapter", True) and scope != "outline":
        t = read(os.path.join(bdir(b), "chapters", ch + ".md"))
        if t.strip():
            if len(t) > 20000:
                t = "[...earlier part omitted...]\n" + t[-20000:]
            extra += "\n\n=== CURRENT CHAPTER (manuscript so far) ===\n" + t
    budget -= len(extra)
    msgs = [x for x in body.get("messages", []) if x.get("role") in ("user", "assistant")]
    keep, used = [], 0
    for x in reversed(msgs):
        used += len(x.get("content", ""))
        if used > max(budget, 4000) and keep:
            break
        keep.append(x)
    keep.reverse()
    return [{"role": "system", "content": system + extra}] + keep


# device presets: (page size, margins, base font pt, line-height)
CSS = {
    "iphone": ("90mm 160mm", "8mm 7mm 11mm 7mm", 14.0, 1.8),
    "ipad": ("148mm 210mm", "12mm 11mm 14mm 11mm", 13.0, 1.8),
    "a4": ("A4", "25mm 22mm 25mm 22mm", 13.0, 1.75),
}
ALIAS = {"mobile": "iphone", "novel": "a4"}      # old links keep working
FS_STEP = {"s": -1.5, "m": 0.0, "l": 1.5, "xl": 3.0}   # font size steps
LH_ADD = {"n": 0.0, "w": 0.2}                         # normal / wide line spacing


THAI_RUN = re.compile(r'[\u0E00-\u0E7F]+(?:[ \u00A0"\u201c\u201d\u2018\u2019\'.,!?\u2026()\-]*[\u0E00-\u0E7F]+)*')


def wrap_thai(html):
    """Thai glyphs look ~12% smaller than Latin at the same pt: wrap Thai runs (outside tags) so CSS can enlarge them."""
    parts = re.split(r"(<[^>]+>)", html)
    return "".join(p if p.startswith("<") else THAI_RUN.sub(lambda m: "<span class='th'>" + m.group(0) + "</span>", p) for p in parts)


def make_pdf(b, size, only=None, fs="m", lh="n"):
    only = [x for x in (only or []) if re.match(r"^ch\d+$", x)] or None
    import markdown as md
    from weasyprint import HTML
    size = ALIAS.get(size, size)
    pg, mg, base_fs, base_lh = CSS.get(size, CSS["a4"])
    fs_pt = base_fs + FS_STEP.get(fs, 0.0)
    lh_v = round(base_lh + LH_ADD.get(lh, 0.0), 2)
    m = meta(b)
    parts = []
    for c in chapters(b):
        if only and c["id"] not in only:
            continue
        t = read(os.path.join(bdir(b), "chapters", c["id"] + ".md"))
        t = re.sub(r"^\s*(\*\s*\*\s*\*|---+|\*\*\*+)\s*$", "\n<div class='sb'>&#10059;</div>\n", t, flags=re.M)
        parts.append("<section class='ch'>" + wrap_thai(md.markdown(t, extensions=["extra"])) + "</section>")
    title = (m.get("title") or b).replace("<", "&lt;")
    author = (m.get("author") or "").replace("<", "&lt;")
    html = f"""<html><head><meta charset='utf-8'><style>
@page {{ size:{pg}; margin:{mg}; @bottom-center {{ content: counter(page); font-size:8.5pt; color:#555 }} }}
@page :first {{ @bottom-center {{ content: '' }} }}
body {{ font-family:'Noto Sans Thai','Noto Sans','Sarabun','Loma','DejaVu Sans',sans-serif; font-size:{fs_pt}pt; line-height:{lh_v}; text-align:left; hyphens:none; overflow-wrap:break-word; }}
.title {{ page-break-after:always; text-align:center; padding-top:30%; }}
.title h1 {{ font-size:2.2em; margin:0 0 .4em; letter-spacing:.04em }} .title p {{ font-size:1.1em; color:#444 }}
section.ch {{ page-break-before:always; }}
section.ch h1 {{ text-align:center; font-size:1.5em; margin:18% 0 1.4em; page-break-after:avoid; }}
p {{ margin:0 0 .35em; text-indent:1.3em; }} h1 + p, .sb + p {{ text-indent:0 }}
.sb {{ text-align:center; margin:.9em 0; color:#666; text-indent:0 }}
blockquote {{ margin:.6em 1.2em; font-style:italic }}
.th {{ font-size:1.12em }}
</style></head><body>
{'' if only and len(only) == 1 else f"<div class='title'><h1>{title}</h1><p>{author}</p></div>"}{''.join(parts)}</body></html>"""
    return HTML(string=html).write_pdf()


def llm_get(path, timeout=5):
    with urllib.request.urlopen(LLM + path, timeout=timeout) as r:
        return r.status


INDEX = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"), encoding="utf-8").read() \
    if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")) else "<h1>index.html missing</h1>"


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "NovelStudio"

    def log_message(self, *a):
        pass

    def _auth(self):
        if not PASSWORD:
            return True
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                if base64.b64decode(h[6:]).decode().split(":", 1)[1] == PASSWORD:
                    return True
            except Exception:
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Novel Studio"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _send(self, code, data, ctype="application/json", extra=None):
        if not isinstance(data, (bytes, bytearray)):
            data = json.dumps(data, ensure_ascii=False).encode("utf-8") if ctype == "application/json" else str(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") or ctype == "application/json" else ""))
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def do_GET(self):
        if not self._auth():
            return
        u = urlparse(self.path)
        p = [x for x in u.path.split("/") if x]
        try:
            if not p:
                return self._send(200, INDEX, "text/html")
            if p == ["api", "status"]:
                try:
                    llm_get("/health")
                    ok = True
                except Exception:
                    ok = False
                return self._send(200, {"llm": ok, "ctx": CTX, "settings": settings()})
            if p == ["api", "books"]:
                return self._send(200, list_books())
            if p == ["api", "trash"]:
                cur = parse_qs(u.query).get("book", [""])[0]
                return self._send(200, trash_list(cur))
            if len(p) >= 3 and p[:2] == ["api", "books"]:
                b = p[2]
                if len(p) == 3:
                    return self._send(200, {"id": b, "meta": meta(b), "bible": read(os.path.join(bdir(b), "bible.md")),
                                            "chapters": chapters(b), "outline": outline(b), "draft": (json.loads(read(os.path.join(bdir(b), "outline_draft.json"), "null") or "null") or {}).get("items"), "setup": json.loads(read(os.path.join(bdir(b), "setup.json"), "{}") or "{}"), "chat": json.loads(read(os.path.join(bdir(b), "chat.json"), "[]") or "[]")})
                if p[3] == "chapters" and len(p) == 5:
                    return self._send(200, {"id": p[4], "text": read(os.path.join(bdir(b), "chapters", p[4] + ".md"))} if SLUG.match(p[4]) else {})
                if p[3] == "chat" and len(p) == 5 and SLUG.match(p[4]):
                    return self._send(200, json.loads(read(os.path.join(bdir(b), "chats", p[4] + ".json"), "[]") or "[]"))
                if p[3] == "export.pdf":
                    q = parse_qs(u.query)
                    only = q.get("ch", [""])[0].split(",") if q.get("ch", [""])[0] else None
                    size = ALIAS.get(q.get("size", ["a4"])[0], q.get("size", ["a4"])[0])
                    size = size if size in CSS else "a4"
                    fsq, lhq = q.get("fs", ["m"])[0], q.get("lh", ["n"])[0]
                    fsq = fsq if fsq in FS_STEP else "m"
                    lhq = lhq if lhq in LH_ADD else "n"
                    pdf = make_pdf(b, size, only, fsq, lhq)
                    suffix = ("-" + "-".join(only)) if only and all(re.match(r"^ch\d+$", x) for x in only) else ""
                    return self._send(200, pdf, "application/pdf", {"Content-Disposition": f'attachment; filename="{b}{suffix}-{size}.pdf"'})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def do_PUT(self):
        if not self._auth():
            return
        p = [x for x in urlparse(self.path).path.split("/") if x]
        try:
            d = self._body()
            if p == ["api", "settings"]:
                cur = settings()
                cur.update({k: d[k] for k in ("system", "thinking", "max_tokens", "temperature") if k in d})
                write(os.path.join(ROOT, "_settings.json"), json.dumps(cur, ensure_ascii=False, indent=1))
                return self._send(200, {"ok": True})
            if len(p) >= 4 and p[:2] == ["api", "books"]:
                b = p[2]
                if p[3] == "bible":
                    write(os.path.join(bdir(b), "bible.md"), d.get("text", ""))
                    return self._send(200, {"ok": True})
                if p[3] == "setup":
                    write(os.path.join(bdir(b), "setup.json"), json.dumps(d.get("setup", {}), ensure_ascii=False))
                    if d.get("bible") is not None:
                        write(os.path.join(bdir(b), "bible.md"), d["bible"])
                    return self._send(200, {"ok": True})
                if p[3] == "meta":
                    m = meta(b)
                    m.update({k: d[k] for k in ("title", "author", "lang") if k in d})
                    write(os.path.join(bdir(b), "book.json"), json.dumps(m, ensure_ascii=False))
                    return self._send(200, {"ok": True})
                if p[3] == "outline" and len(p) == 4:
                    items = d.get("chapters")
                    if not isinstance(items, list) or not items:
                        return self._send(400, {"error": "chapters list required"})
                    return self._send(200, apply_outline(b, [x for x in items if isinstance(x, dict)]))
                if p[3] == "outline" and len(p) == 5 and SLUG.match(p[4]):
                    o = outline(b)
                    cur = o.get(p[4], {})
                    if cur.get("locked") and "locked" not in d:
                        return self._send(423, {"error": "ตอนนี้ล็อกอยู่"})
                    if "summary" in d:
                        cur["summary"] = str(d["summary"])[:1500]
                    if "target" in d:
                        try:
                            cur["target"] = max(0, min(50000, int(float(d["target"] or 0))))
                        except Exception:
                            cur["target"] = 0
                    if "locked" in d:
                        cur["locked"] = bool(d["locked"])
                    cur.setdefault("summary", ""); cur.setdefault("target", 0)
                    o[p[4]] = cur
                    write(os.path.join(bdir(b), "outline.json"), json.dumps(o, ensure_ascii=False))
                    if d.get("title"):
                        cp = os.path.join(bdir(b), "chapters", p[4] + ".md")
                        if os.path.exists(cp):
                            write(cp, set_heading(read(cp), str(d["title"]).strip()[:120]))
                    return self._send(200, {"ok": True})
                if p[3] == "chat":
                    write(os.path.join(bdir(b), "chat.json"), json.dumps(d.get("messages", []), ensure_ascii=False))
                    return self._send(200, {"ok": True})
                if p[3] == "chapters" and len(p) == 5 and SLUG.match(p[4]):
                    if is_locked(b, p[4]):
                        return self._send(423, {"error": "ตอนนี้ล็อกอยู่ — ปลดล็อกก่อนแก้"})
                    write(os.path.join(bdir(b), "chapters", p[4] + ".md"), d.get("text", ""))
                    return self._send(200, {"ok": True})
                if p[3] == "outline-draft" and len(p) == 4:
                    fp = os.path.join(bdir(b), "outline_draft.json")
                    items = d.get("items")
                    if items is None:
                        if os.path.exists(fp):
                            os.remove(fp)
                    else:
                        clean = [{"title": str(x.get("title", ""))[:120], "summary": str(x.get("summary", ""))[:1500], "words": int(float(x.get("words") or 0)) if str(x.get("words") or "0").replace(".", "", 1).isdigit() else 0} for x in items if isinstance(x, dict)][:200]
                        write(fp, json.dumps({"items": clean}, ensure_ascii=False))
                    return self._send(200, {"ok": True})
                if p[3] == "chat" and len(p) == 5 and SLUG.match(p[4]):
                    write(os.path.join(bdir(b), "chats", p[4] + ".json"), json.dumps(d.get("messages", []), ensure_ascii=False))
                    return self._send(200, {"ok": True})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def do_DELETE(self):
        if not self._auth():
            return
        p = [x for x in urlparse(self.path).path.split("/") if x]
        try:
            if len(p) == 5 and p[:2] == ["api", "books"] and p[3] == "chapters" and SLUG.match(p[4]):
                if is_locked(p[2], p[4]):
                    return self._send(423, {"error": "ตอนนี้ล็อกอยู่ — ปลดล็อกก่อนลบ"})
                name = to_trash_chapter(p[2], p[4])  # moved to trash, never deleted
                o = outline(p[2])
                if p[4] in o:
                    o.pop(p[4])
                    write(os.path.join(bdir(p[2]), "outline.json"), json.dumps(o, ensure_ascii=False))
                return self._send(200, {"ok": True, "trash": name})
            if len(p) == 3 and p[:2] == ["api", "books"] and SLUG.match(p[2]):
                src = bdir(p[2])  # whole book -> _trash (recoverable)
                if not os.path.isdir(src):
                    return self._send(404, {"error": "no such book"})
                os.makedirs(trash_root(), exist_ok=True)
                tid = "%s-%d" % (p[2], int(time.time()))
                shutil.move(src, os.path.join(trash_root(), tid))
                return self._send(200, {"ok": True, "tid": tid})
            if len(p) == 4 and p[:3] == ["api", "trash", "book"] and SLUG.match(p[3]):
                d = os.path.join(trash_root(), p[3])
                if not os.path.isdir(d):
                    return self._send(404, {"error": "not in trash"})
                shutil.rmtree(d)  # permanent, user-initiated from the trash tab
                return self._send(200, {"ok": True})
            if len(p) == 5 and p[:3] == ["api", "trash", "chapter"] and SLUG.match(p[3]) and re.match(r"^[A-Za-z0-9_-]+\.md$", p[4]):
                f = os.path.join(bdir(p[3]), "trash", p[4])
                if not os.path.isfile(f):
                    return self._send(404, {"error": "not in trash"})
                os.remove(f)
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def do_POST(self):
        if not self._auth():
            return
        p = [x for x in urlparse(self.path).path.split("/") if x]
        try:
            d = self._body()
            if p == ["api", "books"]:
                b = slugify(d.get("title", ""))
                while os.path.exists(bdir(b)):
                    b += "-2"
                write(os.path.join(bdir(b), "book.json"), json.dumps({"title": d.get("title", b), "author": d.get("author", ""), "lang": d.get("lang", "English")}, ensure_ascii=False))
                write(os.path.join(bdir(b), "bible.md"), "# Story bible\n\n(characters, world, voice, tense, rules)\n")
                return self._send(200, {"id": b})
            if len(p) == 4 and p[:2] == ["api", "books"] and p[3] == "chapters":
                b = p[2]
                cid = new_chapter_id(b)
                title = d.get("title") or "Chapter " + cid[2:]
                write(os.path.join(bdir(b), "chapters", cid + ".md"), d.get("text") or "# " + title + "\n\n")
                return self._send(200, {"id": cid})
            if len(p) == 6 and p[:2] == ["api", "books"] and p[3] == "chapters" and p[5] in ("edit", "undo") and SLUG.match(p[2]) and re.match(r"^ch\d+$", p[4]):
                b, cid = p[2], p[4]
                fp = os.path.join(bdir(b), "chapters", cid + ".md")
                if not os.path.exists(fp):
                    return self._send(404, {"error": "ไม่มีตอนนี้"})
                if is_locked(b, cid):
                    return self._send(423, {"error": "ตอนนี้ล็อกอยู่ — ปลดล็อกก่อนแก้"})
                if p[5] == "undo":
                    sid = str(d.get("snap", ""))
                    if not re.match(r"^\d+$", sid):
                        return self._send(400, {"error": "bad snap"})
                    sp = os.path.join(bdir(b), "history", cid, sid + ".md")
                    if not os.path.exists(sp):
                        return self._send(404, {"error": "ไม่พบสำเนาสำรอง"})
                    snapshot_chapter(b, cid)
                    t = read(sp)
                    write(fp, t)
                    return self._send(200, {"text": t})
                text = read(fp)
                new, errs = apply_chapter_edits(text, d.get("edits") or [], d.get("append") or "")
                if errs:
                    return self._send(422, {"error": "; ".join("รายการ %d: %s" % (e["i"] + 1, e["why"]) for e in errs), "errors": errs})
                if new == text:
                    return self._send(200, {"text": text, "snap": "", "changed": False})
                sid = snapshot_chapter(b, cid)
                write(fp, new)
                return self._send(200, {"text": new, "snap": sid, "changed": True})
            if p == ["api", "trash", "restore"]:
                if d.get("kind") == "book" and SLUG.match(str(d.get("tid", ""))):
                    src = os.path.join(trash_root(), d["tid"])
                    if not os.path.isdir(src):
                        return self._send(404, {"error": "not in trash"})
                    base = re.sub(r"-\d{9,}$", "", d["tid"]) or "book"
                    b = base
                    while os.path.exists(bdir(b)):
                        b += "-r"
                    shutil.move(src, bdir(b))
                    return self._send(200, {"ok": True, "id": b})
                if d.get("kind") == "chapter" and SLUG.match(str(d.get("book", ""))) and re.match(r"^[A-Za-z0-9_-]+\.md$", str(d.get("file", ""))):
                    src = os.path.join(bdir(d["book"]), "trash", d["file"])
                    if not os.path.isfile(src):
                        return self._send(404, {"error": "not in trash"})
                    orig = re.sub(r"-\d{9,}\.md$", "", d["file"])
                    cid = orig if SLUG.match(orig) and not os.path.exists(os.path.join(bdir(d["book"]), "chapters", orig + ".md")) else new_chapter_id(d["book"])
                    os.makedirs(os.path.join(bdir(d["book"]), "chapters"), exist_ok=True)
                    shutil.move(src, os.path.join(bdir(d["book"]), "chapters", cid + ".md"))
                    return self._send(200, {"ok": True, "id": cid})
                return self._send(400, {"error": "bad request"})
            if p == ["api", "outline", "suggest"]:
                return self.suggest(d)
            if p == ["api", "chat"]:
                return self.chat(d)
            if p == ["api", "refine"]:
                return self.refine(d)
            if p == ["api", "assist"]:
                return self.assist(d)
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def refine(self, d):
        prompt = ("Below is a story setup assembled from random and hand-picked cards. Some choices may clash. "
                  "Act as a story editor. Do two things:\n1) Write a cohesive SYNTHESIS in English (250-350 words): who the main characters are to each other, "
                  "what the central situation is, how the setting, conflict and tone fit together, and 3-5 key story beats. Resolve clashes by inventing minimal connecting details; do not contradict any chosen card.\n"
                  "2) Then list in Thai (short bullet lines) any clashes you found and how you resolved them.\n"
                  "Output exactly in this format:\n###SYNTH###\n<english synthesis>\n###NOTES###\n<thai notes>\n\n=== SETUP ===\n" + str(d.get("bible", ""))[:12000])
        payload = {"messages": [{"role": "system", "content": "You are a precise story editor. Adults only in any mature content; never involve minors."}, {"role": "user", "content": prompt}],
                   "stream": False, "max_tokens": 1800, "temperature": 0.7, "chat_template_kwargs": {"enable_thinking": False}}
        req = urllib.request.Request(LLM + "/v1/chat/completions", json.dumps(payload).encode(), {"Content-Type": "application/json"})
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=300).read().decode("utf-8"))
            txt = r["choices"][0]["message"]["content"]
        except Exception as e:
            return self._send(502, {"error": "LLM unavailable: %s" % e})
        synth, notes = txt, ""
        if "###SYNTH###" in txt:
            body = txt.split("###SYNTH###", 1)[1]
            synth, _, notes = body.partition("###NOTES###")
        return self._send(200, {"synth": synth.strip(), "notes": notes.strip()})

    def suggest(self, d):
        b, cid = d.get("book", ""), d.get("id", "")
        if not (SLUG.match(b) and SLUG.match(cid)):
            return self._send(400, {"error": "bad ids"})
        ol, chl = outline(b), {c["id"]: c for c in chapters(b)}
        lines = ["%s | %s | %s" % (k, chl.get(k, {}).get("title", "?"), ol[k].get("summary", "")) for k in sorted(ol)]
        prompt = ("OUTLINE SUMMARY. Story bible:\n" + read(os.path.join(bdir(b), "bible.md"))[:6000] + "\n\nCurrent chapter outline (id | title | summary):\n" + "\n".join(lines)[:4000] +
                  "\n\nWrite a NEW summary for chapter %s (\"%s\"): what happens, 1-3 sentences, in THAI, consistent with the neighbouring chapters and the bible. %s Output only the summary text." % (cid, chl.get(cid, {}).get("title", cid), str(d.get("hint", ""))[:500]))
        payload = {"messages": [{"role": "system", "content": "You are a story editor. Adults only in mature content; never involve minors."}, {"role": "user", "content": prompt}],
                   "stream": False, "max_tokens": 400, "temperature": 0.9, "chat_template_kwargs": {"enable_thinking": False}}
        req = urllib.request.Request(LLM + "/v1/chat/completions", json.dumps(payload).encode(), {"Content-Type": "application/json"})
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=300).read().decode("utf-8"))
            return self._send(200, {"summary": r["choices"][0]["message"]["content"].strip()})
        except Exception as e:
            return self._send(502, {"error": "LLM unavailable: %s" % e})

    def assist(self, d):
        sysmsg = ("ASSIST MODE. You are the story-setup assistant inside a Thai author's novel studio. Talk to the author in THAI, short and warm. "
                  "Goal: agree on the story setup (genre, tone, setting, protagonist, partner/love interest, antagonist, plot, style, outfits, locations) and fill in the details so the author does not have to click each field.\n"
                  "Rules:\n1) If key specs are missing, ask at most 2 short questions. If the author gives enough, or says to decide for them, propose right away.\n"
                  "2) To propose, write one or two Thai sentences, then ONE fenced block exactly like ```proposal\n{json}\n``` containing ONLY valid JSON (no comments, no trailing commas).\n"
                  "JSON shape: {\"top\":{\"genre\":\"detective mystery, noir\",...},\"characters\":[{\"name\":\"Mara Voss\",\"fields\":{\"role\":\"protagonist\",\"age\":\"34\",...},\"outfits\":[{\"name\":\"Work coat\",\"fields\":{\"ostyle\":\"trench coat\"}}]}],\"locations\":[{\"name\":\"Harbour office\",\"fields\":{\"ltype\":\"office\"}}]}.\n"
                  "3) Use only the field keys listed below. Values are short ENGLISH phrases (comma separated for several). Include only fields you want to set or change.\n"
                  "4) NEVER include anything listed as LOCKED. Work around locked items consistently.\n"
                  "5) All characters are adults (18+). Never involve minors.\n6) Keep consistent with CURRENT SETUP; do not contradict chosen items unless the author asks.\n"
                  "7) Keep every proposal SMALL so it fits in the reply: at most 3 characters and 2 locations per turn, at most 1 outfit per character, short phrases. Propose the rest in later turns, and always close the JSON and the fence.\n"
                  "8) NOTHING is created unless you send the proposal block. Whenever the author asks to add or change a character, location, outfit or any setup field, or agrees to a plan you suggested, put the proposal block in THAT SAME reply. Never answer only 'OK' or 'will do'.\n"
                  "\nFIELD KEYS:\n" + str(d.get("spec", ""))[:4000] +
                  "\n\nLOCKED: " + str(d.get("locked", ""))[:1500] +
                  "\n\nCURRENT SETUP:\n" + str(d.get("state", ""))[:9000])
        msgs = [{"role": "system", "content": sysmsg}]
        # History hygiene (tested 28 Oct 2026 on a real book: replacing old proposals with a marker made the model print the marker instead of
        # proposing: 0/8 replies had a proposal; dropping only bad messages and keeping complete proposals intact: 8/8).
        closed_re = re.compile(r"```proposal\s*\n[\s\S]*?\n\s*```")
        for m in (d.get("messages") or [])[-16:]:
            if m.get("role") not in ("user", "assistant") or not str(m.get("content", "")).strip():
                continue
            c = str(m["content"])
            if m["role"] == "assistant":
                # skip UI error placeholders, markers left by older versions, and truncated proposals: the model copies all of these
                if c.startswith("[ใช้ไม่ได้") or "[earlier proposal omitted]" in c or ("```proposal" in c and not closed_re.search(c)):
                    continue
                msgs.append({"role": "assistant", "content": c[:14000]})
            else:
                msgs.append({"role": "user", "content": c[:6000]})
        # keep context bounded: at most the 3 newest complete proposals stay in history (older ones are dropped whole, never replaced by a marker)
        seen = 0
        for i in range(len(msgs) - 1, 0, -1):
            if msgs[i]["role"] == "assistant" and "```proposal" in msgs[i]["content"]:
                seen += 1
                if seen > 3:
                    del msgs[i]
        # 3500 tokens ~ 80 s: stays under the ~100 s proxy limit for non-streaming calls (2200 cut real proposals; tested 28 Oct 2026)
        payload = {"messages": msgs, "stream": False, "max_tokens": 3500, "temperature": 0.8, "chat_template_kwargs": {"enable_thinking": False}}
        req = urllib.request.Request(LLM + "/v1/chat/completions", json.dumps(payload).encode(), {"Content-Type": "application/json"})
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=300).read().decode("utf-8"))
            txt = r["choices"][0]["message"]["content"]
        except Exception as e:
            return self._send(502, {"error": "LLM unavailable: %s" % e})
        txt = txt.strip()
        m = re.search(r"```proposal\s*\n", txt)
        if m and "```" not in txt[m.end():]:
            # unclosed proposal = cut off; the UI would show "invalid proposal", so replace it with a clear instruction
            txt = (txt[:m.start()].strip() + "\n\n⚠ ข้อเสนอยาวเกินจนถูกตัด ลองสั่งให้เสนอทีละส่วน เช่น 'เสนอเฉพาะตัวละครหลัก 2 คนก่อน' แล้วค่อยขอส่วนที่เหลือ").strip()
        elif not m and "?" not in txt and "？" not in txt and "ไหม" not in txt[-120:] and "หรือไม่" not in txt[-120:]:
            # a statement with no proposal and no question: the author may think something was created, but nothing was
            txt += "\n\n⚠ ข้อความนี้ไม่มีข้อเสนอ จึงยังไม่มีอะไรถูกสร้างหรือแก้ พิมพ์ว่า 'ส่งข้อเสนอเลย' เพื่อให้ผู้ช่วยส่งข้อเสนอให้กดใช้"
        return self._send(200, {"reply": txt})

    def chat(self, d):
        st = settings()
        mt = st["max_tokens"]
        if str(d.get("scope", "")).startswith("ch"):
            mt = max(mt, 6000)
        payload = {"messages": build_messages(d), "stream": True, "max_tokens": mt, "temperature": st["temperature"],
                   "chat_template_kwargs": {"enable_thinking": bool(d.get("thinking", st["thinking"]))}}
        payload.update(ANTI_REPEAT)
        req = urllib.request.Request(LLM + "/v1/chat/completions", json.dumps(payload).encode(), {"Content-Type": "application/json"})
        try:
            r = urllib.request.urlopen(req, timeout=600)
        except Exception as e:
            return self._send(502, {"error": "LLM unavailable: %s" % e})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            while True:
                line = r.readline()
                if not line:
                    break
                chunk = line
                self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            r.close()


if __name__ == "__main__":
    print("Novel Studio on :%d root=%s llm=%s" % (PORT, ROOT, LLM), flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
