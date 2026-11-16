"""Читалка: вставляешь ссылку, получаешь чистый текст для чтения.

Запуск:  uvicorn app:app --host 0.0.0.0 --port 8080
"""
import asyncio
import ipaddress
import logging
import os
import re
import socket
import time
import warnings
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
import textfix
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from fastapi.requests import Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

warnings.filterwarnings("ignore", category=UserWarning, module="bs4")
try:
    from bs4 import XMLParsedAsHTMLWarning

    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except ImportError:
    pass

log = logging.getLogger("reader")
BASE = Path(__file__).parent
MAX_BYTES = 8 * 1024 * 1024  # максимум 8 МБ на страницу
MAX_REDIRECTS = 5
CACHE_TTL = 60 * 30
CACHE_MAX = 200
ALLOW_PRIVATE = os.environ.get("ALLOW_PRIVATE") == "1"  # только для локальных тестов

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
    "Accept-Language": "ru,en;q=0.8,es;q=0.6",
}

def safe_join(base: str, href: str) -> str | None:
    """urljoin, который не падает на битых ссылках вроде http://[abc"""
    try:
        return urljoin(base, href)
    except ValueError:
        return None


def safe_scheme(url: str | None) -> str:
    try:
        return urlparse(url or "").scheme
    except ValueError:
        return ""


ALLOWED_TAGS = {
    "p", "br", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "ul", "ol", "li",
    "em", "i", "strong", "b", "u", "s", "sub", "sup", "code", "pre", "hr", "a",
    "img", "figure", "figcaption", "table", "thead", "tbody", "tr", "th", "td",
    "dl", "dt", "dd", "div", "span",
}
ALLOWED_ATTRS = {"a": {"href"}, "img": {"src", "alt"}}

app = FastAPI(title="Читалка")


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    """Любая неожиданная ошибка: пишем подробности в лог и показываем читателю понятное сообщение."""
    log.exception("Ошибка при обработке %s", request.url)
    return JSONResponse(
        status_code=500,
        content={"detail": f"Внутренняя ошибка читалки ({type(exc).__name__}). Пришли это сообщение разработчику."},
    )


_cache: dict[str, tuple[float, dict]] = {}


def _is_public(host: str) -> bool:
    if ALLOW_PRIVATE:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False
    return True


async def _check_url(url: str) -> None:
    try:
        parsed = urlparse(url)
        parsed.hostname  # noqa: B018  (может бросить ValueError)
    except ValueError:
        raise HTTPException(400, "Не получилось разобрать ссылку. Проверь, что она скопирована целиком.")
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(400, "Нужна ссылка, которая начинается с http:// или https://")
    ok = await asyncio.get_running_loop().run_in_executor(None, _is_public, parsed.hostname)
    if not ok:
        raise HTTPException(400, "Этот адрес недоступен для чтения.")


async def fetch(url: str) -> tuple[bytes, str, str]:
    """Скачивает страницу, проверяя каждый редирект. Возвращает (байты, финальный url, content-type)."""
    async with httpx.AsyncClient(timeout=20, headers=HEADERS, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            await _check_url(url)
            try:
                async with client.stream("GET", url) as resp:
                    if resp.is_redirect:
                        loc = resp.headers.get("location")
                        if not loc:
                            raise HTTPException(502, "Сайт вернул пустой редирект.")
                        url = safe_join(url, loc)
                        if not url:
                            raise HTTPException(502, "Сайт вернул некорректный редирект.")
                        continue
                    if resp.status_code >= 400:
                        raise HTTPException(502, f"Сайт ответил ошибкой {resp.status_code}.")
                    ctype = resp.headers.get("content-type", "").lower()
                    chunks, size = [], 0
                    async for chunk in resp.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise HTTPException(413, "Страница слишком большая (больше 8 МБ).")
                        chunks.append(chunk)
                    return b"".join(chunks), str(resp.url), ctype
            except httpx.TimeoutException:
                raise HTTPException(504, "Сайт не ответил за 20 секунд.")
            except httpx.HTTPError as e:
                raise HTTPException(502, f"Не удалось открыть сайт: {type(e).__name__}.")
    raise HTTPException(502, "Слишком много перенаправлений.")


def sanitize(html: str, base_url: str) -> str:
    """Оставляет только безопасные теги и атрибуты, абсолютные ссылки."""
    soup = BeautifulSoup(html, "lxml")
    root = soup.body or soup
    for tag in root.find_all(True):
        if getattr(tag, "parent", None) is None:
            continue  # уже удалён вместе с родителем
        if tag.name not in ALLOWED_TAGS:
            if tag.name in ("script", "style", "iframe", "object", "embed", "form", "svg", "noscript"):
                tag.decompose()
            else:
                tag.unwrap()
            continue
        allowed = ALLOWED_ATTRS.get(tag.name, set())
        for attr in list(tag.attrs):
            if attr not in allowed:
                del tag.attrs[attr]
        if tag.name == "a":
            href = safe_join(base_url, tag.attrs.get("href", ""))
            if safe_scheme(href) in ("http", "https"):
                tag.attrs["href"] = href
                tag.attrs["target"] = "_blank"
                tag.attrs["rel"] = "noopener noreferrer"
            else:
                tag.unwrap()
        elif tag.name == "img":
            src = safe_join(base_url, tag.attrs.get("src", ""))
            if safe_scheme(src) in ("http", "https"):
                tag.attrs["src"] = src
                tag.attrs["loading"] = "lazy"
                tag.attrs["referrerpolicy"] = "no-referrer"
            else:
                tag.decompose()
    return "".join(str(c) for c in root.children)


NEXT_RE = re.compile(r"^\W*(далее|дальше|вперед|вперёд|следующ\w*|продолжить|next\w*|siguiente|continue)\W*$|^\W*[»›→>]+\W*$", re.I)
PREV_RE = re.compile(r"^\W*(назад|предыдущ\w*|пред\.?|prev\w*|previous|anterior|back)\W*$|^\W*[«‹←<]+\W*$", re.I)


def find_neighbors(raw: bytes, url: str) -> dict:
    """Ищет на странице ссылки «дальше» и «назад»: rel=next/prev, затем по тексту ссылки."""
    soup = BeautifulSoup(raw, "lxml")
    host = urlparse(url).hostname
    out = {"next": None, "prev": None}

    def ok(href):
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            return None
        full = safe_join(url, href)
        if not full:
            return None
        full = full.split("#")[0]
        try:
            p = urlparse(full)
        except ValueError:
            return None
        if p.scheme not in ("http", "https") or p.hostname != host:
            return None
        if full.rstrip("/") == url.split("#")[0].rstrip("/"):
            return None
        return full

    for key in ("next", "prev"):
        rel = "next" if key == "next" else "prev"
        for tag in soup.find_all(["link", "a"], rel=True):
            rels = [r.lower() for r in (tag.get("rel") or [])]
            if rel in rels and (hit := ok(tag.get("href"))):
                out[key] = hit
                break
    for key, rx in (("next", NEXT_RE), ("prev", PREV_RE)):
        if out[key]:
            continue
        for a in soup.find_all("a", href=True):
            label = " ".join(a.get_text(" ", strip=True).split()) or (a.get("aria-label") or a.get("title") or "")
            if 0 < len(label) <= 30 and rx.match(label) and (hit := ok(a["href"])):
                out[key] = hit
                break
    return out


def plain_to_html(text: str) -> str:
    from html import escape

    paras = [p.strip() for p in text.replace("\r\n", "\n").split("\n\n")]
    return "".join(f"<p>{escape(p).replace(chr(10), '<br>')}</p>" for p in paras if p)


def extract(raw: bytes, url: str, ctype: str) -> dict:
    if "text/plain" in ctype:
        text = raw.decode("utf-8", errors="replace")
        body = plain_to_html(text)
        title = urlparse(url).path.rsplit("/", 1)[-1] or urlparse(url).hostname
        meta = {"title": title}
    else:
        soup = BeautifulSoup(raw, "lxml")
        html_for_extract = raw
        try:
            textfix.normalize(soup)
            html_for_extract = str(soup)
        except Exception:
            log.exception("textfix.normalize упал, читаем без него")
            soup = None
        body = trafilatura.extract(
            html_for_extract,
            url=url,
            output_format="html",
            include_images=True,
            include_links=True,
            include_tables=True,
            include_formatting=True,
            favor_recall=True,
        )
        if body and soup is not None:
            try:
                body = textfix.drop_nav_paragraphs(textfix.recover(soup, body))
            except Exception:
                log.exception("textfix.recover упал, оставляем результат как есть")
        md = trafilatura.extract_metadata(raw, default_url=url)
        meta = {
            "title": getattr(md, "title", None),
            "author": getattr(md, "author", None),
                        "sitename": getattr(md, "sitename", None),
        }
        if not body:
            raise HTTPException(
                422,
                "Не получилось найти на странице основной текст. "
                "Возможно, сайт строится через JavaScript или закрыт авторизацией.",
            )
        body = sanitize(body, url)
        # убираем первый заголовок, только если он целиком повторяет название страницы
        soup0 = BeautifulSoup(body, "lxml")
        first = soup0.body.find(True) if soup0.body else None
        if first is not None and first.name in ("h1", "h2") and meta.get("title"):
            a = " ".join(first.get_text().lower().split())
            b = " ".join(meta["title"].lower().split())
            if a and (a == b or (b.startswith(a) and len(a) >= 0.6 * len(b))):
                first.decompose()
                body = "".join(str(c) for c in soup0.body.children)
    neighbors = {"next": None, "prev": None}
    if "text/plain" not in ctype:
        try:
            neighbors = find_neighbors(raw, url)
        except Exception:
            log.exception("не удалось найти ссылки далее/назад")
    text_len = len(BeautifulSoup(body, "lxml").get_text(" ", strip=True))
    words = len(BeautifulSoup(body, "lxml").get_text(" ", strip=True).split())
    return {
        "url": url,
        "host": urlparse(url).hostname,
        "title": (meta.get("title") or urlparse(url).hostname or "Без названия").strip(),
        "author": meta.get("author"),
                "sitename": (meta.get("sitename") if (meta.get("sitename") or "").strip().lower() != (meta.get("title") or "").strip().lower() else None),
        "html": body,
        "chars": text_len,
        "words": words,
        "minutes": max(1, round(words / 200)),
        **neighbors,
    }


@app.get("/api/read")
async def read(url: str = Query(..., min_length=4, max_length=2000)):
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    now = time.time()
    hit = _cache.get(url)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    raw, final_url, ctype = await fetch(url)
    if not any(t in ctype for t in ("html", "xml", "text/plain", "")):
        raise HTTPException(415, "Это не веб-страница. Для PDF и файлов читалка пока не подходит.")
    result = await asyncio.get_running_loop().run_in_executor(None, extract, raw, final_url, ctype)
    if len(_cache) >= CACHE_MAX:
        _cache.pop(min(_cache, key=lambda k: _cache[k][0]))
    _cache[url] = (now, result)
    return result


@app.get("/")
async def index():
    return FileResponse(BASE / "static" / "index.html")


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
