"""Исправления для случаев, где автоматическое извлечение текста теряет части страницы.

Книжные сайты часто размечают заголовки и абзацы не тегами h1..h6 и p, а div, font, center
или вообще голым текстом с <br>. Извлекатель такой текст считает мусором и выбрасывает.

Что делает этот модуль:
  1. normalize(soup)  до извлечения: приводит разметку к нормальному виду
     (абзацы в <p>, заголовки в <h2>, текст с <br><br> режется на абзацы).
  2. recover(soup, body_html)  после извлечения: сверяет результат с исходной страницей
     и возвращает на место блоки, которые потерялись в середине текста.
"""
import re

from bs4 import BeautifulSoup, NavigableString, Tag

BLOCK = {
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "blockquote", "pre",
    "center", "td", "th", "tr", "table", "tbody", "thead", "dd", "dt", "dl", "section",
    "article", "main", "body", "figure", "figcaption", "header", "footer", "aside", "nav", "form",
}
LEAF_KEEP = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "td", "th", "dd", "dt", "figcaption"}
CONTAINERS_TO_SPLIT = {"div", "td", "section", "article", "main", "body", "center"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

KEYWORD_RE = re.compile(
    r"^\s*(глава|часть|книга|раздел|пролог|эпилог|предисловие|послесловие|введение|заключение|"
    r"оглавление|содержание|от автора|от издателя|от переводчика|посвящение|благодарност\w*|"
    r"примечани\w*|приложени\w*|аннотация|chapter|part|book|prologue|epilogue|preface|foreword|"
    r"afterword|introduction|conclusion|contents|acknowledg\w*|appendix|prefacio|capítulo|"
    r"capitulo|prólogo|epílogo|introducción)\b",
    re.I,
)
HEAD_CLASS_RE = re.compile(r"(^|[-_ ])(h[1-6]|head|heading|title|subtitle|zag\w*|chapter|caption|take_h\w*)($|[-_ \d])", re.I)
BOILERPLATE_RE = re.compile(
    r"(^|[-_ ])(nav\w*|menu|footer|comment\w*|share|social|banner|advert\w*|ads?|related|breadcrumb\w*|"
    r"pagination|pager|rating|tags?|sidebar|widget|cookie|popup|modal|subscribe|search|login|reg(ister)?|"
    r"counter|bookmark\w*|fav\w*)($|[-_ \d])",
    re.I,
)
SCENE_BREAK_RE = re.compile(r"^[\*\-–—_•·~=#\s]{3,}$")
END_PUNCT = ".,;:!?…»\"”)"


def norm(s: str) -> str:
    s = s.replace("​", "").replace("\xa0", " ")
    return " ".join(s.split()).lower()


def is_block(n) -> bool:
    return isinstance(n, Tag) and n.name in BLOCK


def own_text(nodes) -> str:
    out = []
    for n in nodes:
        out.append(n.get_text(" ", strip=False) if isinstance(n, Tag) else str(n))
    return " ".join("".join(out).split())


def attr_str(el: Tag) -> str:
    cls = el.get("class") or []
    if isinstance(cls, str):
        cls = [cls]
    return " ".join(cls + [el.get("id") or ""])


# ---------------------------------------------------------------- заголовки

def heading_score(el: Tag, text: str) -> int:
    """Считает признаки заголовка у короткого блока: по центру, жирный, крупный, класс."""
    probes = [el]
    kids = [c for c in el.children if isinstance(c, Tag) or str(c).strip()]
    if len(kids) == 1 and isinstance(kids[0], Tag):
        probes.append(kids[0])  # весь текст внутри одного b/font/span
    center = bold = big = False
    for p in probes:
        style = (p.get("style") or "").lower().replace(" ", "")
        if "text-align:center" in style or (p.get("align") or "").lower() == "center":
            center = True
        if p.name in ("b", "strong") or re.search(r"font-weight:(bold|[6-9]00)", style):
            bold = True
        m = re.search(r"font-size:([\d.]+)(px|pt|em|rem|%)", style)
        if m:
            v, u = float(m.group(1)), m.group(2)
            if (u == "px" and v >= 18) or (u == "pt" and v >= 14) or (u in ("em", "rem") and v >= 1.3) or (u == "%" and v >= 120):
                big = True
        if p.name == "font":
            sz = (p.get("size") or "").strip()
            if sz.lstrip("+").isdigit() and (sz.startswith("+") or int(sz) >= 4):
                big = True
    if not center and el.parent is not None and el.parent.name == "center":
        center = True
    cls = any(HEAD_CLASS_RE.search(attr_str(p)) for p in probes)
    # жирным выделен весь короткий блок: это почти всегда заголовок
    whole_bold = bold and len(text) <= 60
    return int(center) + int(bold) + int(whole_bold) + int(big) + int(cls)


def looks_like_heading(el: Tag, text: str) -> bool:
    if not text or len(text) > 120 or len(text.split()) > 14:
        return False
    if not re.search(r"\w", text):
        return False
    if el.find("a") is not None and len(" ".join(a.get_text() for a in el.find_all("a"))) > 0.5 * len(text):
        return False
    keyword = bool(KEYWORD_RE.match(text)) and len(text.split()) <= 8
    if keyword and not (text.rstrip()[-1] in "…?!" and len(text.split()) > 3):
        return True
    if text.rstrip()[-1] in END_PUNCT:
        return False
    return heading_score(el, text) >= 2


# ---------------------------------------------------------------- нормализация

def _split_groups(el: Tag, br_threshold: int):
    """Делит дочерние узлы на группы: блоки отдельно, «голый» текст по <br>-разрывам."""
    groups = []
    cur = []
    br_run = []

    def flush():
        nonlocal cur
        if cur and own_text(cur).strip():
            groups.append(("loose", cur))
        cur = []

    for c in list(el.children):
        if is_block(c):
            br_run = []
            flush()
            groups.append(("block", c))
        elif isinstance(c, Tag) and c.name == "br":
            br_run.append(c)
        elif isinstance(c, NavigableString) and not str(c).strip():
            if br_run:
                br_run.append(c)
            else:
                cur.append(c)
        else:
            if len([b for b in br_run if isinstance(b, Tag)]) >= br_threshold:
                flush()
            else:
                cur.extend(br_run)
            br_run = []
            cur.append(c)
    flush()
    return groups


def _count_brs(el: Tag) -> tuple[int, int]:
    """(пар br подряд между кусками текста, всего br среди прямых детей)."""
    doubles = singles = run = 0
    seen_text = False
    for c in el.children:
        if isinstance(c, Tag) and c.name == "br":
            run += 1
            singles += 1
        elif isinstance(c, NavigableString) and not str(c).strip():
            continue
        else:
            if run >= 2 and seen_text:
                doubles += 1
            run = 0
            seen_text = True
    return doubles, singles


def _rebuild(soup: BeautifulSoup, el: Tag, groups):
    for c in list(el.children):
        c.extract()
    for kind, item in groups:
        if kind == "block":
            el.append(item)
        else:
            p = soup.new_tag("p")
            for n in item:
                p.append(n)
            # убираем пробелы и br по краям абзаца
            while p.contents and ((isinstance(p.contents[0], NavigableString) and not str(p.contents[0]).strip()) or getattr(p.contents[0], "name", "") == "br"):
                p.contents[0].extract()
            while p.contents and ((isinstance(p.contents[-1], NavigableString) and not str(p.contents[-1]).strip()) or getattr(p.contents[-1], "name", "") == "br"):
                p.contents[-1].extract()
            el.append(p)


def normalize(soup: BeautifulSoup) -> None:
    for t in soup(["script", "style", "noscript", "iframe", "svg", "template"]):
        t.decompose()
    # явный мусор (реклама, комментарии, меню, кнопки соцсетей) убираем до превращения div в абзацы
    for el in list(soup.find_all(True)):
        if el.name in ("html", "body", "article", "main") or el.parent is None:
            continue
        if BOILERPLATE_RE.search(attr_str(el)):
            text = el.get_text(" ", strip=True)
            links = " ".join(a.get_text(" ", strip=True) for a in el.find_all("a"))
            if len(text) < 600 or (links and len(links) > 0.5 * len(text)):
                el.decompose()
    # снизу вверх, чтобы вложенные контейнеры обработались раньше внешних
    for el in reversed(soup.find_all(list(CONTAINERS_TO_SPLIT))):
        has_block = any(is_block(c) for c in el.children)
        doubles, singles = _count_brs(el)
        loose_len = len(own_text([c for c in el.children if not is_block(c)]))
        mode = None
        if loose_len >= 150 or (has_block and loose_len > 0):
            if doubles >= 1:
                mode = 2  # <br><br> между абзацами
            elif not has_block and singles >= 3:
                segs = [s for s in re.split(r"\s*<br\s*/?>\s*", "".join(str(c) for c in el.children)) if s.strip()]
                lens = [len(BeautifulSoup(s, "lxml").get_text().strip()) for s in segs]
                if len(lens) >= 3 and sum(lens) / len(lens) >= 70:
                    mode = 1  # длинные строки через одиночный <br>: каждая строка абзац
            if mode is None and has_block:
                mode = 99  # блоки и голый текст рядом: просто заворачиваем текст в <p>
        if mode:
            _rebuild(soup, el, _split_groups(el, mode))
    # div и center без вложенных блоков это абзацы
    for el in soup.find_all(["div", "center"]):
        if el.find(list(BLOCK)) is None and el.get_text(strip=True):
            el.name = "p"
    # заголовки, оформленные не тегом
    for el in soup.find_all(["p", "div"]):
        if el.name in HEADINGS or el.find(list(BLOCK)) is not None:
            continue
        text = " ".join(el.get_text(" ").split())
        if looks_like_heading(el, text):
            el.name = "h2"
            for a in ("style", "align", "class", "id"):
                el.attrs.pop(a, None)


# ---------------------------------------------------------------- возврат потерянного

def _leaf_blocks(root: Tag):
    out = []
    for el in root.find_all(list(LEAF_KEEP | {"div"})):
        if el.find(list(LEAF_KEEP | {"div"})) is None:
            text = " ".join(el.get_text(" ").split())
            if text:
                out.append((el, text))
    return out


def _skip_candidate(el: Tag, text: str, lca: Tag) -> bool:
    if not re.search(r"\w", text) and not SCENE_BREAK_RE.match(text):
        return True
    links = " ".join(a.get_text(" ") for a in el.find_all("a"))
    if links and len(links) > 0.5 * len(text):
        return True
    for p in [el, *el.parents]:
        if p is lca.parent:
            break
        if isinstance(p, Tag) and p.name in ("nav", "footer", "aside"):
            return True
        if isinstance(p, Tag) and BOILERPLATE_RE.search(attr_str(p)):
            return True
    return False


def recover(soup: BeautifulSoup, body_html: str) -> str:
    """Сравнивает результат извлечения с исходной страницей и возвращает потерянные блоки."""
    if not body_html:
        return body_html
    kept = BeautifulSoup(body_html, "lxml")
    k_els = [e for e in kept.find_all(list(LEAF_KEEP)) if e.find(list(LEAF_KEEP)) is None]
    k_txt = [norm(e.get_text(" ")) for e in k_els]
    root = soup.body or soup
    blocks = _leaf_blocks(root)
    o_txt = [norm(t) for _, t in blocks]

    covered: dict[int, int] = {}
    j = 0
    for i, o in enumerate(o_txt):
        if not o:
            continue
        for k in range(j, min(j + 80, len(k_txt))):
            kt = k_txt[k]
            if kt == o:
                covered[i], j = k, k + 1
                break
            if len(o) >= 30 and o in kt:
                covered[i], j = k, k  # несколько блоков слиты в один
                break
            if len(kt) >= 30 and kt in o:
                covered[i], j = k, k + 1  # извлекатель немного обрезал блок
                break
    if not covered:
        return body_html
    first, last = min(covered), max(covered)

    # общий предок найденных блоков: сюда ограничиваем возврат
    lca = blocks[first][0]
    for i in covered:
        ids = {id(p) for p in blocks[i][0].parents} | {id(blocks[i][0])}
        while id(lca) not in ids and lca.parent is not None:
            lca = lca.parent
    start = first
    i = first - 1
    while i >= 0 and i not in covered and blocks[i][0].name in HEADINGS:
        if id(lca) in {id(p) for p in blocks[i][0].parents}:
            start = i
        i -= 1

    inserted_after: dict[int, Tag] = {}
    anchor = -1
    added = 0
    for i in range(start, last + 1):
        if i in covered:
            anchor = covered[i]
            continue
        el, text = blocks[i]
        if id(lca) not in {id(p) for p in el.parents} and el is not lca:
            continue
        if _skip_candidate(el, text, lca):
            continue
        tag_name = "h2" if el.name in HEADINGS else "p"
        new = kept.new_tag(tag_name)
        inner = BeautifulSoup(el.decode_contents(), "lxml")
        for c in list((inner.body or inner).children):
            new.append(c)
        prev = inserted_after.get(anchor)
        if prev is not None:
            prev.insert_after(new)
        elif anchor >= 0:
            k_els[anchor].insert_after(new)
        elif k_els:
            k_els[0].insert_before(new)
        else:
            (kept.body or kept).append(new)
        inserted_after[anchor] = new
        added += 1
    return "".join(str(c) for c in (kept.body or kept).children) if added else body_html


NAV_WORD_RE = re.compile(
    r"(читать дальше|читать далее|далее|дальше|следующ\w+|предыдущ\w+|назад|скачать|купить|подробнее|"
    r"поделиться|в закладки|наверх|оглавлени\w+ книги|next|prev\w*|previous|read more|continue reading|download|share)",
    re.I,
)


def drop_nav_paragraphs(body_html: str) -> str:
    """Убирает одиночные ссылки-кнопки между абзацами («Читать дальше», «Скачать», «Поделиться»)."""
    soup = BeautifulSoup(body_html, "lxml")
    changed = False
    for p in soup.find_all(["p", "div", "li"]):
        text = " ".join(p.get_text(" ").split())
        links = " ".join(" ".join(a.get_text(" ").split()) for a in p.find_all("a"))
        if text and len(text) <= 80 and links and len(links) >= 0.9 * len(text) and NAV_WORD_RE.search(text):
            p.decompose()
            changed = True
    return "".join(str(c) for c in (soup.body or soup).children) if changed else body_html
