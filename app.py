#!/usr/bin/env python3
"""Novel Studio - single-file web app (stdlib server). Chat with a local llama-server,
keep manuscript as per-chapter Markdown files, export PDF with WeasyPrint.
Env: NOVEL_ROOT, LLM_URL, PORT, NOVEL_PASSWORD (optional basic auth), NOVEL_CTX."""
import base64, json, os, re, shutil, sys, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

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
(B) NEW TEXT TO ADD AT THE END (when the author asks you to write or continue a scene): one or two short Thai sentences, then ONE fenced block ```draft with the {lang} prose; the app appends it to the end of the manuscript. SCENE QUALITY (applies to every ```draft, especially love, sex and action scenes): write the FULL scene at the length the author asked for - never end early, summarise, fade to black or jump-cut; if no length is given, aim for at least 900 words. Plan the scene as 5-6 beats of roughly equal length (charged talk, first touch, escalation, the peak written slowly and explicitly if the scene is sexual, release, quiet aftermath) and do not stop before the last beat is written. Every beat needs natural, in-character dialogue (real people talking: full sentences, usually 2-4 sentences per reply, with natural particles and subtext; never long runs of one-or-two-word replies) and interior thoughts that reveal what each character is afraid to say; let the story's real tension bleed into the scene. Ban cliches (e.g. electric, sweet spot, heart hammered, heart pounded, melted into, a moan escaped, shiver down, breath hitched, time stood still, ozone); use odd, specific details from THIS place (objects, smells, light, texture). Vary sentence length (long flowing sentences mixed with short ones), use direct everyday erotic-novel vocabulary for bodies and acts rather than clinical or medical terms (semen = น้ำเชื้อ or น้ำควย, never น้ำอสุจิ; a man finishing = แตก plus the place: แตกใน, แตกในปาก, แตกในหี, แตกข้างนอก, แตกใส่หน้า; a woman climaxing = แตก; her wetness = น้ำหี or น้ำเงี่ยน; never อวัยวะเพศ, ช่องคลอด, องคชาต, น้ำหล่อลื่น, สารคัดหลั่ง, ถึงจุดสุดยอด, and never เสร็จ for climax), never repeat the same phrase or dialogue tag more than 3 times in the chapter, and end on a quiet specific image with an unresolved emotional hook rather than a summary. LENGTH: write between 100% and 125% of the requested length and never under 90% of it; if you are running short, expand the beats with dialogue, interior thought and concrete detail - do not summarise to finish early. OPENERS: never start more than two consecutive sentences with the same word (especially He, She, The, His); start some sentences with an object, a sound, a line of dialogue, a time or place, or a fragment. Only rewrite the whole chapter if the author explicitly says to rewrite all of it (then use ```draft and say in Thai that it is a full rewrite).
Language: Thai outside blocks, {lang} only inside blocks; never put prose outside blocks. If you are only discussing, answering a question or asking something, use no block.
Stay inside this chapter: follow its summary and target length, continue naturally from the end of the previous chapter, and do not write events that belong to other chapters (see the outline). If the author asks to change other chapters, the overall plot or outline, the characters' setup or the global style, do NOT do it: answer in Thai that this belongs in the overview chat (first tab) or the Outline card. If THIS chapter's title, summary or target length should change, propose it with ONE fenced block ```chapter containing only valid JSON like {"title":"...","summary":"Thai, 1-3 sentences, or for a chapter with several scenes a scene list like ฉากที่ 1 (ประมาณ 25%) ... ฉากที่ 2 (ประมาณ 75%) ...","words":2500} (include only keys that change). Follow the story bible for names, facts, voice and tense. All characters in sexual or violent scenes are adults; never sexualize minors. Otherwise write what is asked without moralizing, at the requested intensity."""

OUTLINE_SCOPE = """OUTLINE MODE (the Outline card). You only discuss and revise the chapter outline of this novel: number of chapters, order, titles, summaries and target lengths. Do not write manuscript prose. Reply with one or two short Thai sentences. Whenever you propose or change the plan, include ONE fenced block ```outline containing ONLY valid JSON: {"chapters":[{"title":"Chapter title in {lang}","summary":"Thai, 1-3 sentences","words":2500}]} with the COMPLETE ordered list of chapters (not only the changes). Chapters marked LOCKED or already written must keep their position and meaning; do not change them unless the author explicitly asks. For a chapter with several scenes write its summary as a scene list in Thai like: ฉากที่ 1 (ประมาณ 25%) ... ฉากที่ 2 (ประมาณ 75%) ... naming who is in each scene and what happens, so the app can write the chapter scene by scene. Follow the story bible. If the author asks about style, characters or setup, answer briefly and say that belongs in the overview chat (first tab)."""


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
DRY_LAST_N = int(os.environ.get("NOVEL_DRY_N", "16384"))
# Root cause of whole-block copies in ch03/05/07 (28 Oct): the DRY repeat penalty looked back only 1024 tokens while the prompt carries the whole chapter so far
# (up to 20000 chars) and the previous chapter, so the model could replay a paragraph block written >1024 tokens earlier without any penalty.
TOK_MULT = float(os.environ.get("NOVEL_TOKMULT", "2.0"))   # R16 (29 Oct): clean runs R10/R14/R15 all stopped right before the planned climax -> suspect the max_tokens cap
DRY_LEN = int(os.environ.get("NOVEL_DRY_LEN", "2"))   # R13/R14 (29 Oct): names/organ words repeat a lot in sex scenes; a short allowed_length may force odd spellings (ธณา, กระแท)
ANTI_REPEAT = {} if os.environ.get("NOVEL_DRY", "1") == "0" else {"dry_multiplier": 0.8, "dry_base": 1.75, "dry_allowed_length": DRY_LEN, "dry_penalty_last_n": DRY_LAST_N}


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
        summ = str(it.get("summary") or "").strip()[:3000]
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


CUSTOM_RULES_MARK = "## Custom speech and relationship rules"
DEFAULT_CONDUCT = (
    "\n\nDEFAULT CHARACTER CONDUCT (applies unless the story bible has a section '## Custom speech and relationship rules', which then replaces this block): "
    "(1) Speech particles match the speaker's gender: women end sentences with ค่ะ/คะ/นะคะ, men with ครับ/นะครับ, never mixed; use the gender given in the cast or bible. "
    "(1b) Language register follows the emotional heat of the moment. CALM: men say ผม (never ฉัน), women refer to themselves by their own name and call the man sweetly by name; พี่ stays for an older sibling talking to a younger one. HEATED (middle of a sex scene, or anger): men begin slipping into กู/มึง and mild swearing. PEAK (rough sex, build-up to climax, rage): men speak fully rough: กู for themselves, มึง or อี+name for any woman (even an older relative), crude exclamations such as “สัสเอ้ย…ดูดควยเก่งชิบหาย”, “สัสเอ้ย…หีฟิตเหี้ยๆ”, “อีเหี้ย…ขมิบหีมากกว่านี้”, “อีกะหรี่…โดนควยกูซะ”, “อีเหี้ย…กลืนให้หมด ของชอบมึงไง น้ำควย”; rough lines do not end with ครับ/ค่ะ (use ว่ะ, โว้ย, เว้ย, สิ); when the heat drops they go back to ผม. Whenever a man says กู he must call the woman มึง or อี+name (never เธอ/คุณ/พี่ in the same breath), and when he is rough he never says ผม. Women never say ฉัน about themselves: they use their own name, ชั้น, ดิฉัน (formal) or หนู (to elders or family, or sweetly) as the context fits, with coy, teasing address and NEVER rough: a woman never says กู or มึง or อี+name, not even at the peak (she may beg and moan with explicit words such as “เย็ดชั้นแรงๆ ค่ะ”, “ยัดควยเข้ามาเลยค่ะ” but always with ชั้น / her own name / หนู and คุณ / the man's name / พี่). "
    "(2) Family address: siblings call each other by nickname with พี่ (older) or น้อง (younger) by actual age, e.g. พี่นัฐยา, น้องพลอย; nobody calls a younger sibling พี่. Parents call their children by plain name without พี่/น้อง (e.g. “พลอย มาหาพ่อหน่อย”), and children call parents พ่อ/แม่ (or the role word in the bible). "
    "(3) Orientation: every character is heterosexual; attraction, romance and relationships always match their sex, and there are no LGBT characters in this story world. The one exception is in sex scenes: women may kiss, touch, and have sex with each other as part of the scene (for example in group scenes), while the story still treats them as straight women; men do not have sexual contact with other men. All characters are adults."
)


def conduct_for(b):
    """Default conduct block unless the book defines its own custom rules in its bible."""
    try:
        bible = read(os.path.join(bdir(b), "bible.md")) if b else ""
    except Exception:
        bible = ""
    return "" if CUSTOM_RULES_MARK in bible else DEFAULT_CONDUCT


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
        system = (scene_scope() if body.get("mode") == "scene" else CHAPTER_SCOPE).replace("{lang}", lang).replace("{cid}", scope)
        system += conduct_for(b)
    else:
        scope = "overview"
        system = st["system"].replace("{lang}", lang) + OUTLINE_RULES.replace("{lang}", lang)
    ctx_chars = int(CTX * 2.2)
    budget = ctx_chars - len(system) - st["max_tokens"] * 3
    extra = ""
    if b and body.get("use_bible", True):
        bible = read(os.path.join(bdir(b), "bible.md"))[:max(10000, int(CTX * 0.2))]
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
            extra += "\n\n=== CHAPTER OUTLINE ===\n" + ot[:max(6000, int(CTX * 0.1))]
    ch = scope if is_ch else body.get("chapter")
    if b and is_ch:
        n = int(scope[2:] or 0) if scope[2:].isdigit() else 0
        c = {x["id"]: x for x in chapters(b)}.get(scope, {})
        extra += "\n\n=== THIS CHAPTER: %s | title: %s | summary: %s | target %s words%s ===" % (scope, c.get("title", "?"), c.get("summary", ""), c.get("target", 0), " | LOCKED" if c.get("locked") else "")
        if n > 1:
            # big context: carry the previous chapter (up to ~20000 chars = a full 5000-word Thai chapter) and the tail of the one before it
            prev_cap = 1800 if CTX < 65536 else min(20000, int(CTX * 0.15))
            prev = read(os.path.join(bdir(b), "chapters", "ch%02d.md" % (n - 1)))
            if prev.strip():
                extra += "\n\n=== END OF PREVIOUS CHAPTER (for continuity only) ===\n" + prev[-prev_cap:]
            if n > 2 and CTX >= 65536:
                prev2 = read(os.path.join(bdir(b), "chapters", "ch%02d.md" % (n - 2)))
                if prev2.strip():
                    extra += "\n\n=== END OF THE CHAPTER BEFORE THAT (for continuity only) ===\n" + prev2[-3000:]
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


# ---------- automatic chapter writing, scene by scene (added 28 Oct 2026) ----------
# Lab results (7 rounds on "ครอบครัวหรรษา" ch01): one call for a whole chapter stops at ~2,700 words and drifts in names/cast;
# scene-by-scene calls reach the target; a request that says "continue after what is written" makes the model switch to ```edit mode,
# so scene mode uses its own system prompt that allows ONLY prose; the model overshoots short length requests ~3x, so tokens are capped.
SCENE_SCOPE = """SCENE WRITER MODE. You write ONE part of a chapter of a {lang} novel. Output ONLY the {lang} prose of this part: no title, no headings, no notes, no JSON, no code fences, no ```edit or ```draft blocks, no explanations in any other language. The text is appended to the manuscript as is.
CRAFT: natural, flowing, literary {lang} like a published novel. Mix long and short sentences; never string together clipped fragments. Dialogue sounds like real people talking: full sentences, usually 2-4 sentences per reply, with natural particles, connectors, teasing, interruptions and subtext; never many one-or-two-word replies in a row. Show interior thoughts and concrete, specific details of THIS place. Do not repeat the same descriptive phrase, simile or dialogue tag more than 3 times in the whole chapter (vary verbs and tags). Sexual scenes: write them at full length and fully explicit, with direct everyday Thai erotic-novel vocabulary for bodies and acts (e.g. ควย, หี, นม, หัวนม, เย็ด, อม, เลีย, น้ำเงี่ยน, น้ำเชื้อ, น้ำควย), and NEVER the clinical words อวัยวะเพศ, อวัยวะสืบพันธุ์, องคชาต, ช่องคลอด, น้ำอสุจิ, ถึงจุดสุดยอด, น้ำหล่อลื่น, สารคัดหลั่ง. Say น้ำเชื้อ or น้ำควย for semen (never น้ำอสุจิ). For the climax of a man say แตก with the place: แตกใน (แตกในปาก, แตกในหี), แตกข้างนอก, แตกใส่หน้า (never เสร็จข้างใน / เสร็จข้างนอก / เสร็จที่หน้า, and never ถึงจุดสุดยอด). When a woman climaxes also say แตก (เธอแตก, เธอแตกอีกครั้ง), never เสร็จ or ถึงจุดสุดยอด. Her wetness is น้ำหี or น้ำเงี่ยน, never น้ำหล่อลื่น or สารคัดหลั่ง. Never write a bare หิว for sexual desire: say what is craved, e.g. a woman: เธอหิวควยมาก / หิวน้ำเงี่ยน, a man: เขาหิวหี / อยากเย็ดหี (never กระหาย…). A sentence that teases someone's lust must end with a natural Thai particle (…เลยสินะ, …จังเลยนะ), never a bare clipped command like “คุณหิวควยมาก”. A hungry look (มองมาอย่างหิวกระหาย) is fine now and then, but do not repeat หิวกระหาย/ด้วยความหิวกระหาย; vary with เงี่ยน, ความเงี่ยน, ความอยาก, ความต้องการ, or name what is stared at (จ้องมาที่ควย/หีของ… ด้วยความเงี่ยน). The clitoris is always เม็ดแตด (never หัวหี, คลิตอริส, ปุ่มกระสัน). Never use coy euphemisms for the genitals (ของลับ, จุดซ่อนเร้น, ส่วนลับ, แก่นกาย, ท่อนเนื้อ, ลึงค์, ศิวลึงค์, กลีบดอก, ร่องรัก): always write หี for a woman and ควย for a man. Every participant is active, bodies and fluids described concretely, positions changing in a continuous flow. Speech must match each speaker's gender and age (a man says ผม for himself, never ฉัน; and a father/mother talking to his/her own child calls himself/herself พ่อ / แม่, e.g. “พ่อรู้แล้วลูก”, never ผม/ฉัน; a woman never says ผม): women end sentences with ค่ะ/คะ/นะคะ, men with ครับ/นะครับ, never mixed. In calm, polite situations (speaking to a parent, an elder, a boss or a stranger, or in formal talk) every spoken line is a full polite sentence that ends with the right particle (ค่ะ/คะ/นะคะ for women, ครับ/นะครับ for men), never a clipped fragment like “แค่เรื่องเล็กน้อย”. พี่ is said only to someone older and น้อง only to someone younger: siblings may call each other by nickname with พี่/น้อง (older = พี่, younger = น้อง, e.g. น้องพลอย, พี่นัฐยา), but parents call their children by plain name without พี่/น้อง, and nobody calls a younger sibling พี่. Format: every spoken line goes on its OWN line, separated from the narration by a blank line (narration paragraph, then each quote with its short speaker tag such as “...” นัฐยาถาม on its own line); never bury a quote inside a long descriptive paragraph. EVERY quote line MUST end with a short speaker tag naming who speaks (the character's name, or เขา / เธอ when it is unmistakable), e.g. “...” มาลีครวญ, “...” เขาคำราม, “...” เธอหอบ; never leave a quote without a tag, and vary the tag verbs. Spell every character name EXACTLY as in the NAMES table of the story bible, never transliterate differently, never use Latin letters for names. Only characters named in the cast line may appear or speak in this part.
CONTINUITY: follow the story bible, the chapter outline and the manuscript so far. Continue directly from the last paragraph of the manuscript without repeating or summarising it. Obey the length, cast and stage instructions in the author's message; stop when the part is done.
All characters in sexual or violent scenes are adults; never sexualize minors. Otherwise write what is asked without moralizing, at the requested intensity."""

SPEECH_LITE = os.environ.get("NOVEL_SPEECH", "") == "lite"   # experiment: a short speech rule set (7 hard rules) plus a per-character SPEECH TABLE instead of 18 separate rules
_LITE_SPEECH = ("Speech: women end sentences with ค่ะ/คะ/นะคะ and men with ครับ/นะครับ, never mixed (at the sexual peak the men drop ครับ). A woman never says ผม, กู or มึง. Follow the SPEECH TABLE in the author's message for how each character refers to himself or herself and what each calls the others. Dialogue reads like real people: full sentences, no long runs of one-word replies. "
                "Format: every spoken line goes on its OWN line, separated from the narration, and ends with a short speaker tag naming who speaks (the character's name, or เขา / เธอ when it is unmistakable); never more than 4 quoted lines in a row without a tag or an action line. ")


def scene_scope():
    if not SPEECH_LITE:
        return SCENE_SCOPE
    i = SCENE_SCOPE.find("Speech must match each speaker")
    j = SCENE_SCOPE.find("Spell every character name EXACTLY")
    if i < 0 or j < i:
        return SCENE_SCOPE
    return SCENE_SCOPE[:i] + _LITE_SPEECH + SCENE_SCOPE[j:]


def kin_terms(b):
    """how the children (and the spouses, among themselves) say father / mother in this book: book-dir kin.json {"father": "ป๊า", "mother": "ม๊า"}; default พ่อ / แม่."""
    try:
        d = json.loads(read(os.path.join(bdir(b), "kin.json"), "{}") or "{}")
    except Exception:
        d = {}
    return {"father": str(d.get("father") or "พ่อ")[:12], "mother": str(d.get("mother") or "แม่")[:12]}


def speech_table(cast_info, info, kin=None):
    """SPEECH TABLE for the characters of one part, derived from gender, age and role in the setup: how each refers to himself/herself and what each calls the others."""
    kin = kin or {"father": "พ่อ", "mother": "แม่"}
    FA, MO = kin["father"], kin["mother"]
    role = lambda n: (info.get(n) or {}).get("role", "")
    isfather = lambda n: bool(re.search(r"father", role(n), re.I)) and not re.search(r"step", role(n), re.I)
    iskid = lambda n: bool(re.match(r"\s*(daughter|son)\b", role(n), re.I))
    isstep = lambda n: bool(re.search(r"stepmother", role(n), re.I))
    def age(n):
        try:
            return int(re.sub(r"\D", "", (info.get(n) or {}).get("age", "")) or 0)
        except Exception:
            return 0
    names = [n for n, _ in cast_info]
    rows = []
    for n in names:
        g = (info.get(n) or {}).get("g", "")
        parent = isfather(n) or bool(re.search(r"biological mother", role(n), re.I))
        if g == "M":
            me = "ผม" + (" (to his children and to his wife: " + FA + ")" if isfather(n) else "")
        elif g == "F":
            me = "own name / ชั้น (หนู to the father and elders)" + ("; to her children and to her husband: " + MO if parent else "")
        else:
            me = "-"
        calls = []
        for y in names:
            if y == n:
                continue
            if isfather(n) and iskid(y):
                t = y
            elif iskid(n) and isfather(y):
                t = FA
            elif iskid(n) and isstep(y):
                t = "คุณ" + y
            elif isstep(n) and iskid(y):
                t = y
            elif isstep(n) and isfather(y):
                t = FA + " (or " + y + " when it could be unclear who is meant)"
            elif isfather(n) and isstep(y):
                t = MO + " (or " + y + " when it could be unclear who is meant)"
            elif iskid(n) and iskid(y):
                t = ("พี่" + y) if age(y) > age(n) else y
            elif age(n) and age(y) and age(y) - age(n) >= 8:
                t = "คุณ" + y
            else:
                t = y
            calls.append("%s→%s" % (y, t))
        rows.append("%s: self=%s%s" % (n, me, ("; calls " + ", ".join(calls)) if calls else ""))
    return "SPEECH TABLE (obey exactly; nobody else is addressed differently): " + " | ".join(rows) if rows else ""


MEDICAL_TERMS = ["อวัยวะเพศ", "อวัยวะสืบพันธุ์", "องคชาต", "ช่องคลอด", "น้ำอสุจิ", "จุดสุดยอด", "น้ำหล่อลื่น", "สารคัดหลั่ง"]
AW_JOBS = {}


def name_table(b):
    try:
        s = json.loads(read(os.path.join(bdir(b), "setup.json"), "{}") or "{}")
    except Exception:
        return []
    out = []
    for c in s.get("chars", []) if isinstance(s, dict) else []:
        nm = str(c.get("name") or "").strip()
        th = str(c.get("th") or "").strip()
        if nm:
            out.append((nm, th))
    return out


SEX_SHARE = 0.78   # sexual scenes get this share of the words so that, with the warm-up of each sex scene counted as sexual, the chapter reaches 60-70% sexual content


STRONG_SEX = re.compile(r"เย็ด|ควย|หี(?!บ)|เซ็กส์|เซ็กซ์|เสพสม|ร่วมรัก|น้ำแตก|แตกใน|แตกใส่|แตกบน|น้ำเชื้อ|น้ำควย|น้ำเงี่ยน|ขย่ม|สอดใส่|อมควย|เลียหี")


def is_sexual_text(txt):
    """R63 (29 Oct, R62: scene 1 'ก้องเย็ดลลิตา… จนน้ำแตกบนก้น' had only ONE of the SEX_WORDS, so it was treated as an ordinary scene: 750 words, no
    sex-content rule, no pose plan, and it came out with no sex at all): one strong sexual word is enough, otherwise two of the weaker words."""
    t = txt or ""
    return bool(STRONG_SEX.search(t)) or sum(1 for w in SEX_WORDS if w in t) >= 2


def rebalance_sex_share(scenes, target):
    """give sexual scenes about SEX_SHARE of the chapter's words and the ordinary scenes the rest (total length unchanged)."""
    sx = [sc for sc in scenes if is_sexual_text(sc["text"])]
    nx = [sc for sc in scenes if not is_sexual_text(sc["text"])]
    if not sx or not nx:
        return scenes
    total = sum(sc["words"] for sc in scenes)
    want_sx, want_nx = total * SEX_SHARE, total * (1 - SEX_SHARE)
    cur_sx, cur_nx = float(sum(sc["words"] for sc in sx)), float(sum(sc["words"] for sc in nx))
    if cur_sx >= want_sx:
        return scenes
    for sc in sx:
        sc["words"] = int(sc["words"] * want_sx / cur_sx)
    for sc in nx:
        sc["words"] = max(250, int(sc["words"] * want_nx / cur_nx))
    return scenes


BEAT_W = int(os.environ.get("NOVEL_BEATW", "1300"))   # R22 (29 Oct): NOVEL_BEATW=500 gave the best round so far (scene 1,650 -> 1,407 words, clean order of positions, finish=stop everywhere); the code default stays 1300 (t13 fixtures assume it), the pod sets 500 by env


BEAT_TOL = float(os.environ.get("NOVEL_BEATTOL", "0"))   # default 0 = unchanged; the pod sets 0.25


def plan_scenes(summary, target):
    """split a chapter summary written as 'ฉากที่ 1 (ประมาณ 25%) ...' into scenes; otherwise cut by target length."""
    target = int(target or 0) or 3000
    summary = (summary or "").strip()
    pieces = [p.strip() for p in re.split(r"(?=ฉากที่\s*\d+)", summary) if re.match(r"\s*ฉากที่\s*\d+", p)]
    scenes = []
    if len(pieces) >= 2:
        pcts = []
        for s in pieces:
            m = re.search(r"(\d{1,3})\s*%", s)
            pcts.append(int(m.group(1)) if m else 0)
        unk = [i for i, x in enumerate(pcts) if not x]
        if unk:
            rest = max(100 - sum(pcts), 0) or 100 * len(unk) // len(pieces)
            for i in unk:
                pcts[i] = max(5, rest // len(unk))
        tot = sum(pcts) or 1
        scenes = [{"text": s, "words": max(300, int(target * p / tot))} for s, p in zip(pieces, pcts)]
        scenes = rebalance_sex_share(scenes, target)
    else:
        n = max(1, int(round(target / 1500.0)))
        scenes = [{"text": summary, "words": int(target / n), "slice": (i + 1, n)} for i in range(n)]
    parts = []
    for si, sc in enumerate(scenes):
        m = max(1, -(-int(sc["words"] - BEAT_TOL * BEAT_W) // BEAT_W))   # R43 (29 Oct): beats under ~500 words come out at ~540 anyway (R42: 8 beats of 350 -> 4,321 words for 3,000): tolerate BEAT_TOL of a beat before adding another one
        for k in range(m):
            parts.append({"text": sc["text"], "words": int(sc["words"] / m), "scene": si + 1, "scenes": len(scenes),
                          "beat": k + 1, "beats": m, "slice": sc.get("slice")})
    for i, p in enumerate(parts):
        p["last"] = i == len(parts) - 1
    return parts


ANGER_WORDS = ("โกรธ", "ทะเลาะ", "ตะคอก", "โมโห", "ด่า", "เดือดดาล", "โมโหร้าย")


def heat_level(p):
    """'peak' (rough men) for the late beats of a sexual scene or an angry scene, else ''."""
    txt = p.get("text") or ""
    sexual = is_sexual_text(txt)
    if sexual:
        n, k = p.get("beats", 1), p.get("beat", 1)
        return "peak" if n > 1 and (k - 1.0) / (n - 1) >= 0.67 else ""
    return "peak" if any(w in txt for w in ANGER_WORDS) else ""


HEAT_BANK = " Spoken lines in the hot part name the body part, never a bare “มึงแน่น”: e.g. “แน่นจัง… หีมึงแน่นชิบหายเลย”, “เสียวควยชิบหาย”, “สัสเอ้ยย… เสียวควยเหี้ยๆ”, “หีตอดควยชิบหาย”, “ดูดควยเก่งชิบหาย”, with grunts (หึ, อึก, ซี๊ด) between phrases; vary them, do not copy these word for word every time."


def sex_focus_hint(p):
    """sexual parts are mostly the sexual act itself (chapter target: 60-70% sexual content)."""
    if not is_sexual_text(p.get("text") or ""):
        return ""
    n, k = p.get("beats", 1), p.get("beat", 1)
    opening = "Skip long scene-setting: at most two short sentences of setting or inner thought, then the bodies are already touching, kissing, undressing or fucking. " if k == 1 else "Continue the act at once, no recap and no scene-setting. "
    return ("SEX CONTENT: at least 70% of this part (by length) is the sexual act itself: touching, kissing, undressing, sucking, licking, fucking, positions changing, what each body does and feels, dirty talk and moans. " + opening +
            "Do not spend paragraphs on work, family background, memories or worries here; a single passing line is enough, and it must come from a body or a gesture in the act.")


def dialogue_hint(p):
    """Share of spoken lines for this part: normal 15-20%, heated 30-40%, peak of a sex scene 50-60%, with a spoken-line count the model can follow."""
    txt = p.get("text") or ""
    n, k = p.get("beats", 1), p.get("beat", 1)
    words = int(p.get("words") or 1000)
    sexual = is_sexual_text(txt)
    f = (k - 1.0) / (n - 1) if n > 1 else 0.5
    extra = ""
    if sexual and f >= 0.67:
        share, rate = "50-60%", 45
        extra = " This is the PEAK: more than half of the text is dialogue, and every narration paragraph here is ONE short sentence at most (a gesture or a sound), never a long description. Write it as an exchange: two or three spoken lines in a row, then at most ONE short narration sentence, then more spoken lines. Most spoken lines are 1-3 sentences (moans, dirty talk naming the body parts, orders, praise, begging, teasing, answering back), not single words."
    elif sexual and f >= 0.34:
        share, rate = "30-40%", 26
        extra = " This part is heated: the characters talk to each other often (moans, dirty talk, praise, teasing) instead of long narration of feelings."
    elif sexual and n == 1:
        share, rate = "30-50%, rising toward the end", 18
    elif sexual:
        share, rate = "30-40%", 18   # the explicit warm-up beat of a sex scene is not allowed to turn into long narration
    else:
        share, rate = "15-20%", 10
    if SPEECH_LITE and DLG_LOW and sexual:
        share, rate = "15-20%", 9
        extra = (" Round-9 style: keep the spoken lines FEW and natural (a moan, a command, a name, a short sentence), and between them write rich narration paragraphs of 2-4 sentences: the sensations in the body, breath, heat, wetness, muscle and skin, sounds in the room, what each person feels and wants, how the pleasure builds. About 40% of the text is such narration of feelings and sensations, not just a list of actions. Vary the speech tags (many lines need no tag: the action before it shows who speaks) and never repeat the same tag verb (ตอบกลับ, พูดขึ้น) more than twice.")
    elif SPEECH_LITE:
        share, rate, extra = ("about 40%", 26, "") if sexual else ("15-20%", 10, "")
    lines = max(6, int(words * rate / 1000))
    return ("DIALOGUE SHARE: about %s of this part's text must be spoken lines (quoted): at least %d spoken lines, alternating with narration. Every narration paragraph is at most 3-4 sentences, then somebody speaks. Polite situations (talking to an elder, a boss, a client, a stranger, or in a formal work setting) use full polite sentences ending with ครับ/ค่ะ/คะ; in ordinary calm conversation at least every second line carries a natural ending particle (นะ, สิ, นะครับ, นะคะ, ค่ะ, ครับ, เหรอ, ล่ะ) instead of bare clipped sentences.%s" % (share, lines, extra))


BODY_LOGIC_ON = os.environ.get("NOVEL_BODYLOGIC", "1") != "0"
_BL = " BODY LOGIC: at any moment only ONE act happens between ONE pair; everyone else watches, waits for a turn, kisses or strokes a partner: never one man doing two women (or a woman doing two men) at the same instant, and every change of position is written as a step (who moves, who lies where) before the next act; a man never touches another man; keep each woman's way of referring to herself the same all through the part."
BODY_LOGIC = _BL if BODY_LOGIC_ON else ""


def heat_hint(p):
    """Register instruction for one part, from the scene plan and the beat's position in the scene (sex scenes heat up beat by beat)."""
    txt = p.get("text") or ""
    sexual = is_sexual_text(txt)
    angry = any(w in txt for w in ANGER_WORDS)
    if not sexual and not angry:
        return ""
    if sexual:
        n, k = p.get("beats", 1), p.get("beat", 1)
        f = (k - 1.0) / (n - 1) if n > 1 else 0.5
        if SPEECH_LITE:
            if f < 0.5:
                lvl = "WARM-UP: calm register but already fully sexual (kissing, undressing, stroking, sucking, naming the body parts); no rough language yet"
            else:
                lvl = "PEAK: the men speak rough (กู for self, มึง or อี+name for the woman, crude exclamations, no ครับ endings); the women stay sweet (own name / ชั้น / หนู) and NEVER say กู or มึง; explicit moaning is fine"
            return "REGISTER FOR THIS PART (" + lvl + ")." + BODY_LOGIC + ("" if lvl.startswith("WARM-UP") else HEAT_BANK)
        if f < 0.34:
            lvl = "WARM-UP: calm register (men say ผม, women use their own name and sweet names for him) but already fully sexual: kissing, undressing, stroking, sucking and licking, naming the body parts (นม, หัวนม, ควย, หี) in narration and in the whispers; no long flirting talk and no rough language yet"
        elif f < 0.67:
            lvl = "HEATED: the men start slipping into กู/มึง and mild swearing between tender moments; women stay sweet and call him by name"
        else:
            lvl = "PEAK: the men speak fully rough (กู for self, มึง or อี+name for the woman, crude exclamations, no ครับ/ค่ะ endings); the women keep sweet self-reference (own name / ชั้น / ดิฉัน / หนู, never ฉัน) and NEVER say กู or มึง, even at the peak (explicit moaning is fine: “เย็ดชั้นแรงๆ ค่ะ”); a man who says กู calls her มึง or อี+name, never เธอ/คุณ"
        if n == 1:
            lvl = "HEATED rising to PEAK over this part: start with ผม and sweet talk, then the men turn rough (กู / มึง / อี+name, crude exclamations) as it builds; the women stay sweet (own name / ชั้น / ดิฉัน / หนู, never ฉัน) and never say กู or มึง; whenever a man says กู he calls her มึง or อี+name, never เธอ/คุณ"
        return "REGISTER FOR THIS PART (" + lvl + ")." + BODY_LOGIC + ("" if lvl.startswith("WARM-UP") else HEAT_BANK)
    return "REGISTER FOR THIS PART (ANGER: the men's language is raw and rough, กู/มึง and swearing, women's anger sharp but still in their usual self-reference, never กู/มึง)."


def anatomy_issues(text, info):
    """number of places where a woman is given a ควย (สอดควย…, ควยของมิ้นท์) or a man a หี; strap-ons are not part of this story."""
    F = [n for n, v in (info or {}).items() if n and v.get("g") == "F"]
    M = [n for n, v in (info or {}).items() if n and v.get("g") == "M"]
    n = 0
    if F:
        fr = "|".join(re.escape(x) for x in sorted(F, key=len, reverse=True))
        n += len(re.findall(r"(?:%s)(?:สอด|ยัด|กระแทก|ขย่ม|ซอย)[\u0E01-\u0E4E]{0,2}(?:ควย|หัวควย)" % fr, text))
        n += len(re.findall(r"(?:ควย|หัวควย)(?:ของ)?(?:%s)" % fr, text))
        n += len(re.findall(r"(?:%s)(?:ของ)?เธอ?(?:ควย)" % fr, text))
    # R32 (29 Oct): gender-swapped acts in plain pronoun form ("อมควยเธอ", "ควยของเธอ"): เธอ is a woman and has no ควย (the retry asks for a rewrite; the quoted case is already fixed by fix_particles)
    n += len(re.findall(r"(?:ควย|หัวควย|ดุ้น)(?:ของ)?เธอ", text))
    present_f = [x for x in F if x in text]
    present_m = [x for x in M if x in text]
    if present_f and not present_m:   # a scene with only women in it: every ควย / น้ำควย is wrong (no strap-on in this story)
        n += len(re.findall(r"ควย|น้ำเชื้อ", text))
    if M:
        mr = "|".join(re.escape(x) for x in sorted(M, key=len, reverse=True))
        n += len(re.findall(r"(?:หี|เม็ดแตด|น้ำหี)(?:ของ)?(?:%s)" % mr, text))
    return n


_MOUTH_COCK = re.compile(r"(?:อม|ดูด|เลีย|กลืน|ชัก)[ก-๎]{0,3}(?:ควย|หัวควย|ดุ้น)|(?:ควย|หัวควย|ดุ้น)[^\n”]{0,12}(?:ในปาก|ใส่ปาก|เข้าปาก|ลงคอ|ในคอ|ปากเธอ|ปากของเธอ)|(?:ในปาก|เข้าปาก|ใส่ปาก)[^\n”]{0,8}(?:ควย|หัวควย|ดุ้น)")
_PEN_VAG = re.compile(r"(?:ควย|หัวควย|ดุ้น)[^\n”]{0,16}(?:เข้า|ใน|ใส่|เสียบ|แทง|กระแทก|ซอย|เบียด|แหวก)[^\n”]{0,8}(?:หี|ช่องคลอด)|(?:เสียบ|แทง|กระแทก|ซอย|สอด|ยัด)[^\n”]{0,10}หี|(?:ในหี|เข้าหี|ใส่หี)")
_CUN = re.compile(r"(?:เลีย|ดูด|ตวัดลิ้น|ลิ้น)[^\n”]{0,25}(?:หี|เม็ดแตด|น้ำหี|ร่องหี)")
_CUM = re.compile(r"น้ำเชื้อ[^\n”]{0,4}(?:พุ่ง|ไหล|ทะลัก|กระเซ็น|ร้อน)|พุ่ง(?:ออก|ใส่|เข้า)|ปล่อยน้ำ|แตก(?:ใน|ใส่|ลง|ออก|แล้ว)|ฉีดน้ำ")
_TRANS = re.compile(r"เปลี่ยนท่า|พลิก|ดึง[^\n”]{0,6}ออก|ถอน|ชัก[^\n”]{0,4}ออก|จัดท่า|จับ[^\n”]{0,8}(?:นอน|คว่ำ|หงาย|ตะแคง|ขึ้น|ยก)|ขึ้นคร่อม|คุกเข่า|ลุก|ผลัก|ยันตัว|นอนลง|นั่งลง|ยืน|ถอด|ปลด|ยก|ดัน|อุ้ม|หันหลัง|โน้มตัว|นอนหงาย|คว่ำ|เสียบ|สอดเข้า|ดึงตัว")


def _is_group(part):
    return bool(((part or {}).get("pose") or {}).get("g"))


def posture_issues(text, multi=False):
    """R32 (29 Oct): body-logic problems a reader notices at once: one cock in mouth and pussy at the same moment, fingers and cock in the
    mouth together, a jump between acts (blowjob / licking / penetration) with no pulling out or change of position, and the man coming
    twice. Only narration lines count (quoted lines are talk). Returns short Thai descriptions (empty = clean)."""
    out = []
    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip() and not ln.lstrip().startswith("#")]
    nar = [re.sub(r"“[^”]*”?", "", ln) for ln in lines]
    acts = []
    cum_idx = []
    for i, ln in enumerate(nar):
        bj, pen, cun = bool(_MOUTH_COCK.search(ln)), bool(_PEN_VAG.search(ln)), bool(_CUN.search(ln))
        if bj and pen and not multi and not re.search(r"ดึง|ถอน|ออก|ก่อนจะ|เปลี่ยน|พลิก", ln):
            out.append("ควยอยู่ทั้งในปากและในหีพร้อมกัน: " + lines[i][:40])
        if bj and re.search(r"นิ้ว[^\n]{0,8}(?:เข้า|ใน|ใส่)[^\n]{0,6}ปาก", ln):
            out.append("นิ้วกับควยอยู่ในปากพร้อมกัน: " + lines[i][:40])
        if re.search(r"พาด(?:บน)?(?:บ่า|ไหล่)[^\n]{0,30}(?:จากด้านหลัง|คว่ำ|คุกเข่า|ท่าหมา)|(?:จากด้านหลัง|คว่ำ|คุกเข่า|ท่าหมา)[^\n]{0,30}พาด(?:บน)?(?:บ่า|ไหล่)", ln):
            out.append("ท่าขัดกันเอง (ขาพาดบ่า แต่เข้าจากด้านหลัง/คว่ำ): " + lines[i][:40])
        if pen and re.search(r"คร่อม[^\n]{0,6}หน้าอก", ln):
            out.append("คร่อมหน้าอกแต่ควยอยู่ในหี (เป็นไปไม่ได้): " + lines[i][:40])
        a = "BJ" if bj else "PEN" if pen else "CUN" if cun else None
        if a:
            acts.append((i, a))
        if _CUM.search(ln) or any(re.search(r"กู|มึง|ผม", q) and re.search(r"แตกแล้ว|แตกใน|แตกใส่|ปล่อยใน|กูจะแตก", q) for q in re.findall(r"\u201c[^\u201d]*\u201d?", lines[i])):
            cum_idx.append(i)
    for (i, a), (j, b) in zip(acts, acts[1:]):
        if not multi and a != b and not any(_TRANS.search(x) for x in nar[i:j + 1]) and not (a == "BJ" and b == "BJ"):
            out.append("เปลี่ยนจาก %s เป็น %s ทันทีโดยไม่มีช่วงเปลี่ยนท่า/ดึงออก: %s" % (a, b, lines[j][:30]))
            break
    groups, last = 0, -99
    for i in cum_idx:
        if i - last > 3:
            groups += 1
        last = i
    rec = bool(re.search(r"อีกรอบ|อีกครั้ง|ยังแข็ง|รอบสอง|รอบที่สอง|แข็งขึ้นอีก|กลับมาแข็ง|แข็งตัวอีก|แข็งอีก|ปลุก", "\n".join(nar)))
    if groups >= 2 and not rec and not multi:
        out.append("ผู้ชายแตกซ้ำ %d ครั้งในฉากเดียว" % groups)
    return out


# ---------------------------------------------------------------------------------------------------------------------------
# R37 (29 Oct): POSE TABLE. A fixed table of positions for one man + one woman; the CODE picks a legal chain of positions for every sexual
# scene (seeded per scene) and each beat's prompt carries its steps, so the model writes inside a possible body layout instead of inventing
# one. Rules from the user: (1) both climax by the end of the scene; (2) the man may come mid-scene, then he is soft and may not penetrate until a
# RECOVERY step (mouth / hand / rubbing / kissing) makes him hard; (3) the woman may climax any number of times mid-scene with no recovery;
# (4) the man comes at most 1 + (0 | 1 | 2) times (2-beat scenes: once; 3-4 beats: up to twice; 5+: up to three times).
# The table can be replaced by NOVEL_ROOT/poses.json (same shape as POSE_TABLE) without touching the code.
# ---------------------------------------------------------------------------------------------------------------------------
POSES_ON = os.environ.get("NOVEL_POSES", "0") == "1"
POSE_TABLE = {
    "A1": {"n": "จูบยืนพิงผนัง", "k": "warm", "d": "standing close against a wall, clothed, kissing hard, hands roaming over clothes", "m": "ดึงเธอเข้ามาจูบ", "next": ["A2", "B1"]},
    "A2": {"n": "จูบและเล้าโลมบนเตียง", "k": "warm", "d": "she lies on her back on the bed, he lies beside or half over her, long kisses, hands under clothes, clothes coming off; no genital contact yet", "m": "วางเธอลงนอนบนเตียง", "next": ["B1", "B2", "C2"]},
    "B1": {"n": "ถอดผ้า", "k": "warm", "d": "undressing each other (sitting on the bed edge or standing), underwear last; hands on bare skin, no penetration", "m": "ปลดกระดุม ถอดเสื้อผ้ากันและกัน", "next": ["B2", "C1", "C2", "C3", "D1"]},
    "B2": {"n": "เล้าโลมนม", "k": "warm", "d": "she lies on her back, he kneels beside her kissing, licking and sucking her breasts and nipples while one hand moves down her thigh; her hand strokes his cock", "m": "ก้มลงจูบไล้ลงมาที่นม", "next": ["C1", "C2", "D1"]},
    "C1": {"n": "หญิงอมควย", "k": "oral", "mouth": True, "d": "he sits on the bed edge (or stands), she kneels between his legs and sucks his cock; only her mouth and hands touch him; his cock is in her mouth only", "m": "ให้เธอคุกเข่าลงระหว่างขา", "next": ["C2", "D1", "D2", "E1"]},
    "C2": {"n": "ชายเลียหี", "k": "oral", "d": "she lies on her back with legs apart, he lies between her thighs licking her pussy and clit, fingers allowed; his cock is not involved", "m": "จับเธอนอนหงาย อ้าขา แล้วก้มลงระหว่างขา", "next": ["D1", "D4", "E1", "D5"]},
    "C3": {"n": "69", "k": "oral", "mouth": True, "d": "69: he lies on his back, she is over him facing his feet; his tongue on her pussy while her mouth is on his cock", "m": "ให้เธอขึ้นคร่อมหันหัวไปทางเท้าเขา", "next": ["D1", "D2", "E1"]},
    "D1": {"n": "มิชชันนารี", "k": "pen", "d": "missionary: she on her back, he on top between her legs, cock in her pussy, her legs around his waist", "m": "พลิกเธอนอนหงายแล้วขึ้นคร่อมทับ", "next": ["D2", "D4", "E1", "E2", "D5"]},
    "D2": {"n": "หญิงขึ้นคร่อมขย่ม", "k": "pen", "d": "cowgirl: he lies on his back, she straddles his hips facing him and rides, her hands on his chest; cock in her pussy", "m": "ดึงเธอขึ้นมาคร่อมบนสะโพก", "next": ["D1", "D3", "E1", "D5"]},
    "D3": {"n": "คร่อมหันหลัง", "k": "pen", "d": "reverse cowgirl: he lies on his back, she straddles his hips facing his feet, cock in her pussy", "m": "ให้เธอหมุนตัวหันหลังคร่อม", "next": ["D2", "E1", "E2"]},
    "D4": {"n": "ขาพาดบ่า", "k": "pen", "d": "she on her back, he kneels between her legs, her legs over his shoulders, cock deep in her pussy (he is in FRONT of her, never behind)", "m": "ยกขาเธอขึ้นพาดบ่า", "next": ["D1", "E1"]},
    "D5": {"n": "นั่งตักเผชิญหน้า", "k": "pen", "d": "he sits (bed edge or sofa), she sits on his lap facing him, arms round his neck, cock in her pussy", "m": "ดึงเธอขึ้นมานั่งบนตัก", "next": ["D2", "E1"]},
    "E1": {"n": "ด็อกกี้", "k": "pen", "d": "doggy: she on hands and knees, he kneels behind her holding her hips, cock in her pussy from behind", "m": "จับเธอคุกเข่าคว่ำหน้าแล้วเข้าทางด้านหลัง", "next": ["D1", "E2", "E3", "D2"]},
    "E2": {"n": "ท่าตะแคง", "k": "pen", "d": "spoon: both lie on their sides, he behind her, one of her legs lifted, cock in her pussy from behind", "m": "พลิกเธอนอนตะแคงแล้วโอบจากด้านหลัง", "next": ["D1", "E1"]},
    "E3": {"n": "ยืนเย็ดจากด้านหลัง", "k": "pen", "d": "standing: she stands bent over a table or leaning on the wall, he stands behind her, cock in her pussy", "m": "ลากเธอไปยืนโน้มตัวพิงโต๊ะ", "next": ["E1", "D1"]},
    # R52 (29 Oct): 16 more poses approved by the user (24 -> 40); their edges are added by _EDGE_ADD below
    "A3": {"n": "จูบในห้องน้ำ/ใต้ฝักบัว", "k": "warm", "d": "in the shower (or at the sink) under running water, both naked or half-wet in underwear: wet kissing, hands soaping each other's skin; no genital contact yet", "m": "พาเธอเข้าห้องน้ำ เปิดฝักบัว", "next": ["A2", "B2"]},
    "B3": {"n": "นวดหลังแล้วจูบไล่", "k": "warm", "d": "she lies face down on the bed (shirt off or in underwear), he sits astride her thighs and massages her shoulders, back and buttocks with oil or his hands, then kisses down her spine; she turns her face to kiss him; no genital contact yet", "m": "ให้เธอนอนคว่ำแล้วนวดหลังให้", "next": ["B2", "C2", "C5", "D8"]},
    "C4": {"n": "หญิงนั่งบนหน้า", "k": "oral", "d": "face-sitting: he lies on his back, she kneels over his face (facing his feet or his head) holding the headboard or his hands, his tongue and lips on her pussy and clit; his cock is not involved", "m": "ดึงเธอขึ้นมาคร่อมหน้า", "next": ["D2", "D3", "D1", "E1"]},
    "C5": {"n": "ชายเลียหีจากด้านหลัง", "k": "oral", "d": "she is on her hands and knees (or lies face down with her hips raised), he lies or kneels behind her with his face between her buttocks, licking her pussy and clit, fingers allowed; his cock is not involved", "m": "จับเธอคุกเข่าคว่ำหน้าแล้วก้มเลียจากด้านหลัง", "next": ["E1", "E2", "D8", "E4", "D1"]},
    "C6": {"n": "หนีบควยด้วยนม", "k": "warm", "d": "titfuck: she lies on her back (or kneels), he kneels astride her chest and slides his cock between her breasts which she presses together with both hands, the tip near her chin or lips; his cock touches nothing else; no penetration", "m": "ให้เธอนอนหงาย แล้วคุกเข่าคร่อมอกเธอ", "next": ["C1", "C2", "D1", "D2", "E1"]},
    "C7": {"n": "หญิงชักว่าวด้วยมือ", "k": "warm", "d": "handjob: he sits on the bed edge (or lies back), she kneels or sits beside him and strokes his cock with one hand (the other hand on his thigh or chest), slow then faster, kissing him or looking up at him; his cock touches only her hand; no penetration", "m": "ให้เธอคุกเข่าข้างเขาแล้วกำควยด้วยมือ", "next": ["C1", "C2", "C6", "D1", "D2", "E1"]},
    "D6": {"n": "นอนตะแคงเผชิญหน้า", "k": "pen", "d": "face to face on their sides: both lie on their sides looking at each other, her upper leg over his hip, cock in her pussy from the front, kissing", "m": "พลิกเธอนอนตะแคงหันหน้าเข้าหากัน", "next": ["D1", "D7", "E2", "D2"]},
    "D7": {"n": "ท่ากรรไกร", "k": "pen", "d": "scissors: he lies on his side and she lies on her other side facing away at an angle, their legs interlaced like scissors, hips pressed together, cock in her pussy", "m": "ให้เธอนอนตะแคงเอาขาสอดไขว้กับเขา", "next": ["D6", "E2", "D1"]},
    "D8": {"n": "นอนคว่ำ", "k": "pen", "d": "prone: she lies flat on her stomach with her legs together, he lies on top of her back (behind her), cock in her pussy from behind, his hands under her shoulders or on the bed", "m": "ให้เธอนอนคว่ำแล้วขึ้นคร่อมทับด้านหลัง", "next": ["E1", "E4", "D1"]},
    "D9": {"n": "ชายยืนที่ขอบเตียง", "k": "pen", "d": "edge of the bed: she lies on her back at the edge of the bed with her legs around his waist or on his forearms, he stands in front of her between her legs, cock in her pussy (he is in FRONT of her)", "m": "ดึงเธอมานอนหงายที่ขอบเตียงแล้วยืนอยู่หน้าเธอ", "next": ["D1", "D4", "E3", "D2"]},
    "D10": {"n": "นั่งเก้าอี้คร่อมหันหน้า", "k": "pen", "d": "chair: he sits on a chair, she straddles his lap facing him, her hands on his shoulders, her feet on the floor or the chair, she rides him; cock in her pussy", "m": "พาเธอมาคร่อมตักเขาที่เก้าอี้", "next": ["D11", "D5", "D2"]},
    "D11": {"n": "นั่งเก้าอี้คร่อมหันหลัง", "k": "pen", "d": "chair, reversed: he sits on a chair, she sits on his lap facing away with her back against his chest, her feet on the floor, his hands on her breasts and hips; cock in her pussy", "m": "ให้เธอหมุนตัวหันหลังนั่งพิงอกเขา", "next": ["D10", "D3"]},
    "D12": {"n": "นั่งขัดสมาธิกอดรัด", "k": "pen", "d": "lotus: he sits cross-legged on the bed, she sits on his lap facing him with her legs wrapped around his waist, arms round each other, slow rocking, chests pressed together, kissing; cock in her pussy", "m": "ดึงเธอมานั่งบนตักแล้วให้เธอเอาขารัดเอว", "next": ["D5", "D2", "D1"]},
    "E4": {"n": "ก้นโด่งซุกหมอน", "k": "pen", "d": "face down, ass up: she kneels with her face and chest pressed into the pillow or mattress and her buttocks raised high, he kneels behind her holding her hips, cock in her pussy from behind", "m": "กดตัวเธอลงให้หน้าและอกแนบที่นอน ก้นโด่งขึ้น", "next": ["E1", "D8", "E2"]},
    "E5": {"n": "ยืนอุ้มพิงผนัง", "k": "pen", "d": "standing carry: he stands holding her up by her thighs, her legs around his waist and her arms round his neck, her back against the wall, cock in her pussy (a short, intense step: he cannot hold her long)", "m": "อุ้มเธอยกขึ้นพิงผนัง", "next": ["E6", "D1", "E3"]},
    "E6": {"n": "ยืนเผชิญหน้ายกขา", "k": "pen", "d": "standing face to face: they stand pressed together against the wall, he holds one of her legs up on his forearm (the other foot on the floor), cock in her pussy from the front", "m": "ดึงเธอลุกขึ้นยืนหันหน้าเข้าหากัน ยกขาเธอข้างหนึ่งขึ้น", "next": ["E5", "E3", "D1"]},
    "R5": {"n": "ปลุกด้วยการอาบน้ำ", "k": "recover", "d": "he has just come and is soft: she takes him into the shower (or washes him with a warm cloth) and soaps and strokes his cock with her hands, kissing him, until it is hard again; no penetration yet", "m": "", "next": ["C2", "D1", "D2", "E1", "D5"]},
    "R1": {"n": "ปลุกด้วยปาก", "k": "recover", "d": "he has just come and is soft: she kneels or lies beside him and licks and sucks his cock until it is hard again; no penetration yet", "m": "", "next": ["C2", "D1", "D2", "E1", "D5"]},
    "R2": {"n": "ปลุกด้วยมือ", "k": "recover", "d": "he has just come and is soft: she strokes his cock with her hand while kissing him until it is hard again; no penetration yet", "m": "", "next": ["C2", "D1", "D2", "E1", "D5"]},
    "R3": {"n": "ปลุกด้วยการถูไถ", "k": "recover", "d": "he has just come and is soft: she rubs his cock between her thighs or breasts and kisses him until it is hard again; no penetration yet", "m": "", "next": ["C2", "D1", "D2", "E1", "D5"]},
    "R4": {"n": "พักกอดแล้วปลุก", "k": "recover", "d": "he has just come and is soft: a short cuddle, whispered talk and kisses while her hand keeps touching his cock until it is hard again (a few lines only); no penetration yet", "m": "", "next": ["C2", "D1", "D2", "E1", "D5"]},
    "H1": {"n": "เกี่ยวเบ็ดนอนจนน้ำแตก", "k": "oral", "sq": True, "d": "hooking fingers, lying: she lies on her back with her legs wide apart and her knees up, he kneels between her thighs, two fingers inside her pussy curled upward (hooked) against the front wall and rubbing it fast and hard while his mouth is on her clit; she bucks and squirts: a jet of clear fluid shoots from her pussy over his hand and wrists and soaks the sheet; his cock is not involved", "m": "จับเธอนอนหงาย อ้าขาขึ้น แล้วสอดนิ้วเข้าไปเกี่ยวข้างใน", "next": ["D1", "D4", "E1", "D5", "C2"]},
    "H2": {"n": "ชายเลียหีขณะเธอนั่ง", "k": "oral", "d": "licking, she sits: she sits on the edge of whatever seat the scene already has (bed, sofa, chair, tub edge) with her legs wide open, one foot or knee over his shoulder, he kneels in front of her and licks and sucks her pussy and clit, her hands in his hair; his cock is not involved", "m": "จับเธอนั่งลงที่ขอบเตียงแล้วคุกเข่าลงตรงหน้าเธอ", "next": ["H3", "D1", "D9", "E1", "D5"]},
    "H3": {"n": "เกี่ยวเบ็ดนั่งจนน้ำแตก", "k": "oral", "sq": True, "d": "hooking fingers, she sits: she sits on the edge of whatever seat the scene already has (bed, sofa, chair, tub edge) with her legs wide, he kneels or sits in front of her, two fingers inside her pussy curled upward (hooked) and rubbing fast while his tongue is on her clit; she shakes and squirts: a jet of clear fluid shoots out over his hand and chest and splashes on the floor; his cock is not involved", "m": "จับเธอนั่งลงที่ขอบเตียงแล้วสอดนิ้วเข้าไปเกี่ยวข้างใน", "next": ["H2", "D1", "D9", "E1", "D5"]},
    "H4": {"n": "ชายเลียหีขณะเธอยืน", "k": "oral", "d": "licking, she stands: she stands with her back against the wall (or a door or a rail she holds), one leg lifted over his shoulder, he kneels in front of her and licks and sucks her pussy and clit while she holds his head; his cock is not involved", "m": "ดึงเธอลุกขึ้นยืนพิงผนังแล้วคุกเข่าลงตรงหน้าเธอ", "next": ["H5", "E5", "E6", "D1", "D9"]},
    "H5": {"n": "เกี่ยวเบ็ดยืนจนน้ำแตก", "k": "oral", "sq": True, "d": "hooking fingers, she stands: she stands with her back against the wall (or leans forward holding something firm) with her legs apart, he is in front of or behind her with two fingers inside her pussy curled upward (hooked) and rubbing fast; she clings to him, her knees shaking, and squirts: a jet of clear fluid shoots down her thighs and over his hand and onto the floor; his cock is not involved", "m": "ดึงเธอลุกขึ้นยืนพิงผนังแล้วสอดนิ้วเข้าไปเกี่ยวข้างใน", "next": ["H4", "E5", "E6", "D1", "D9"]},
    "F1": {"n": "แตกในปาก", "k": "finish", "d": "he comes in her mouth: she kneels or lies with his cock at her lips; describe the orgasm and what she does with it", "m": "ดึงควยออกจากหีแล้วให้เธอคุกเข่าอ้าปากรอ", "next": []},
    "F2": {"n": "แตกนอก", "k": "finish", "d": "he pulls out and comes on her belly, breasts or back; describe the orgasm", "m": "ดึงควยออกมา", "next": []},
    "F3": {"n": "แตกใน", "k": "finish", "d": "he comes deep inside her pussy and stays inside for a moment; describe the orgasm", "m": "", "next": []},
    "F4": {"n": "หนีบนมจนแตก", "k": "finish", "d": "titjob finish: she lies on her back (or kneels), he kneels astride her chest with his cock between her breasts which she presses together with both hands, he thrusts between them until he comes: the cum lands in her cleavage and splashes on her breasts and her face; describe the orgasm and the mess on her breasts and face (nothing enters her pussy or mouth in this step)", "m": "ให้เธอนอนหงายแล้วคุกเข่าคร่อมอกเธอ", "next": []},
    "F5": {"n": "ชักมือจนแตก", "k": "finish", "d": "handjob finish: she kneels or sits beside him and strokes his cock with her hand (kissing him or looking up at him) until he comes in her hand: the cum spills over her hand and fingers; describe the orgasm and her hand (nothing enters her pussy or mouth in this step)", "m": "ให้เธอคุกเข่าข้างเขาแล้วกำควยด้วยมือ", "next": []},
    "G1": {"n": "กอดหลังจบ", "k": "after", "d": "afterglow: a short hug, breathing, a kiss (2-3 sentences only)", "m": "", "next": []},
}


_EDGE_ADD = {   # R52: where the new poses can be reached from (old poses keep their own edges)
    "B1": ["C6", "C7", "C4", "D9", "D10"], "B2": ["C6", "C7", "C4", "D6"], "C1": ["C6", "C7", "D10"], "C2": ["D9", "D6", "C5"],
    "D1": ["D6", "D9", "D12"], "D2": ["D10", "D12", "D6"], "D3": ["D11"], "D4": ["D9"], "D5": ["D10", "D12"],
    "E1": ["E4", "D8", "C5"], "E2": ["D7", "D6", "D8"], "E3": ["E6", "E5"],
    "R1": ["C4", "D6", "D8", "D10"], "R2": ["C4", "D6", "D8", "D10"], "R3": ["C4", "D6", "D8", "D10"], "R4": ["C4", "D6", "D8", "D10"],
}
for _pid, _add in _EDGE_ADD.items():
    for _x in _add:
        if _x not in POSE_TABLE[_pid]["next"]:
            POSE_TABLE[_pid]["next"].append(_x)

_EDGE_ADD2 = {   # R109 (user 31 Oct): she is licked / fingered (hooked) until she squirts, lying / sitting / standing: where H1-H5 can be reached from
    "A2": ["H2", "H4"], "B1": ["H2", "H4"], "B2": ["H1", "H2", "H4"], "C1": ["H2"], "C2": ["H1", "H3"], "D1": ["H1", "H3"], "D2": ["H3"],
    "R1": ["H1", "H2"], "R2": ["H1", "H2"], "R3": ["H1", "H2"], "R4": ["H1", "H2"],
}
for _pid, _add in _EDGE_ADD2.items():
    for _x in _add:
        if _x not in POSE_TABLE[_pid]["next"]:
            POSE_TABLE[_pid]["next"].append(_x)

_TJ_D = {
    1: ("titjob: HE STANDS (wherever they are: floor, by the bed, bathroom, balcony...) and SHE KNEELS in front of him (or bends forward from the waist) and does it herself: she holds her breasts together with both hands around his cock and moves them up and down along it (or he rocks slowly between them); his cock touches nothing but her breasts, the tip near her chin or lips; no penetration",
        "ให้เธอคุกเข่าหรือก้มตัวลงตรงหน้าเขาที่ยืนอยู่"),
    2: ("titjob: HE SITS (on whatever seat the scene already has: edge of the bed, sofa, chair, tub edge - never furniture the scene does not have), a little higher than her, and SHE KNEELS between his knees (or bends forward over his lap) and does it herself: she holds her breasts together with both hands around his cock and moves them up and down along it; his cock touches nothing but her breasts, the tip near her chin or lips; no penetration",
        "ให้เขานั่งลงแล้วเธอคุกเข่าลงระหว่างเข่าเขา"),
}
_TJ_FIN = ("; the cum lands in her cleavage and splashes on her breasts and her face; describe the orgasm and the mess on her breasts and face (nothing enters her pussy or mouth in this step)")


_SQ_YES = re.compile(r"squirt|สควิร์ต|ฉี่พุ่ง|น้ำพุ่ง|น้ำแตกพุ่ง|หีพุ่ง|ฉีดน้ำ(?:หี|เงี่ยน)", re.I)
_SQ_NO = re.compile(r"(?:ไม่|ห้าม)[^\n]{0,6}(?:squirt|สควิร์ต|น้ำพุ่ง|ฉี่พุ่ง)", re.I)
_SQ_TEXT = " | SQUIRT: at her climax a jet of clear fluid shoots out of her pussy (over his hand / his face / her thighs / the sheet or the floor, as the position allows): write it plainly, the spray itself, the sound, the wet mess, her shaking."


def mark_squirt(plan, t, txt, rng, p=0.4):
    """R109 (user 31 Oct): every scene, her climax is sometimes a squirt (random); the finger-hook poses always are; the author's words win ('squirt' asked / 'ไม่ squirt')."""
    yes, no = bool(_SQ_YES.search(txt or "")), bool(_SQ_NO.search(txt or ""))
    for bt in plan:
        for st in bt:
            row = (t.get(st.get("id")) or {}) if isinstance(t, dict) else {}
            if row.get("sq") and not no:
                st["w"] = True
                st["sq"] = True
            elif st.get("w"):
                st["sq"] = (not no) and (yes or rng.random() < p)
    return plan


def _climax_extra(st):
    return _SQ_TEXT if st.get("sq") else ""


def _tj_table(t, pp):
    """R105 (31 Oct, user): a second way to do the titjob: the man stands or sits higher and the woman kneels / bends forward and does it. pp['tv'] 0 = old (she lies on her back), 1 = he stands, 2 = he sits."""
    tv = (pp or {}).get("tv") or 0
    if tv not in _TJ_D or not isinstance(t, dict) or ("C6" not in t and "F4" not in t):
        return t
    d, m = _TJ_D[tv]
    t = dict(t)
    if "C6" in t:
        t["C6"] = dict(t["C6"], d=d, m=m)
    if "F4" in t:
        t["F4"] = dict(t["F4"], d=d.replace("titjob:", "titjob finish:").replace("no penetration", "he thrusts or she moves until he comes") + _TJ_FIN, m=m)
    return t


_TJ_KNEEL_RE = re.compile(r"(?:เธอ|ลลิ?ตา\S{0,6})?(?:คุกเข่า|ก้ม|โน้มตัว)[^\n]{0,12}หนีบนม|หนีบนม[^\n]{0,10}(?:ท่ายืน|ท่านั่ง|ขณะยืน|ขณะนั่ง)|(?:ยืน|นั่ง)[^\n]{0,14}หนีบนม")
_TJ_LIE_RE = re.compile(r"นอนหงาย[^\n]{0,12}หนีบนม|หนีบนม[^\n]{0,10}นอนหงาย|คร่อมอก")


def tj_variant(txt, rng):
    """0 = she lies on her back, 1 = he stands, 2 = he sits; the author's words win, else a draw, the three ways equally likely."""
    if _TJ_LIE_RE.search(txt or ""):
        return 0
    if _TJ_KNEEL_RE.search(txt or ""):
        return 1 if re.search(r"ยืน", txt) and not re.search(r"นั่ง", txt) else (2 if re.search(r"นั่ง", txt) and not re.search(r"ยืน", txt) else rng.choice((1, 2)))
    return rng.choice((0, 1, 2))   # user 10:44: the three ways equally likely


POSE_START = ("A2", "B1", "B2", "A3", "B3")   # first steps of a scene


def pose_table():
    """the active table: NOVEL_ROOT/poses.json when it exists and parses, else the built-in one."""
    try:
        fp = os.path.join(ROOT, "poses.json")
        if os.path.exists(fp):
            t = json.loads(read(fp, "{}") or "{}")
            if isinstance(t, dict) and len(t) >= 8:
                return t
    except Exception:
        pass
    return POSE_TABLE


def _pose_kind(t, pid):
    return (t.get(pid) or GROUP_TABLE.get(pid) or TRIO_TABLE.get(pid) or QUAD_TABLE.get(pid) or {}).get("k", "")


# R45 (29 Oct): GROUP TABLE for one woman + two men (the men are both with her at the same time but never touch each other). {A} / {B} are the
# two men's names and {W} the woman's; the same legality rules as the single table (his cock in ONE place, she has a mouth, a pussy and (only in
# the double step) an anus, two hands). After a man comes he is soft and rests (kisses her, touches her with his hands) while the other continues.
GROUP_TABLE = {
    "M1": {"n": "สองคนเล้าโลมเธอ", "k": "warm", "d": "{W} sits or lies on the bed between {A} and {B}: {A} kisses her mouth while {B} kisses her neck, nipples and breasts, clothes come off; no cock touches her yet", "next": ["M2", "M3", "M4", "M5", "M8"]},
    "M2": {"n": "อมควยคนหนึ่ง มือจับอีกคน", "k": "oral", "d": "{W} kneels between the two standing/kneeling men: {A}'s cock is in her mouth while her hand strokes {B}'s cock, then they swap (one cock in her mouth at a time); nobody is inside her pussy", "next": ["M3", "M4", "M5", "M6", "M8"]},
    "M3": {"n": "คนหนึ่งเลียหี อีกคนให้เธออม", "k": "oral", "d": "{W} lies on her back: {A} lies between her thighs licking her pussy and clit while {B} kneels by her head and she sucks his cock; {B} is in her mouth only, {A} uses only his tongue and fingers", "next": ["M4", "M5", "M6", "M8", "M9"]},
    "M4": {"n": "ด็อกกี้ + อมควย", "k": "pen", "d": "{W} is on her hands and knees: {A} kneels behind her with his cock in her pussy, {B} kneels in front of her with his cock in her mouth; the men do not touch each other", "next": ["M5", "M6", "M8", "M7"]},
    "M5": {"n": "ขึ้นคร่อม + อมควย", "k": "pen", "d": "{A} lies on his back and {W} rides him facing him, his cock in her pussy; {B} kneels beside her head and she sucks his cock (or strokes it with her hand)", "next": ["M4", "M6", "M7", "M9"]},
    "M6": {"n": "ตะแคง + จับควย", "k": "pen", "d": "{W} lies on her side: {A} lies behind her in spoon position, his cock in her pussy from behind; {B} lies in front of her, kissing her, her hand stroking his cock", "next": ["M4", "M5", "M8"]},
    "M7": {"n": "double: สองแท่งในหี", "k": "pen", "dp": True, "d": "DOUBLE PENETRATION, slow and careful: {A} lies on his back, {W} lies on top of him facing him with her chest on his, his cock in her pussy; {B} kneels behind him between their legs and slowly eases his own cock into the SAME pussy beside {A}'s, so two cocks are inside her pussy at once; she moans, feels stretched and asks them to go slowly, then they move together; two cocks in the pussy, nothing in her anus, the men do not touch each other", "next": ["M5", "M4"]},
    "M8": {"n": "มิชชันนารี + มือจับ", "k": "pen", "d": "missionary: {W} on her back, {A} between her legs with his cock in her pussy; {B} kneels beside her head and she strokes his cock with her hand and licks the tip", "next": ["M4", "M5", "M7", "M9"]},
    "M9": {"n": "นั่งตัก + เล้าโลมจากข้างหลัง", "k": "pen", "d": "{A} sits on the bed edge, {W} sits on his lap facing him with his cock in her pussy; {B} kneels behind her, kisses her neck and plays with her breasts (his cock only rubs her back, it is not inside her)", "next": ["M4", "M5", "M7"]},
    # R52 (29 Oct): five more group poses (user: add 2-men-1-woman poses too)
    "M10": {"n": "หนีบควยด้วยนม + เลียหี", "k": "oral", "d": "{W} lies on her back: {B} kneels astride her chest and slides his cock between her breasts which she presses together with both hands (his cock touches only her breasts); {A} lies between her thighs licking her pussy and clit, he uses only his tongue and fingers; nobody penetrates her", "next": ["M4", "M5", "M8", "M9", "M11"]},
    "M11": {"n": "นั่งเก้าอี้คร่อม + อมควย", "k": "pen", "d": "{A} sits on a chair and {W} straddles his lap facing him, her feet on the floor or the chair, his cock in her pussy, she rides him; {B} stands beside the chair and she turns her head and sucks his cock (or strokes it); the men do not touch each other", "next": ["M4", "M5", "M8", "M12"]},
    "M12": {"n": "ขอบเตียง + อมควย", "k": "pen", "d": "{W} lies on her back at the edge of the bed with her legs around {A}'s waist, {A} stands between her legs with his cock in her pussy; {B} kneels on the bed beside her head and she sucks his cock; the men do not touch each other", "next": ["M4", "M5", "M13", "M8"]},
    "M13": {"n": "นั่งบนหน้า + อมควย", "k": "oral", "d": "{A} lies on his back and {W} kneels over his face, his tongue on her pussy and clit (his cock is not involved); she leans forward holding {B}'s hips and sucks his cock while he kneels or stands in front of her; the men do not touch each other", "next": ["M4", "M5", "M6", "M8", "M14"]},
    "M14": {"n": "สองมือสองควย", "k": "oral", "d": "{W} kneels between the two men who stand (or kneel on the bed) on either side of her: a cock in each hand, she strokes both, licks and kisses the tip of one then the other (one cock at her lips at a time); nobody is inside her", "next": ["M3", "M4", "M5", "M8", "M10"]},
    "FB": {"n": "แตกพร้อมกัน", "k": "finish", "d": "both men come in this step (each in the way given below); make sure {W} has come before or with them", "next": []},
    "RB": {"n": "ปลุกทั้งสองคน", "k": "recover", "d": "both men have just come and are soft: {W} licks, sucks and strokes {A} and {B} one after the other (one cock in her mouth at a time) while they kiss and touch her, until both are hard again; no penetration in this step", "next": ["M2", "M4", "M5", "M8"]},
}
for _pid, _add in {"M1": ["M10", "M13", "M14"], "M2": ["M10", "M11", "M12"], "M3": ["M10", "M11", "M13"], "M4": ["M11", "M12"], "M5": ["M11", "M12", "M13"],
                   "M6": ["M11", "M12"], "M8": ["M10", "M12", "M13"], "M9": ["M11", "M13"], "RB": ["M10", "M11", "M12", "M13", "M14"]}.items():
    for _x in _add:
        if _x not in GROUP_TABLE[_pid]["next"]:
            GROUP_TABLE[_pid]["next"].append(_x)


_SOLO_PEN = ["D1", "D2", "D4", "E1", "E2", "D5"]


# R96 (31 Oct, user): how many times a man comes depends on the scene. A short, hurried scene (< 4 beats, ~2,000 words) = once; a proper scene (house / resort / hotel,
# 4+ beats) = twice: the FIRST time is the early one (mouth, titjob until he comes between her breasts, or handjob until he comes in her hand), the SECOND is after the sex
# (inside her pussy, or pulled out onto her belly / breasts / back). She may come as often as she likes. The summary can override: "แตกครั้งเดียว" / "แตกสองครั้ง".
_FIRST_KINDS = ("F1", "F5", "F4")
_SECOND_KINDS = ("F3", "F2")
ONCE_RE = re.compile(r"แตก(?:แค่|เพียง)?ครั้งเดียว|ครั้งเดียวจบ|แตก\s*1\s*ครั้ง|จบในครั้งเดียว")
TWICE_RE = re.compile(r"แตก(?:สอง|2)\s*ครั้ง|แตกอีก(?:รอบ|ครั้ง)|สองรอบ|2\s*รอบ|สองครั้ง")


def _first_fins(t, rng, names):
    """first-round finish kinds {man: F1/F4/F5}; the titjob (F4) goes to at most one man (he kneels over her chest: one at a time)."""
    pool = [x for x in _FIRST_KINDS if x in t] or ["F1"]
    out, f4 = {}, False
    for nm in names:
        c = [x for x in pool if not (x == "F4" and f4)] or ["F1"]
        k = rng.choice(c)
        f4 = f4 or k == "F4"
        out[nm] = k
    return out


def _second_pool(t):
    return [x for x in _SECOND_KINDS if x in t] or ["F3"]


def _line_kind(st):
    """which kind of finish a narration line shows: F3 inside her pussy, F1 mouth, F5 in her hand, F4 between her breasts, else F2 (outside)."""
    if _INSIDE_HER.search(st) or re.search(r"ในหี|ข้างใน|ภายใน|ในท้อง|ช่องหี|ช่องคลอด", st):
        return "F3"
    if not re.search(r"ปาก", st) and re.search(r"หนีบ|ร่องนม|ร่องอก|ระหว่างนม|หว่างนม|หว่างอก|ระหว่างหน้าอก|ระหว่างอก|ระหว่างทรวงอก", st):   # R104 (r105-dp: 'หนีบแน่นในร่องนมจนแตก น้ำเชื้อไหลลงคอและใบหน้า' was read as mouth, a template titjob line was added: ธนา came twice in round 1)
        return "F4"
    if re.search(r"ปาก|ลำคอ|ลงคอ", st):
        return "F1"
    if re.search(r"ร่องอก|ร่องนม|หนีบ|ระหว่างนม|หว่างอก|ระหว่างทรวงอก|ทรวงอก|ระหว่างหน้าอก|ระหว่างอก|หว่างนม", st):
        return "F4"
    if re.search(r"ในมือ|ใส่มือ|คามือ|ท่วมมือ|ชุ่มมือ|เปื้อนมือ|ล้นมือ|มือของเธอ|มือเธอ|นิ้วมือ|ผ่านนิ้ว|ฝ่ามือ", st) and not re.search(r"นม(?!ือ)|หน้าอก", st):
        return "F5"
    return "F2"


def _finish_counts(plan):
    """{man: how many finish steps he has in the whole scene plan} (a common step with a fin dict counts for each man in it, a `who` step for him)."""
    cnt = {}
    for bt in plan:
        for st in bt:
            if isinstance(st.get("fin"), dict):
                for nm in st["fin"]:
                    cnt[nm] = cnt.get(nm, 0) + 1
            elif st.get("who") and st.get("id") in ("F1", "F2", "F3", "F4", "F5"):
                cnt[st["who"]] = cnt.get(st["who"], 0) + 1
    return cnt


def make_pose_plan(nbeats, rng, table=None, rounds=None):
    """one scene's chain of steps, split per beat. Each step {"id", "w": bool (she climaxes here)}. Rules: starts with a warm-up step; an
    oral/penetration step never follows a man's climax without a recovery step; the scene ends with the man's finish; every legal edge follows
    the table's "next". Returns a list (one list of steps per beat)."""
    t = table or pose_table()
    nbeats = max(1, int(nbeats))
    S = max(4, 2 * nbeats)
    if rounds is None:
        mids = 0 if nbeats < 3 else rng.randint(0, 1 if nbeats < 5 else 2)
    else:
        mids = 1 if (rounds == 2 and nbeats >= 3) else 0   # R96: one finish + recovery in the middle for a proper scene
    core = S - 1 - 2 * mids
    segs = [core // (mids + 1) + (1 if i < core % (mids + 1) else 0) for i in range(mids + 1)]
    chain = []
    prev = None
    used = {}
    for si, ln in enumerate(segs):
        for j in range(ln):
            if prev is None:
                cands = [x for x in POSE_START if x in t]
            elif _pose_kind(t, prev) == "recover" or _pose_kind(t, prev) == "finish":
                cands = [x for x in (t.get(prev) or {}).get("next", []) if x in t]
            else:
                cands = [x for x in (t.get(prev) or {}).get("next", []) if x in t and x != prev]
            if si == 0 and j == 0:
                pass
            if not cands:
                cands = [x for x in t if _pose_kind(t, x) == "pen"]
            # late in a segment prefer penetration; avoid repeating a step used before
            late = j == ln - 1 and j >= 1   # R38: only the last core step of a segment is steered to penetration (the earlier ones may be oral/warm), so openings differ more
            pool = [x for x in cands if _pose_kind(t, x) == "pen"] if late and any(_pose_kind(t, x) == "pen" for x in cands) else cands
            fresh = [x for x in pool if used.get(x, 0) == 0] or pool
            pick = rng.choice(fresh)
            used[pick] = used.get(pick, 0) + 1
            chain.append({"id": pick})
            prev = pick
        if si < mids:
            if rounds is None:
                chain.append({"id": rng.choice([x for x in t if _pose_kind(t, x) == "finish"] or ["F3"])})
            else:
                chain.append({"id": rng.choice([x for x in _FIRST_KINDS if x in t] or ["F1"]), "rd": 1})
            chain.append({"id": rng.choice([x for x in t if _pose_kind(t, x) == "recover"] or ["R1"])})
            prev = chain[-1]["id"]
    fin = [x for x in t if _pose_kind(t, x) == "finish"] or ["F3"]
    if rounds == 2 and mids:
        chain.append({"id": rng.choice(_second_pool(t)), "rd": 2})
    else:
        chain.append({"id": rng.choice(fin)})
    # her climaxes: on one or two oral/penetration steps that are not the first; at least one before the final finish
    act = [i for i, c in enumerate(chain[:-1]) if _pose_kind(t, c["id"]) in ("oral", "pen") and i > 0]
    if act:
        for i in rng.sample(act, min(len(act), rng.randint(1, 2))):
            chain[i]["w"] = True
        if not any(c.get("w") for c in chain):
            chain[act[-1]]["w"] = True
    else:
        chain[-1]["w"] = True
    # split into beats in order
    out, n = [], len(chain)
    for b in range(nbeats):
        lo, hi = (n * b) // nbeats, (n * (b + 1)) // nbeats
        out.append(chain[lo:hi])
    return out


QUICKIE_RE = re.compile(r"ควิกกี้|quickie|ทำให้(?:เขา)?(?:แบบ)?เร็ว|แบบเร็วๆ|รีบๆ\s*(?:ทำให้|อม|เอา)")


def make_quickie_plan(nbeats, rng, table=None):
    """R52 (29 Oct, user): a quickie scene where the woman gives it to the man fast: a short start, then the mouth (C1) or the breasts (C6), then his
    finish in her mouth or on her body. No climax of hers is required (she does it for him). Steps carry q=True."""
    t = table or pose_table()
    start = rng.choice([x for x in ("A1", "B1", "B2", "A2") if x in t] or ["B1"])
    giver = rng.choice([x for x in ("C1", "C6", "C7") if x in t] or ["C1"])
    # R96 (user: titjob / handjob can stand alone, like sucking him until he comes in her mouth): the finish follows the giver
    fin = rng.choice([x for x in {"C1": ("F1", "F2"), "C6": ("F4", "F2"), "C7": ("F5", "F2")}.get(giver, ("F1", "F2")) if x in t] or ["F2"])
    chain = [{"id": start}, {"id": giver}, {"id": fin, "q": True}]
    nb = max(1, int(nbeats))
    out, n = [], len(chain)
    for b in range(nb):
        out.append(chain[(n * b) // nb:(n * (b + 1)) // nb])
    return out


def make_group_plan(nbeats, rng, A, B, table=None, dp=False, tail_ok=True, rounds=1):
    """one scene's chain for one woman and two men A, B (names). Steps: {"id", "w", "who"?, "fin"?}. Shape: warm-up for both, group steps (her
    climax in one or two), then (3+ beats) the first man comes and rests while the second continues alone, then he comes too; with 5+ beats a
    recovery of both, another group step and a last common finish. Two beats: warm-up, two group steps, one common finish. Both men always come."""
    t = table or pose_table()
    G = GROUP_TABLE
    n = max(1, int(nbeats))
    S = max(4, 2 * n)
    if n < 3:
        ng, ns, tail = 2, 0, False
    elif n < 5:
        ns = 1 if n == 3 else 2
        ng, tail = S - 3 - ns, False
    else:
        ns, tail = 2, bool(tail_ok)   # R79: a scene whose summary names where each man comes has no second round of finishes
        ng = S - 3 - ns - (3 if tail else 0)
    first, second = (A, B) if rng.random() < 0.5 else (B, A)
    chain = [{"id": "M1"}]
    used = {}
    prev = ["M1"]

    def add_group():
        gcount = sum(1 for c in chain if (G.get(c["id"]) or {}).get("k") in ("oral", "pen"))
        c = [x for x in G[prev[0]].get("next", []) if x in G and G[x]["k"] in ("oral", "pen") and x != prev[0]]
        if gcount < 2 or used.get("M7", 0):
            c = [x for x in c if x != "M7"]
        if not c:
            c = [x for x in G if G[x]["k"] in ("oral", "pen") and x != "M7" and x != prev[0]]
        fresh = [x for x in c if used.get(x, 0) == 0] or c
        p = rng.choice(fresh)
        used[p] = used.get(p, 0) + 1
        chain.append({"id": p})
        prev[0] = p

    fins = [x for x in t if _pose_kind(t, x) == "finish"] or ["F3"]
    two = rounds == 2 and n >= 3
    for _ in range(0 if two else ng):
        add_group()
    if two:
        # R96: round 1 (early: mouth / titjob / handjob for each man, together), a recovery, group steps, round 2 (after the sex: inside / outside, one man after the other)
        oral = [x for x in G["M1"]["next"] if x in G and G[x]["k"] == "oral"]
        if oral:
            pk = rng.choice(oral)
            used[pk] = 1
            chain.append({"id": pk})
            prev[0] = pk
        else:
            add_group()
        chain.append({"id": "FB", "fin": _first_fins(t, rng, [first, second]), "rd": 1})
        chain.append({"id": "RB"})
        prev[0] = "RB"
        for _ in range(max(1, S - 8)):
            add_group()
        pool2 = _second_pool(t)
        chain.append({"id": rng.choice(pool2), "who": first, "rd": 2})
        sp = None
        for _ in range(2 if n >= 5 else 1):
            c = [x for x in ((t.get(sp) or {}).get("next", []) if sp else _SOLO_PEN) if x in _SOLO_PEN and x != sp] or _SOLO_PEN
            sp = rng.choice(c)
            chain.append({"id": sp, "who": second})
        chain.append({"id": rng.choice(pool2), "who": second, "rd": 2})
    elif n < 3:
        k = rng.sample(["F1", "F2", "F3"], 2)
        chain.append({"id": "FB", "fin": {first: k[0], second: k[1]}})
    else:
        chain.append({"id": rng.choice(fins), "who": first})
        sp = None
        for _ in range(ns):
            c = [x for x in ((t.get(sp) or {}).get("next", []) if sp else _SOLO_PEN) if x in _SOLO_PEN and x != sp] or _SOLO_PEN
            sp = rng.choice(c)
            chain.append({"id": sp, "who": second})
        chain.append({"id": rng.choice(fins), "who": second})
        if tail:
            chain.append({"id": "RB"})
            prev[0] = "RB"
            add_group()
            k = rng.sample(["F1", "F2", "F3"], 2)
            chain.append({"id": "FB", "fin": {first: k[0], second: k[1]}})
    if dp:   # R79: the summary asks for the double: M7 replaces the last group step before the first finish (needs two oral/pen steps before it)
        gi = [i for i, c in enumerate(chain) if (G.get(c["id"]) or {}).get("k") in ("oral", "pen")]
        if len(gi) >= 3 and "M7" not in [c["id"] for c in chain]:
            chain[gi[-1]] = {"id": "M7"}
    force = os.environ.get("NOVEL_FORCEPOSE", "")   # experiment switch: put this group pose (e.g. M7) into every group chain, in place of the last group step before the first finish
    if force in G and force not in [c["id"] for c in chain]:
        gi = [i for i, c in enumerate(chain) if (G.get(c["id"]) or {}).get("k") in ("oral", "pen")]
        if gi:
            chain[gi[-1]] = {"id": force}
    act = [i for i, c in enumerate(chain) if i > 0 and _pose_kind(t, c["id"]) in ("oral", "pen")]
    for i in rng.sample(act, min(len(act), rng.randint(1, 2))):
        chain[i]["w"] = True
    if act and not any(c.get("w") for c in chain):
        chain[act[0]]["w"] = True
    out, m = [], len(chain)
    for b in range(n):
        out.append(chain[(m * b) // n:(m * (b + 1)) // n])
    return out


# R69 (29 Oct, user: "3 ชาย 1 หญิง"): TRIO TABLE for one woman + three men A, B, C (all three with her at once; the men never touch each other). Her body has
# five places: mouth (ONE cock), pussy (ONE cock), anus (only in the double-penetration step T6), and two hands (a cock each). Every pose uses all three men
# in roles A, B, C; the plan rotates which man plays which role from step to step.
TRIO_TABLE = {
    "T1": {"n": "สามคนเล้าโลมเธอ", "k": "warm", "d": "{W} sits or lies on the bed between {A}, {B} and {C}: {A} kisses her mouth, {B} kisses her neck, nipples and breasts, {C} kisses and strokes her thighs; clothes come off; no cock touches her yet", "next": ["T2", "T3", "T4", "T5", "T7", "T9"]},
    "T2": {"n": "อมควยหนึ่ง มือจับสอง", "k": "oral", "d": "{W} kneels in front of the three standing men: {A}'s cock is in her mouth while her right hand strokes {B}'s cock and her left hand strokes {C}'s cock, then they swap (one cock at her lips at a time); nobody is inside her pussy", "next": ["T3", "T4", "T5", "T7", "T9", "T10"]},
    "T3": {"n": "เลียหี + อมควย + มือจับ", "k": "oral", "d": "{W} lies on her back: {A} lies between her thighs licking her pussy and clit with his tongue and fingers only; {B} kneels by her head and she sucks his cock; {C} kneels on her other side and she strokes his cock with her hand; nobody penetrates her", "next": ["T4", "T5", "T6", "T7", "T8", "T10"]},
    "T4": {"n": "ด็อกกี้ + อมควย + มือจับ", "k": "pen", "d": "{W} is on her hands and knees: {A} kneels behind her with his cock in her pussy; {B} kneels in front of her with his cock in her mouth; {C} kneels beside her and she strokes his cock with one hand (the other hand holds the bed)", "next": ["T5", "T6", "T7", "T8", "T10"]},
    "T5": {"n": "ขึ้นคร่อม + อมควย + มือจับ", "k": "pen", "d": "{A} lies on his back and {W} rides him facing him, his cock in her pussy; {B} kneels beside her head and she sucks his cock; {C} kneels on her other side and she strokes his cock with her hand", "next": ["T4", "T6", "T7", "T8", "T10"]},
    "T6": {"n": "double: สองแท่งในหี + อมควย", "k": "pen", "dp": True, "d": "DOUBLE PENETRATION plus mouth, slow and careful: {A} lies on his back and {W} lies on top of him facing him with her chest on his, his cock in her pussy; {B} kneels behind him between their legs and slowly eases his own cock into the SAME pussy beside {A}'s, so two cocks are inside her pussy at once; {C} kneels in front of her face and she sucks his cock; she moans, feels stretched and asks them to go slowly, then they move together; two cocks in the pussy, one in the mouth, nothing in her anus, the men do not touch each other. ROLES for the whole step: {A} and {B} are both in her pussy, {C} stays at her mouth", "next": ["T5", "T4"]},
    "T7": {"n": "มิชชันนารี + อมควย + มือจับ", "k": "pen", "d": "missionary: {W} on her back, {A} between her legs with his cock in her pussy; {B} kneels beside her head and she sucks his cock; {C} kneels on her other side and she strokes his cock with her hand", "next": ["T4", "T5", "T6", "T8", "T10"]},
    "T8": {"n": "ตะแคงช้อน + อมควย + นมหนีบ", "k": "pen", "d": "{W} lies on her side: {A} lies behind her in spoon position, his cock in her pussy from behind; {B} lies in front of her and she sucks his cock; {C} kneels over her chest and slides his cock between her breasts, which she presses together (his cock touches only her breasts)", "next": ["T4", "T5", "T7", "T10"]},
    "T9": {"n": "นมหนีบ + เลียหี + มือจับ", "k": "oral", "d": "{W} lies on her back: {B} kneels astride her chest and slides his cock between her breasts which she presses together (his cock touches only her breasts); {A} lies between her thighs licking her pussy and clit with his tongue and fingers only; {C} kneels beside her head and she strokes his cock with her hand and licks the tip; nobody penetrates her", "next": ["T4", "T5", "T7", "T10"]},
    "T10": {"n": "นั่งตัก + อมควย + เล้าโลมหลัง", "k": "pen", "d": "{A} sits on the bed edge, {W} sits on his lap facing him with his cock in her pussy; {B} stands beside her and she sucks his cock; {C} kneels behind her, kisses her neck and plays with her breasts (his cock only rubs her back, it is not inside her)", "next": ["T4", "T5", "T6", "T8"]},
    "FT": {"n": "แตกพร้อมกันสามคน", "k": "finish", "d": "all three men come in this step (each in the way given below); make sure {W} has come before or with them", "next": []},
    "RT": {"n": "ปลุกทั้งสามคน", "k": "recover", "d": "all three men have just come and are soft: {W} licks, sucks and strokes {A}, {B} and {C} one after the other (one cock in her mouth at a time) while they kiss and touch her, until all are hard again; no penetration in this step", "next": ["T2", "T4", "T5", "T7"]},
}


def make_group3_plan(nbeats, rng, men, table=None, dp=False, tail_ok=True, rounds=1):
    """one scene's chain for one woman and three men (names list). Steps: {"id", "w", "r": [A,B,C] roles, "who", "ab"/"rest", "fin"}.
    Shape: warm-up and trio steps with all three men; (3+ beats) the first man comes and rests while the other two continue with the two-men table;
    the second comes and rests while the third continues alone; the third comes. 5+ beats add a recovery of all three, one more trio step and a last
    common finish. Under 3 beats: warm-up, two trio steps, one common finish. Every man always comes."""
    t = table or pose_table()
    n = max(1, int(nbeats))
    order = list(men)
    rng.shuffle(order)
    m1, m2, m3 = order
    chain = [{"id": "T1", "r": list(order)}]
    used = {}
    prev = ["T1"]
    last_r = [tuple(order)]

    def roles():
        for _ in range(8):
            r = list(men)
            rng.shuffle(r)
            if tuple(r) != last_r[0]:
                break
        last_r[0] = tuple(r)
        return r

    def add_trio():
        c = [x for x in TRIO_TABLE[prev[0]].get("next", []) if x in TRIO_TABLE and TRIO_TABLE[x]["k"] in ("oral", "pen") and x != prev[0]]
        gcount = sum(1 for x in chain if (TRIO_TABLE.get(x["id"]) or {}).get("k") in ("oral", "pen"))
        if gcount < 2 or used.get("T6", 0):
            c = [x for x in c if x != "T6"]
        if not c:
            c = [x for x in TRIO_TABLE if TRIO_TABLE[x]["k"] in ("oral", "pen") and x not in ("T6", prev[0])]
        fresh = [x for x in c if used.get(x, 0) == 0] or c
        pk = rng.choice(fresh)
        used[pk] = used.get(pk, 0) + 1
        chain.append({"id": pk, "r": roles()})
        prev[0] = pk

    fins = [x for x in t if _pose_kind(t, x) == "finish"] or ["F3"]
    ngr = 2 if n < 3 else (1 if n == 3 else 3)   # R72: 4+ beats get three trio steps so the double-penetration pose (needs two oral/pen steps before it) can actually come up
    two = rounds == 2 and n >= 3
    for _ in range(0 if two else ngr):
        add_trio()
    if two:
        # R96: round 1 (early, all three: mouth / titjob (one man) / handjob), a recovery, trio steps, then round 2 one man after the other (inside / outside)
        oral = [x for x in TRIO_TABLE["T1"]["next"] if x in TRIO_TABLE and TRIO_TABLE[x]["k"] == "oral"]
        if oral:
            pk = rng.choice(oral)
            used[pk] = 1
            chain.append({"id": pk, "r": roles()})
            prev[0] = pk
        else:
            add_trio()
        chain.append({"id": "FT", "r": list(order), "fin": _first_fins(t, rng, order), "rd": 1})
        chain.append({"id": "RT", "r": roles()})
        prev[0] = "RT"
        for _ in range(2):
            add_trio()
        pool2 = _second_pool(t)
        chain.append({"id": rng.choice(pool2), "who": m1, "rd": 2})
        gp = [x for x in GROUP_TABLE if GROUP_TABLE[x]["k"] in ("oral", "pen") and x != "M7"]
        chain.append({"id": rng.choice(gp), "ab": [m2, m3], "rest": [m1]})
        chain.append({"id": rng.choice(pool2), "who": m2, "rd": 2})
        chain.append({"id": rng.choice(_SOLO_PEN), "who": m3})
        chain.append({"id": rng.choice(pool2), "who": m3, "rd": 2})
    elif n < 3:
        k = ["F1", "F2", "F3"]
        rng.shuffle(k)
        chain.append({"id": "FT", "r": list(order), "fin": dict(zip(order, k))})
    else:
        chain.append({"id": rng.choice(fins), "who": m1})
        gp = [x for x in GROUP_TABLE if GROUP_TABLE[x]["k"] in ("oral", "pen") and x != "M7"]
        chain.append({"id": rng.choice(gp), "ab": [m2, m3], "rest": [m1]})
        chain.append({"id": rng.choice(fins), "who": m2})
        sp = rng.choice(_SOLO_PEN)
        chain.append({"id": sp, "who": m3})
        chain.append({"id": rng.choice(fins), "who": m3})
        if n >= 5 and tail_ok:
            chain.append({"id": "RT", "r": roles()})
            prev[0] = "RT"
            add_trio()
            k = ["F1", "F2", "F3"]
            rng.shuffle(k)
            chain.append({"id": "FT", "r": list(order), "fin": dict(zip(order, k))})
    if dp and ngr >= 3 and "T6" not in [c["id"] for c in chain]:   # R79: the summary asks for the double: it replaces the last trio step
        ti = [i for i, c in enumerate(chain) if c["id"] in TRIO_TABLE and TRIO_TABLE[c["id"]]["k"] in ("oral", "pen")]
        if len(ti) >= 3:
            chain[ti[-1]] = {"id": "T6", "r": roles()}
    act = [i for i, c in enumerate(chain) if i > 0 and _pose_kind(t, c["id"]) in ("oral", "pen")]
    for i in rng.sample(act, min(len(act), rng.randint(1, 2))):
        chain[i]["w"] = True
    if act and not any(c.get("w") for c in chain):
        chain[act[0]]["w"] = True
    out, m = [], len(chain)
    for b in range(n):
        out.append(chain[(m * b) // n:(m * (b + 1)) // n])
    return out


_ACT_OF = {   # R107: what each finish kind is, in words the model cannot mix up (used for every multi-man finish step, any cast, any story)
    "F1": ("is the only one in her mouth", "in her mouth"),
    "F2": ("is the only one who pulls out and finishes outside her", "on her body"),
    "F3": ("is the only one inside her pussy", "inside her pussy"),
    "F4": ("is the only one with his cock between her breasts (the titjob is his alone)", "on her breasts and face"),
    "F5": ("is the only one with his cock in her hand (the handjob is his alone)", "in her hand"),
}


def _act_binding(fin):
    """R107 (user, 31 Oct: fixes must be principles that work for every scene and every novel): in a finish step where several men come, bind each man to his OWN act and his own climax so the model cannot give one man's act to another (r107-dp: the planned titjob man was written in the mouth act and the mouth man in the titjob). Works for any men names and any kinds; men with the same kind are not bound (nothing to swap)."""
    if not isinstance(fin, dict) or len(fin) < 2:
        return ""
    cnt = {}
    for k_ in fin.values():
        cnt[k_] = cnt.get(k_, 0) + 1
    parts, ends = [], []
    for nm, k_ in fin.items():
        if k_ in _ACT_OF and cnt[k_] == 1:
            parts.append("%s %s" % (nm, _ACT_OF[k_][0]))
            ends.append("%s's cum lands %s" % (nm, _ACT_OF[k_][1]))
    if len(parts) < 2:
        return ""
    return " | FIXED ARRANGEMENT for this whole step (nobody swaps acts): %s. Write each man's own act and his own climax in it, one after another: %s." % ("; ".join(parts), "; ".join(ends))


def pose_block_group3(part):
    pp = part["pose"]
    t = pose_table()
    t = _tj_table(t, pp)
    cs = pp.get("cast") or {}
    men = cs.get("men") or ["A", "B", "C"]
    W = cs.get("W", "she")
    L = ["POSITION PLAN FOR %s WITH THREE MEN, %s, %s AND %s, AT THE SAME TIME (ground truth: the positions come from a fixed table so the bodies stay in possible places; obey it exactly, in this order, and use NO other position in this part):" % (W, men[0], men[1], men[2])]
    if pp.get("carry"):
        L.append("CARRY-OVER FROM THE LAST SCENE: %s was left wanting more and is still aroused: show it in the first lines of this part (a look, a touch, a request) before STEP 1." % pp["carry"])
    if pp.get("prev"):
        pv = t.get(pp["prev"]) or GROUP_TABLE.get(pp["prev"]) or TRIO_TABLE.get(pp["prev"]) or {}
        L.append("Where the scene stands now: %s." % (pv.get("n", "")))
    for k, st in enumerate(pp["steps"], 1):
        pid = st["id"]
        row = TRIO_TABLE.get(pid) or GROUP_TABLE.get(pid) or t.get(pid) or {}
        kind = row.get("k", "")
        who = st.get("who")
        if pid in TRIO_TABLE:
            r = st.get("r") or men
            ln = "STEP %d [%s]: %s" % (k, row.get("n", pid), row.get("d", "").format(A=r[0], B=r[1], C=r[2], W=W))
            if pid == "FT":
                fin = st.get("fin") or {}
                ln += " | " + " ; ".join("%s: %s" % (nm, (t.get(fid) or {}).get("d", "")) + ((" (the cum lands on her %s)" % st["place"][nm]) if (st.get("place") or {}).get(nm) else "") for nm, fid in fin.items())
                pl = {"F3": "is between her legs with his cock in her pussy", "F1": "kneels at her head with his cock at her lips", "F2": "kneels beside her with his cock in his own hand"}
                ar = [("%s %s" % (nm, pl[fid])) for nm, fid in fin.items() if fid in pl]
                ln += _act_binding(fin)   # R107: generic - any set of first/second-round kinds, any men
                if len(ar) == 3:
                    ln += " | FIXED ARRANGEMENT for this whole step (nobody changes place, nobody swaps): %s is on her back; %s. Only the man at her head is ever in her mouth; only the man between her legs is ever in her pussy; write the three climaxes one after another in this arrangement." % (W, "; ".join(ar))
        elif st.get("ab"):
            a, b_ = st["ab"]
            ln = "STEP %d [%s]: ONLY %s and %s are with %s now; %s has already come and rests beside them, kissing her and touching her with his hands, his cock soft and not used: %s" % (
                k, row.get("n", pid), a, b_, W, " and ".join(st.get("rest") or []), row.get("d", "").format(A=a, B=b_, W=W))
        else:
            others = [x for x in men if x != who]
            ln = "STEP %d [%s]: ONLY %s is with %s in this step (in the description below 'he' / 'his' means %s)%s: %s" % (
                k, row.get("n", pid), who or men[0], W, who or men[0],
                ("; " + " and ".join(others) + " have already come and rest beside them, kissing her and touching her breasts with their hands, their cocks soft and not used") if who and kind != "finish" else "",
                row.get("d", ""))
        if st.get("w"):
            ln += " | HER CLIMAX happens in this step: show it plainly; afterwards they simply carry on, she needs no recovery."
            ln += _climax_extra(st)
        if kind == "finish" and pid != "FT":
            ln += " | %s's CLIMAX happens here and only here; %s has come before or with him." % (who or men[0], W)
        if pid == "T6":   # R80 (r79-dp: the model wrote only the mouth part of the double, in 4 attempts)
            rr = st.get("r") or men
            ln += " | THIS STEP MUST CONTAIN, each in its own short paragraph and in this order: (1) %s is already in her pussy; (2) %s slowly eases his cock into the SAME pussy beside it (ดันควยเข้าไปในหีพร้อมกับอีกคน), her pussy is stretched full with TWO cocks (ควยสองแท่งในหีเดียว), she gasps and asks them to go slow; (3) then both move together while %s is in her mouth. Use the words สองแท่ง and พร้อมกัน. Nothing goes in her anus." % (rr[0], rr[1], rr[2])
        if pid == "FT":
            ln += " | ALL THREE MEN'S CLIMAX happens here and only here."
            if st.get("rd") == 1:
                ln += ' | THIS IS THE FIRST TIME EACH OF THEM COMES IN THIS SCENE (an early finish, before the real sex): write it fully, then they rest and are soft; each comes AGAIN later in the scene.'
        if st.get("rd") == 2 and kind == "finish":
            ln += ' | THIS IS HIS SECOND TIME COMING IN THIS SCENE (after the sex): write it as the real climax of the scene, in the way given for this step.'
        if kind == "recover":
            ln += " | The men stay soft until the end of this step; nobody penetrates anyone in it."
        L.append(ln)
    if not any(_pose_kind(t, x["id"]) == "finish" for x in pp["steps"]):   # R80 (r79-dp: a man came in beat 2 and again at the end)
        L.append("NOBODY COMES IN THIS PART: no man ejaculates here and none shouts that he HAS come; do not write แตกแล้ว, น้ำเชื้อ, น้ำควย or ปล่อยน้ำ for any man (only her climax may happen); a man may at most say he is close.")
    L.append("PLAN RULES: each man's cock is in ONE place at a time; her mouth holds ONE cock at a time, her pussy ONE cock (TWO cocks only in the double-penetration step), her anus never, each hand at most one cock; the men NEVER touch, kiss, lick or rub each other; a man comes only at his CLIMAX step; after he comes he is soft and rests (he may kiss her and touch her with his hands) until a recovery step; the woman has a pussy only and the men a cock only; keep every paragraph to one position; every spoken line that uses ผม, กู, มึง or ครับ is followed by a tag naming the man who says it (“...” ก้องครางเสียงแหบ).")
    L.append("SPEAKING RULES (four people are talking): every spoken line is said by exactly ONE person and its tag names that person (%s, %s, %s or %s). Her moans, her climax and her requests are said by %s ONLY, tagged to her, never to a man. The men speak TO her or about her; the three men never speak vulgar or insulting words to each other and never talk about each other's body (between themselves only a short plain line such as asking the other to wait or to move). A man never calls himself by his own name and never speaks of himself in the third person. She never says กู or มึง; the men never say ชั้น. In the narration three men are present, so never write a bare 'เขา' for a man: write his name; and do not repeat 'ควยของ…' in every sentence (at most twice per paragraph, otherwise just say what he does). 'ให้หมด' goes only with a liquid (กลืนให้หมด, กินน้ำควยให้หมด), never with a body part (never 'ดูดควยให้หมด')." % (men[0], men[1], men[2], W, W))
    return " ".join(L[:1]) + "\n" + "\n".join(L[1:])


# R110 (user 31 Oct): scene with FOUR men and TWO women. Built from the pieces that already work: each woman with one, two or three men uses the single / group / trio
# poses; the two women have their own table (kiss, deep kiss, licking, fingers, scissors); a man (woman) who is not busy watches and strokes himself (touches herself)
# without coming; men swap between the women (both groups at once, or only some men); every man comes at least twice; the scene may end with some men coming in one
# woman's mouth and the two women kissing and sharing it.
WW_TABLE = {
    "X1": {"n": "จูบกันเบาๆ", "k": "warm", "d": "{A} and {B} (the two women) kiss each other softly on the lips, cheeks and neck, hands on each other's waists and hair; no tongues yet"},
    "X2": {"n": "จูบดูดดื่ม", "k": "warm", "d": "{A} and {B} kiss each other deeply, tongues sliding, sucking each other's lips, bodies pressed together, hands on each other's breasts"},
    "X3": {"n": "เลียหน้าอกกัน", "k": "oral", "d": "{A} lies back and {B} kisses down her neck and licks and sucks her breasts and nipples, one hand between her thighs; then they swap"},
    "X4": {"n": "เลียหีกัน", "k": "oral", "d": "{A} lies on her back with her legs open and {B} kneels between her thighs licking and sucking her pussy and clit; later they swap places"},
    "X5": {"n": "เกี่ยวเบ็ดให้กัน", "k": "oral", "sq": True, "d": "{B} slides two fingers into {A}'s pussy, curls them upward (hooked) and rubs fast while her mouth is on {A}'s breast or clit; {A} shakes and squirts: a jet of clear fluid shoots over {B}'s hand and the sheet"},
    "X6": {"n": "ท่ากรรไกร", "k": "pen", "d": "scissoring: {A} and {B} lie facing each other with their legs interlocked, their pussies pressed together and rubbing, hands on each other's breasts, until they climax"},
    "X7": {"n": "69 หญิงกับหญิง", "k": "oral", "d": "69 between the two women: {A} lies on her back and {B} is over her facing her feet; each one's mouth is on the other's pussy and clit"},
}
QUAD_TABLE = {
    "Q1": {"n": "ทุกคนเล้าโลม", "k": "warm", "d": "{W1}, {W2} and the four men ({M}) undress and kiss: the two women kiss each other and the men kiss and touch both of them; clothes come off; no cock inside anyone yet"},
    "QV": {"n": "หญิงจูบกัน ชายดูชักรอ", "k": "warm", "d": "the two women are busy with each other; the men do not touch them"},
    "QW": {"n": "หญิงกับหญิง ชายดูชักรอ", "k": "oral", "d": "the two women are busy with each other; the men do not touch them"},
    "QG": {"n": "สองกลุ่มพร้อมกัน", "k": "pen", "d": "each woman is with her own men at the same time"},
    "QM": {"n": "หญิงหนึ่งกับชาย อีกคนช่วยตัวเอง", "k": "pen", "d": "one woman is with some men while the other woman masturbates and watches"},
    "FQ": {"n": "ชายสี่คนแตกรอบแรก", "k": "finish", "d": "all four men come in this step, each with his own woman, each in the way given below"},
    "RQ": {"n": "หญิงปลุกชายทั้งสี่", "k": "recover", "d": "the four men have just come and are soft: the two women lick, suck and stroke them and kiss each other and the men until all four are hard again; no penetration in this step"},
    "CQ": {"n": "แตกในปากแล้วจูบแลกน้ำ", "k": "finish", "d": "closing image: the men named below come in one woman's mouth one after another; she keeps the cum in her mouth, then the two women kiss deeply and pass the cum between their mouths"},
}
_QSOLO = ["D1", "D2", "D4", "E1", "E2", "D5", "C1", "C2", "H1", "H2", "H3", "H4", "H5", "D6", "D8", "D10"]


_CLOSE_RE = re.compile(r"แลกน้ำ(?:ควย|เชื้อ)?|จูบแลก|แบ่งน้ำ(?:ควย|เชื้อ)")


def close_wanted(txt):
    """how many men come in the closing mouth-and-kiss step: 0 when the summary says no sharing, else None (random 1-3)."""
    if re.search(r"ไม่[^\n]{0,8}(?:จูบแลก|แลกน้ำ|แบ่งน้ำ)", txt or ""):
        return 0
    m = re.search(r"([1-3]|หนึ่ง|สอง|สาม)\s*คน[^\n]{0,12}แตกในปาก", txt or "")
    if m:
        return {"หนึ่ง": 1, "สอง": 2, "สาม": 3}.get(m.group(1)) or int(m.group(1))
    return None


def close_pos(txt):
    """True when the summary puts the cum-sharing kiss at the end, False when in the middle, else None (random)."""
    if re.search(r"(?:ท้ายฉาก|ตอนจบ|จบฉาก|ปิดฉาก|ท้ายสุด)[^\n]{0,25}(?:จูบ|แลก)|(?:จูบ|แลกน้ำ)[^\n]{0,25}(?:ท้ายฉาก|ตอนจบ|จบฉาก|ปิดฉาก|ท้ายสุด)", txt or ""):
        return True
    if re.search(r"(?:ระหว่างฉาก|กลางฉาก|ระหว่างนั้น|กลางเรื่อง)[^\n]{0,25}(?:จูบ|แลก)|(?:จูบ|แลกน้ำ)[^\n]{0,25}(?:ระหว่างฉาก|กลางฉาก|กลางเรื่อง)", txt or ""):
        return False
    return None


def _quad_first_fins(rng, asg):
    """first-round finish kinds for four men: each woman's two men get two different kinds from mouth / titjob / handjob, one titjob in all."""
    out, f4 = {}, False
    for w, ms in asg.items():
        pool = ["F1", "F4", "F5"]
        rng.shuffle(pool)
        for m in ms:
            k = next((x for x in pool if not (x == "F4" and f4)), "F1")
            pool = [x for x in pool if x != k] or ["F1", "F5"]
            f4 = f4 or k == "F4"
            out[m] = k
    return out


def make_quad_plan(nbeats, rng, men, women, table=None, close_n=None, close_last=None):
    """chain for TWO women and FOUR men (names). Steps: {"id", "cells"/"ww"/"mast"/"rest", "wc": [women who climax], "sqw": [women who squirt], "fin", "wm", "who", "wf", "rd"}.
    Shape: warm-up, 2-3 layout steps, round-1 finish of all four (each with his woman), recovery, 2-3 layout steps, the men who do not close finish one by one
    (outside / inside, each with a woman), then the closing step: the remaining men come in one woman's mouth and the women share it with a kiss. Each man comes twice."""
    t = table or pose_table()
    W1, W2 = women[0], women[1]
    n = max(1, int(nbeats))
    order = list(men)
    rng.shuffle(order)
    asg = {W1: order[:2], W2: order[2:]}
    place = {m: None for m in men}
    last = {W1: None, W2: None}
    chain = [{"id": "Q1"}]
    for w_, ms_ in asg.items():
        for m_ in ms_:
            place[m_] = w_

    def pick(pool, w):
        pool = [x for x in pool if x != last[w]] or pool
        p_ = rng.choice(pool)
        last[w] = p_
        return p_

    def cell(w, ms):
        if len(ms) == 1:
            return {"w": w, "men": list(ms), "pid": pick([x for x in _QSOLO if x in t], w)}
        if len(ms) == 2:
            return {"w": w, "men": list(ms), "pid": pick([x for x in GROUP_TABLE if GROUP_TABLE[x]["k"] in ("oral", "pen") and x != "M7"], w)}
        return {"w": w, "men": list(ms), "pid": pick([x for x in TRIO_TABLE if TRIO_TABLE[x]["k"] in ("oral", "pen") and x != "T6"], w)}

    def swap():
        if rng.random() < 0.5:
            asg[W1], asg[W2] = asg[W2], asg[W1]
        else:
            i, j = rng.randrange(2), rng.randrange(2)
            asg[W1][i], asg[W2][j] = asg[W2][j], asg[W1][i]

    def layout(kind):
        if kind == "QG":
            swap()
            st = {"id": "QG", "cells": [cell(W1, asg[W1]), cell(W2, asg[W2])], "rest": []}
        elif kind == "QM":
            a, b = (W1, W2) if rng.random() < 0.5 else (W2, W1)
            k_ = rng.choice([1, 2, 2, 3])
            ms = rng.sample(list(men), k_)
            st = {"id": "QM", "cells": [cell(a, ms)], "mast": b, "rest": [m for m in men if m not in ms]}
        else:
            pid = rng.choice(["X1", "X2"] if kind == "QV" else ["X3", "X4", "X5", "X6", "X7"])
            a, b = (W1, W2) if rng.random() < 0.5 else (W2, W1)
            st = {"id": kind, "ww": [a, b], "wpid": pid, "rest": list(men)}
        # who is where now (for the "moves over" lines)
        now = {m: None for m in men}
        for c in st.get("cells", []):
            for m in c["men"]:
                now[m] = c["w"]
        st["mv"] = [[m, now[m]] for m in men if now[m] and now[m] != place[m]]
        place.update(now)
        return st

    core_n = 2 if n < 6 else 3
    kinds1 = ["QG", "QW", "QM", "QV"]
    rng.shuffle(kinds1)
    first = [k for k in kinds1 if k != "QV"][:core_n]
    if "QV" in kinds1 and core_n >= 3 and rng.random() < 0.5:
        first[-1] = "QV"
    for k in first:
        chain.append(layout(k))
    # the women are with their own men for the first finish
    asg_now = {W1: [m for m in men if place[m] == W1], W2: [m for m in men if place[m] == W2]}
    if len(asg_now[W1]) != 2 or len(asg_now[W2]) != 2:   # a layout left the men unevenly placed: put them back two by two for the finish
        rest_ = list(men)
        rng.shuffle(rest_)
        asg_now = {W1: rest_[:2], W2: rest_[2:]}
    asg.update({W1: list(asg_now[W1]), W2: list(asg_now[W2])})
    for w_, ms_ in asg.items():
        for m_ in ms_:
            place[m_] = w_
    fins1 = _quad_first_fins(rng, asg)
    chain.append({"id": "FQ", "fin": fins1, "wm": {m: place[m] for m in men}, "rd": 1})
    chain.append({"id": "RQ"})
    kinds2 = ["QG", "QM", "QW", "QG"]
    rng.shuffle(kinds2)
    for k in kinds2[:core_n]:
        chain.append(layout(k))
    pool2 = _second_pool(t)
    cn = close_n if close_n is not None else rng.choice([1, 2, 2, 3])
    cn = max(0, min(3, int(cn)))
    shuffled = list(men)
    rng.shuffle(shuffled)
    closers = shuffled[:cn]
    singles = shuffled[cn:]
    for m in singles:
        chain.append(layout(rng.choice(["QG", "QM"])))
        wf = place[m] or rng.choice([W1, W2])
        chain.append({"id": rng.choice(pool2), "who": m, "wf": wf, "rd": 2})
        place[m] = wf
    if closers:
        wa = rng.choice([W1, W2])
        wb = W2 if wa == W1 else W1
        cq = {"id": "CQ", "fin": {m: "F1" for m in closers}, "wm": {m: wa for m in closers}, "share": wb, "rd": 2, "last": True}
        # R111 (user 31 Oct): the cum-sharing kiss need not end the scene, it may come between - only after a man has come in a woman's mouth
        first_single = len(chain) - 2 * len(singles)
        if singles and close_last is not True and (close_last is False or rng.random() < 0.35):
            chain.insert(first_single, cq)
            cq["last"] = False
        else:
            chain.append(cq)
    # her climaxes: every woman at least once; random squirt
    act = [i for i, c in enumerate(chain) if i > 0 and (c.get("cells") or c.get("ww") or c.get("mast"))]
    cl = {W1: 0, W2: 0}
    for i in act:
        c = chain[i]
        who_ = [x["w"] for x in c.get("cells", [])] + list(c.get("ww") or []) + ([c["mast"]] if c.get("mast") else [])
        if c["id"] == "QV":
            continue
        wc = [w for w in who_ if rng.random() < 0.4]
        c["wc"] = wc
        for w in wc:
            cl[w] += 1
    for w in (W1, W2):
        if not cl[w] and act:
            for i in rng.sample(act, len(act)):
                c = chain[i]
                who_ = [x["w"] for x in c.get("cells", [])] + list(c.get("ww") or []) + ([c["mast"]] if c.get("mast") else [])
                if w in who_ and c["id"] != "QV":
                    c.setdefault("wc", []).append(w)
                    break
    for c in chain:
        if c.get("wc"):
            c["sqw"] = [w for w in c["wc"] if rng.random() < 0.4 or (c.get("wpid") and (WW_TABLE.get(c["wpid"]) or {}).get("sq"))]
            c["w"] = True
    out, m_ = [], len(chain)
    for b in range(n):
        out.append(chain[(m_ * b) // n:(m_ * (b + 1)) // n])
    return out


def _quad_cell_text(c, t, W_other=None):
    w, ms, pid = c["w"], c["men"], c["pid"]
    if len(ms) == 1:
        row = t.get(pid) or {}
        return "%s WITH %s ONLY (in the description 'he' means %s, 'she' means %s): %s" % (w, ms[0], ms[0], w, row.get("d", ""))
    if len(ms) == 2:
        row = GROUP_TABLE.get(pid) or {}
        return "%s WITH %s AND %s: %s" % (w, ms[0], ms[1], row.get("d", "").format(A=ms[0], B=ms[1], W=w))
    row = TRIO_TABLE.get(pid) or {}
    return "%s WITH %s, %s AND %s: %s" % (w, ms[0], ms[1], ms[2], row.get("d", "").format(A=ms[0], B=ms[1], C=ms[2], W=w))


def _quad_fin_text(fin, wm, t):
    """per-woman act binding for a multi-man finish step with two women."""
    by_w = {}
    for m, k in fin.items():
        by_w.setdefault(wm.get(m), {})[m] = k
    parts = []
    for w, f in by_w.items():
        for m, k in f.items():
            parts.append("%s (with %s): %s" % (m, w, (t.get(k) or {}).get("d", "")))
        parts.append(_act_binding(f).replace(" | FIXED ARRANGEMENT", "FIXED ARRANGEMENT (%s)" % w).strip())
    return " | " + " ; ".join(x for x in parts if x)


def pose_block_quad(part):
    pp = part["pose"]
    t = pose_table()
    t = _tj_table(t, pp)
    cs = pp.get("cast") or {}
    men = cs.get("men") or ["A", "B", "C", "D"]
    W1, W2 = cs.get("W", "she"), cs.get("W2", "she2")
    L = ["POSITION PLAN FOR TWO WOMEN, %s AND %s, AND FOUR MEN, %s, %s, %s AND %s (ground truth: the positions come from a fixed table so the bodies stay in possible places; obey it exactly, in this order, and use NO other position in this part). Rules for the whole plan: (1) every step says who is with whom: a man who is named in no cell of a step touches neither woman - he watches and strokes his OWN cock slowly with his hand, and he does NOT come in that step; (2) a woman who masturbates in a step touches only herself (fingers in her own pussy, her own breasts), watches the others, and the others do not touch her; (3) the men never touch each other; (4) each woman has her own name, her own voice and her own way of speaking: never swap their lines or names, and when both women are in one sentence write their names, not 'เธอ' twice; (5) when the plan says a man moves over to the other woman, write the move in one sentence before the new act." % (W1, W2, men[0], men[1], men[2], men[3])]
    if pp.get("carry"):
        L.append("CARRY-OVER FROM THE LAST SCENE: %s was left wanting more and is still aroused: show it in the first lines of this part (a look, a touch, a request) before STEP 1." % pp["carry"])
    for k, st in enumerate(pp["steps"], 1):
        pid = st["id"]
        row = QUAD_TABLE.get(pid) or t.get(pid) or {}
        ln = "STEP %d [%s]: " % (k, row.get("n", pid))
        if pid == "Q1":
            ln += row["d"].format(W1=W1, W2=W2, M=", ".join(men))
        elif pid in ("QG", "QM"):
            ln += row.get("d", "") + " | " + " ;; ".join(_quad_cell_text(c, t) for c in st.get("cells", []))
            if st.get("mast"):
                ln += " ;; %s MASTURBATES alone beside them: she watches, hooks two fingers into her own pussy and rubs her clit, breathing harder and getting wetter" % st["mast"]
        elif pid in ("QV", "QW"):
            a, b = st["ww"]
            ln += row.get("d", "") + " | " + (WW_TABLE.get(st["wpid"]) or {}).get("d", "").format(A=a, B=b)
        elif pid == "FQ":
            ln += row.get("d", "") + _quad_fin_text(st.get("fin") or {}, st.get("wm") or {}, t)
        elif pid == "RQ":
            ln += row.get("d", "")
        elif pid == "CQ":
            fin = st.get("fin") or {}
            wa = next(iter((st.get("wm") or {}).values()), W1)
            ln += row.get("d", "") + " | %s kneels with her mouth open; %s come%s in her mouth one after another (she keeps it all in her mouth, no swallowing yet; ONLY %s takes cum in her mouth - %s's mouth stays empty and %s does not put his cock in %s's mouth); then %s kisses %s deeply and they pass the cum from mouth to mouth, tongues and lips wet and white, a little drips from their chins%s" % (wa, " and ".join(fin), "" if len(fin) > 1 else "s", wa, st.get("share") or W2, "any man", st.get("share") or W2, st.get("share") or W2, wa, "; end the scene on that image" if st.get("last", True) else "; the scene goes on afterwards with the other men (do not end the scene here)")
        else:   # a single man's finish step (outside / inside) with one woman
            who, wf = st.get("who"), st.get("wf") or W1
            ln += "ONLY %s comes now, with %s (in the description 'he' means %s): %s | %s's CLIMAX happens here and only here" % (who, wf, who, row.get("d", ""), who)
        rest = [m for m in st.get("rest", [])]
        if pid in ("QG", "QM", "QV", "QW") and rest:
            ln += " | NOT in any cell: %s - watching and stroking their own cocks, not coming" % ", ".join(rest)
        if pid not in ("Q1", "FQ", "RQ", "CQ") and st.get("who"):
            others = [m for m in men if m != st["who"]]
            ln += " | the other men rest or stroke their own cocks, nobody else comes; the other woman %s" % "watches and touches herself"
        if st.get("mv"):
            ln += " | MOVES: " + "; ".join("%s moves over to %s" % (m, w) for m, w in st["mv"])
        for w in st.get("wc", []):
            ln += " | %s CLIMAXES in this step: show it plainly" % w + ((" and SQUIRTS: a jet of clear fluid shoots out of her pussy (over a hand / a face / her thighs / the sheet), write the spray and the wet mess") if w in st.get("sqw", []) else "")
        L.append(ln)
    L.append("The finish steps are written exactly as listed: no man comes in a part that has no finish step.")
    return "\n".join(L)


def pose_block_group(part):
    pp = part["pose"]
    t = pose_table()
    t = _tj_table(t, pp)
    cs = pp.get("cast") or {}
    A, B, W = cs.get("A", "A"), cs.get("B", "B"), cs.get("W", "she")
    L = ["POSITION PLAN FOR %s WITH TWO MEN, %s AND %s, AT THE SAME TIME (ground truth: the positions come from a fixed table so the bodies stay in possible places; obey it exactly, in this order, and use NO other position in this part):" % (W, A, B)]
    if pp.get("carry"):
        L.append("CARRY-OVER FROM THE LAST SCENE: %s was left wanting more and is still aroused: show it in the first lines of this part (a look, a touch, a request) before STEP 1." % pp["carry"])
    if pp.get("prev"):
        pv = t.get(pp["prev"]) or GROUP_TABLE.get(pp["prev"]) or {}
        L.append("Where the scene stands now: %s." % (pv.get("n", "")))
    for k, st in enumerate(pp["steps"], 1):
        pid = st["id"]
        row = GROUP_TABLE.get(pid) or t.get(pid) or {}
        kind = row.get("k", "")
        who = st.get("who")
        other = (B if who == A else A) if who else ""
        if pid in GROUP_TABLE:
            d = row.get("d", "").format(A=A, B=B, W=W)
            ln = "STEP %d [%s]: %s" % (k, row.get("n", pid), d)
            if pid == "FB":
                fin = st.get("fin") or {}
                parts_ = []
                for nm, fid in fin.items():
                    parts_.append("%s: %s" % (nm, (t.get(fid) or {}).get("d", "")) + ((" (the cum lands on her %s)" % st["place"][nm]) if (st.get("place") or {}).get(nm) else ""))
                ln += " | " + " ; ".join(parts_)
                ln += _act_binding(fin)
        else:
            d = row.get("d", "")
            ln = "STEP %d [%s]: ONLY %s is with %s in this step (in the description below 'he' / 'his' means %s)%s: %s" % (
                k, row.get("n", pid), who or A, W, who or A,
                ("; " + other + " has already come and rests beside them, kissing her and touching her breasts with his hands, his cock soft and not used") if (who and kind != "finish") or kind == "recover" else "",
                d)
        if st.get("w"):
            ln += " | HER CLIMAX happens in this step: show it plainly; afterwards they simply carry on, she needs no recovery."
            ln += _climax_extra(st)
        if kind == "finish" and pid != "FB":
            ln += " | %s's CLIMAX happens here and only here; %s has come before or with him." % (who or A, W)
            if (st.get("place") or {}).get(who or A):
                ln += " | WHERE HE COMES: the cum lands on her %s (narrate it landing there)." % st["place"][who or A]
        if pid == "M7":   # R80
            ln += " | THIS STEP MUST CONTAIN, each in its own short paragraph and in this order: (1) %s is already in her pussy; (2) %s slowly eases his cock into the SAME pussy beside it (ดันควยเข้าไปในหีพร้อมกับอีกคน), her pussy is stretched full with TWO cocks (ควยสองแท่งในหีเดียว), she gasps and asks them to go slow; (3) then both move together. Use the words สองแท่ง and พร้อมกัน. Nothing goes in her anus." % (A, B)
        if kind == "finish" and pid == "FB":
            ln += " | BOTH MEN'S CLIMAX happens here and only here."
            if st.get("rd") == 1:
                ln += ' | THIS IS THE FIRST TIME BOTH MEN COME IN THIS SCENE (an early finish, before the real sex): write it fully, then they rest and are soft; each comes AGAIN later in the scene.'
        if st.get("rd") == 2 and kind == "finish":
            ln += ' | THIS IS HIS SECOND TIME COMING IN THIS SCENE (after the sex): write it as the real climax of the scene, in the way given for this step.'
        if kind == "recover":
            ln += " | The men stay soft until the end of this step; nobody penetrates anyone in it."
        L.append(ln)
    if not any(_pose_kind(t, x["id"]) == "finish" for x in pp["steps"]):   # R80 (r79-dp: a man came in beat 2 and again at the end)
        L.append("NOBODY COMES IN THIS PART: no man ejaculates here and none shouts that he HAS come; do not write แตกแล้ว, น้ำเชื้อ, น้ำควย or ปล่อยน้ำ for any man (only her climax may happen); a man may at most say he is close.")
    L.append("PLAN RULES: each man's cock is in ONE place at a time; her mouth holds ONE cock at a time, her pussy ONE cock (TWO cocks only in the double-penetration step), her anus never; the men NEVER touch, kiss, lick or rub each other; a man comes only at his CLIMAX step; after he comes he is soft and rests (he may kiss her and touch her with his hands) until a recovery step; the woman has a pussy only and the men a cock only; keep every paragraph to one position; every spoken line that uses ผม, กู, มึง or ครับ is followed by a tag naming the man who says it (“...” ก้องครางเสียงแหบ).")
    if os.environ.get("NOVEL_GSPEAK", "1") != "0":   # R48 (29 Oct): speaker / address rules for three-person scenes
        L.append("SPEAKING RULES (three people are talking): every spoken line is said by exactly ONE person and its tag names that person (%s, %s or %s). Her moans, her climax and her requests are said by %s ONLY, tagged to her, never to a man. The men speak TO her or about her; %s and %s never speak vulgar or insulting words to each other and never talk about each other's body (between themselves only a short plain line such as asking the other to wait or to move). A man never calls himself by his own name and never speaks of himself in the third person. She never says กู or มึง; the men never say ชั้น. In the narration two men are present, so never write a bare 'เขา' for a man: write his name; and do not repeat 'ควยของ…' in every sentence (at most twice per paragraph, otherwise just say what he does). 'ให้หมด' goes only with a liquid (กลืนให้หมด, กินน้ำควยให้หมด), never with a body part (never 'ดูดควยให้หมด')." % (A, B, W, W, A, B))
    return " ".join(L[:1]) + "\n" + "\n".join(L[1:])


def pose_block(part):
    """the POSITION PLAN lines for one part (empty when the part has no plan)."""
    pp = (part or {}).get("pose")
    if not pp or not pp.get("steps"):
        return ""
    if pp.get("g") == 4:
        return pose_block_quad(part)
    if pp.get("g") == 3:
        return pose_block_group3(part)
    if pp.get("g"):
        return pose_block_group(part)
    t = pose_table()
    t = _tj_table(t, pp)
    L = ["POSITION PLAN (ground truth: the positions below come from a fixed table so the bodies stay in possible places; obey it exactly, in this order, and use NO other position in this part):"]
    if pp.get("quick"):
        L.append("QUICKIE: this scene is short and hurried: she gives it to him fast (clothes mostly on or half off, no long foreplay, no talk about feelings); it ends when he comes. Only she does things to him: he never licks, sucks or fingers her, and she is not undressed below the waist. He keeps ONE way of speaking the whole scene (ผม/ครับ, or กู/มึง if he is rough), never switching.")
    if pp.get("carry"):
        L.append("CARRY-OVER FROM THE LAST SCENE: %s was left wanting more and is still aroused: show it in the first lines of this part (a look, a touch, a request) before STEP 1." % pp["carry"])
    if pp.get("prev"):
        pv = t.get(pp["prev"]) or {}
        L.append("Where the scene stands now: %s - %s." % (pv.get("n", ""), pv.get("d", "")))
    prev = pp.get("prev")
    for k, st in enumerate(pp["steps"], 1):
        row = t.get(st["id"]) or {}
        kind = row.get("k", "")
        ln = "STEP %d [%s]: %s" % (k, row.get("n", st["id"]), row.get("d", ""))
        if prev and prev != st["id"]:
            pk = _pose_kind(t, prev)
            if kind == "recover":
                ln += " | HOW THEY GET THERE: right after he comes."
            elif kind == "finish":
                ln += " | HOW THEY GET THERE: build to it from the position they are in" + ((" (" + row.get("m") + ")") if row.get("m") else "") + "."
            elif pk in ("oral", "pen", "recover", "finish") or kind in ("oral", "pen"):
                mv = row.get("m") or "เปลี่ยนท่า"
                ln += " | HOW THEY GET THERE: first write the move itself in one or two sentences (he pulls out or lifts his head or she lets go, then e.g. “%s”), and only then the new act." % mv
        if st.get("w"):
            ln += " | HER CLIMAX happens in this step: show it plainly (her body tightening, shaking, she says she is coming); afterwards they simply carry on, she needs no recovery."
            ln += _climax_extra(st)
        if kind == "finish":
            if pp.get("quick"):
                ln += " | HIS CLIMAX happens here and only here: write the moment; this is a quickie, she does it for him and needs no climax of her own."
            else:
                ln += " | HIS CLIMAX happens here and only here: write the moment, and make sure she has come before or with him."
            if pp.get("place"):
                ln += " | WHERE HE COMES: the cum lands on her %s (narrate it landing there, not on any other part of her body)." % pp["place"]
            elif pp.get("fin") == "F1":
                ln += " | WHERE HE COMES: in her mouth (narrate it in her mouth, not on her body)."
            elif pp.get("fin") == "F3":
                ln += " | WHERE HE COMES: deep inside her pussy."
        if kind == "finish" and st.get("rd") == 1:
            ln += " | THIS IS THE FIRST TIME HE COMES IN THIS SCENE (an early finish, before the real sex): write it fully, then he rests and is soft; he comes AGAIN later in the scene."
        elif kind == "finish" and st.get("rd") == 2:
            ln += " | THIS IS HIS SECOND TIME COMING IN THIS SCENE (after the sex): write it as the real climax of the scene."
        if kind == "recover":
            ln += " | He stays soft until the end of this step; nobody may penetrate him in it."
        L.append(ln)
        prev = st["id"]
    L.append("PLAN RULES: his cock is in ONE place at a time (mouth, pussy, hand); never two positions in one paragraph; he comes only at a HIS CLIMAX step; after he comes he is soft and nothing penetrates until a recovery step has made him hard; the woman has a pussy only and the man a cock only.")
    return " ".join(L[:1]) + "\n" + "\n".join(L[1:])


def pose_run_salt(b, cid):
    """number of times this chapter has been written (persisted in the book folder): a rewrite gets a new seed, so it gets a new chain of positions."""
    try:
        fp = os.path.join(bdir(b), "pose_runs.json")
        d = json.loads(read(fp, "{}") or "{}")
        d[cid] = int(d.get(cid, 0)) + 1
        write(fp, json.dumps(d))
        return d[cid]
    except Exception:
        return 0


def _plan_sig(plan):
    return tuple(s["id"] + (":" + s["who"] if s.get("who") else "") for bt in plan for s in bt)


def finish_wanted(txt):
    """R63 (29 Oct, R62): where the author says he comes ('แตกในปาก', 'แตกบนก้น', 'แตกใน…หี') decides the finish step of the plan (F1 mouth / F2 outside / F3 inside); '' = free."""
    t = txt or ""
    if re.search(r"หนีบนม(?:จน)?แตก|ทิตจ็อบ|ทิตจอบ|titjob|tit\s*job|แตกคา(?:ร่อง)?นม|แตก(?:ใส่|บน|ที่)ร่องนม|แตก(?:ใส่|บน|ที่)นมและหน้า|แตกเปื้อนนม", t, re.I):   # R96: titjob until he comes (cum on her breasts and face)
        return "F4"
    if re.search(r"แตกคามือ|แตก(?:ใส่|ใน)มือ|handjob|hand\s*job|ชักว่าว(?:จน)?แตก|ใช้มือ(?:ชัก)?จนแตก", t, re.I):   # R96: handjob until he comes in her hand
        return "F5"
    if re.search(r"แตก(?:ใน)?ปาก|ใส่ปาก|เข้าปาก|น้ำแตกที่ปาก|ลงคอ", t):
        return "F1"
    if re.search(r"แตกใน(?!ปาก)|ข้างในหี|ปล่อยใน|หลั่งใน|ฝังน้ำ|แตกข้างใน|น้ำแตกในหี", t):
        return "F3"
    if re.search(r"แตก(?:บน|ใส่|ที่|ลง)(?:ก้น|หลัง|หน้าท้อง|ท้อง|นม|หน้าอก|หน้า|ตัว|ต้นขา|ใบหน้า|แผ่นหลัง|ตูด)|แตกนอก|แตกข้างนอก|น้ำแตกบน|น้ำแตกที่", t):
        return "F2"
    return ""


_DP_RE = re.compile(r"สอดสองรู|สองรูพร้อม|สองรู|ดับเบิล|double|DP|สอดทวารพร้อม|หีกับทวาร|หีและทวาร|สองแท่งในหี|สองแท่งพร้อม|สองควยในหี|สองดอกในหี", re.I)


def dp_wanted(txt):
    """R79 (r76-dp: the summary said 'มีท่าสอดสองรูพร้อมกัน' and the double was never written): the author asks for the double penetration."""
    return bool(_DP_RE.search(txt or ""))


def finish_wanted_men(txt, names):
    """R75 (29 Oct, r74-mix): in a scene with several men the author names where EACH man comes ('ปกรณ์แตกในปากเธอ ก้องแตกบนหน้าท้องเธอ'):
    {name: (kind F1/F2/F3, place)} for the men the text names, others free."""
    out = {}
    for nm in names:
        m = re.search(re.escape(nm) + r"\s*((?:แตก|หนีบนม(?:จน)?แตก|ทิตจ็อบ(?:จน)?แตก|ชักว่าว(?:จน)?แตก|handjob|titjob)\S{0,16})", txt or "", re.I)
        if not m:
            continue
        chunk = m.group(1)
        for o in names:
            if o != nm and o in chunk:
                chunk = chunk[:chunk.index(o)]
        k = finish_wanted(chunk)
        if k:
            out[nm] = (k, finish_place(chunk) if k == "F2" else "")
    return out


def apply_men_finish(plan, wanted):
    """overwrite the finish kinds of the named men in a group plan (beats -> steps) and remember the place of an outside finish."""
    two = any(st.get("rd") for bt in plan for st in bt)   # R96: a two-round scene: a named early kind (mouth / titjob / handjob) is the first round, a named inside / outside kind is the second
    for bt in plan:
        for st in bt:
            fin = st.get("fin")
            if isinstance(fin, dict):
                applied = set()
                for nm in list(fin):
                    if nm in wanted:
                        if two and not (st.get("rd") == 1 and wanted[nm][0] in _FIRST_KINDS):
                            continue
                        applied.add(nm)
                        fin[nm] = wanted[nm][0]
                        if wanted[nm][1]:
                            st.setdefault("place", {})[nm] = wanted[nm][1]
                f4 = [nm for nm, k_ in fin.items() if k_ == "F4"]
                if len(f4) > 1:   # R102 (r103-dp: ก้อง was drawn F4 and ธนา was named F4: two titjobs, one man got none): only one titjob finish per step, a drawn one gives way to a named one
                    keep = next((nm for nm in f4 if nm in applied), f4[0])   # R103: 'applied' = named kinds really written in this step (r104-dp: ก้อง was named F2 for round 2, so 'in wanted' wrongly protected his drawn F4)
                    for nm in f4:
                        if nm != keep and nm not in applied:
                            fin[nm] = "F1"
            elif st.get("who") in wanted and st["id"] in ("F1", "F2", "F3", "F4", "F5"):
                if two and (st.get("rd") != 2 or wanted[st["who"]][0] in _FIRST_KINDS):
                    continue
                st["id"] = wanted[st["who"]][0]
                if wanted[st["who"]][1]:
                    st.setdefault("place", {})[st["who"]] = wanted[st["who"]][1]
    return plan


_PLACES = [("ก้น", r"ก้น|ตูด|แก้มก้น"), ("หลัง", r"หลัง"), ("ใบหน้า", r"ใบหน้า|หน้า(?!ท้อง|อก)"), ("หน้าอก", r"หน้าอก|นม|เนินอก"), ("หน้าท้อง", r"ท้อง"), ("ต้นขา", r"ต้นขา")]


def finish_place(txt):
    """R65: the body part the author names for an outside finish ('แตกบนก้น') -> key of _PLACES, '' = free."""
    m = re.search(r"แตก(?:บน|ใส่|ที่|ลง)\s*(ก้น|ตูด|หลัง|แผ่นหลัง|หน้าท้อง|ท้อง|นม|หน้าอก|ใบหน้า|หน้า|ต้นขา)", txt or "")
    if not m:
        return ""
    w = m.group(1)
    for k, rx in _PLACES:
        if re.search(rx, w):
            return k
    return ""


def attach_pose_plans(b, parts, cid="", salt=0):
    """put a seeded pose plan on the parts of every sexual scene with one man + one woman (single table) or two men + one woman (group table);
    other casts keep the old behaviour.
    R38 (29 Oct): no two scenes of one chapter get the same chain; while possible they also differ in their first two steps and in the kind of
    finish of the scene before (strict phase), then only the exact chain is forbidden.
    R45 (29 Oct): 2 men + 1 woman scenes get a group chain (both men at once, double penetration included); a seeded 'still wants more' flag
    (a woman or a man left aroused) is carried into the first part of the next planned scene when that person is in it."""
    if not POSES_ON:
        return parts
    import random, zlib
    info = char_info(b)
    t = pose_table()
    by_scene = {}
    first_sc = {}
    for p in parts:
        if p.get("slice"):
            first_sc.setdefault(p["text"], p["scene"])
    for p in parts:
        # R84 (r83-dp: a one-scene summary with a 3,000-word target is cut into 2 'slices' with the SAME text; each slice got its own 3-beat plan, so every man finished twice
        # and the double (needs 4+ beats) never came up): the slices of one summary are ONE scene for the pose plan.
        by_scene.setdefault(first_sc.get(p["text"], p["scene"]) if p.get("slice") else p["scene"], []).append(p)
    seen, seen_open, last_fin = set(), set(), None
    want = None
    for sc, ps in sorted(by_scene.items()):
        txt = ps[0]["text"]
        if not is_sexual_text(txt):
            continue
        present = [n for n in info if n and n in txt]
        men = [n for n in present if info[n].get("g") == "M"]
        women = [n for n in present if info[n].get("g") == "F"]
        group = len(men) == 2 and len(women) == 1
        group3 = len(men) == 3 and len(women) == 1
        group4 = len(men) == 4 and len(women) == 2 and len(ps) >= 4   # R110: four men and two women
        if not group and not group3 and not group4 and (len(men) != 1 or len(women) != 1):
            continue
        group = group or group3 or group4
        quick = (not group) and bool(QUICKIE_RE.search(txt))
        want_fin = finish_wanted(txt)
        # R96 (31 Oct, user): a proper scene (4+ beats ~ 2,000+ words) = each man comes twice (early: mouth / titjob / handjob, then after the sex: inside / outside); a short one = once
        rounds = 1 if (quick or ONCE_RE.search(txt) or len(ps) < 4) else 2
        if TWICE_RE.search(txt) and not quick and not ONCE_RE.search(txt) and len(ps) >= 3:
            rounds = 2
        pick = None
        for k in range(60):
            rng = random.Random(zlib.crc32(("%s|%s|%d|%d|%d" % (b, cid, sc, salt, k)).encode("utf-8")))
            if group4:
                plan = make_quad_plan(len(ps), rng, men, women, t, close_n=close_wanted(txt), close_last=close_pos(txt))
            elif group3:
                plan = make_group3_plan(len(ps), rng, men, t, dp=dp_wanted(txt), tail_ok=False, rounds=rounds)
            elif group:
                plan = make_group_plan(len(ps), rng, men[0], men[1], t, dp=dp_wanted(txt), tail_ok=False, rounds=rounds)
            elif quick:
                plan = make_quickie_plan(len(ps), rng, t)
            else:
                plan = make_pose_plan(len(ps), rng, t, rounds=rounds)
            sig = _plan_sig(plan)
            if want_fin and not group:
                fins_ = [st["id"] for bt in plan for st in bt if _pose_kind(t, st["id"]) == "finish"]
                chk = fins_[0] if (rounds == 2 and want_fin in _FIRST_KINDS and fins_) else sig[-1].split(":")[0]
                if k < 50 and chk != want_fin:
                    continue   # R63: the author named the place he comes: keep drawing seeds until the plan ends there (fallback after 50 draws); R96: a named early kind is the first round
            if sig in seen:
                continue
            if k < 40 and (sig[:2] in seen_open or (last_fin and sig[-1] == last_fin and len(t) > 12)):
                pick = pick or plan   # remember a merely-unique fallback
                continue
            pick = plan
            break
        plan = pick or plan
        if group:
            plan = apply_men_finish(plan, finish_wanted_men(txt, men))
        plan = mark_squirt(plan, t, txt, random.Random(zlib.crc32(("%s|%s|%d|%d|sq" % (b, cid, sc, salt)).encode("utf-8"))))   # R109
        sig = _plan_sig(plan)
        seen.add(sig)
        seen_open.add(sig[:2])
        last_fin = sig[-1]
        prev = None
        carry = want if (want and want in txt) else None
        tv_ = tj_variant(txt, random.Random(zlib.crc32(("%s|%s|%d|%d|tj" % (b, cid, sc, salt)).encode("utf-8"))))   # R105
        for p, steps in zip(ps, plan):
            p["pose"] = {"steps": steps, "prev": prev}
            p["pose"]["tv"] = tv_
            if want_fin and not group and not (rounds == 2 and want_fin in _FIRST_KINDS):
                p["pose"]["fin"] = want_fin
                if finish_place(txt):
                    p["pose"]["place"] = finish_place(txt)
            if quick:
                p["pose"]["quick"] = True
            if group:
                p["pose"]["nfin"] = _finish_counts(plan)   # R96: how many times each man comes in the whole scene (the repeat-cum check allows exactly that many)
                p["pose"]["g"] = 4 if group4 else (3 if group3 else True)
                p["pose"]["cast"] = {"men": list(men), "W": women[0], "W2": women[1], "women": list(women)} if group4 else ({"men": list(men), "W": women[0]} if group3 else {"A": men[0], "B": men[1], "W": women[0]})
            if carry and p is ps[0]:
                p["pose"]["carry"] = carry
            if steps:
                prev = steps[-1]["id"]
        rw = random.Random(zlib.crc32(("%s|%s|%d|%d|want" % (b, cid, sc, salt)).encode("utf-8")))
        cast = men + women
        want = rw.choice(cast) if rw.random() < 0.4 else None
        fw = os.environ.get("NOVEL_FORCEWANT", "")   # experiment switch: W = the woman, M = the first man is always left wanting more
        if fw == "W" and women:
            want = women[0]
        elif fw == "M" and men:
            want = men[0]
    return parts


_CODA_RE = re.compile(r"ประตู[^\n“”]{0,15}(?:ถูก)?(?:เปิด|เคาะ|ดัง)|เสียงกริ่ง|เสียงเคาะ|ใครบางคน(?:เดิน|เปิด|เข้า)|(?:เดิน|ก้าว)เข้ามา(?:ใน)?ห้อง|เสียงฝีเท้า|มีคน(?:เปิด|เดิน|มา)")


def plan_issues(text, part):
    """R66: wrapper: the pose-plan checks plus the coda check (a scene's last beat must not end on someone arriving: R64 'เสียงประตูห้องนอนถูกเปิดออก')."""
    out = _plan_issues_pose(text, part)
    pt = part or {}
    if pt.get("beat") and pt.get("beat") == pt.get("beats") and not pt.get("last"):
        tail = "\n".join(re.sub(r"“[^”]*”?", "", ln) for ln in (text or "").split("\n") if ln.strip())[-350:]
        if _CODA_RE.search(tail):
            out.append("ท้ายฉากห้ามมีใครเดินเข้า/เคาะ/เปิดประตู: ให้จบฉากด้วยภาพเงียบๆ ที่เดิม ฉากถัดไปจะเปิดสถานที่ใหม่เอง")
    return out


def _plan_issues_pose(text, part):
    """checks of a written part against its plan: he comes only in a part whose plan has a finish step, and that part must show it."""
    pp = (part or {}).get("pose")
    if not pp:
        return []
    t = pose_table()
    has_f = any(_pose_kind(t, s["id"]) == "finish" for s in pp.get("steps", []))
    lines = [re.sub(r"“[^”]*”?", "", ln) for ln in (text or "").split("\n") if ln.strip()]
    cum_n = sum(1 for ln in lines if _CUM.search(ln))
    out = []
    if any(st.get("id") in ("T6", "M7") for st in pp.get("steps", [])) and not re.search(r"สองแท่ง|สองดอก|ควยทั้งสอง|สองควย|ควยสองอัน", "\n".join(lines)):
        out.append("แผนมีท่า double (ควยสองแท่งในหีเดียวพร้อมกัน) แต่ไม่พบการเขียนว่ามีควยสองแท่งอยู่ในหีพร้อมกันเลย: ต้องเขียนให้ชัด (คนหนึ่งอยู่ในหีแล้ว อีกคนค่อยๆ ดันเข้าไปข้างกัน หีเธอตึงเต็มด้วยควยสองแท่ง เธอร้องขอให้ช้า แล้วขยับพร้อมกัน) ห้ามใส่ทวาร")
    if pp.get("g"):
        men_ = (pp.get("cast") or {}).get("men") or [x for x in ((pp.get("cast") or {}).get("A"), (pp.get("cast") or {}).get("B")) if x]
        bad = 0
        for ln in (text or "").split("\n"):
            st = ln.strip()
            m = QUOTE_RE.match(st)
            if m and men_ and re.search(r"(?<![ก-ฮ])ผม(?![ก-ฮ]*(?:ยาว|สั้น|หอม|เปียก|ดำ|ขาว))|ครับ", m.group(0)) and not any(x in st[m.end():][:40] for x in men_):
                bad += 1
        if bad:
            out.append("มีบทพูดที่ใช้ ผม/ครับ แต่ไม่ระบุว่าใครเป็นคนพูด %d จุด: ทุกบทพูดที่มีคำว่าผม กู มึง ครับ ต้องมีท้ายประโยคบอกชื่อผู้ชายที่พูด" % bad)
    if not has_f and cum_n:
        out.append("ผู้ชายแตกทั้งที่แผนของ beat นี้ยังไม่ถึงจุดจบ")
    if pp.get("quick") and re.search(r"(?:เลีย|ดูด|กิน)(?:ยอด)?(?:หี|เม็ดแตด|แตด)|(?:ปาก|ลิ้น|ริมฝีปาก)ของ(?:เขา|[ก-ฮ]{2,8})[^\n“]{0,25}(?:หี|เม็ดแตด|ยอดหี)", "\n".join(lines)):
        out.append("ฉากควิกกี้: ผู้หญิงทำให้ผู้ชายอย่างเดียว ห้ามผู้ชายเลีย/ดูดหีหรือทำให้เธอ")
    if has_f and not cum_n:
        out.append("beat นี้ต้องมีการแตกของผู้ชายตามแผน แต่ไม่พบ")
    if has_f and cum_n and not pp.get("g"):
        # R40 (29 Oct): the kind of finish must match the plan (R38: said "inside" then came outside; R39: plan outside, written in her mouth)
        fid = [s["id"] for s in pp.get("steps", []) if _pose_kind(t, s["id"]) == "finish"][-1]
        alltxt = "\n".join(lines)   # R65: narration only: a line she SAYS ("แตกใส่ปากชั้นได้เลย") is not where he comes
        inside = re.search(r"แตก(?:ข้าง)?ใน(?!ปาก)|ข้างในตัว|ในหีของ|ปล่อยใน|หลั่งใน|ฝังน้ำ", alltxt)
        mouth = re.search(r"(?:น้ำเชื้อ|น้ำควย|แตก|พุ่ง)[^\n]{0,30}(?:ในปาก|ใส่ปาก|เข้าปาก|ลงคอ)|(?:ในปาก|ใส่ปาก)[^\n]{0,30}(?:น้ำเชื้อ|น้ำควย|แตก|พุ่ง)", alltxt)
        outside = re.search(r"แตกนอก|แตกข้างนอก|(?:น้ำเชื้อ|น้ำควย|พุ่ง)[^\n]{0,40}(?:หน้าท้อง|หน้าอก|ท้อง|ก้น|แผ่นหลัง|หลังของ|ต้นขา|ใบหน้า|นม)", alltxt)
        if fid == "F2" and (inside or mouth):
            out.append("แผนให้ผู้ชายแตกข้างนอกบนตัวเธอ ห้ามแตกในหีหรือในปาก")
        elif fid == "F1" and not mouth:
            out.append("แผนให้ผู้ชายแตกในปากเธอ ต้องเขียนให้ชัด")
        elif fid == "F3" and not inside:
            out.append("แผนให้ผู้ชายแตกข้างในหีเธอ ต้องเขียนให้ชัด")
        elif fid == "F3" and (mouth or (outside and not inside)):
            out.append("แผนให้ผู้ชายแตกข้างในหีเธอ ห้ามแตกนอกหรือในปาก")
    if pp.get("g"):
        out += _dp_role_issues(pp, lines)
        out += _early_climax_issues(t, pp, text)
    if has_f and cum_n and pp.get("g"):
        out += _group_finish_issues(t, pp, lines)
    if has_f and cum_n and not pp.get("g") and pp.get("place"):
        rx = dict(_PLACES).get(pp["place"], "")
        near = [ln for ln in lines if _CUM.search(ln)] + [lines[i + 1] for i, ln in enumerate(lines[:-1]) if _CUM.search(ln)]
        if rx and not any(re.search(rx, ln) for ln in near):
            out.append("น้ำเชื้อต้องไปตกที่%s ของเธอตามแผน แต่เขียนไปตกที่อื่น" % pp["place"])
    return out


_DONE_CUM = re.compile(r"แตกแล้ว|แตกใน|แตกใส่|แตกบน|แตกที่|กูแตก|ผมแตก|น้ำ(?:เชื้อ|ควย)กู(?:พุ่ง|ไหล|ออก)")
_NOT_YET = re.compile(r"(?:ก็จะ|จะ|ใกล้จะ|ใกล้|ขอ|อยาก|ให้(?:มึง|เธอ|คุณ|หนู|เรา|ลลิ?ตา)?|อย่า|ห้าม|ไม่ให้|ตอน|เดี๋ยว(?:ก็)?)\s*แตก|แตกพร้อม")   # R86: "เย็ดให้มึงแตกใน" / "ตอนแตก" are intentions, not a finish (r85-dp: ก้อง's real finish was cut as a repeat)


def _early_climax_issues(t, pp, text):
    """R75 (r75-men scene 2: ก้อง shouts 'แตกในปากมึงแล้ว' in the beat where only ปกรณ์ comes, and comes again later): in a part of a 2/3-men scene a man says he HAS
    come only in a part whose plan has his own finish step (the narration check cannot see what a man shouts)."""
    cast = pp.get("cast") or {}
    men = cast.get("men") or [x for x in (cast.get("A"), cast.get("B")) if x]
    if not men:
        return []
    own = set()
    for st in pp.get("steps", []):
        if _pose_kind(t, st["id"]) != "finish":
            continue
        if isinstance(st.get("fin"), dict):
            own |= set(st["fin"])
        elif st.get("who"):
            own.add(st["who"])
        else:
            own |= set(st.get("r") or [])
    out = []
    for ln in (text or "").split("\n"):
        m = QUOTE_RE.match(ln.strip())
        if not m:
            continue
        who = [x for x in men if ln.strip()[m.end():].strip().startswith(x)]
        if len(who) != 1:
            continue
        q = _NOT_YET.sub("", m.group(0))
        if re.search(r"แตก(?:ใน)?(?:ก้น|ทวาร|ตูด)", q):
            out.append("ไม่มีผู้ชายคนไหนแตกในก้น/ทวารเธอตามแผน: ห้ามให้ผู้ชายพูดว่าแตกในก้น (แตกได้แค่ตามแผนของเขา: ปาก ข้างนอกตัว หรือในหี)")
            break
        if who[0] in own:
            continue
        if _DONE_CUM.search(q):
            out.append("%s พูดว่าแตกแล้ว แต่ในช่วงนี้ตามแผนยังไม่ถึงตาที่เขาแตก (คนที่แตกในช่วงนี้คือ %s): ให้ %s ยังไม่แตก ห้ามพูดว่าแตก" % (who[0], ", ".join(sorted(own)) or "ไม่มี", who[0]))
            break
    return out


def _dp_role_issues(pp, lines):
    """R81: the double is two cocks in the PUSSY; no man may enter her anus or use gel for it (the old anal version was too hard for the model)."""
    if not any(st.get("id") in ("T6", "M7") for st in pp.get("steps", [])):
        return []
    alltxt = "\n".join(lines)
    if re.search(r"(?:สอด|ดัน|เสียบ|ยัด|กด|จ่อ|เบียด)[^\n]{0,30}(?:ทวาร|รูก้น|รูตูด|ก้นเธอ|ก้นของเธอ)", alltxt):
        return ["ท่า double ในแผนคือควยสองแท่งในหีเดียว ไม่มีใครสอดทวาร/ก้นเธอ: ห้ามเขียนการสอดทวารและห้ามบีบเจล ให้เขียนว่าสองแท่งอยู่ในหีพร้อมกัน"]
    return []


_ANAL_CUM = re.compile(r"(?:น้ำเชื้อ|น้ำควย|แตก|พุ่ง|ปล่อย|หลั่ง|ฉีด|สาด)[^\n]{0,25}(?:ในทวาร|ในรูทวาร|ในรูก้น|ในก้น|เข้าทวาร|ในตูด)|แตก(?:ใน)?(?:ก้น|ทวาร|ตูด)")
_IN_STRICT = re.compile(r"(?:น้ำเชื้อ|น้ำควย|แตก|พุ่ง|ปล่อย|หลั่ง|ฉีด|สาด)[^\n]{0,25}(?:ในหี|ข้างใน|ในตัวเธอ|เข้าไปในหี|ในช่อง)|แตกใน(?!ปาก)|ออก(?:มา)?จากหี|ไหลออกจากหี")
_MOUTH_STRICT = re.compile(r"(?:น้ำเชื้อ|น้ำควย|แตก|พุ่ง)[^\n]{0,30}(?:ในปาก|ใส่ปาก|เข้าปาก|ลงคอ)|(?:ในปาก|ใส่ปาก)[^\n]{0,30}(?:น้ำเชื้อ|น้ำควย|แตก|พุ่ง)")


def _group_finish_issues(t, pp, lines):
    """R75: in a part of a 2/3-men scene the kinds of finishes of the plan (mouth / outside / inside, and the named place) must all be shown, and no kind
    that nobody has planned may appear (r74-mix: planned on the belly, written 'inside her')."""
    kinds, places = [], []
    for st in pp.get("steps", []):
        if _pose_kind(t, st["id"]) != "finish":
            continue
        if isinstance(st.get("fin"), dict):
            kinds += list(st["fin"].values())
        else:
            kinds.append(st["id"])
        places += list((st.get("place") or {}).values())
    if not kinds:
        return []
    alltxt = "\n".join(lines)
    inside, mouth = _IN_STRICT.search(alltxt), _MOUTH_STRICT.search(alltxt)
    out = []
    if "F3" not in kinds and inside:
        out.append("ในแผนไม่มีผู้ชายคนไหนแตกข้างในหี แต่เขียนว่าน้ำเชื้อแตก/ไหลออกจากในหีเธอ: แก้ให้ไปตกตามแผน (ในปาก หรือข้างนอกบนตัวเธอ)")
    if "F1" not in kinds and mouth:
        out.append("ในแผนไม่มีผู้ชายคนไหนแตกในปากเธอ แต่เขียนว่าแตกในปาก: แก้ให้ตรงแผน")
    if "F3" in kinds and not inside:
        out.append("แผนให้ผู้ชายคนหนึ่งแตกข้างในหีเธอ ต้องเขียนให้ชัด")
    if "F1" in kinds and not mouth:
        out.append("แผนให้ผู้ชายคนหนึ่งแตกในปากเธอ ต้องเขียนให้ชัด")
    if _ANAL_CUM.search(alltxt):
        out.append("ไม่มีผู้ชายคนไหนแตกในทวาร/ในก้นเธอตามแผน (ทวารใช้แค่ในท่า double ส่วนการแตกต้องเป็นปาก ข้างนอกตัว หรือในหี): ห้ามเขียนว่าน้ำเชื้อออกมาในทวาร")
    near = [ln for ln in lines if _CUM.search(ln)] + [lines[i + 1] for i, ln in enumerate(lines[:-1]) if _CUM.search(ln)]
    for pl in places:
        rx = dict(_PLACES).get(pl, "")
        if rx and not any(re.search(rx, ln) for ln in near):
            out.append("น้ำเชื้อของผู้ชายคนหนึ่งต้องไปตกที่%s ของเธอตามแผน แต่เขียนไปตกที่อื่น" % pl)
    return out


def anatomy_line(cast_info):
    f = [n for n, v in cast_info if v.get("g") == "F"]
    m = [n for n, v in cast_info if v.get("g") == "M"]
    if not f:
        return ""
    s = "ANATOMY (obey strictly): " + ", ".join(f) + (" are women: they have หี, เม็ดแตด, นม, หัวนม and NO ควย" if f else "")
    if m:
        s += "; " + ", ".join(m) + " are men: they have ควย and no หี"
    s += ". Never give a woman a ควย and never use a strap-on or toy unless the plan names it; sex between two women is done with mouths, fingers, tongues and rubbing หี against หี (ขย่มหีชนหี)."
    return s


UNTAG_RUN = 8   # this many (or more) consecutive spoken lines with no narration or speaker tag between them = nobody can tell who talks (ch01 ending: ~100 such lines)
_PURE_Q = re.compile(r"^\s*[“\"][^”\"\n]+[”\"][\s.!?…]*$")


def untagged_dialogue(text, run=UNTAG_RUN):
    """number of spoken lines that sit in runs of at least `run` bare quoted lines (a quote alone on its line: no tag, no narration)."""
    n = cur = 0
    for ln in (text or "").split("\n"):
        if not ln.strip():
            continue
        if _PURE_Q.match(ln):
            cur += 1
        else:
            if cur >= run:
                n += cur
            cur = 0
    if cur >= run:
        n += cur
    return n


def echo_runs(text, minrun=8, maxlen=12):
    """R54 (29 Oct, R53 scene 2): a ping-pong of one-word tagged lines ("อม" / "ดูด" / "แรง" / "หมด"): the number of quoted lines that sit in runs of at
    least `minrun` consecutive lines whose quote is at most `maxlen` characters (punctuation and spaces not counted). Narration breaks a run."""
    n = cur = 0
    for ln in (text or "").split("\n"):
        if not ln.strip():
            continue
        m = QUOTE_RE.match(ln.strip())
        if m and len(re.sub(r"[\s.…!?,“”\"]", "", m.group(0))) <= maxlen:
            cur += 1
        else:
            if cur >= minrun:
                n += cur
            cur = 0
    if cur >= minrun:
        n += cur
    return n


def echo_n(t):
    return echo_runs(split_dialogue(t or ""))


def untag_n(t):
    return untagged_dialogue(split_dialogue(t or ""))


def kin_line(info, present, kin=None):
    """family facts taken from the characters' roles in the setup (father, children, stepmother, biological mother) so the model stops swapping พ่อ/พ่อเลี้ยง/แม่/ลูกสาว; empty when none of them is in this part."""
    role = lambda n: (info.get(n) or {}).get("role", "")
    fathers = [n for n in info if re.search(r"father", role(n), re.I) and not re.search(r"step", role(n), re.I)]
    kids = [n for n in info if re.match(r"\s*(daughter|son)\b", role(n), re.I)]
    step = [n for n in info if re.search(r"stepmother", role(n), re.I)]
    bio = [n for n in info if re.search(r"biological mother", role(n), re.I)]
    kin = kin or {"father": "พ่อ", "mother": "แม่"}
    FA, MO = kin["father"], kin["mother"]
    fam = set(fathers + kids + step + bio)
    if not fam & set(present) or not (fathers and kids):
        return ""
    s = "KINSHIP (obey strictly): " + "/".join(fathers) + " is the REAL father (never พ่อเลี้ยง) of " + ", ".join(kids) + ". The children are siblings: they call each other by name or พี่/น้อง and NEVER ลูก/ลูกสาว/ลูกชาย (only a parent calls a child ลูก). The children call the father " + FA + "."
    if step:
        s += " " + "/".join(step) + " is the stepmother (the father's second wife): the children call her คุณ" + step[0] + " or คุณแม่" + step[0] + ", never a bare แม่" + ((" (their real mother is " + "/".join(bio) + ")") if bio else "") + "."
    if step:
        s += " " + "/".join(step) + " is the father's WIFE: husband and wife may call each other and themselves " + FA + " / " + MO + " naturally as a family (e.g. " + MO + " จัดการให้นะ, " + FA + " อยากกินข้าว); when a scene has several people and it could be unclear who is addressed (sex scenes with the children present), each calls the other by NAME, while self-reference stays " + FA + " / " + MO + "."
    s += " Do not swap these relations in the middle of the chapter."
    return s


LEN_SCALE = float(os.environ.get("NOVEL_LENSCALE", "0.9"))  # R21 switch (default unchanged 0.9); chapters came out at ~110% of target (ch03-08: 5,191 / 5,286 / 6,011 / 5,980 / 5,872 / 5,858 words for 5,000): ask for 10% less
G_LEN = float(os.environ.get("NOVEL_GLEN", "1.0"))  # R50 switch: extra length factor for group (2 men + 1 woman) parts


def part_message(b, parts, i, first_empty, names, avoid=None):
    p = parts[i]
    low = p["text"]
    cast = []
    info = char_info(b)
    for nm, th in names:
        if (nm and nm in low) or (th and th in low):
            disp = th or nm
            ci = info.get(disp) or {}
            extra = ", ".join(x for x in ({"F": "female", "M": "male"}.get(ci.get("g", ""), ""), ("age %s" % ci["age"]) if ci.get("age") else "", ci.get("role", "")) if x)
            cast.append(disp + (" (%s)" % extra if extra else ""))
    lines = []
    if p.get("slice"):
        lines.append("WRITE: part %d of %d of this chapter (follow the chapter summary in order; this part covers roughly the %s of it)." % (p["slice"][0], p["slice"][1], ["opening", "middle", "latter half", "end"][min(3, int(4.0 * (p["slice"][0] - 1) / p["slice"][1]))]))
    else:
        lines.append("WRITE: scene %d of %d%s." % (p["scene"], p["scenes"], (", beat %d of %d of this scene" % (p["beat"], p["beats"])) if p["beats"] > 1 else ""))
        lines.append("PLAN OF THIS SCENE (Thai): " + p["text"])
    ls_ = LEN_SCALE * (G_LEN if _is_group(p) else 1.0)   # R50 (29 Oct): three-person parts overshoot by ~23% even at LENSCALE 0.6 (R45/R48/R49): ask for less
    lines.append("LENGTH: about %d Thai words (never more than %d)." % (int(p["words"] * ls_), int(p["words"] * ls_ * 1.2)))
    ci_list = [(n.split(" (")[0], info.get(n.split(" (")[0]) or {}) for n in dict.fromkeys(cast)]
    al = anatomy_line(ci_list) if is_sexual_text(p.get("text")) else ""
    if cast:
        lines.append("CAST OF THIS PART (only these characters may appear or speak, no one else enters; gender and age are given: speech must match them): " + ", ".join(dict.fromkeys(cast)))
    if al:
        lines.append(al)
    kl = kin_line(info, [n.split(" (")[0] for n in cast], kin_terms(b))
    if kl:
        lines.append(kl)
    if SPEECH_LITE:
        st = speech_table(ci_list, info, kin_terms(b))
        if st:
            lines.append(st)
    lines.append("SPEAKERS: every spoken line carries a speaker tag or sits right next to a narration beat naming who speaks; never write more than %d quoted lines in a row without a short action or tag line between them (even in the peak)." % (UNTAG_RUN - 4))
    hh = heat_hint(p)
    if hh:
        lines.append(hh)
    lines.append(dialogue_hint(p))
    sf = sex_focus_hint(p)
    if sf:
        lines.append(sf)
    pb = pose_block(p)
    if pb:
        lines.append(pb)
    if avoid:
        lines.append("OVERUSED SO FAR - do not use these phrases again in this part, find fresh wording: " + ", ".join("“%s”" % a for a in avoid))
    lines.append("Begin the chapter's prose now (no title line)." if (i == 0 and first_empty) else "Continue directly after the last paragraph of the manuscript so far; do not repeat it.")
    plan_f = bool(p.get("pose")) and any(_pose_kind(pose_table(), x["id"]) == "finish" for x in p["pose"].get("steps", []))
    if p["beats"] > 1 and p["beat"] < p["beats"] and not plan_f:
        lines.append("This is NOT the end of the scene: move the action on to the next stage and stop mid-flow; do not climax, conclude or wrap up the scene yet.")
    elif not p["last"]:
        lines.append("Do not end or conclude the chapter; this scene ends at a natural point and the next part continues. If this is the LAST beat of its scene: close it on a quiet image in the SAME place and moment; nobody arrives, no door opens or knocks, no new character, no change of place or time (the next scene opens its own setting).")
    else:
        lines.append("This is the FINAL part of the chapter: bring the scene to its ending and close the chapter on a specific image with an unresolved hook.")
    return "\n".join(lines)


PLAIN_WORDS = [("อวัยวะเพศ", "ของลับ"), ("อวัยวะสืบพันธุ์", "ของลับ"), ("องคชาต", "ควย"), ("ช่องคลอด", "หี"), ("น้ำอสุจิ", "น้ำเชื้อ"), ("เสร็จข้างใน", "แตกใน"), ("เสร็จข้างนอก", "แตกข้างนอก"), ("เสร็จที่หน้า", "แตกใส่หน้า"), ("เสร็จในปาก", "แตกในปาก"), ("เสร็จในหี", "แตกในหี"), ("ถึงจุดสุดยอด", "แตก"), ("น้ำหล่อลื่น", "น้ำหี"), ("สารคัดหลั่ง", "น้ำหี")]


TYPO_FIX = [("พึมพ่า", "พึมพำ"), ("พึมพ้ำ", "พึมพำ"), ("พึมพร", "พึมพำ"), ("หนุกมาก", "สนุกมาก"), ("เปียกชับ", "เปียกชื้น"), ("กระแทควย", "กระแทกควย"), ("เยียดให้", "เย็ดให้"), ("ริมฝีริมฝีปาก", "ริมฝีปาก"), ("แรงตดของ", "แรงกระแทกของ"), ("การตดจน", "การกระแทกจน"), ("ขมับหี", "ขมิบหี"), ("อ่อนโยล", "อ่อนโยน")]   # R32 additions seen in r32-r34
LATIN_IN_THAI = "(?<=[\\u0E00-\\u0E7F])[a-z]{1,8}(?=[\\u0E00-\\u0E7F\\s\\u201c\\u201d\"!?.,]|$)"   # lowercase Latin letters glued to Thai text (น้ำควyli): never legitimate in this story
TYPO_RE = re.compile(LATIN_IN_THAI + r"|กรมถา|หันมมอง|ลูกสะไภ้|แน่วแน่วนิยม|(?<!โ)ซเฟีย|โนหัว|ขมับไว้รอบ|เข\s*ของ|[\u0E48-\u0E4B]{2}|\u0E31{2}")   # garbles seen in ch01-08 that no fixed replacement can repair: reported by the check


def plain_words(t):
    for a, b in PLAIN_WORDS:
        t = t.replace(a, b)
    t = re.sub(r"หัวหี(?!บ)|คลิตอริส|คลิทอริส|ปุ่มกระสัน", "เม็ดแตด", t)   # the clitoris is เม็ดแตด
    t = re.sub(r"([\u0E48-\u0E4B])\1+", r"\1", t)   # doubled tone marks (น้้ำ)
    for a, b in TYPO_FIX:
        t = t.replace(a, b)
    t = re.sub(r"(?<!โ)ซเฟีย", "โซเฟีย", t)
    t = t.replace("กรมถา", "กรมท่า").replace("หันมมอง", "หันมอง").replace("ลูกสะไภ้", "ลูกสะใภ้")
    t = re.sub(r"\b[Mm]issionary\b", "ท่ามิชชันนารี", t)
    t = re.sub(r"\b[Oo]ral\b", "อม", t)
    return t


FEMALE_HINT = ("female", "woman", "girl", "daughter", "mother", "wife", "stepmother", "housekeeper", "หญิง", "ผู้หญิง", "เลขาหญิง")
MALE_HINT = ("male", "man", "boy", "son", "father", "husband", "ชาย", "ผู้ชาย")


def _gender_of(c):
    v = c.get("v") or {}
    g = ((v.get("gender") or {}).get("c") or "") + " " + " ".join((v.get("gender") or {}).get("s") or [])
    g = g.lower()
    if any(h in g for h in ("female", "woman", "หญิง")):
        return "F"
    if any(h in g for h in ("male", "man", "ชาย")):
        return "M"
    r = ((v.get("role") or {}).get("c") or "").lower()
    def hit(hints):
        return any((re.search(r"\b%s\b" % re.escape(h), r) if h.isascii() else h in r) for h in hints)
    if hit(FEMALE_HINT):
        return "F"
    if hit(MALE_HINT):
        return "M"
    return ""


def char_info(b):
    """{display name (Thai if set): {"g": "F"/"M"/"", "age": str, "role": str}} for every character in the setup."""
    try:
        st = json.loads(read(os.path.join(bdir(b), "setup.json"), "{}") or "{}")
    except Exception:
        return {}
    out = {}
    for c in st.get("chars", []) if isinstance(st, dict) else []:
        nm = str(c.get("name") or "").strip()
        th = str(c.get("th") or "").strip()
        if not nm:
            continue
        v = c.get("v") or {}
        out[th or nm] = {"g": _gender_of(c), "age": str((v.get("age") or {}).get("c") or "").strip(), "role": str((v.get("role") or {}).get("c") or "").strip()[:60]}
    return out


QUOTE_RE = re.compile(r"[\u201c\"][^\u201d\"\n]+[\u201d\"]")


def close_open_quotes(text, info):
    """R88 (r88-dp: “สัส… แตกในมึงแล้ว… ก้องครางหอบ — the closing mark is missing, so no later fix sees the quote or its speaker): a line that opens “ and never closes it
    gets the mark right before the first name in it (the speaker tag)."""
    names = sorted((n for n in (info or {}) if n), key=len, reverse=True)
    out = []
    for ln in (text or "").split("\n"):
        st = ln.strip()
        if st.startswith("\u201c") and "\u201d" not in st and '"' not in st[1:]:
            hits = [st.find(n, 3) for n in names if st.find(n, 3) > 3]
            hits += [m_.start() + 4 for m_ in re.finditer(r"\sเ(?:ธอ|ขา)", st[3:])]   # R95 (r93-dp: “ชั้นจะแตกแล้ว เย็ดให้แรงๆ ค่ะ เธอกรีดร้อง): a pronoun tag closes the quote too
            if hits:
                i = min(hits)
                ln = ln.replace(st, st[:i].rstrip() + "\u201d " + st[i:], 1)
        out.append(ln)
    return "\n".join(out)


def split_dialogue(text):
    """Put every spoken line (quote + its short tag) on its own paragraph; narration stays in its own paragraphs."""
    out = []
    for para in re.split(r"\n+", text.strip()):
        para = para.strip()
        if not para:
            continue
        qs = list(QUOTE_RE.finditer(para))
        if not qs:
            out.append(para)
            continue
        pos = 0
        for k, m in enumerate(qs):
            pre = para[pos:m.start()].strip()
            if pre:
                out.append(pre)
            nxt = qs[k + 1].start() if k + 1 < len(qs) else len(para)
            tail = para[m.end():nxt].strip()
            rest = ""
            if len(tail) > 70:
                sp = tail.find(" ", 8)
                if 0 < sp <= 70:
                    tail, rest = tail[:sp].strip(), tail[sp:].strip()
            out.append((m.group(0) + (" " + tail if tail else "")).strip())
            if rest:
                out.append(rest)
            pos = nxt
    return "\n\n".join(out)


def dedupe_quotes(part, existing, window=14):
    """Drop a spoken line whose quote text already appeared among the last `window` spoken lines."""
    recent = [QUOTE_RE.match(l.strip()).group(0) for l in existing.split("\n") if QUOTE_RE.match(l.strip())][-window:]
    out = []
    for para in part.split("\n"):
        m = QUOTE_RE.match(para.strip())
        if m:
            if m.group(0) in recent:
                continue
            recent.append(m.group(0))
            recent = recent[-window:]
        out.append(para)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _age_num(v):
    m = re.search(r"\d+", str(v or ""))
    return int(m.group(0)) if m else None


PARTICLE_END = re.compile(r"(ครับ|ค่ะ|คะ|คับ|ค่า|จ้ะ|จ๊ะ|จ้า|จ๋า)\s*[!?…\.]*$")
QUESTION_END = re.compile(r"(ไหม|มั้ย|หรือ|เหรอ|หรอ|อะไร|ไหน|ใคร|ทำไม|เท่าไหร่|เท่าไร|อย่างไร|ยังไง|กี่[ก-ฮ]*|หรือยัง|รึเปล่า)\s*$")


def polite_line(q, sp):
    """Give a calm spoken line the polite ending of its speaker's gender (ครับ / ค่ะ / คะ)."""
    m = re.match(r"^([\u201c\"])(.*?)([\u201d\"])$", q, flags=re.S)
    if not m:
        return q
    body = m.group(2).rstrip()
    tail = re.search(r"[\s.!?…]*$", body).group(0)
    core = body[:len(body) - len(tail)] if tail else body
    if len(core) < 8 or PARTICLE_END.search(core) or re.search(r"(ครับ|ค่ะ|คะ|คับ)(?:คุณ|พี่|น้อง|ท่าน)?[\u0E01-\u0E4E]{0,12}$", core):
        return q   # already polite, also when a vocative follows the particle (ค่ะคุณกฤษณ์ must not become ค่ะคุณกฤษณ์ค่ะ)
    ask = QUESTION_END.search(core) is not None or "?" in tail
    pm = {"M": "ครับ", "F": "คะ" if ask else "ค่ะ"}[sp]
    if re.search(r"(นะ|สิ)$", core):
        pm = {"M": "ครับ", "F": "คะ"}[sp]   # นะคะ / นะครับ / สิคะ / สิครับ
    new = core + pm
    return m.group(1) + new + tail + m.group(3)


def elder_listener(q, spn, info):
    """True when the quote is addressed to someone older (parent, relative, boss): decided by an addressed character's age or a family address word."""
    sa = _age_num((info.get(spn) or {}).get("age")) if spn else None
    for nm, v in (info or {}).items():
        if nm and nm != spn and nm in q:
            a = _age_num(v.get("age"))
            if a is not None and sa is not None and a >= sa + 5:
                return True
    own = ((info.get(spn) or {}).get("role") or "") if spn else ""
    if re.search(r"ลูก|หลาน", q) or re.search(r"พ่อ|แม่|ป๊า|ม๊า|ปู่|ย่า|ตา|ยาย", own):
        return False
    return re.search(r"พ่อ|แม่|ป๊า|ม๊า|ป้า|ลุง|(?<!ห)น้า|ปู่|(?<!อ)ย่า|ยาย|คุณพ่อ|คุณแม่|ท่าน(?![ีั]|ไหน)", q) is not None   # R32: "อย่า" contains ย่า and "หน้า" contains น้า: they made every "อย่า…" / "หน้าอก" line count as talk to an elder (polite ครับ/ค่ะ added)


def tag_speaker(tag, info):
    """the speaker named in the tag after a quote: the name that appears FIRST in the tag (the subject), longest name on a tie; not simply the longest name in it
    (“...” กฤษณ์ถามโซเฟีย is Krit speaking)."""
    best = None
    t40 = tag[:40]
    for nm in info:
        if not nm:
            continue
        i = t40.find(nm)
        if i < 0:
            continue
        if best is None or i < best[0] or (i == best[0] and len(nm) > len(best[1])):
            best = (i, nm)
    return best[1] if best else None


def thin_owner(text, names=None):
    """R51 (29 Oct): "ควยของเขา" came out 9-16 times per chapter: in one part keep the 1st and every 3rd, the others become plain "ควย" (the man is named in the same paragraph).
    R56: the same for หน้าอก / ริมฝีปาก / มือ / ต้นขา / ลำตัว / สะโพก ของเขา|เธอ (R55: หน้าอกของ x9, ริมฝีปากของ x8).
    R72 (r71-trio: "ควยของปกรณ์" x8): the same for "<noun>ของ<name>" when that name appears in the 80 characters before it (the owner has just been named)."""
    cnt = {}
    def rep(m):
        w = m.group(1)
        cnt[w] = cnt.get(w, 0) + 1
        return m.group(0) if (cnt[w] - 1) % 3 == 0 else w
    NOUNS = r"(ควย|หน้าอก|ริมฝีปาก|มือ|ต้นขา|ลำตัว|สะโพก|ร่างกาย|ร่าง|ปลายนิ้ว|ปลายลิ้น|หัวนม|ลิ้น|นิ้ว)"
    text = re.sub(NOUNS + r"ของ(?:เขา|เธอ)(?!วง|เอง)", rep, text)
    nm = [x for x in (names or []) if x]
    if nm:
        alt = "|".join(re.escape(x) for x in sorted(nm, key=len, reverse=True))
        cn = {}
        def rep2(m):
            w, who = m.group(1), m.group(2)
            if who not in text[max(0, m.start() - 80):m.start()]:
                return m.group(0)
            cn[(w, who)] = cn.get((w, who), 0) + 1
            return m.group(0) if (cn[(w, who)] - 1) % 3 == 0 else w
        text = re.sub(NOUNS + r"ของ(" + alt + r")", rep2, text)
    return text


def _thin_owner_n(b, t):
    try:
        return thin_owner(t, [n for n in char_info(b) if n])
    except Exception:
        return thin_owner(t)


def thin_tagverb(text, verb="พึมพำ", keep=1):
    """R56 (R54: พึมพำ x6-10): in one part keep `keep` of the tag verb, the others rotate to other verbs."""
    alts = ["กระซิบ", "ว่า", "เอ่ย", "ตอบ"]
    n = [0]
    def rep(m):
        n[0] += 1
        return m.group(0) if n[0] <= keep else alts[(n[0] - keep - 1) % len(alts)]
    return re.sub(verb, rep, text)


def unify_register(text, info):
    """R70 (29 Oct, r69-trio): a man who is rough (says กู/มึง at least twice in the chapter) keeps ONE register across parts: his dirty quotes that still say
    ผม (no ครับ-only politeness elsewhere) become กู (R61/R55 only look inside one part, and an early warm-up part has too few sex words to trigger them)."""
    men = {n for n, v in (info or {}).items() if n and v.get("g") == "M"}
    PRE = r"(?:(?<![ก-ฮ])|(?<=ของ)|(?<=ให้)|(?<=แก่)|(?<=กับ)|(?<=จาก)|(?<=ที่)|(?<=ถึง)|(?<=ควย)|(?<=ปาก)|(?<=ตัว)|(?<=มือ)|(?<=เอา))"   # "ของกู" / "ให้ผม": the pronoun glued to a preposition still counts
    if not men:
        return text
    rough = {}
    polite = {}
    parsed = []
    for ln in text.split("\n"):
        st = ln.strip()
        m = QUOTE_RE.match(st)
        spn = tag_speaker(st[m.end():], info) if m else None
        parsed.append((ln, m, spn))
        if m and spn in men and re.search(PRE + r"กู(?!เก|รู)|มึง", m.group(0)):
            rough[spn] = rough.get(spn, 0) + 1
        elif m and spn in men and "ครับ" not in m.group(0) and re.search(PRE + r"ผม", m.group(0)):
            polite[spn] = polite.get(spn, 0) + 1
    # R75 (r74-mix: every man mixed ผม and กู, e.g. ก้อง 3/2, ธนา 1/1): rough = two rough quotes, or at least one and not outnumbered by his plain ผม quotes
    rough = {n for n, c in rough.items() if c >= 2 or c >= polite.get(n, 0)}
    if not rough:
        return text
    HAIR_ = r"(?![ก-ฮ]*(?:ยาว|สั้น|หอม|เปียก|ดำ|ขาว|หงอก|ร่วง))"
    present = {n for n in men if n in text}
    allrough = bool(present) and present <= rough   # R88: every man of the chapter is rough, so a quote nobody tagged can only be a rough man's
    out = []
    for ln, m, spn in parsed:
        if m and spn is None and allrough and not re.search(r"ค่ะ|คะ|นะจ๊ะ|จ๋า|(?<![ก-ฮ])ชั้น(?!วาง|ที่|หนึ่ง|สอง|สาม|บน|ล่าง|ใน|เรียน|ปี|ๆ)|หนู", m.group(0)):
            spn = next(iter(rough))
        if m and spn in rough:
            q = m.group(0)
            if "ครับ" in q and not re.search(PRE + r"ผม" + HAIR_, q):   # R85 (r84-dp: "เตรียมตัวมาดีจังเลยนะครับ" / "พร้อมหรือยังครับ" from men who say กู everywhere else): a rough man's ครับ goes even when the line has no ผม
                q3 = re.sub(r"\s*(?:นะ)?ครับ", "", q)
                ln = ln.replace(q, q3, 1)
            if re.search(PRE + r"ผม" + HAIR_, q):   # R75: every ผม of a rough man, not only the dirty lines; R79 (r78-dp: "สิครับ ผมอยากเห็น" from a man who says กู 7 times): his ครับ goes too
                q2 = re.sub(r"\s*(?:นะ)?ครับ", "", q) if "ครับ" in q else q
                q2 = re.sub(PRE + r"ผม" + HAIR_, "กู", q2)
                ln = ln.replace(q, q2, 1)
        out.append(ln)
    return "\n".join(out)


def fix_man_pussy(text, info):
    """R85 (r84-dp: ธนา says “ปล่อยน้ำในหีกูเลย”): a man has no pussy; in a quote tagged to a man, หีกู / หีของกู / หีผม becomes หีมึง / หีของมึง."""
    men = {n for n, v in (info or {}).items() if n and v.get("g") == "M"}
    if not men:
        return text
    out = []
    for ln in text.split("\n"):
        st = ln.strip()
        m = QUOTE_RE.match(st)
        if m and tag_speaker(st[m.end():], info) in men and re.search(r"หี(?:ของ)?(?:กู|ผม)", m.group(0)):
            q2 = re.sub(r"หี(ของ)?(?:กู|ผม)", lambda x: "หี" + (x.group(1) or "") + "มึง", m.group(0))
            ln = ln.replace(m.group(0), q2, 1)
        out.append(ln)
    return "\n".join(out)


def chapter_polish(text, info):
    """R71 (29 Oct, r70-trio): end-of-chapter pass: (1) one register per rough man (unify_register); (2) "พุ่งออกมา" x9: keep every 3rd, rotate the rest;
    (3) a pussy that overflows "ออกทางปาก" (anatomy nonsense) overflows "ออกมา"; (4) a trailing echo run of one-word lines (>= 8 short quotes at the very end)
    is cut to its first three lines."""
    t = fix_man_pussy(unify_register(text, info), info)
    alts = ["สาดออกมา", "ทะลักออกมา", "ไหลทะลักออกมา"]
    n = [0]
    def rep(m):
        n[0] += 1
        return m.group(0) if (n[0] - 1) % 3 == 0 else alts[(n[0] - 2) % len(alts)]
    t = re.sub(r"พุ่งออกมา", rep, t)
    hn = [0]
    def rep_h(m):   # R74 (r73-trio-9: "เอามือลูบ" x8): keep the 1st and every 4th, rotate the rest
        hn[0] += 1
        return m.group(0) if (hn[0] - 1) % 4 == 0 else ["ลูบ", "ไล้", "ใช้ฝ่ามือไล้"][(hn[0] - 2) % 3]
    t = re.sub(r"เอามือลูบ", rep_h, t)
    nm = [x for x in (info or {}) if x]
    if nm:   # R73 (r72-trio-e: "ควยของปกรณ์" x8, "ควยของธนา" x8): chapter-wide, keep the 1st and every 3rd per name; the others lose "ของ<name>" when that man was named in the last 150 characters
        alt = "|".join(re.escape(x) for x in sorted(nm, key=len, reverse=True))
        cn = {}
        base = t
        def rep3(m):
            w, who = m.group(1), m.group(2)
            cn[(w, who)] = cn.get((w, who), 0) + 1
            if (cn[(w, who)] - 1) % 3 == 0:
                return m.group(0)
            return w if who in base[max(0, m.start() - 150):m.start()] else m.group(0)
        t = re.sub(r"(ควย|หน้าอก|ริมฝีปาก|มือ|ต้นขา|สะโพก|ปลายนิ้ว|ปลายลิ้น)ของ(" + alt + r")", rep3, t)
    t = re.sub(r"(หี[^\n“”]{0,30})ล้นออกทางปาก", r"\1ล้นออกมาไหลเยิ้ม", t)
    lines = t.split("\n")
    k = len(lines)
    run = 0
    while k > 0:
        st = lines[k - 1].strip()
        if not st:
            k -= 1
            continue
        m = QUOTE_RE.match(st)
        if m and len(re.sub(r"[\s.…!?,“”\"]", "", m.group(0))) <= 12:
            run += 1
            k -= 1
        else:
            break
    if run >= 8:
        body = [i for i in range(k, len(lines)) if lines[i].strip()]
        keep = body[:3]
        lines = lines[:k] + [lines[i] for i in keep]
        t = "\n".join(lines)
    return t


_MALE_CUM_LINE = re.compile(r"น้ำเชื้อ|น้ำควย(?!เธอ)|ปล่อยน้ำ|พุ่ง(?:ออก|ใส่|เข้า)|ฉีดน้ำ|แตก(?:ใน|ใส่|บน)")
_HER_LINE = re.compile(r"น้ำหี|น้ำเงี่ยน|เธอ(?:แตก|ถึง)|ลลิ?ตา(?:แตก|ถึง)")


def strip_early_cum(text, pp, names=None):
    """R80 (r79-dp: ก้อง came in beat 2, then again at the end): in a part of a 2/3-men scene whose plan has no finish step, a narration line in which a man comes
    (น้ำเชื้อ / ปล่อยน้ำ / พุ่งออก...) and a spoken line in which a man says he HAS come are removed (her own climax lines are kept)."""
    if not pp or not pp.get("g"):
        return text
    t = pose_table()
    if any(_pose_kind(t, x["id"]) == "finish" for x in pp.get("steps", [])):
        return text
    out = []
    for ln in (text or "").split("\n"):
        st = ln.strip()
        if not st:
            out.append(ln)
            continue
        m = QUOTE_RE.match(st)
        if m:
            q = _NOT_YET.sub("", m.group(0))
            tag = st[m.end():].strip()
            cast = pp.get("cast") or {}
            men = cast.get("men") or [x for x in (cast.get("A"), cast.get("B")) if x]
            if _DONE_CUM.search(q) and any(tag.startswith(x) for x in men):
                continue
            out.append(ln)
            continue
        if _MALE_CUM_LINE.search(st) and not _HER_LINE.search(st):
            continue
        out.append(ln)
    return "\n".join(out)


_DP_DONE = re.compile(r"สองแท่ง|สองดอก|ควยทั้งสอง|สองควย|ควยสองอัน")


def _dp_patch_prompt(A, B, C, W, ctx):
    mouth = (" %s is at her mouth and she sucks him." % C) if C else ""
    return ("Continue this Thai erotic novel scene with ONLY the next 4-6 short paragraphs (Thai prose, no title, no notes). Moment to write: %s is already inside %s's pussy; now %s slowly eases his cock into the SAME pussy beside it, so TWO cocks are inside her pussy at once (ควยสองแท่งในหีเดียว), her pussy is stretched full, she gasps and asks them to go slow, then the two men move together (พร้อมกัน).%s "
            "You MUST use the words ควยสองแท่ง and พร้อมกัน. Rules: nothing goes in her anus; nobody comes or says he has come (only she may climax); the men never touch each other; the woman has only a pussy and the men only cocks; direct erotic-novel words (ควย, หี, น้ำเงี่ยน), no clinical words; include at least 4 quoted lines of moaning and rough talk, every quote followed by who says it; the men speak rough (กู, มึง). One short paragraph per movement.\n\nThe scene so far ends with:\n%s") % (A, W, B, mouth, ctx)


def dp_patch(part, pp, log=None):
    """R82 (r79-dp, r80-dp, r81-dp: the model never wrote the double, in the anal or the pussy version): when the plan has the double step (T6/M7) and the finished part has no
    'two cocks' wording, ask the model for ONE short passage that is only that moment and put it at the step's place. Returns (part, inserted)."""
    lg = log if log is not None else []
    if os.environ.get("NOVEL_DPPATCH", "1") == "0" or not pp:
        lg.append("off")
        return part, False
    steps = pp.get("steps") or []
    idx = [k for k, x in enumerate(steps) if x.get("id") in ("T6", "M7")]
    if not idx:
        lg.append("no-dp-step:" + ",".join(x.get("id", "?") for x in steps))
        return part, False
    if _DP_DONE.search(part or ""):
        lg.append("model-wrote-it")
        return part, False
    st = steps[idx[0]]
    cast = pp.get("cast") or {}
    if st.get("id") == "T6":
        r = st.get("r") or cast.get("men") or []
        if len(r) < 3:
            lg.append("no-roles")
            return part, False
        A, B, C = r[0], r[1], r[2]
    else:
        A, B, C = cast.get("A"), cast.get("B"), None
        if not (A and B):
            lg.append("no-cast")
            return part, False
    W = cast.get("W") or "she"
    sep = "\n\n" if "\n\n" in part else "\n"
    paras = [x for x in part.split(sep) if x.strip()]
    if len(paras) < 3:
        lg.append("part-too-short")
        return part, False
    pos = max(2, min(len(paras), int(len(paras) * (idx[0] + 0.5) / len(steps))))
    ctx = sep.join(paras[max(0, pos - 3):pos])
    prompt = _dp_patch_prompt(A, B, C, W, ctx)
    st_ = settings()
    for _ in range(2):
        try:
            payload = {"messages": [{"role": "user", "content": prompt}], "stream": False, "max_tokens": 900,
                       "temperature": st_["temperature"], "chat_template_kwargs": {"enable_thinking": False}}
            payload.update(ANTI_REPEAT)
            req = urllib.request.Request(LLM + "/v1/chat/completions", json.dumps(payload).encode(), {"Content-Type": "application/json"})
            r = json.loads(urllib.request.urlopen(req, timeout=600).read().decode("utf-8"))
            t = clean_part(r["choices"][0]["message"].get("content") or "").strip()
        except Exception as e:
            lg.append("call-error:" + str(e)[:60])
            continue
        if not t or "```" in t or t.lstrip().startswith("{") or not _DP_DONE.search(t):
            lg.append("reply-without-keywords:%d:%s" % (len(t or ""), (t or "")[:60].replace("\n", " ")))
            continue
        if re.search(r"(?:สอด|ดัน|เสียบ|ยัด|กด|จ่อ|เบียด)[^\n]{0,30}(?:ทวาร|รูก้น|รูตูด|ก้นเธอ|ก้นของเธอ)|เจล", t):
            lg.append("reply-anal-or-gel")
            continue
        t2 = strip_early_cum(t, {"g": True, "steps": [{"id": "M1"}], "cast": cast})
        if len(t2) < len(t) * 0.8 or len(t2) < 120:
            lg.append("reply-too-short-after-strip:%d" % len(t2))
            continue
        new = [x for x in t2.split("\n") if x.strip()]
        lg.append("inserted")
        return sep.join(paras[:pos] + new + paras[pos:]), True
    return part, False


def _her_only(st):
    """R100: a line about HER wetness / climax - but 'น้ำหีของเขาและเธอผสมปนเปกัน' next to a man's น้ำเชื้อ is still his finish line (r99-dp: ก้อง's finish was not seen, a template line was added, he came twice)."""
    return bool(_HER_LINE.search(st)) and not re.search(r"น้ำเชื้อ|น้ำควย", st)


def strip_repeat_cum(text, prior, pp):
    """R83 (r82-dp: ธนา came in the mouth twice, ปกรณ์ announced it 4 times, ก้อง twice in one scene): a man comes ONCE per scene. In a group part, a line in which a man comes /
    says he has come is removed when he already did in the earlier parts of the same scene (`prior`) or earlier in this part; a bare cum-narration line right after a removed
    announcement goes too. Her climax lines stay."""
    if not pp or not pp.get("g"):
        return text
    cast = pp.get("cast") or {}
    men = cast.get("men") or [x for x in (cast.get("A"), cast.get("B")) if x]
    if not men:
        return text

    def owner(st):
        m = QUOTE_RE.match(st)
        if m:
            q = _NOT_YET.sub("", m.group(0))
            tag = st[m.end():].strip()
            if _DONE_CUM.search(q):
                for x in men:
                    if tag.startswith(x):
                        return x, True
            return None, False
        if _MALE_CUM_LINE.search(st) and not _her_only(st):
            # R92 (r91-dp: “ลลิตากลืนน้ำควย... ปกรณ์และก้องมองลงมา” made ปกรณ์ 'already come' and his real finish was cut): the owner is the man named BEFORE the cum word
            # (nearest one, with no woman's name between) or in "น้ำเชื้อของ<man>"; a man only mentioned after it is a bystander
            cm = _MALE_CUM_LINE.search(st)
            pre = st[:cm.start()]
            hits = [(pre.rfind(x), x) for x in men if x in pre]
            wn = cast.get("W")
            if hits:
                at, who = max(hits)
                if not (wn and wn in pre[at:]):
                    return who, False
            for x in men:
                if re.search(r"(?:น้ำเชื้อ|น้ำควย)[^\s“”]{0,8}\s?ของ" + re.escape(x), st):
                    return x, False
        return None, False

    # R94 (r93-dp: ก้อง's real finish "เขาดันควย...ปล่อยน้ำเชื้อออกมาเต็มในหีของเธอ" was cut as a bare line after another man's duplicate announcement, and ธนา's nameless
    # "น้ำเชื้ออุ่นๆ พุ่งพล่านออกมาเต็มปากเธอ" was not counted so he came twice): a cum line with NO man's name belongs to the man whose planned finish kind matches what the line says
    kinds = {}   # man -> the kinds of finish he has in this part (a man can have the early one and the late one in one part)
    for st_ in pp.get("steps", []):
        f_ = st_.get("fin")
        if isinstance(f_, dict):
            for nm, k_ in f_.items():
                if k_ in ("F1", "F2", "F3", "F4", "F5"):
                    kinds.setdefault(nm, set()).add(k_)
        elif st_.get("who") and st_.get("id") in ("F1", "F2", "F3", "F4", "F5"):
            kinds.setdefault(st_["who"], set()).add(st_["id"])
    wn_ = cast.get("W")
    line_kind = _line_kind
    allowed = pp.get("nfin") or {}   # R96: how many times each man comes in the whole scene (1 for a short scene, 2 for a proper one)

    def pron_owner(st, prev):
        if not kinds or any(x in st for x in men):
            return None
        cm_ = _MALE_CUM_LINE.search(st)
        if not cm_ or (wn_ and wn_ in st[:cm_.start()]) or re.match(r"\s*เธอ", st):
            return None
        lk = line_kind(st)
        cands = [x for x in men if lk in kinds.get(x, ())]
        if prev[0] in cands and prev[1] <= 2:
            return prev[0]
        if len(kinds) < 2:
            return None   # R98: with ONE planned man in the part a nameless line is his only when the man named just before it is he (r98-dp: 'เขาถอยควยออก... น้ำควยพุ่งเปื้อนแผ่นหลัง' + 'น้ำเชื้อของปกรณ์สาด...' came out as two finishes)
        return cands[0] if len(cands) == 1 else None

    came_n, first_at = {}, {}
    EVENT = 6   # lines: everything of one man's finish (announcement, narration, swallow) lies within this window; a later line of his is a new finish

    def verdict(o, k):
        """'keep' / 'drop' for a finish line of man o at line k (and register it)."""
        if o in first_at:
            d = k - first_at[o]
            if d <= 1:
                return "keep"       # the lines right after the first announcement are the same finish
            if d <= EVENT:
                return "drop"       # told again inside the same finish
        if came_n.get(o, 0) >= allowed.get(o, 1):
            return "drop"           # he has come as often as the plan lets him in this scene
        came_n[o] = came_n.get(o, 0) + 1
        first_at[o] = k
        return "keep"

    k = 0
    for ln in (prior or "").split("\n"):
        if not ln.strip():
            continue
        k += 1
        o, _ = owner(ln.strip())
        if o:
            verdict(o, k)
    for o_ in list(first_at):
        first_at[o_] = -999   # a finish of an earlier part is over: any further finish of his in this part is judged by the count alone
    out = []
    drop_next = None
    prev_n = (None, 9)
    for ln in (text or "").split("\n"):
        st = ln.strip()
        if not st:
            out.append(ln)
            continue
        k += 1
        o, spoken = owner(st)
        if o is None and not QUOTE_RE.match(st) and _MALE_CUM_LINE.search(st) and not _her_only(st):
            o, spoken = pron_owner(st, prev_n), False
        _nm = [x for x in men if x in st]
        prev_n = (min(_nm, key=lambda x: st.find(x)), 0) if _nm else (prev_n[0], prev_n[1] + 1)
        if o and verdict(o, k) == "drop":
            drop_next = o if spoken else None
            continue
        if (drop_next and not QUOTE_RE.match(st) and _MALE_CUM_LINE.search(st) and not _her_only(st) and not any(x in st for x in men)
              and line_kind(st) in kinds.get(drop_next, (line_kind(st),))):   # R94: only the same kind of finish (his own, told twice)
            drop_next = None
            continue
        drop_next = None
        out.append(ln)
    return "\n".join(out)


_PLACE_WORD = {"ก้น": "ก้น", "หลัง": "แผ่นหลัง", "ใบหน้า": "ใบหน้า", "หน้าอก": "หน้าอก", "หน้าท้อง": "หน้าท้อง", "ต้นขา": "ต้นขา"}
_ANY_PLACE = re.compile(r"(?:แผ่น)?หลัง(?!จาก|ที่|เสร็จ)|หน้าท้อง|ท้องน้อย|เนินอก|หน้าอก|แก้มก้น|ก้น|ใบหน้า|ต้นขา")


_INSIDE_HER = re.compile(r"(?:ภายใน|ข้างใน|ใน)(?:หี|ตัว)(?:ของ)?(?:เธอ|ลลิ?ตา)?")


def fix_finish_place(text, pp):
    """R87 (r86-dp: summary 'ปกรณ์แตกบนหลังเธอ', written on her belly): in a group part, in the narration of a man whose plan names the place of his outside finish,
    a wrong body part in his cum lines (and the 2 lines after) is swapped for the planned one."""
    if not pp or not pp.get("g"):
        return text
    want = {}
    for st in pp.get("steps", []):
        for nm, pl in (st.get("place") or {}).items():
            if pl in _PLACE_WORD:
                want[nm] = pl
    if not want:
        return text
    cast = pp.get("cast") or {}
    allmen = list(cast.get("men") or [x for x in (cast.get("A"), cast.get("B")) if x]) + list(want)
    out, cur, left, win = [], None, 0, 0
    prev_named, prev_age = None, 9
    nm_rx = "|".join(re.escape(x) for x in sorted(allmen, key=len, reverse=True))
    for ln_full in (text or "").split("\n"):
        ln = ln_full
        st = ln.strip()
        qm = QUOTE_RE.match(st) if st else None
        lead = ln[:len(ln) - len(ln.lstrip())]
        qpart = qm.group(0) if qm else ""
        if qm:   # R90c (r90b-dp: “สัส... แตกแล้ว!” เขาตวาดเสียงก้อง น้ำควย...): the narration after a leading quote is checked too
            ln = ln[len(lead) + len(qpart):]
            st = ln.strip()
        st = re.sub(r"เสียง(?:%s)" % nm_rx, "เสียง", st) if nm_rx else st   # "เสียงก้อง" is a voice, not the man
        if st:
            hits = sorted((st.find(x), x) for x in want if x in st)
            if cur and any(x in st and x != cur for x in allmen):
                cur = None
            if not hits and win <= 0 and not any(x in st for x in allmen) and prev_named in want and prev_age <= 2 and _MALE_CUM_LINE.search(st) and not _HER_LINE.search(st):
                hits = [(0, prev_named)]   # he is "เขา" here: the man named in the line just before
            if hits and _MALE_CUM_LINE.search(st) and not _HER_LINE.search(st):
                cur, left = hits[0][1], 3
            if hits and _MALE_CUM_LINE.search(st) and not _HER_LINE.search(st):
                win = 6
            if (win > 0 and not hits and _MALE_CUM_LINE.search(st) and not _HER_LINE.search(st) and not any(x in st for x in allmen)
                  and any(re.search(dict(_PLACES).get(w_, "x^"), st) for w_ in want.values())):
                win = 0   # a bare cum line on the planned place right after his own finish: the same finish told twice
                continue
            elif cur and left > 0 and _MALE_CUM_LINE.search(st) and not _HER_LINE.search(st):
                rx = dict(_PLACES).get(want[cur], "")
                w_ = _PLACE_WORD.get(want[cur], "")
                if rx and _INSIDE_HER.search(st):   # planned outside, written inside her pussy: move it to the planned place
                    ln = _INSIDE_HER.sub("บน" + w_ + "ของเธอ", ln, count=1)
                elif rx and not re.search(rx, st) and _ANY_PLACE.search(st):
                    # R95 (r93-dp: "ลงบนหน้าอกและหน้าท้อง" -> "แผ่นหลังและหน้าท้อง"): EVERY wrong part goes, "<planned>และ<planned>" collapses
                    ln = _ANY_PLACE.sub(w_, ln)
                    ln = re.sub(r"(%s)(?:และ|กับ|ทั้ง)\1" % re.escape(w_), r"\1", ln)
                elif rx:
                    # R90 (r89-dp: "อาบหน้าท้องของเธอ ... หยดลงบนหน้าอกและหลัง", planned: the back only): the cum goes to the planned place ONLY.
                    # a wrong part in a clause that has no planned word becomes the planned one; "<wrong>และ<planned>" / "<planned>และ<wrong>" keeps the planned one
                    bad = _ANY_PLACE.pattern
                    parts_ = ln.split(" ")
                    for k_, c_ in enumerate(parts_):
                        if _ANY_PLACE.search(c_) and not re.search(rx, c_) and re.search(r"น้ำเชื้อ|น้ำควย|พุ่ง|สาด|อาบ|หยด|ฉีด|กระเซ็น|เปื้อน|ยิง|ปล่อย|ราด|เลอะ|ไหล", c_):
                            _n_ = [0]
                            def _rp(m_, w_=w_, _n_=_n_):   # R93 (r92-dp: "เปื้อนหน้าท้องและไหลลงไปตามหน้าอก"): EVERY wrong part goes; the first becomes the planned place, later ones "ตัว"
                                _n_[0] += 1
                                return w_ if _n_[0] == 1 else "ตัว"
                            parts_[k_] = _ANY_PLACE.sub(_rp, c_)
                    ln = " ".join(parts_)
                    ln = re.sub(r"(?:%s)(?:และ|กับ|ทั้ง)(%s)" % (bad, rx), r"\1", ln)
                    ln = re.sub(r"(%s[ก-ฮ]{0,3})(?:และ|กับ)(?:%s)" % (rx, bad), r"\1", ln)
        if st:
            named = [x for x in allmen if x in st]
            if named:
                prev_named, prev_age = min(named, key=lambda x: st.find(x)), 0
            else:
                prev_age += 1
        if qm:
            ln = lead + qpart + ln
        if win > 0:
            win -= 1
        if left > 0:
            left -= 1
        out.append(ln)
    return "\n".join(out)


def fix_name_krub(text, info):
    """R95 (r93-dp: "“รอเดี๋ยว” ปกรณ์ครับ ก้องบอก"): a man's name followed by ครับ in narration / a speaker tag is just the name (a ครับ is only ever inside a quote)."""
    M = [n for n, v in (info or {}).items() if n and v.get("g") == "M"]
    if not M:
        return text
    mr = "|".join(re.escape(x) for x in sorted(M, key=len, reverse=True))
    out = []
    for ln in (text or "").split("\n"):
        m = QUOTE_RE.match(ln.strip())
        if m:
            i = ln.find(m.group(0)) + len(m.group(0))
            ln = ln[:i] + re.sub(r"(%s)ครับ" % mr, r"\1", ln[i:])
        else:
            ln = re.sub(r"(%s)ครับ" % mr, r"\1", ln)
        out.append(ln)
    return "\n".join(out)


def ensure_finish_lines(text, prior, pp, fallback=True):
    """R95 (r93-dp, r92-dp: ก้อง said 'กูจะแตกในมึงแล้ว' but the model never wrote that he came): in a group part whose plan holds a man's finish step, if that man ANNOUNCES it
    (a quote of his saying แตก) and no narrated cum line of that kind exists in this part or earlier in the scene, one short narrated line of the planned kind / place is added after
    his last announcement."""
    if not pp or not pp.get("g"):
        return text
    cast = pp.get("cast") or {}
    men = list(cast.get("men") or [x for x in (cast.get("A"), cast.get("B")) if x])
    kinds, place, second = {}, {}, set()   # man -> kinds of finish in THIS part; man -> place of an outside finish; men whose second-round finish is in this part
    for st_ in pp.get("steps", []):
        f_ = st_.get("fin")
        if isinstance(f_, dict):
            for nm, k_ in f_.items():
                if k_ in ("F1", "F2", "F3", "F4", "F5"):
                    kinds.setdefault(nm, set()).add(k_)
        elif st_.get("who") and st_.get("id") in ("F1", "F2", "F3", "F4", "F5"):
            kinds.setdefault(st_["who"], set()).add(st_["id"])
        place.update(st_.get("place") or {})
        if st_.get("rd") == 2 and st_.get("who"):
            second.add(st_["who"])
        if st_.get("rd") == 2 and isinstance(f_, dict):   # R111: the men of a round-2 group step (closing image) are judged on this part too
            second.update(f_)
    if not kinds or not men:
        return text
    women_n = list(cast.get("women") or [x for x in (cast.get("W"), cast.get("W2")) if x])
    closing_men = set()
    for st_ in pp.get("steps", []):
        if st_.get("id") == "CQ" and isinstance(st_.get("fin"), dict):
            closing_men |= set(st_["fin"])
    kind_of = _line_kind

    def narrated(blob, who):
        last_named = []   # R113 (r113-dp: a nameless line 'ลลิตายิ้มในลำคอขณะปล่อยน้ำเชื้อออกมา…' after ปกรณ์'s and ธนา's cum was credited to เอก, so his planned closing cum was never added): a nameless cum line belongs to the man named in the last few lines before it
        for ln_i, ln in enumerate((blob or "").split("\n")):
            st = ln.strip()
            qm_ = QUOTE_RE.match(st)
            if qm_:
                st = st[qm_.end():].strip()   # R102 (r103-dp: “ชั้นจะแตกให้ดู” เธอตอบก่อนจะเขย่าจนน้ำควยพุ่งออกมาเต็มฝ่ามือ was narration too, a template line was added): narration after a leading quote counts
            if not st or _her_only(st):
                continue
            named = sorted((st.find(x), x) for x in men if x in st)
            if named:
                last_named.append((ln_i, named[-1][1]))
            elif _MALE_CUM_LINE.search(st):
                near_ = [x for i_, x in last_named if ln_i - i_ <= 4]
                if near_ and near_[-1] != who:
                    continue
            if len(named) >= 2:   # R108 (r108-dp: one line 'ธนาแตกรัวๆ ... ปกรณ์แตกรัวๆ ... ก้องแตกลงปาก' was judged as one kind, so each man got a second, template line): a line that names several men is judged clause by clause, each man by his own clause
                pos = [p_ for p_, x in named if x == who]
                if not pos:
                    continue
                nxt = [p_ for p_, x in named if p_ > pos[0] and x != who]
                st = st[pos[0]:(nxt[0] if nxt else len(st))].strip()
                if not (_MALE_CUM_LINE.search(st) or re.match(re.escape(who) + r"\s*(?:ก็)?\s*แตก", st)):
                    continue
            elif not _MALE_CUM_LINE.search(st):
                continue
            if not named and re.search(r"จูบ|แลกน้ำ|ถอนจูบ|มุมปากของทั้ง|ปากของทั้งคู่", st):   # R111: the cum-sharing kiss ('สายน้ำสีขาวขุ่นไหลย้อยจากมุมปากของทั้งคู่') is no man's finish
                continue
            k_ = kind_of(st)
            if k_ not in kinds[who] and not (k_ == "F2" and "F4" in kinds[who] and re.search(r"หน้าอก|นม(?!ือ)|ร่องอก", st)):   # R108: cum 'on her chest and face' is the titjob finish too
                continue
            cm = _MALE_CUM_LINE.search(st) or re.search(re.escape(who), st)
            near = [x for x in men if x in st[:cm.end() + 10]]   # R98: men named before the cum words or right after them ('น้ำควยของธนา'); a name far after belongs to the next sentence
            if (who in near) or not near:
                return True
        return False

    lines = (text or "").split("\n")
    for who in men:
        if who not in kinds or narrated(text, who) or (who not in second and narrated(prior, who)):   # R98: a second-round finish is judged on this part only (an earlier pre-cum / round-1 line of his must not hide it)
            continue
        last = None
        for i, ln in enumerate(lines):
            m = QUOTE_RE.match(ln.strip())
            if m and re.search(r"แตก", _NOT_YET.sub("", m.group(0)) + m.group(0)) and ln.strip()[m.end():].strip().startswith(who):
                last = i
        if last is None:
            if not fallback:
                continue
            last = len(lines) - 1   # R97: he never announced it either - the planned finish is added at the end of the part (a man the model skipped in a common finish step)
            while last > 0 and not lines[last].strip():
                last -= 1
            if closing_men:   # R111 (r111-dp: ปกรณ์'s line landed after the shared-cum kiss; r112-dp: the kiss lines were taken for the closers' cum and nothing was added): the closers' cum lines (and a late line of another man) go BEFORE the cum-sharing kiss
                kl_ = [i for i, ln in enumerate(lines)
                       if re.search(r"แลกน้ำ|แลกกัน|ส่งน้ำ|ป้อนน้ำ|ถ่ายน้ำ|ถอนจูบ", ln) or (re.search(r"จูบ", ln) and sum(1 for w_ in women_n if w_ in ln) >= 2)]
                cl_ = [i for i, ln in enumerate(lines) if who not in closing_men and any(c in ln for c in closing_men) and _MALE_CUM_LINE.search(ln)]
                cand_ = kl_ + cl_
                if cand_:
                    last = max(0, min(cand_) - 1)
        k = sorted(kinds[who], key=lambda x: ("F3", "F2", "F4", "F5", "F1").index(x))[0]   # a man with two finishes in one part gets the late one added first
        if k == "F4":
            new = "%sชักควยหนีบอยู่ระหว่างนมของเธอแรงๆ จนแตก น้ำเชื้อร้อนๆ พุ่งกระเซ็นเปื้อนร่องอก นมและใบหน้าของเธอ" % who
        elif k == "F5":
            new = "%sขยับควยในมือเธอเร็วขึ้นจนแตก น้ำเชื้อร้อนๆ ทะลักล้นมือของเธอ" % who
        elif k == "F3":
            new = "%sดันควยเข้าไปลึกที่สุดในหีของเธอ แล้วปล่อยน้ำเชื้อร้อนๆ พุ่งออกมาเต็มข้างใน" % who
        elif k == "F1" and who in closing_men:
            new = "น้ำเชื้อของ%sพุ่งออกมาเต็มปากเธอ เธออมมันไว้ในปากไม่กลืน" % who
        elif k == "F1":
            new = "น้ำเชื้อของ%sพุ่งออกมาเต็มปากเธอ แล้วเธอก็กลืนลงไป" % who
        else:
            new = "%sชักควยออกมาแล้วปล่อยน้ำเชื้อพุ่งลงบน%sของเธอ" % (who, _PLACE_WORD.get(place.get(who, ""), "แผ่นหลัง"))
        lines.insert(last + 1, new)
        text = "\n".join(lines)
    return "\n".join(lines)


def thin_manner(text):
    """R60 (R60: "…ออกมาเสียง…" x10 e.g. ครางออกมาเสียงแหลม): in one part keep the first, the others lose the "เสียงX" tail (ครางออกมา)."""
    n = [0]
    def rep(m):
        n[0] += 1
        return m.group(0) if n[0] == 1 else "ออกมา"
    adj = r"(?:สั่นเครือ|แหบพร่า|แหลมสูง|ทุ้มต่ำ|แผ่วเบา|แหลม|ทุ้ม|สั่น|แผ่ว|เบา|ต่ำ|ดัง|ห้าว|พร่า|แหบ|เครือ|ลั่น|ขาด)"
    m_ = [0]
    def rep3(m):
        m_[0] += 1
        return m.group(0) if m_[0] == 1 else m.group(1)   # R61: "ครางเสียงแหลม" x9 -> keep the first per part, then just "คราง"
    text = re.sub(r"(คราง|ร้อง|คราง)เสียง" + adj, rep3, text)
    return re.sub(r"ออกมาเสียง" + adj, rep, text)


def thin_fluid(text):
    """R58 (R57: "น้ำเงี่ยน" x8): in one part keep every other "น้ำเงี่ยน", the others become "น้ำหี" (kept for น้ำเงี่ยนของ... too)."""
    n = [0]
    def rep(m):
        n[0] += 1
        return m.group(0) if n[0] % 2 == 1 else "น้ำหี"
    return re.sub(r"น้ำเงี่ยน", rep, text)


def fix_swallow(text):
    """R50 (29 Oct, user): "ให้หมด" goes with a liquid, never with a body part: you cannot suck a cock / eat a pussy "until it is all gone".
    ดูดควย…ให้หมด -> ดูดควย…แรงๆ ; อมควย…ให้หมด -> อมควย…ให้ลึก ; เลีย/กินหี…ให้หมด -> …แรงๆ. A phrase with น้ำ (น้ำควย, น้ำเชื้อ, น้ำหี) is left alone."""
    def rep(m):
        verb, mid = m.group(1), m.group(3)
        if "น้ำ" in m.group(2) + mid:
            return m.group(0)
        return m.group(0)[: m.start(4) - m.start(0)] + ("ให้ลึก" if verb == "อม" else "แรงๆ")
    text = re.sub(r"(ดูด|อม|เลีย|กิน|แดก)((?:ควย|ดุ้น|หี|แตด|เม็ดแตด|นม|หัวนม))([^\s“”\"]{0,10}?)(ให้หมด(?:เลย)?)", rep, text)
    # R54: a bare "อมให้หมด / ดูดจนหมด" (no object) has the same problem
    return re.sub(r"(?<![ก-ฮ])(อม|ดูด)(?:ให้|จน)หมด(?=เลย|นะ|สิ|ซิ|ด้วย|[^ก-ฮ]|$)", lambda m: m.group(1) + ("ให้ลึก" if m.group(1) == "อม" else "แรงๆ"), text)


def fix_particles(text, info, rough=False):
    """Make ครับ / ค่ะ agree with the gender of the speaker named in the tag after the quote."""
    lines = []
    # R35 (29 Oct): a part where a man says กู / มึง is a rough scene: his polite ครับ in the same part (e.g. "ท่านี้เสียวกว่าไหมครับ") is dropped
    rough_scene = bool(re.search(r"(?<![ก-ฮ])กู(?!เก|รู)|มึง", text))
    # R55 (29 Oct, R54 scene 2): one man must keep ONE register inside a sexual part (ผม in the foreplay, then กู / มึง): collect who says กู / มึง
    rough_men = set()
    for ln0 in text.split("\n"):
        m0 = QUOTE_RE.match(ln0.strip())
        if m0 and re.search(r"(?<![ก-ฮ])กู(?!เก|รู)|มึง", m0.group(0)):
            n0 = tag_speaker(ln0.strip()[m0.end():], info)
            if n0 and info.get(n0, {}).get("g") == "M":
                rough_men.add(n0)
    _mn = "|".join(re.escape(x) for x in sorted([n for n, v in (info or {}).items() if n and v.get("g") == "M"], key=len, reverse=True))
    if _mn:   # R59 (R59 text: "หิวน้ำหีของปกรณ์"): a man has น้ำควย, never น้ำหี
        text = re.sub(r"น้ำ(?:หี|เงี่ยน)(?:ของ)?(%s)" % _mn, r"น้ำควยของ\1", text)
    men_n = len({tag_speaker(ln0.strip()[QUOTE_RE.match(ln0.strip()).end():], info) for ln0 in text.split("\n") if QUOTE_RE.match(ln0.strip())} & {n for n, v in info.items() if v.get("g") == "M"})   # R56: how many different men talk in this part
    blow_part = bool(re.search(r"อมควย|ดูดควย|อ้าปากรับควย|ควย[^\n“]{0,12}(?:เข้าปาก|ในปาก)", text))
    sexy_part = len(re.findall(r"ควย|หี(?![ก-ฮ])|เย็ด|น้ำเงี่ยน|น้ำเชื้อ|หัวนม", text)) >= 3   # R50: a clearly sexual part (self-name rule also for plain lines like "ก้องก็อยากเล่นด้วย")
    for ln in text.split("\n"):
        m = QUOTE_RE.match(ln.strip())
        if m:
            tag = ln.strip()[m.end():]
            sp = None
            spn = tag_speaker(tag, info)
            if spn:
                sp = info[spn]["g"]
            if sp is None:
                tg = tag.lstrip()
                sp = "M" if tg.startswith("เขา") else "F" if tg.startswith("เธอ") else None
            q = m.group(0)
            if sp is None:   # untagged line: infer from the words themselves
                if re.search(r"ค่ะ|คะ|นะจ๊ะ|จ๋า", q) or re.search(r"(?<![ก-ฮ])ชั้น(?!วาง|ที่|หนึ่ง|สอง|สาม|บน|ล่าง|ใน|เรียน|ปี|ประถม|มัธยม|เชิง|ๆ)", q):   # R19: ชั้น is the women's pronoun (men say ผม/กู): untagged "ชั้น...มึง" is a woman -> มึง becomes คุณ
                    sp = "F"
                elif re.search(r"ครับ|กู|มึง", q):
                    sp = "M"
                elif re.search(r"(?<!ดิ)ฉัน(?![ทใ])", q):
                    sp = "F"   # an untagged ฉัน is treated as a woman (หนู to elders/family, else ชั้น below)
            if spn and sp in ("M", "F") and spn in q and (sexy_part or re.search(r"ควย|ดุ้น|หี(?![ก-ฮ])|แตก|เย็ด|ดูด|อม|เลีย|น้ำ", q)):
                # R49 (29 Oct): a speaker never names himself/herself in bed ("ดูดควยก้องให้หมด" said by ก้อง): the name becomes ผม / กู / ชั้น, a leading self-call is dropped
                me = "ชั้น" if sp == "F" else ("กู" if (rough_scene or re.search(r"มึง|(?<![ก-ฮ])กู(?!เก|รู)|ดูด|กลืน|กิน|อม|ให้หมด|แรงๆ|สั่ง", q)) else "ผม")   # user 29 Oct: a man's dirty order in bed is กู ("ดูดควยกูแรงๆ", "กินน้ำควยกูให้หมด"), not ผม
                nm_ = re.escape(spn)
                q = re.sub(r"(ควย|ดุ้น|หี|น้ำเงี่ยน|น้ำควย|น้ำเชื้อ|น้ำหี|ปาก|ลิ้น|นม|ตัว)(?:ของ)?" + nm_, lambda mm: mm.group(1) + me, q)
                q = re.sub(r"ของ" + nm_, "ของ" + me, q)
                q = re.sub(r"(?<![ก-ฮ])" + nm_ + r"(?=\s*(?:จะ|ก็|อยาก|กำลัง|ชอบ|ต้องการ|ไม่ไหว|ขอ))", me, q)
                q = re.sub(r"^([“\"])" + nm_ + r"[\s.…,!]*(?=[ก-ฮเแโใไ])", r"\1", q)
            if sp == "M" and (men_n <= 1 or re.search(r"ควยกู|ควยผม|ของกู|ของผม|ตอดควย|กูจะ|ผมจะ|กูกำลัง|ผมกำลัง", q)):   # two men: only when he speaks about what her body does to HIM (not to the other man)
                # R56 (R54/R55): a man talking to her never names her body by her name ("หีของลลิตา…"): หีมึง (rough) / หีของคุณ (polite)
                for fn in [n for n, v in info.items() if v.get("g") == "F" and n != spn and n in q]:
                    pr = "มึง" if (spn in rough_men or re.search(r"(?<![ก-ฮ])กู(?!เก|รู)|มึง", q)) else "คุณ"
                    q = re.sub(r"(ควย|หี|นม|หัวนม|น้ำหี|น้ำเงี่ยน|ปาก|ลิ้น|ก้น|ตูด|เม็ดแตด|ตัว|น้ำนม)(?:ของ)?" + re.escape(fn), lambda mm: mm.group(1) + ("" if pr == "มึง" else "ของ") + pr, q)
            if sp == "M" and blow_part and re.search(r"รสชาติ(?:ของ)?หี", q):
                # R56: while her mouth is on his cock he does not taste her pussy
                q = re.sub(r"รสชาติ(?:ของ)?หี(?:ของ)?(?:มึง|เธอ|คุณ|[ก-ฮ]{2,8}(?=[^ก-ฮ]|ห|ใ))?", "ปากมึง", q)
            HAIR = r"(?![ก-ฮ]*(?:ยาว|สั้น|หอม|เปียก|ดำ|ขาว|หงอก|ร่วง))"
            if sp in ("M", "F") and re.search(r"ลูก(?![ก-ฮ]|ค้า|น้อง|พี่)|ลูก(?=นะ|สิ|เถอะ|ค่ะ|คะ|ครับ|จ้ะ|จ๊ะ|ไหม|เหรอ|ล่ะ|เลย|รัก|คนนี้|คนดี)", q) and not re.search(r"ลูกค้า|ลูกน้อง|ลูกพี่", q):
                # a parent talking to his/her child says พ่อ / แม่, never ผม / ฉัน
                pw = "พ่อ" if sp == "M" else "แม่"
                q = re.sub(r"(?<!ดิ)ฉัน(?![ทใ])", pw, q)
                q = re.sub(r"(?<![ก-ฮ])ผม" + HAIR, pw, q)
            if sp == "F":   # a woman never says ผม about herself
                if re.search(r"(?<![ก-ฮ])ผม" + HAIR, q):
                    pl = "หนู" if re.search(r"พ่อ|แม่|ป๊า|ม๊า|พี่|ป้า|ลุง|น้า|(?<![ก-ฮ])อา(?![ก-ฮ])", q) else "ชั้น"
                    q = re.sub(r"(?<![ก-ฮ])ผม" + HAIR, pl, q)
            if sp == "M" and rough:   # peak of a sex scene / anger: the men are fully rough
                q = re.sub(r"(?<!ดิ)ฉัน(?![ทใ])", "กู", q).replace("ผม", "กู")
                q = re.sub(r"นะครับ", "นะ", q).replace("ครับ", "")
                if "กู" in q:
                    q = re.sub(r"เธอ|คุณ", "มึง", q)
            if sp == "F":   # women do not say ฉัน: หนู to elders/family, ชั้น otherwise
                if re.search(r"(?<!ดิ)ฉัน(?![ทใ])", q):
                    pl = "หนู" if re.search(r"พ่อ|แม่|ป๊า|ม๊า|พี่|ป้า|ลุง|น้า|(?<![ก-ฮ])อา(?![ก-ฮ])", q) else "ชั้น"
                    q = re.sub(r"(?<!ดิ)ฉัน(?![ทใ])", pl, q)
            if sp == "M" and (spn in rough_men or ("ครับ" not in q and re.search(r"ควย|หี(?!บ)|เย็ด|น้ำเงี่ยน|น้ำควย|แตกใน|แตกใส่", q))) and sexy_part and not elder_listener(q, spn, info) and re.search(r"(?<![ก-ฮ])ผม" + HAIR, q):
                # R55: this man says กู / มึง elsewhere in the same sexual part, so his ผม lines become กู (polite ครับ dropped, เธอ / คุณ -> มึง)
                q = re.sub(r"(?<![ก-ฮ])ผม" + HAIR, "กู", q)
                q = re.sub(r"นะครับ", "นะ", q).replace("ครับ", "")
                q = re.sub(r"เธอ|คุณ", "มึง", q)
            if sp == "M" and re.search(r"มึง|(?<![ก-ฮ])กู(?!เก|รู)", q) and re.search(r"(?<![ก-ฮ])ผม" + HAIR, q):
                # R18 (29 Oct): mixed register inside one quote (ผม...มึง): a man who says มึง is rough, so ผม -> กู and ครับ is dropped
                q = re.sub(r"(?<![ก-ฮ])ผม" + HAIR, "กู", q)
                q = re.sub(r"นะครับ", "นะ", q).replace("ครับ", "")
            if sp == "M":   # R23 (29 Oct): a man never says ชั้น (women's pronoun): ผม, or กู in the rough register
                q = re.sub(r"(?:(?<![ก-ฮ])|(?<=ของ)|(?<=ให้)|(?<=กับ)|(?<=ถึง)|(?<=แก่)|(?<=ควย)|(?<=ดุ้น)|(?<=ปาก)|(?<=ลิ้น)|(?<=น้ำ)|(?<=มือ))ชั้น(?!วาง|ที่|หนึ่ง|สอง|สาม|บน|ล่าง|ใน|เรียน|ปี|ประถม|มัธยม|เชิง|ๆ)", "กู" if re.search(r"มึง|(?<![ก-ฮ])กู(?!เก|รู)", q) else "ผม", q)
            if sp == "M" and rough_scene and "ครับ" in q and not elder_listener(q, spn, info):
                q = re.sub(r"นะครับ", "นะ", q).replace("ครับ", "")
            if sp == "M" and "ครับ" in q and re.search(r"อย่า|ห้าม|ให้หมด|ซะ|สัส|เหี้ย|ชิบหาย|ควย|หี(?![ก-ฮ])|เย็ด|กลืน|ดูด|อ้าปาก|แตก", q):
                # R32 (29 Oct): a man's order / dirty talk in bed is never polite ("อย่าให้เล็ดครับ"): drop ครับ
                q = re.sub(r"นะครับ", "นะ", q).replace("ครับ", "")
            if sp == "M":
                q = re.sub(r"(?<!ดิ)ฉัน(?![ทใ])", "ผม" if "กู" not in q else "กู", q)   # a man says ผม, not ฉัน (กู when rough)
                if "กู" in q:   # rough register: ผม->กู, and he addresses her มึง, not เธอ/คุณ
                    q = q.replace("ผม", "กู")
                    q = re.sub(r"เธอ|คุณ", "มึง", q)
            if sp == "F" and re.search(r"กู(?!เก|รู)|มึง", q):   # a woman never says กู / มึง (user rule 29 Oct): ชั้น (หนู to elders/family) and คุณ
                pl = "หนู" if re.search(r"พ่อ|แม่|ป๊า|ม๊า|พี่|ป้า|ลุง|น้า|(?<![ก-ฮ])อา(?![ก-ฮ])", q) else "ชั้น"
                q = re.sub(r"กู(?!เก|รู)", pl, q)
                q = re.sub(r"มึง", "คุณ", q).replace("หีคุณ", "หีของคุณ").replace("ควยคุณ", "ควยของคุณ")
            if sp == "F" and re.search(r"(ควย|น้ำควย|ดุ้น)(?:ของ)?เธอ", q):   # R25 (29 Oct): she says ควยคุณ to him, never ควยเธอ (seen in R19/R23/R24)
                q = re.sub(r"(ควย|น้ำควย|ดุ้น)(?:ของ)?เธอ", r"\1ของคุณ", q)
            if sp == "F" and "ครับ" in q:
                q = re.sub(r"(นะ|ไหม|มั้ย|หรือ|เหรอ|ล่ะ|หรอ)ครับ", r"\1คะ", q).replace("ครับ", "ค่ะ")
            elif sp == "M" and re.search(r"ค่ะ|คะ", q):
                q = re.sub(r"ค่ะ|คะ", "ครับ", q)
            if sp in ("M", "F") and not re.search(r"กู|มึง|เหี้ย|สัส|ชิบหาย|ควย|หี|เย็ด|อี[ก-ฮ]", q) and elder_listener(q, spn, info):
                q = polite_line(q, sp)   # calm talk to an elder: polite ending
            ln = ln.strip().replace(m.group(0), q, 1)
        lines.append(ln)
    return "\n".join(lines)


def female_rough_issues(text, info):
    """spoken lines whose tagged speaker is a woman but that contain กู / มึง (forbidden for women since 29 Oct)."""
    n = 0
    for ln in text.split("\n"):
        m = QUOTE_RE.match(ln.strip())
        if m and re.search(r"กู(?!เก|รู)|มึง", m.group(0)):
            nm = tag_speaker(ln.strip()[m.end():], info)
            if nm and info[nm]["g"] == "F":
                n += 1
    return n


def particle_issues(text, info):
    n = 0
    for ln in text.split("\n"):
        m = QUOTE_RE.match(ln.strip())
        if m:
            tag = ln.strip()[m.end():]
            nm = tag_speaker(tag, info)
            if nm:
                g = info[nm]["g"]
                if (g == "F" and "ครับ" in m.group(0)) or (g == "M" and re.search(r"ค่ะ|คะ", m.group(0))):
                    n += 1
    return n


SEX_WORDS = ("ควย", "หี", "อม", "เย็ด", "นม", "หัวนม", "น้ำเงี่ยน", "น้ำเชื้อ", "น้ำควย", "น้ำหี", "ครวญคราง", "ครางลั่น", "กระแทก", "เลีย", "ดูด", "ถอดเสื้อ", "เปลือย", "ขย่ม", "สอดใส่")
NOT_CLIMAX_BEFORE = ("ทำ", "กิน", "ตัดต่อ", "อ่าน", "เขียน", "ถ่าย", "แต่งตัว", "อาบน้ำ", "ล้าง", "เก็บ", "จัด", "ส่ง", "งาน", "ประชุม", "ข้าว", "มื้อ", "วิดีโอ", "คอนเทนต์", "เรียน", "สอน", "ซ่อม", "ปรุง", "ทำอาหาร", "ซื้อ", "เตรียม", "เช็ค", "ตรวจ")
NOT_CLIMAX_AFTER = ("สิ้น", "งาน", "กิจ", "เรียบร้อย", "เมื่อ", "ภารกิจ", "ธุระ", "แล้วค่อย", "แล้วเรา", "ไปแล้ว")
CLIMAX_PLACE = ("ใน", "ข้างใน", "ข้างนอก", "ที่หน้า", "บนหน้า", "ลงใน", "ภายใน", "บนหลัง", "บนนม", "ที่นม")


EUPH_FIXED = [("ศิวลึงค์", "ควย"), ("ลึงค์", "ควย"), ("ท่อนเนื้อ", "ควย"), ("แก่นกาย", "ควย"), ("กลีบดอก", "หี"), ("ร่องรัก", "หี"), ("ช่องรัก", "หี")]
EUPH_ACT = ("ดัน", "เสียบ", "สอด", "แทรก", "กระแทก", "เลีย", "อม", "จูบ", "ลูบ", "ไล้", "สัมผัส", "ซุก", "จับ", "กำ", "บีบ", "แตะ", "ถู", "ขย้ำ", "เข้าไปใน", "ฝัง")
EUPH_CTX = ["ของลับ", "จุดซ่อนเร้น", "ส่วนลับ"]


def fix_euphemisms(t, info=None):
    """Replace coy euphemisms for genitals with plain Thai: หี for a woman's, ควย for a man's (decided by the nearest pronoun or name)."""
    for a, b in EUPH_FIXED:
        t = t.replace(a, b)
    fem = ["เธอ"] + [n for n, v in (info or {}).items() if v.get("g") == "F" and n]
    mal = ["เขา"] + [n for n, v in (info or {}).items() if v.get("g") == "M" and n]
    def last_hit(seg, words):
        return max([seg.rfind(w) for w in words] + [-1])
    def first_hit(seg, words):
        xs = [seg.find(w) for w in words if seg.find(w) >= 0]
        return min(xs) if xs else 10 ** 9
    pat = re.compile("|".join(map(re.escape, EUPH_CTX)))
    out, pos = [], 0
    for m in pat.finditer(t):
        out.append(t[pos:m.start()])
        pre, post = t[max(0, m.start() - 90):m.start()], t[m.end():m.end() + 14]
        # possessive right after: "ของลับของเธอ" / "ของลับเธอ"
        pf, pm = first_hit(post, fem), first_hit(post, mal)
        if pf < 6 or pm < 6:
            w = "หี" if pf < pm else "ควย"
        else:
            lf, lm = last_hit(pre, fem), last_hit(pre, mal)
            if lf < 0 and lm < 0:
                pf2, pm2 = first_hit(t[m.end():m.end() + 80], fem), first_hit(t[m.end():m.end() + 80], mal)
                w = "หี" if pf2 < pm2 else "ควย" if pm2 < pf2 else "หี"
            else:
                w = "หี" if lf > lm else "ควย"
                if any(v in pre[-14:] for v in EUPH_ACT):   # "เขาดัน…ของลับ": the thing acted on belongs to the other sex
                    w = "ควย" if w == "หี" else "หี"
        out.append(w)
        pos = m.end()
    out.append(t[pos:])
    return "".join(out)


HUNGRY_SKIP = ("โหย", "กระหาย", "ข้าว", "อาหาร", "น้ำ", "ควย", "หี", "เย็ด", "ปาก", "เนื้อ", "ขนม", "นม", "ซุป", "เหล้า", "กาแฟ")


LUST_CYCLE = ["ความเงี่ยน", "ความอยาก", "ความเงี่ยน", "ความต้องการ"]


def fix_hungry(t, info=None):
    """Sex scenes: a bare 'หิว' said of a person means lust (woman -> หิวควย, man -> หิวหี; for 'คุณ' the sex comes from the speaker tag),
    quote endings are softened (…เลยสินะ), and the narration cliché 'ความหิวกระหาย/หิวโหย' becomes ความเงี่ยน/ความอยาก/ความต้องการ in rotation."""
    fem = ["เธอ"] + [n for n, v in (info or {}).items() if v.get("g") == "F" and n]
    mal = ["เขา"] + [n for n, v in (info or {}).items() if v.get("g") == "M" and n]
    cnt = [0]
    def fix_para(p, ctx):
        if "หิว" not in p or sum(1 for w in SEX_WORDS if w in ctx) < 2:
            return p
        out, pos = [], 0
        for m in re.finditer("หิว", p):
            if m.start() < pos:
                continue
            post = p[m.end():m.end() + 6]
            pre = p[max(0, m.start() - 14):m.start()]
            pre2 = re.sub(r"[\s…]+$", "", pre)
            if post.startswith("กระหาย") or post.startswith("โหย"):
                skip = 6 if post.startswith("กระหาย") else 3
                gaze = any(g in p[max(0, m.start() - 30):m.end() + 14] for g in ("มอง", "จ้อง", "ดวงตา", "สายตา", "แววตา"))
                if not pre2.endswith("ความ") or gaze:
                    continue   # "มองมาอย่างหิวกระหาย" / hungry eyes is natural Thai: leave it
                out.append(p[pos:m.start() - 4] + LUST_CYCLE[cnt[0] % len(LUST_CYCLE)])
                cnt[0] += 1
                pos = m.end() + skip
                continue
            if any(post.startswith(x) for x in HUNGRY_SKIP):
                continue
            add = ""
            if any(pre2.endswith(n) for n in fem):
                add = "ควย"
            elif any(pre2.endswith(n) for n in mal):
                add = "หี"
            elif pre2.endswith("คุณ"):
                tag = re.sub(r"^[^\u201d\"]*[\u201d\"]\s*", "", p[m.end():])[:14]
                if any(tag.startswith(n) for n in mal):
                    add = "ควย"
                elif any(tag.startswith(n) for n in fem):
                    add = "หี"
            if not add:
                continue
            out.append(p[pos:m.end()] + add)
            pos = m.end()
        out.append(p[pos:])
        q = "".join(out)
        q = re.sub(r"(หิว(?:ควย|หี))((?:มาก)?)(?:เลย)?นะ([\u201d\"])", r"\1\2เลยสินะ\3", q)
        q = re.sub(r"(หิว(?:ควย|หี))มาก([\u201d\"])", r"\1มากเลยสินะ\2", q)
        return q
    ps = t.split("\n")
    return "\n".join(fix_para(p, "\n".join(ps[max(0, i - 3):i + 4])) for i, p in enumerate(ps))


def fix_bare_organ(t):
    """In a sexual context a man's quoted praise must name the body part: “มึงแน่น/ฟิต/เปียก/เสียว” -> “หีมึงแน่น…”."""
    ls = t.split("\n")
    for i, ln in enumerate(ls):
        if not QUOTE_RE.match(ln.strip()) or "มึง" not in ln:
            continue
        ctx = "\n".join(ls[max(0, i - 3):i + 4])
        if sum(1 for w in SEX_WORDS if w in ctx) < 2:
            continue
        ls[i] = re.sub(r"(?<!หี)(?<!ควย)(?<!ปาก)(?<!ตูด)มึง(?=แน่น|ฟิต|เปียก|เสียว)", "หีมึง", ln)
    return "\n".join(ls)


def erotic_words(t, keep=(), info=None):
    """Deterministic vocabulary fix for generated prose: clinical terms, stray Latin words and climax 'เสร็จ' -> 'แตก' (only in sexual context)."""
    t = fix_bare_organ(fix_hungry(fix_euphemisms(plain_words(t), info), info))
    t = re.sub(r"\b[A-Za-z]+(?:-[A-Za-z]+)*\b", lambda m: m.group(0) if (len(m.group(0)) < 3 or m.group(0).lower() in [k.lower() for k in keep]) else "", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    out = []
    for para in t.split("\n"):
        sexual = sum(1 for w in SEX_WORDS if w in para) >= 2
        def fix(m):
            pre = para[max(0, m.start() - 8):m.start()]
            post = para[m.end():m.end() + 8]
            if any(post.startswith(a) for a in NOT_CLIMAX_AFTER):
                return m.group(0)
            if any(pre.endswith(b) for b in NOT_CLIMAX_BEFORE):
                return m.group(0)
            if any(post.startswith(p) for p in CLIMAX_PLACE) or sexual:
                return "แตก"
            return m.group(0)
        out.append(re.sub(r"เสร็จ", fix, para))
    return "\n".join(out)


def climax_leftovers(t):
    """Paragraphs with sexual words that still contain 'เสร็จ' (ambiguous, for the quality report)."""
    n = 0
    for para in t.split("\n"):
        if "เสร็จ" in para and sum(1 for w in SEX_WORDS if w in para) >= 2:
            n += para.count("เสร็จ")
    return n


def norm_quotes(t):
    """straight \" pairs on a line become curly quotes (the model drifts to ASCII quotes half way through a chapter)."""
    def fix(line):
        if line.count('"') % 2:
            return line
        k = [0]
        def r(m):
            k[0] += 1
            return "\u201c" if k[0] % 2 else "\u201d"
        return re.sub(r'"', r, line)
    return "\n".join(fix(l) for l in (t or "").split("\n"))


def clean_part(t):
    t = re.sub(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]+", "", t.strip())   # stray Chinese characters the model sometimes emits inside Thai text
    t = re.sub(r"[\u0400-\u052F\u0590-\u06FF\u0900-\u0DFF\u0E80-\u0EFF\u1000-\u109F\uFB1D-\uFDFF\uFE70-\uFEFF\u3040-\u30FF\uAC00-\uD7AF]+", "", t)   # stray Cyrillic / Hebrew / Arabic / Indic / Lao / Burmese / Kana / Hangul
    t = re.sub(LATIN_IN_THAI, "", t)   # stray Latin letters inside Thai words
    m = re.search(r"```(?:draft|text|markdown)?\s*\n([\s\S]*?)(```|$)", t)
    if m and not re.search(r"```edit", t):
        t = m.group(1).strip()
    return norm_quotes(t)


def trim_to_paragraph(t):
    cut = max(t.rfind("\n\n"), t.rfind("\n"))
    if cut > len(t) * 0.5:
        return t[:cut].strip()
    m = list(re.finditer(r"[.!?”\"]\s|ๆ\s|\s", t))
    return t[:m[-1].start()].strip() if m else t


FILLERS = ["เบาๆ", "ราวกับ", "พึมพำ", "เงียบงัน", "ระยิบระยับ", "อย่างช้าๆ", "สั่นเล็กน้อย", "แผ่วเบา"]


def overused(body, names, top=5, fill_min=10):
    """phrases repeated too often in a manuscript: known filler words (>=10) and 9-char Thai n-grams (>=8)."""
    skip = set()
    for nm, th in names:
        skip.update([nm, th])
    out = [(f, body.count(f)) for f in FILLERS if body.count(f) >= fill_min]
    cnt = {}
    for para in re.split(r"\n+", body):
        for run in re.findall(r"[\u0E00-\u0E7F]{9,}", para):
            for k in range(0, len(run) - 8):
                g = run[k:k + 9]
                cnt[g] = cnt.get(g, 0) + 1
    seen6 = set()
    for g, c in sorted(cnt.items(), key=lambda x: -x[1]):
        if c < 8 or len(out) >= top:
            break
        if any(sk and sk in g for sk in skip):
            continue
        grams = {g[i:i + 4] for i in range(0, 6)}
        if grams & seen6:
            continue
        seen6 |= grams
        out.append((g, c))
    return out[:top]


def prefix_runs(text, k=9, minrun=4):
    """number of runs of >= minrun consecutive narration paragraphs that start with the same k characters ("เขาลากควย..." repeated with tiny changes)."""
    ps = [p.strip() for p in (text or "").split("\n") if p.strip() and not p.startswith("#")]
    n = i = 0
    while i < len(ps):
        j = i
        if not QUOTE_RE.match(ps[i]) and k <= len(ps[i]) <= 70:
            while j + 1 < len(ps) and not QUOTE_RE.match(ps[j + 1]) and len(ps[j + 1]) <= 70 and ps[j + 1][:k] == ps[i][:k]:
                j += 1
            if j - i + 1 >= minrun:
                n += 1
        i = j + 1
    return n


def collapse_prefix_runs(text, k=8, minrun=3):
    """keep only the longest paragraph of each prefix run."""
    ps = (text or "").split("\n")
    out, i = [], 0
    nz = [x for x in range(len(ps)) if ps[x].strip()]
    keep = set(nz)
    pos = 0
    while pos < len(nz):
        a = nz[pos]
        t = ps[a].strip()
        run = [a]
        if not QUOTE_RE.match(t) and not t.startswith("#") and len(t) >= k:
            q = pos
            while q + 1 < len(nz) and not QUOTE_RE.match(ps[nz[q + 1]].strip()) and ps[nz[q + 1]].strip()[:k] == t[:k]:
                q += 1
                run.append(nz[q])
            pos = q + 1
        else:
            pos += 1
        if len(run) >= minrun:
            best = max(run, key=lambda x: len(ps[x].strip()))
            for x in run:
                if x != best:
                    keep.discard(x)
    res = []
    for x, line in enumerate(ps):
        if line.strip() and x not in keep:
            continue
        res.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(res))


def strip_self_oral(text):
    """R101 (r102-dp: “ปกรณ์ก้มหน้าลงมาก้มลงอมหัวควยของตัวเองอย่างตั้งใจ”): a man cannot suck / lick his own cock; the clause (between two spaces) is cut from narration,
    a line that was only that clause goes. Pulling out / stroking his own cock (ดึง / ชัก... ของตัวเอง) is fine and stays."""
    rx = re.compile(r"(?:อม|เลีย|ดูด)[\u0E01-\u0E4E]{0,4}(?:ควย|หัวควย|ดุ้น)ของตัวเอง")
    out = []
    for ln in (text or "").split("\n"):
        st = ln.strip()
        if st and not QUOTE_RE.match(st):
            for _ in range(4):
                mm = rx.search(ln)
                if not mm:
                    break
                left = ln.rfind(" ", 0, mm.start()) + 1
                right = ln.find(" ", mm.end())
                right = len(ln) if right < 0 else right
                ln = (ln[:left].rstrip() + " " + ln[right:].lstrip()).strip()
            if not ln.strip():
                continue
        out.append(ln)
    return "\n".join(out)


def strip_man_on_man(text, info):
    """R89 (r88-dp / r88b-dp: "ปกรณ์ใช้มือลูบไล้ควยของก้อง", "ปกรณ์ยังคงอมควยของก้อง"): the retries keep failing this rule, so it is enforced in code: in narration, the clause (the stretch
    between two spaces) in which one man touches / licks / sucks another man's cock is cut, together with the doer's own name when it stands just before it."""
    M = [n for n, v in (info or {}).items() if n and v.get("g") == "M"]
    if len(M) < 2:
        return text
    mr = "|".join(re.escape(x) for x in sorted(M, key=len, reverse=True))
    allr = "|".join(re.escape(x) for x in sorted([n for n in info if n and len(n) >= 2], key=len, reverse=True))
    obj_mark = r"(?:หี|นม|ปาก|ก้น|หน้าอก|ต้นขา|สะโพก|ขา|แก้ม|ไหล่|หลัง|ท้อง|เอว|คอ|ใบหน้า|ผม|ให้|กับ|ที่|จาก|ของ|ถึง|ข้าง|ใกล้|หา|มอง|ตา|เห็น|เรียก|ดึง|ดัน|จูบ|กอด)$"
    rx = re.compile(r"(?:อม|เลีย|ดูด|ชัก|จับ|ลูบ|ไล้|กำ)[\u0E01-\u0E4E]{0,4}(?:ควย|หัวควย|ดุ้น)(?:ของ)?(%s)" % mr)
    rx_pron = re.compile(r"(?:อม|เลีย|ดูด|ชัก|จับ|ลูบ|ไล้|กำ)[\u0E01-\u0E4E]{0,4}(?:ควย|หัวควย|ดุ้น)เขา(?![\u0E01-\u0E2E])")

    def cut_pron(s_):
        # R94 (r93-dp: “…” ลลิตาอ้าปากกว้างให้ธนาอมควยเขา): a man sucking / touching "เขา"'s cock is another man's; works on narration after a leading quote too
        for _ in range(4):
            mm = rx_pron.search(s_)
            if not mm:
                break
            prev = [x for x in re.finditer(allr, s_[:mm.start()])] if allr else []
            if not prev or prev[-1].group(0) not in M:
                break
            ac = prev[-1]
            left = s_.rfind(" ", 0, mm.start()) + 1
            if 0 <= mm.start() - ac.start() <= 25:
                left = min(left, s_.rfind(" ", 0, ac.start()) + 1)
            right = s_.find(" ", mm.end())
            right = len(s_) if right < 0 else right
            s_ = (s_[:left].rstrip() + " " + s_[right:].lstrip()).strip()
        return s_

    out = []
    for ln in (text or "").split("\n"):
        st = ln.strip()
        if st and "เขา" in st:
            qm_ = QUOTE_RE.match(st)
            if qm_:
                rest_ = st[qm_.end():]
                new_ = cut_pron(rest_)
                if new_ != rest_.strip():
                    ln = (qm_.group(0) + " " + new_).rstrip()
            else:
                new_ = cut_pron(ln)
                if new_ != ln.strip():
                    ln = new_
            st = ln.strip()
        if not st or QUOTE_RE.match(st):
            if ln.strip():
                out.append(ln)
            continue
        for _ in range(6):
            hit = None
            for mm in rx.finditer(ln):
                prev = [x for x in re.finditer(allr, ln[:mm.start()]) if not re.search(obj_mark, ln[max(0, x.start() - 5):x.start()])] if allr else []
                if prev and prev[-1].group(0) in M and prev[-1].group(0) != mm.group(1):
                    hit = (mm, prev[-1])
                    break
            if not hit:
                break
            mm, ac = hit
            left = ln.rfind(" ", 0, mm.start()) + 1
            if 0 <= mm.start() - ac.start() <= 25:
                left = min(left, ln.rfind(" ", 0, ac.start()) + 1)
            right = ln.find(" ", mm.end())
            right = len(ln) if right < 0 else right
            ln = (ln[:left].rstrip() + " " + ln[right:].lstrip()).strip()
        if ln.strip():
            out.append(ln)
    return "\n".join(out)


def family_issues(text, info):
    """hard story-rule violations the model keeps making: (a) man-on-man sex acts, (b) [removed 29 Oct: spouses may call each other พ่อ/แม่/ป๊า/ม๊า],
    (c) a real daughter/son called บุญธรรม (adopted). Returns a list of short Thai descriptions (empty = clean)."""
    out = []
    info = info or {}
    role = lambda n: (info.get(n) or {}).get("role", "")
    M = [n for n, v in info.items() if n and v.get("g") == "M"]
    fathers = [n for n in info if re.search(r"father", role(n), re.I) and not re.search(r"step", role(n), re.I)]
    wives = [n for n in info if re.search(r"stepmother", role(n), re.I)]
    kids = [n for n in info if re.match(r"\s*(daughter|son)\b", role(n), re.I)]
    paras = [p for p in (text or "").split("\n") if p.strip()]
    if len(M) >= 2:
        mr = "|".join(re.escape(x) for x in sorted(M, key=len, reverse=True))
        allr = "|".join(re.escape(x) for x in sorted([n for n in info if n and len(n) >= 2], key=len, reverse=True))
        for p in paras:
            for mm in re.finditer(r"(?:อม|เลีย|ดูด|ชัก|จับ|ลูบ)[\u0E01-\u0E4E]{0,4}(?:ควย|หัวควย)(?:ของ)?(%s)" % mr, p):
                # actor = the nearest character name BEFORE the act verb (29 Oct: a man named earlier in the paragraph made "ลลิตา...อมควยของธนา" a false 'man on man')
                obj_mark = r"(?:หี|นม|ปาก|ก้น|หน้าอก|ต้นขา|สะโพก|ขา|แก้ม|ไหล่|หลัง|ท้อง|เอว|คอ|ใบหน้า|ผม|ให้|กับ|ที่|จาก|ของ|ถึง|ข้าง|ใกล้|หา|มอง|ตา|เห็น|เรียก|ดึง|ดัน|จูบ|กอด)$"   # a name right after these is the object, not the doer
                prev = [x.group(0) for x in re.finditer(allr, p[:mm.start()]) if not re.search(obj_mark, p[max(0, x.start() - 5):x.start()])] if allr else []
                actor = prev[-1] if prev else None
                if actor and actor in M and actor != mm.group(1):
                    out.append("ชายกับชาย: " + p[max(0, mm.start() - 20):mm.end() + 5])
                    break
        for p in paras:
            m = re.search(r"ให้(?:%s)(?:อม|เลีย|ดูด|ชัก|จับ)[\u0E01-\u0E4E]{0,3}(?:ควย|ดุ้น|หัวควย)" % mr, p)   # R58: "ให้ก้องอมควยชั้น" - a man ordering a man to suck
            if m:
                out.append("ชายกับชาย (บทพูด): " + p[:50])
        for p in paras:
            m = re.search(r"\u201c(?:พ่อ|พี่)[^\u201d]{0,12}ควย(?:ของ)?(?:พ่อ|พี่)", p)
            if m:
                out.append("ชายกับชาย (บทพูด): " + p[:50])
    if re.search(r"(?:เลีย|อม|ดูด)[\u0E01-\u0E4E]{0,3}(?:หี|ควย|นม|หัวนม|เม็ดแตด)(?:ของ)?(?:ตัวเอง|เธอเอง|เขาเอง)|(?:เลีย|อม|ดูด|กิน|จูบ)[^\n\u201c\u201d]{0,6}(?:หี|นม|เม็ดแตด|ควย)ของ(?:ตัวเอง|เธอเอง|เขาเอง)|หว่างขาของเธอ?เอง|หว่างขาของเธองเอง", text or ""):
        out.append("ทำกับร่างกายตัวเองไม่ได้ (เลีย/อมของตัวเอง)")
    F2 = [n for n, v in info.items() if n and v.get("g") == "F"]
    fr2 = "|".join(re.escape(x) for x in sorted(F2, key=len, reverse=True))
    if re.search(r"น้ำเชื้อของเธอ" + (r"|น้ำเชื้อของ(?:%s)|(?:%s)(?:ปล่อย|พุ่ง|หลั่ง|แตก)[^\n\u201c\u201d]{0,6}น้ำเชื้อ" % (fr2, fr2) if fr2 else ""), text or ""):
        out.append("ผู้หญิงมีน้ำเชื้อ (ผู้หญิงมีแต่ น้ำหี/น้ำเงี่ยน)")
    if prefix_runs(text):
        out.append("ย่อหน้าบรรยายขึ้นต้นเหมือนกันติดกัน ≥4 ย่อหน้า (ซ้ำ/วน)")
    if kids and re.search(r"บุญธรรม", text or "") and not any(re.search(r"adopt", role(n), re.I) for n in info):
        out.append("เรียกลูกแท้ว่าบุญธรรม")
    return out


FORMULA_RE = re.compile(r"^[\u201c\"][^\u201c\u201d\"\n]{1,30}(?:\.\.\.|\u2026)[^\u201c\u201d\"\n]{1,30}[\u201d\"]\s*$")


def formula_lines(text):
    """(formula quoted lines, all quoted lines): short two-beat lines 'X... Y' that make the dialogue read like a loop."""
    q = [ln.strip() for ln in (text or "").split("\n") if QUOTE_RE.match(ln.strip())]
    return sum(1 for ln in q if FORMULA_RE.match(ln)), len(q)


def formula_heavy(text):
    f, n = formula_lines(text)
    return n >= 8 and f >= 0.7 * n


def loop_tail(text):
    """True if the last 8 non-empty lines are all very short (<=22 chars): a looping/fading ending."""
    ls = [ln.strip() for ln in (text or "").split("\n") if ln.strip() and not ln.startswith("#")]
    return len(ls) >= 10 and all(len(x) <= 22 for x in ls[-8:])


def check_text(b, text, target=0):
    """quality report for a chapter text (Thai messages)."""
    issues = []
    body = re.sub(r"^\s*#\s+.*\n?", "", text, count=1)
    w = body_words(text)
    if target and w < target * 0.85:
        issues.append("ความยาว %d คำ สั้นกว่าเป้า %d คำ (%d%%)" % (w, target, w * 100 // target))
    names = name_table(b)
    for nm, th in names:
        if re.search(r"[A-Za-z]", nm):
            c = len(re.findall(re.escape(nm), body))
            if c:
                issues.append("พบชื่อ %s เป็นตัวอักษรอังกฤษ %d ครั้ง%s" % (nm, c, " (ควรเป็น " + th + ")" if th else " (ตั้งค่า “ชื่อไทย” ของตัวละครนี้)"))
    med = [(t, body.count(t)) for t in MEDICAL_TERMS if body.count(t) >= 3]
    if med:
        issues.append("ศัพท์แบบแพทย์ซ้ำ: " + ", ".join("%s ×%d" % x for x in med))
    pi = particle_issues(body, char_info(b))
    if pi:
        issues.append("คำลงท้าย ครับ/ค่ะ ไม่ตรงเพศผู้พูด %d จุด" % pi)
    ty = TYPO_RE.findall(body)
    if ty:
        issues.append("พบคำเพี้ยน/พิมพ์ผิด %d จุด เช่น %s" % (len(ty), " / ".join(dict.fromkeys(ty[:4]))))
    ud = {}
    for g in ("ชุดชั้นใน", "กางเกงใน", "บรา", "ถุงน่อง", "เสื้อเชิ้ต", "เสื้อสูท", "ชุดนอน", "ผ้าคลุม"):
        c = len(re.findall(r"(?:ถอด|ปลด|แกะ)[^\n\u201c\u201d]{0,12}" + g, body))
        if c >= 4:
            ud[g] = c
    if ud:
        issues.append("ถอดเสื้อผ้าชิ้นเดียวกันซ้ำหลายรอบ (ตรวจเองว่าซ้ำคนเดิมหรือไม่): " + ", ".join("%s ×%d" % x for x in ud.items()))
    fr_n = female_rough_issues(body, char_info(b))
    if fr_n:
        issues.append("ผู้หญิงพูด กู/มึง %d บรรทัด (ห้ามตามกฎใหม่)" % fr_n)
    left = climax_leftovers(body)
    if left:
        issues.append("พบคำว่า “เสร็จ” ในย่อหน้าฉากเซ็กซ์ %d จุดที่ระบบไม่ได้แทน (ตรวจเองว่าควรเป็น “แตก” หรือไม่)" % left)
    qchars = sum(len(m.group(0)) for m in QUOTE_RE.finditer(body))
    pct = 100 * qchars // max(1, len(body))
    if len(body) > 3000 and pct < 15:
        issues.append("บทพูดมีสัดส่วนแค่ %d%% ของเนื้อหา (เป้า ≥15%%, ช่วงพีค 50–60%%)" % pct)
    longn = sum(1 for ln in body.split("\n") if ln.strip() and not QUOTE_RE.match(ln.strip()) and len(ln.strip()) > 400)
    if longn >= 5:
        issues.append("ย่อหน้าบรรยายยาวเกิน 400 ตัวอักษร %d ย่อหน้า" % longn)
    dp = dup_paragraphs(body)
    if dp >= 3:
        issues.append("ย่อหน้าซ้ำคำต่อคำ %d ย่อหน้า (%d%% ของย่อหน้ายาว)" % (dp, 100 * dp // _para_total(body)))
    nt = fix_name_typos(body, names, count_only=True)
    if nt:
        issues.append("ชื่อตัวละครสะกดผิดจากตาราง %d จุด (เช่น กฤษฎ์ แทน กฤษณ์)" % nt)
    an = anatomy_issues(body, char_info(b))
    if an:
        issues.append("กายวิภาคผิดเพศ %d จุด (ผู้หญิงมี ควย / ผู้ชายมี หี)" % an)
    aq = body.count('"')
    if aq >= 4 and body.count("\u201c") >= 4:
        issues.append("เครื่องหมายคำพูดปนกัน: ตรง \" %d ตัว และโค้ง “ ” %d ตัว (ควรเป็นแบบเดียวกัน)" % (aq, body.count("\u201c")))
    fi = family_issues(body, char_info(b))
    if fi:
        issues.append("ขัดกฎความสัมพันธ์/เพศ %d จุด เช่น %s" % (len(fi), " | ".join(fi[:2])))
    ff, fn = formula_lines(body)
    if fn >= 8 and ff >= 0.5 * fn:
        issues.append("บทพูดสูตรเดียวกัน “X... Y” %d จาก %d บรรทัด (อ่านเหมือนวน ควรหลากหลายรูปประโยค)" % (ff, fn))
    if loop_tail(body):
        issues.append("ตอนจบเป็นบรรทัดสั้นๆ ต่อกัน 8 บรรทัดท้าย (เสี่ยงวน/จบไม่สมบูรณ์)")
    ut = untagged_dialogue(body)
    if ut:
        issues.append("บทพูดล้วนไม่มีผู้พูด/บรรยายคั่น %d บรรทัด (ติดกัน ≥%d บรรทัด) อ่านไม่รู้ว่าใครพูด" % (ut, UNTAG_RUN))
    if re.search(r"พ่อเลี้ยง", body) and not re.search(r"stepfather", " ".join(v.get("role", "") for v in char_info(b).values()), re.I):
        issues.append("พบคำว่า “พ่อเลี้ยง” %d ครั้ง ทั้งที่ตัวละครพ่อเป็นพ่อแท้ตามตารางตัวละคร" % body.count("พ่อเลี้ยง"))
    if target and w > target * 1.2:
        issues.append("ความยาว %d คำ เกินเป้า %d คำ (%d%%)" % (w, target, w * 100 // target))
    rep = overused(body, names)
    if rep:
        issues.append("วลีซ้ำบ่อย: " + ", ".join("“%s” ×%d" % x for x in rep))
    return {"words": w, "issues": issues}


REPEAT_LIMITS = [("เบาๆ", 4, [""]), ("เบา ๆ", 4, [""]), ("ปากของเธอ", 5, ["ปากเธอ", "ริมฝีปากนั้น", "ปาก"]), ("ริมฝีปากของเธอ", 4, ["ริมฝีปากเธอ", "ปาก"]),
    ("ทั้งสองข้าง", 4, ["สองข้าง"]), ("เงยหน้าขึ้น", 4, ["เงยหน้า"]), ("ปลายลิ้นของเธอ", 4, ["ลิ้นของเธอ", "ลิ้นเธอ"]),
    ("แล้วค่อยๆ", 4, ["ค่อยๆ"]), ("ราวกับ", 6, ["เหมือน"]), ("อีกครั้ง", 5, [""])]


def thin_repeats(part, existing):
    """Keep stock phrases under a per-chapter limit by dropping or varying later occurrences."""
    for phrase, limit, alts in REPEAT_LIMITS:
        seen = existing.count(phrase)
        out, pos, k = [], 0, 0
        for m in re.finditer(re.escape(phrase), part):
            out.append(part[pos:m.start()])
            if seen >= limit:
                out.append(alts[k % len(alts)])
                k += 1
            else:
                out.append(phrase)
            seen += 1
            pos = m.end()
        out.append(part[pos:])
        part = "".join(out)
    return part


def _para_key(p):
    return re.sub(r"[\s\.\!\?\u2026,\u201c\u201d\"\u0e46]+", "", p)


DUP_MIN_LEN = 20   # paragraphs shorter than this (moans, one-word lines) are left to dedupe_quotes


def _shingles(k, n=8):
    return {k[i:i + n] for i in range(0, max(1, len(k) - n + 1))}


def _is_dup(k, keys, shs, near=0.7):
    """True when the paragraph key repeats an earlier one exactly or shares at least `near` of its 8-character shingles with one (a copy with a few words changed)."""
    if k in keys:
        return True
    sh = _shingles(k)
    for o in shs:
        inter = len(sh & o)
        if inter and inter / float(min(len(sh), len(o))) >= near:
            return True
    return False


def _scan_dups(part, existing=""):
    """yield (paragraph, is_duplicate) for the paragraphs of `part`, comparing with `existing` and earlier paragraphs of `part`."""
    keys, shs = set(), []
    for p in re.split(r"\n+", existing or ""):
        k = _para_key(p)
        if len(k) >= DUP_MIN_LEN:
            keys.add(k)
            shs.append(_shingles(k))
    res = []
    for p in part.split("\n"):
        k = _para_key(p)
        if len(k) >= DUP_MIN_LEN and not p.lstrip().startswith("#"):
            d = _is_dup(k, keys, shs)
            keys.add(k)
            shs.append(_shingles(k))
            res.append((p, d))
        else:
            res.append((p, False))
    return res


def dup_paragraphs(text):
    """number of paragraphs that repeat an earlier paragraph of the same text (word for word, or with only a few words changed)."""
    return sum(1 for _, d in _scan_dups(text) if d)


def drop_dup_paragraphs(part, existing=""):
    """remove paragraphs of `part` that already appear (or nearly) in `existing` or earlier in `part`."""
    out = [p for p, d in _scan_dups(part, existing) if not d]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _para_total(t):
    return max(1, sum(1 for p in re.split(r"\n+", t or "") if len(_para_key(p)) >= DUP_MIN_LEN))


NAME_CTX = r"()"   # a Thai name sits right after a Thai verb (เธอมองกฤษฎ์) so the typo may follow any character; false hits are blocked by the known-names guard in fix_name_typos


def _name_variant_res(names):
    """for every Thai name of 5+ characters: a regex matching the name with exactly one consonant replaced (a typo such as กฤษฎ์ for กฤษณ์)."""
    known = {th for _, th in names if th} | {nm for nm, _ in names if nm}
    res = []
    for nm, th in names:
        if not th or len(th) < 5 or re.search(r"[A-Za-z\s]", th):
            continue
        alts = []
        for i, ch in enumerate(th):
            if re.match(r"[\u0E01-\u0E2E]", ch):
                alts.append(re.escape(th[:i]) + "[\u0E01-\u0E2E]" + re.escape(th[i + 1:]))
        if alts:
            res.append((th, re.compile(NAME_CTX + "(" + "|".join(alts) + ")"), known))
    return res


def fix_name_typos(text, names, count_only=False):
    n = 0
    # R24 (29 Oct): a name with ONE extra repeated consonant (ลลลิตา for ลลิตา, seen ×12 in R20/R23) is repaired first
    known_all = {th for _, th in names if th} | {nm for nm, _ in names if nm}
    for nm, th in names:
        if not th or len(th) < 4 or re.search(r"[A-Za-z\s]", th):
            continue
        for i, ch in enumerate(th):
            if re.match(r"[\u0E01-\u0E2E]", ch):
                var = th[:i] + ch + th[i:]
                if var in known_all or var == th:
                    continue
                c = text.count(var)
                if c:
                    n += c
                    text = text.replace(var, th)
    for th, rx, known in _name_variant_res(names):
        def sub(m, th=th, known=known):
            nonlocal n
            if m.group(2) == th or m.group(2) in known:
                return m.group(0)
            rest = m.string[m.start(2):]
            if any(k and len(k) >= 3 and rest.startswith(k) for k in known):
                return m.group(0)   # the text continues as another character's name (คุณอนันต์ must not become คุณนนันต์)
            n += 1
            return m.group(1) + th
        text = rx.sub(sub, text)
    return n if count_only else text




DLG_LOW = os.environ.get("NOVEL_DLG_LOW", "") == "1"   # experiment round 9 (user-approved 29 Oct, this trial only): peak parts 15-20% dialogue + feeling narration
DLG_MIN = (0.12 if DLG_LOW else 0.35) if SPEECH_LITE else 0.50   # a PEAK part with less spoken text than this is written again (at most twice), the version with the most dialogue is kept


def quote_share(t):
    t = t or ""
    if not t.strip():
        return 0.0
    return sum(len(x) for x in re.findall(r"[\u201c\"][^\u201d\"]*[\u201d\"]", t)) / float(len(t))


SEX_DENS_MIN = 5.0   # explicit sex words per 1000 characters a part of a sex scene must reach
_SEX_DENS_RE = re.compile(r"ควย|หี|เย็ด|แตด|เงี่ยน|กระแทก|สอดใส่|ซอย|เลีย|ดูด|หัวนม|น้ำแตก|เปลือย|ขย่ม|นม")


def sex_density(t):
    t = t or ""
    if not t.strip():
        return 0.0
    return len(_SEX_DENS_RE.findall(t)) * 1000.0 / len(t)


CALM_DENS_MAX = 3.5   # an ordinary (non-sexual) scene may not exceed this many explicit sex words per 1000 characters
DUP_PART_MAX = 0.05   # at most this share of a part's long paragraphs may repeat word for word


def part_score(t, need_dlg, need_sex, need_calm=False, existing="", anat=False, untag=False):
    """1.0 each for a satisfied check, proportional when short; the sum is used to pick the better of two versions."""
    a = min(quote_share(t) / DLG_MIN, 1.0) if need_dlg else 1.0
    b = min(sex_density(t) / SEX_DENS_MIN, 1.0) if need_sex else 1.0
    c = min(CALM_DENS_MAX / max(sex_density(t), 0.01), 1.0) if need_calm else 1.0
    d = 1.0 - min(1.0, dup_ratio(t, existing) / 0.5)
    return a + b + c + d - (0.5 if anat else 0.0) - (0.5 if untag else 0.0)


def dup_ratio(t, existing=""):
    sc = [d for p, d in _scan_dups(t, existing) if len(_para_key(p)) >= DUP_MIN_LEN]
    return sum(sc) / float(len(sc)) if sc else 0.0


LONG_FACTOR = 1.2   # R68: a part longer than this x its planned words is rewritten (R66: 3,779 words for a 3,000-word chapter). Measured against the plan, not the prompt's scaled ask: the pod asks for 0.6x on purpose because the model overshoots


def part_too_long(t, p):
    return body_words("# x\n" + (t or "")) > max(1, int(p["words"])) * LONG_FACTOR


def aw_run(b, cid, key):
    job = AW_JOBS[key]
    try:
        fp = os.path.join(bdir(b), "chapters", cid + ".md")
        o = outline(b).get(cid, {})
        text0 = read(fp)
        first_empty = body_words(text0) < 60
        parts = attach_pose_plans(b, plan_scenes(o.get("summary", ""), o.get("target", 0)), cid, pose_run_salt(b, cid) if POSES_ON else 0)
        names = name_table(b)
        tpw = 1.3   # tokens per Thai "word" (body_words unit); recalibrated from the model's own usage numbers after each part
        job.update({"n": len(parts), "i": 0, "state": "running"})
        snapshot_chapter(b, cid)
        scene_pos = {}
        for i in range(len(parts)):
            if job.get("cancel"):
                job["state"] = "cancelled"
                break
            job["i"] = i + 1
            avoid = [g for g, _ in overused(re.sub(r"^\s*#\s+.*\n?", "", read(fp), count=1), names, 6, 5)] if i else []
            msg = part_message(b, parts, i, first_empty, names, avoid)
            part = ""
            cand = None
            need_dlg = heat_level(parts[i]) == "peak" and is_sexual_text(parts[i].get("text"))
            need_sex = is_sexual_text(parts[i].get("text"))
            need_calm = not need_sex
            cur0 = read(fp)
            info0 = char_info(b)
            maxa = 4 if (parts[i].get("pose") or {}).get("g") else 3   # R78: parts of 2/3-men scenes get one more rewrite (r76-dp: the double and the finish places were still wrong after 3)
            for attempt in range(maxa):
                if attempt >= 2 and cand is None:
                    break   # the later calls exist only for rewrites
                if cand is not None:
                    why = []
                    if need_dlg and quote_share(cand[1]) < DLG_MIN:
                        why.append("it had only %d%% spoken lines but this PEAK part needs at least %d%%: write at least %d quoted lines (moans, dirty talk, orders, answers) and keep every narration paragraph to ONE short sentence" % (int(quote_share(cand[1]) * 100), int(DLG_MIN * 100), max(12, int(parts[i]["words"] * 45 / 1000))))
                    if need_sex and sex_density(cand[1]) < SEX_DENS_MIN:
                        why.append("it did not stay on the sex: this part must be the sexual act itself from the first sentence to the last, with the body parts named (ควย, หี, นม, หัวนม, น้ำเงี่ยน) in nearly every paragraph and no drifting into work, family, memories or chat")
                    if need_calm and sex_density(cand[1]) > CALM_DENS_MAX:
                        why.append("this is an ORDINARY scene (no sex yet): remove the explicit sex words and acts, keep it to talk, atmosphere, glances and light touches")
                    if anatomy_issues(cand[1], info0):
                        why.append("it gave a woman a ควย or a man a หี: women have only หี, เม็ดแตด and นม; men have ควย; no strap-ons; rewrite those acts correctly")
                    if dup_ratio(cand[1], cur0) > DUP_PART_MAX:
                        why.append("it repeated whole paragraphs and lines that were already written: every paragraph must be new, with new body movements, new words and new events")
                    if family_issues(cand[1], info0):
                        why.append("it broke a story rule: " + " | ".join(family_issues(cand[1], info0)[:3]) + ". Rules: nobody can lick, suck or touch their own body parts; a woman has no น้ำเชื้อ (only น้ำหี / น้ำเงี่ยน); never write several paragraphs that start with the same words (each paragraph must move the scene forward); the men NEVER touch, suck or lick each other (every sexual act is man-with-woman; two men may be with the same woman at the same time, but their bodies never touch each other); the daughter is the husband's real daughter, never บุญธรรม")
                    if posture_issues(cand[1], _is_group(parts[i])) or plan_issues(cand[1], parts[i]):
                        why.append("its body logic was impossible or it ignored the POSITION PLAN: " + " | ".join((plan_issues(cand[1], parts[i]) + posture_issues(cand[1], _is_group(parts[i])))[:4]) + ". Rules: one cock is in ONE place at a time (mouth OR pussy OR hand); to switch, first write the man pulling out or the couple changing position (e.g. ดึงควยออก, พลิกตัว, เปลี่ยนท่า) and the new position; nobody has two things in the mouth; the man comes only at a HIS CLIMAX step of the plan")
                    if formula_heavy(cand[1]):
                        why.append("most quoted lines had the same shape 'short phrase... short phrase': vary the dialogue: full sentences, questions, commands, names, one-word cries, interrupted lines, and mix action beats between the lines")
                    if part_too_long(cand[1], parts[i]):
                        why.append("it was far too long: write about %d Thai words and never more than %d: fewer repeated body-part descriptions and fewer fillers, keep the plan steps and the ending" % (int(parts[i]["words"] * 0.85), int(parts[i]["words"])))
                    if echo_n(cand[1]):
                        why.append("it had a long run of one-word lines echoed back and forth (like “อม” / “ดูด” / “แรง” / “หมด”): never answer a line by repeating its word; write real sentences, give each line new content, and put an action beat of the body between every two or three lines")
                    if untag_n(cand[1]):
                        why.append("it had a long run of bare quoted lines with no speaker or action between them: after every 2-3 quoted lines add a short action beat or tag that names who is speaking and what the bodies are doing, so the reader always knows who talks")
                    m = msg + "\nREWRITE: your previous version was not acceptable: " + "; and ".join(why or ["write it more vividly"]) + ". Write the part again, keeping the same events."
                elif attempt == 0:
                    m = msg
                else:
                    m = msg + "\nREMINDER: reply with the story prose ONLY. No code fences, no JSON, no edits."
                body = {"book": b, "scope": cid, "mode": "scene", "messages": [{"role": "user", "content": m}]}
                msgs = build_messages(body)
                st = settings()
                payload = {"messages": msgs, "stream": False, "max_tokens": int(parts[i]["words"] * tpw * TOK_MULT) + 100,
                           "temperature": st["temperature"], "chat_template_kwargs": {"enable_thinking": False}}
                payload.update(ANTI_REPEAT)
                req = urllib.request.Request(LLM + "/v1/chat/completions", json.dumps(payload).encode(), {"Content-Type": "application/json"})
                r = json.loads(urllib.request.urlopen(req, timeout=900).read().decode("utf-8"))
                ch0 = r["choices"][0]
                job.setdefault("finish", []).append("%s/%s" % (ch0.get("finish_reason"), (r.get("usage") or {}).get("completion_tokens")))   # instrumentation: finish reason + tokens per attempt
                try:
                    ct = int(r["usage"]["completion_tokens"])
                    wd = max(1, body_words("# x\n" + (ch0["message"].get("content") or "")))
                    if ct > 200 and wd > 100:
                        tpw = max(0.6, min(3.5, ct / float(wd)))
                except Exception:
                    pass
                t = erotic_words(clean_part(ch0["message"].get("content") or ""), keep=[x for nm, th in name_table(b) for x in (nm, th) if x and re.search(r"[A-Za-z]", x)], info=char_info(b))
                if ch0.get("finish_reason") == "length":
                    t = trim_to_paragraph(t)
                if t and not t.lstrip().startswith("{") and "```" not in t and len(t) > 200:
                    untag_t = untag_n(t)
                    fh_t = formula_heavy(t) or bool(echo_n(t))   # R54: an echo loop counts like a formula-heavy part
                    fam_t = family_issues(t, info0)
                    pos_t = posture_issues(t, _is_group(parts[i])) + plan_issues(t, parts[i])
                    if attempt == 0:
                        job.setdefault("raw_pos", []).append(len(pos_t))   # experiment instrumentation: how much of the model's FIRST answer repeats what is already in the chapter
                        job.setdefault("raw_dup", []).append(round(dup_ratio(t, cur0), 3))
                        job.setdefault("raw_anat", []).append(anatomy_issues(t, info0))
                        job.setdefault("raw_untag", []).append(untag_t)
                    sc_t = part_score(t, need_dlg, need_sex, need_calm, cur0, bool(anatomy_issues(t, info0)), bool(untag_t) or fh_t or bool(fam_t) or bool(pos_t) or part_too_long(t, parts[i])) - min(1.2, 0.3 * len(pos_t))   # R78: the version with fewer plan problems wins when none is clean
                    ok_t = ((not need_dlg or quote_share(t) >= DLG_MIN) and (not need_sex or sex_density(t) >= SEX_DENS_MIN)
                            and (not need_calm or sex_density(t) <= CALM_DENS_MAX) and dup_ratio(t, cur0) <= DUP_PART_MAX and not anatomy_issues(t, info0) and not untag_t and not fh_t and not fam_t and not pos_t and not part_too_long(t, parts[i]))
                    if cand is not None:
                        job["retries"] = job.get("retries", 0) + 1
                        if sc_t >= cand[0]:
                            cand = (sc_t, t)
                        if ok_t or attempt >= maxa - 1:
                            part = cand[1]
                            break
                        continue
                    if not ok_t:
                        cand = (sc_t, t)
                        continue
                    part = t
                    break
            if not part and cand is not None:
                part = cand[1]
            if not part:
                job.update({"state": "error", "error": "ส่วนที่ %d โมเดลไม่ตอบเป็นเนื้อเรื่อง" % (i + 1)})
                return
            try:
                _lg = []
                part, _dpi = dp_patch(part, parts[i].get("pose"), _lg)
                job.setdefault("dpinfo", []).append([i + 1] + _lg)   # instrumentation: why the double passage was or was not inserted
                if _dpi:
                    job.setdefault("dppatch", []).append(i + 1)   # instrumentation: which parts got the separate double passage
            except Exception:
                pass
            cur = read(fp)
            part = drop_dup_paragraphs(fix_name_typos(part, names), cur)
            part = strip_early_cum(thin_manner(thin_fluid(thin_tagverb(_thin_owner_n(b, fix_swallow(fix_particles(dedupe_quotes(split_dialogue(close_open_quotes(thin_repeats(part, cur), char_info(b))), cur), char_info(b), rough=(heat_level(parts[i]) == "peak"))))))), parts[i].get("pose"))
            try:
                _scn = parts[i].get("scene")
                _spos = scene_pos.setdefault(_scn, len(cur))
                _before = len(part)
                _p0 = part
                part = strip_repeat_cum(part, cur[_spos:], parts[i].get("pose"))
                _p1 = part
                part = fix_finish_place(part, parts[i].get("pose"))
                _p2 = part
                try:   # R93 instrumentation: [part, planned finish places in this part, whether fix_finish_place changed the text]
                    _pp_ = parts[i].get("pose") or {}
                    _w_ = {nm: pl for st_ in _pp_.get("steps", []) for nm, pl in (st_.get("place") or {}).items()}
                    job.setdefault("fixplace", []).append([i + 1, bool(_pp_.get("g")), _w_, _p1 != _p2])
                except Exception:
                    pass
                part = strip_man_on_man(part, char_info(b))
                part = strip_self_oral(part)
                part = fix_name_krub(part, char_info(b))
                try:   # R97 instrumentation: the plan of this part [id, who, fin, rd, place]
                    job.setdefault("plan", []).append([i + 1, [[st_.get("id"), st_.get("who"), st_.get("fin"), st_.get("rd"), st_.get("place")] for st_ in (parts[i].get("pose") or {}).get("steps", [])], (parts[i].get("pose") or {}).get("tv")])
                except Exception:
                    pass
                _pe = part
                part = ensure_finish_lines(part, cur[_spos:], parts[i].get("pose"))
                if part != _pe:
                    job.setdefault("addfinish", []).append(i + 1)   # R95 instrumentation: parts where a narrated finish line was added
                if len(part) < _before:
                    # R91 instrumentation: [part, chars cut by strip_repeat_cum, by fix_finish_place, by strip_man_on_man, up to 3 samples of the removed lines]
                    _gone = [x.strip()[:90] for x in _p0.split("\n") if x.strip() and x not in part.split("\n")][:3]
                    job.setdefault("repeatcum", []).append([i + 1, len(_p0) - len(_p1), len(_p1) - len(_p2), len(_p2) - len(part), _gone])
            except Exception as _e:
                job.setdefault("fixerr", []).append([i + 1, repr(_e)[:160]])
            tail = cur.rstrip()[-60:]
            if tail and part.startswith(tail[-40:]):
                part = part[len(tail[-40:]):].lstrip()
            new, _ = apply_chapter_edits(cur, [], part)
            write(fp, new)
            job["words"] = body_words(new)
        try:   # R114 (r115-dp: after the last planned finish the model went on and ก้อง/ปกรณ์/ธนา came a third time; offline, strip_repeat_cum on the finished text removes those lines): one sweep over each 2/3-men scene with the whole scene as context (the 4-men scene is left alone: its closing mouth-cum lines are wanted)
            _c2 = read(fp)
            _last = {}
            for _pr in parts:
                _pp2 = _pr.get("pose") or {}
                if _pp2.get("g") in (True, 3) and _pp2.get("nfin"):
                    _last[_pr.get("scene")] = _pp2
            _cuts = 0
            for _sc, _pp2 in sorted(_last.items(), key=lambda kv: -(scene_pos.get(kv[0]) or 0)):
                _p0 = scene_pos.get(_sc)
                if _p0 is None:
                    continue
                _body = _c2[_p0:]
                _nb = strip_repeat_cum(_body, "", _pp2)
                if _nb != _body:
                    _cuts += len(_body) - len(_nb)
                    _c2 = _c2[:_p0] + _nb
            if _cuts:
                write(fp, _c2)
                job["sweep"] = _cuts   # R114 instrumentation: characters removed by the final sweep
        except Exception as _e:
            job["sweeperr"] = repr(_e)[:160]
        if job.get("state") == "running":
            job["state"] = "done"
        try:
            _cur = read(fp)
            _uni = chapter_polish(_cur, char_info(b))
            if _uni != _cur:
                write(fp, _uni)
        except Exception:
            pass
        rep = check_text(b, read(fp), o.get("target", 0))
        job["issues"] = rep["issues"]
        job["words"] = rep["words"]
    except Exception as e:
        job.update({"state": "error", "error": str(e)})


def aw_start(b, cid):
    key = b + "/" + cid
    j = AW_JOBS.get(key)
    if j and j.get("state") == "running":
        return {"error": "กำลังเขียนตอนนี้อยู่แล้ว"}
    if is_locked(b, cid):
        return {"error": "ตอนนี้ล็อกอยู่ — ปลดล็อกก่อน"}
    if not os.path.exists(os.path.join(bdir(b), "chapters", cid + ".md")):
        return {"error": "ไม่มีตอนนี้"}
    AW_JOBS[key] = {"state": "starting", "i": 0, "n": 0, "words": 0, "issues": [], "t0": time.time()}
    import threading
    threading.Thread(target=aw_run, args=(b, cid, key), daemon=True).start()
    return {"ok": True}


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


def pdf_filename(b, only=None):
    """Download name: the book title, then the chapter number(s): 'ครอบครัวหรรษา ตอนที่ 1.pdf' / '... ตอนที่ 1-3.pdf' / whole book 'ครอบครัวหรรษา.pdf'."""
    title = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", str(meta(b).get("title") or b)).strip(" .") or b
    title = re.sub(r"\s+", " ", title)
    ids = [c["id"] for c in chapters(b)]
    nums = sorted({ids.index(x) + 1 for x in (only or []) if x in ids})
    if not nums:
        return title + ".pdf"
    if len(nums) == 1:
        return "%s ตอนที่ %d.pdf" % (title, nums[0])
    if nums == list(range(nums[0], nums[-1] + 1)):
        return "%s ตอนที่ %d-%d.pdf" % (title, nums[0], nums[-1])
    return "%s ตอนที่ %s.pdf" % (title, ", ".join(str(n) for n in nums))


def llm_get(path, timeout=5):
    with urllib.request.urlopen(LLM + path, timeout=timeout) as r:
        return r.status


INDEX = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"), encoding="utf-8").read() \
    if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")) else "<h1>index.html missing</h1>"


def english_share(txt):
    """Share of long free-text values (>=12 chars) in a proposal that contain no Thai letters; 0 when there is nothing to judge."""
    m = re.search(r"```proposal\s*\n([\s\S]*?)\n\s*```", txt)
    if not m:
        return 0.0
    try:
        p = json.loads(m.group(1))
    except Exception:
        return 0.0
    n = en = 0
    for it in list(p.get("characters") or []) + list(p.get("locations") or []):
        for o in [it] + list(it.get("outfits") or []):
            for v in (o.get("fields") or {}).values():
                if isinstance(v, str) and len(v) >= 12:
                    n += 1
                    if not re.search(r"[\u0E00-\u0E7F]", v):
                        en += 1
    return en / n if n >= 3 else 0.0


ASSIST_LEVELS = {
    "short": {"temp": 0.8, "style": "Keep free-text values as short phrases (a few words each).",
              "size": "Keep every proposal SMALL so it fits in the reply: at most 3 characters and 2 locations per turn, at most 1 outfit per character."},
    "rich": {"temp": 0.9, "style": "Make every free-text value vivid and specific, like a novelist's character sheet: one or two flowing sentences (about 15-40 words) with concrete sensory detail, a habit or quirk and a hook the author can use in scenes. Never write dry labels such as 'confident' or 'beautiful'. Names, places and backstories must be memorable and colourful, never generic.",
             "size": "Keep every proposal small enough to finish: at most 2 characters and 2 locations per turn, at most 1 outfit per character."},
    "max": {"temp": 0.95, "style": "Make every free-text value lush and detailed: two to four flowing sentences (about 40-90 words) with texture, smell, voice, mannerisms, secrets and contradictions, and how the person behaves in social and intimate scenes; for places give atmosphere, light, sound, smell and what makes the place good for scenes. Never write dry labels. Names and backstories must be memorable.",
            "size": "Keep every proposal small enough to finish: ONE character, or up to 2 locations, per turn, at most 1 outfit."},
}


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
                if p[3] == "autowrite" and len(p) == 5 and re.match(r"^ch\d+$", p[4]):
                    j = dict(AW_JOBS.get(b + "/" + p[4]) or {"state": "none"})
                    j.pop("cancel", None)
                    return self._send(200, j)
                if p[3] == "check" and len(p) == 5 and re.match(r"^ch\d+$", p[4]):
                    return self._send(200, check_text(b, read(os.path.join(bdir(b), "chapters", p[4] + ".md")), outline(b).get(p[4], {}).get("target", 0)))
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
                    nm = pdf_filename(b, only)
                    return self._send(200, pdf, "application/pdf", {"Content-Disposition": f"attachment; filename=\"{b}{suffix}.pdf\"; filename*=UTF-8''{quote(nm)}"})
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
                        cur["summary"] = str(d["summary"])[:3000]
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
                        clean = [{"title": str(x.get("title", ""))[:120], "summary": str(x.get("summary", ""))[:3000], "words": int(float(x.get("words") or 0)) if str(x.get("words") or "0").replace(".", "", 1).isdigit() else 0} for x in items if isinstance(x, dict)][:200]
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
            if len(p) == 5 and p[:2] == ["api", "books"] and p[3] == "autowrite" and re.match(r"^ch\d+$", p[4]) and SLUG.match(p[2]):
                if d.get("action") == "cancel":
                    j = AW_JOBS.get(p[2] + "/" + p[4])
                    if j:
                        j["cancel"] = True
                    return self._send(200, {"ok": True})
                r = aw_start(p[2], p[4])
                return self._send(409 if r.get("error") else 200, r)
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
            if p == ["api", "scene-plan"]:
                return self.scene_plan(d)
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

    def scene_plan(self, d):
        """rewrite one chapter's summary as a scene list ('ฉากที่ 1 (ประมาณ 25%) ...') that the scene-by-scene writer can follow."""
        b, cid = d.get("book", ""), d.get("id", "")
        if not (SLUG.match(b) and re.match(r"^ch\d+$", cid)):
            return self._send(400, {"error": "bad ids"})
        ol, chl = outline(b), {c["id"]: c for c in chapters(b)}
        if cid not in ol:
            return self._send(404, {"error": "ตอนนี้ไม่อยู่ใน outline"})
        names = name_table(b)
        try:
            cs = json.loads(read(os.path.join(bdir(b), "setup.json"), "{}") or "{}").get("chars", [])
        except Exception:
            cs = []
        roles = {str(c.get("name") or ""): ((c.get("v") or {}).get("role") or {}).get("c", "") for c in cs}
        ntxt = "\n".join("- %s (%s)%s" % (th or nm, nm, (": " + roles.get(nm, "")) if roles.get(nm) else "") for nm, th in names)
        keys = sorted(ol)
        i = keys.index(cid)
        near = "\n".join("%s | %s | %s" % (k, chl.get(k, {}).get("title", "?"), ol[k].get("summary", "")[:300]) for k in keys[max(0, i - 1):i + 2])
        task = ("TASK: rewrite the summary of chapter %s (\"%s\", target %s words) as a SCENE LIST in THAI for a scene-by-scene writer. Current summary: %s\n"
                "FORMAT: 2-5 scenes, exactly like: ฉากที่ 1 (ประมาณ 20%%) <who is there, where, what happens in order> ฉากที่ 2 (ประมาณ 80%%) ... Percentages add up to 100. "
                "Keep every plot point, setting and group composition of the current summary and stay consistent with the neighbouring chapters and the bible. Use ONLY characters named or clearly implied by the current summary (match descriptions such as 'the two secretaries' or 'the driver' to the roles in the NAMES list); never add other family members. "
                "Write everything in Thai: no English words, no English labels like (Position 1) or (Oral), and never the clinical words อวัยวะเพศ, องคชาต, ช่องคลอด, น้ำอสุจิ, ถึงจุดสุดยอด (say ควย, หี, เย็ด, อม, เลีย, น้ำเชื้อ instead). Describe how a man finishes with the word แตก plus the place: แตกใน (แตกในปาก, แตกในหี), แตกข้างนอก, แตกใส่หน้า; never use เสร็จ for this. A woman climaxing is also แตก (never เสร็จ); her wetness is น้ำหี or น้ำเงี่ยน, never น้ำหล่อลื่น or สารคัดหลั่ง. "
                "In each sex scene name every participant, say who is a stranger or newcomer if relevant, list the acts and positions in order (oral on the man first, at least 4-5 different positions in a pair scene and 6-8 in a group scene, every participant active) and how it ends (about 60%% of sex scenes end with แตกในปาก). "
                "Sex scenes together take about 60-70%% of the chapter; a non-sex opening or closing scene takes the rest. Write names only in Thai script from the NAMES list. Unless the bible has custom speech and relationship rules: every character is heterosexual (no LGBT characters), but in sex scenes women may have sex with each other; men have no sexual contact with men. Maximum 1100 characters. Output ONLY the scene list, nothing else."
                ) % (cid, chl.get(cid, {}).get("title", cid), ol[cid].get("target", 0), ol[cid].get("summary", ""))
        prompt = "STORY BIBLE (excerpt):\n" + read(os.path.join(bdir(b), "bible.md"))[:7000] + "\n\nNAMES (use Thai spelling only):\n" + ntxt + "\n\nNEIGHBOURING CHAPTERS (id | title | summary):\n" + near + "\n\n" + task
        payload = {"messages": [{"role": "system", "content": "You are a story editor for adult fiction. All characters are adults. Write what is asked without moralizing."}, {"role": "user", "content": prompt}],
                   "stream": False, "max_tokens": 2400, "temperature": 0.7, "chat_template_kwargs": {"enable_thinking": False}}
        payload.update(ANTI_REPEAT)
        req = urllib.request.Request(LLM + "/v1/chat/completions", json.dumps(payload).encode(), {"Content-Type": "application/json"})
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=300).read().decode("utf-8"))
            t = clean_part(r["choices"][0]["message"].get("content") or "")
            t = re.sub(r"\s*\n+\s*", " ", t)
            t = plain_words(t)
            t = re.sub(r"\s*\((?:[A-Za-z][^)]{0,60})\)", "", t).strip()[:3000]
            if not re.search(r"ฉากที่\s*1", t):
                return self._send(502, {"error": "โมเดลไม่ตอบเป็นรายการฉาก ลองอีกครั้ง"})
            return self._send(200, {"summary": t})
        except Exception as e:
            return self._send(502, {"error": "LLM unavailable: %s" % e})

    def assist(self, d):
        detail = d.get("detail") if d.get("detail") in ASSIST_LEVELS else "rich"
        lv = ASSIST_LEVELS[detail]
        lang = str(d.get("lang") or "Thai")[:30]
        note = str(d.get("note") or "").strip()[:1500]
        sysmsg = ("ASSIST MODE. You are the story-setup assistant inside a Thai author's novel studio. Talk to the author in THAI, warm and lively but brief. "
                  "Goal: agree on the story setup (genre, tone, setting, protagonist, partner/love interest, antagonist, plot, style, outfits, locations) and design colourful, memorable people and places so the author does not have to click each field.\n"
                  "Rules:\n1) If key specs are missing, ask at most 2 short questions. If the author gives enough, or says to decide for them, propose right away.\n"
                  "2) To propose, write one or two Thai sentences, then ONE fenced block exactly like ```proposal\n{json}\n``` containing ONLY valid JSON (no comments, no trailing commas).\n"
                  "JSON shape: {\"top\":{\"genre\":\"detective mystery, noir\",...},\"characters\":[{\"name\":\"Mara Voss\",\"fields\":{\"role\":\"protagonist\",\"age\":\"34\",...},\"outfits\":[{\"name\":\"Work coat\",\"fields\":{\"ostyle\":\"trench coat\"}}]}],\"locations\":[{\"name\":\"Harbour office\",\"fields\":{\"ltype\":\"office\"}}]}.\n"
                  "3) Use only the field keys listed below. Include only fields you want to set or change. For fields that have a fixed list of options use an option from the list; write every free-text value in " + lang + ". " + lv["style"] + "\n"
                  "4) NEVER include anything listed as LOCKED. Work around locked items consistently.\n"
                  "5) All characters are adults (18+). Never involve minors.\n6) Keep consistent with CURRENT SETUP; do not contradict chosen items unless the author asks. Do not duplicate people or places that already exist there; make new ones clearly different from each other (age, temperament, look, voice, role).\n"
                  "7) " + lv["size"] + " Propose the rest in later turns, and always close the JSON and the fence.\n"
                  "8) NOTHING is created unless you send the proposal block. Whenever the author asks to add or change a character, location, outfit or any setup field, or agrees to a plan you suggested, put the proposal block in THAT SAME reply. Never answer only 'OK' or 'will do'.\n"
                  + (("\nAUTHOR'S OWN INSTRUCTIONS (always obey; they override the defaults above):\n" + note + "\n") if note else "") +
                  "\nLANGUAGE RULE (strict): every free-text value, every character name and every location name MUST be written in " + lang + " (native script), never in English, and never mix English words into the text. Names must be distinctive and different from every existing name. Only fields with a fixed option list keep the listed option.\n"
                  "\nFIELD KEYS:\n" + str(d.get("spec", ""))[:6000] +
                  "\n\nLOCKED: " + str(d.get("locked", ""))[:1500] +
                  "\n\nCURRENT SETUP:\n" + str(d.get("state", ""))[:max(9000, int(CTX * 0.15))])
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
        # the setup text and field keys are English, so the model drifts into English: repeat the language rule on the newest user turn
        if lang.lower() == "thai":
            for mm in reversed(msgs):
                if mm["role"] == "user":
                    mm["content"] += "\n\n[ค่าทุกช่อง ชื่อตัวละคร ชื่อสถานที่ ในข้อเสนอ ต้องเป็นภาษาไทยเท่านั้น]"
                    break
        # keep context bounded: at most the 3 newest complete proposals stay in history (older ones are dropped whole, never replaced by a marker)
        seen = 0
        for i in range(len(msgs) - 1, 0, -1):
            if msgs[i]["role"] == "assistant" and "```proposal" in msgs[i]["content"]:
                seen += 1
                if seen > 3:
                    del msgs[i]
        # 3500 tokens ~ 80 s: stays under the ~100 s proxy limit for non-streaming calls (2200 cut real proposals; tested 28 Oct 2026)
        payload = {"messages": msgs, "stream": False, "max_tokens": 3500, "temperature": lv["temp"], "chat_template_kwargs": {"enable_thinking": False}}
        def ask(ms):
            p2 = dict(payload, messages=ms)
            rq = urllib.request.Request(LLM + "/v1/chat/completions", json.dumps(p2).encode(), {"Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(rq, timeout=300).read().decode("utf-8"))["choices"][0]["message"]["content"].strip()
        t_start = time.time()
        try:
            txt = ask(msgs)
            # the setup text is English, so the model sometimes answers in English: retry once (only if there is time left before the ~100 s proxy cut)
            if lang.lower() == "thai" and english_share(txt) > 0.3 and time.time() - t_start < 45:
                retry = msgs + [{"role": "assistant", "content": txt}, {"role": "user", "content": "ค่าในข้อเสนอยังเป็นภาษาอังกฤษ ส่งข้อเสนอเดิมอีกครั้ง โดยเขียนค่าทุกช่องที่เป็นข้อความอิสระเป็นภาษาไทยทั้งหมด ห้ามมีอังกฤษปน ตอบสั้นๆ แล้วตามด้วยบล็อก proposal"}]
                t2 = ask(retry)
                if english_share(t2) < english_share(txt):
                    txt = t2
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
