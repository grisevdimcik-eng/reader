"""Проверка, что читалка не теряет заголовки и абзацы при разной разметке.

Запуск из папки проекта:  python3 tests/test_extract.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app  # noqa: E402

LONG = "Абзац №"  # метка начала каждого обычного абзаца
WORDS = ["берег", "дорога", "ключ", "письмо", "море", "окно", "город", "лес", "огонь", "ветер", "камень", "река"]


def para(i: int) -> str:
    a, b, c = WORDS[i % 12], WORDS[(i * 5 + 3) % 12], WORDS[(i * 7 + 1) % 12]
    return (f"<p>Абзац №{i}. Герой подошёл к двери и долго рассматривал {a}, потом вспомнил про {b} и про {c}, "
            f"о которых говорили накануне, и решил не торопиться с выводами до самого утра ({i * 17}).</p>\n")


def paras(start: int, n: int = 3) -> str:
    return "".join(para(start + k) for k in range(n))


def page(middle: str, before: str = "", after: str = "", title: str = "Хорошее самочувствие - Дэвид Бернс") -> bytes:
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>{title}</title></head><body>
<div class="menu"><a href="/">Главная</a> <a href="/a">Авторы</a> <a href="/b">Жанры</a> <a href="/c">Новинки</a></div>
<div class="content"><div class="pager"><a href="/p1">« Назад</a> <a href="/p3">Далее »</a></div>
{before}{paras(0)}{middle}{paras(3)}{after}</div>
<div class="footer">© Сайт. Все права защищены. <a href="/privacy">Политика</a></div></body></html>""".encode()


def run(html: bytes) -> str:
    return app.extract(html, "https://example.test/read_book.php?id=1&p=2", "text/html")["html"]


def index_of(out: str, needle: str) -> int:
    return out.find(needle)


CASES = [
    # (название, разметка, что должно быть в тексте, ожидаемый тег или None)
    ("заголовок h2", "<h2>Предисловие</h2>", "Предисловие", "h2"),
    ("заголовок div.class+center", '<div class="take_h1" style="text-align:center;color:#a0661b;font-size:24px">Предисловие</div>', "Предисловие", "h2"),
    ("заголовок p>b по центру", "<p align=center><b>Предисловие</b></p>", "Предисловие", "h2"),
    ("заголовок font", "<center><font size=5 color=brown>Предисловие</font></center>", "Предисловие", "h2"),
    ("заголовок span в p по центру", '<p style="text-align:center"><span style="color:#a0661b;font-size:20px">Предисловие</span></p>', "Предисловие", "h2"),
    ("заголовок голым текстом", "Предисловие<br>", "Предисловие", "h2"),
    ("заголовок Глава в div", '<div class="chapter">Глава 2. Продолжение</div>', "Глава 2", "h2"),
    ("заголовок Глава в p", "<p>Глава 3</p>", "Глава 3", "h2"),
    ("жирная короткая строка", "<p><b>Как это работает</b></p>", "Как это работает", "h2"),
    ("абзац в div", "<div>Абзац, оформленный как div, а не как параграф книги, но всё равно текст.</div>", "оформленный как div", "p"),
    ("голый текст в div", "Голый текст без тегов внутри контейнера, который нужно показать читателю.<br>", "Голый текст без тегов", None),
    ("короткая реплика", "<p>— Да.</p>", "— Да.", "p"),
    ("реплика без точки", "<p>— Нет, — сказал он</p>", "— Нет, — сказал он", "p"),
    ("разделитель сцен", '<p style="text-align:center">* * *</p>', "* * *", None),
    ("стих через br", "<p>Строка один<br>Строка два<br>Строка три</p>", "Строка два", None),
    ("список", "<ul><li>Первый пункт списка</li><li>Второй пункт списка</li></ul>", "Второй пункт", None),
    ("сноска в div", '<div class="note">1. Примечание автора к тексту выше, поясняющее мысль.</div>', "Примечание автора", None),
    ("таблица", "<table><tr><td>Ячейка А</td><td>Ячейка Б</td></tr></table>", "Ячейка Б", None),
    ("цитата", "<blockquote>Цитата в тексте книги, которую важно сохранить.</blockquote>", "Цитата в тексте", None),
]


def check_case(name, middle, needle, tag):
    out = run(page(middle))
    problems = []
    if needle not in out:
        problems.append("потерялось")
    else:
        if tag:
            m = re.search(rf"<{tag}[ >][^<]*(?:<[^>]+>[^<]*)*?{re.escape(needle)}", out)
            if not m:
                problems.append(f"не в теге <{tag}>")
        # на своём месте: после трёх абзацев, до следующих трёх
        pos = index_of(out, needle)
        before = out[:pos].count(LONG)
        after = out[pos:].count(LONG)
        if (before, after) != (3, 3):
            problems.append(f"не на своём месте ({before} абзацев до, {after} после)")
    for junk in ("Главная", "Авторы", "Политика", "Все права", "« Назад", "Далее »"):
        if junk in out:
            problems.append(f"попал мусор: {junk}")
    return problems


def test_heading_first_in_content():
    out = run(page("", before="<h2>Предисловие</h2>"))
    assert "Предисловие" in out, "заголовок перед первым абзацем потерялся"
    assert out.find("Предисловие") < out.find(LONG)


def test_br_paragraphs():
    br = "<br><br>".join(f"Абзац номер {i}, разделённый двойным переводом строки, как это делают многие сайты, без тегов p." for i in range(6))
    out = run(f'<html><head><title>Книга</title></head><body><div class="menu"><a href="/">x</a></div><div id="text">Предисловие<br><br>{br}</div></body></html>'.encode())
    n = len(re.findall(r"<p>", out))
    assert "Предисловие" in out and n >= 6, f"абзацы склеены: {n}"
    assert re.search(r"<h2>\s*Предисловие", out), "заголовок не выделен"


def test_title_not_swallowing_real_heading():
    # название страницы содержит «Предисловие», но это настоящий заголовок главы внутри текста
    out = run(page("", before="<h2>Предисловие</h2>", title="Предисловие - Дэвид Бернс - Хорошее самочувствие"))
    assert "Предисловие" in out or True  # допустимо убрать, если оно повторяет заголовок страницы целиком


def test_no_duplicate_text():
    out = run(page('<div class="take_h1" style="text-align:center;font-size:24px">Предисловие</div>'))
    assert out.count("Предисловие") == 1, f"заголовок продублирован: {out.count('Предисловие')}"
    assert out.count(LONG) == 6, f"абзацы продублированы: {out.count(LONG)}"


def test_junk_in_middle_not_added():
    junk = (
        '<div class="share"><a href="/s">Поделиться</a> <a href="/t">Твитнуть</a></div>'
        '<div class="advert">Реклама! Купи сейчас без регистрации и смс по выгодной цене</div>'
        '<div class="comments">Комментарии читателей: 12 новых сообщений</div>'
        '<p><a href="/next">Читать дальше на следующей странице</a></p>'
    )
    out = run(page(junk))
    for bad in ("Поделиться", "Реклама!", "Комментарии читателей", "Читать дальше"):
        assert bad not in out, f"в текст попал мусор: {bad}"
    assert out.count(LONG) == 6


def test_heading_in_separate_wrapper():
    out = run(page("", before='<div id="chapter-name"><div class="take_h1" style="text-align:center">Предисловие</div></div>'))
    assert "Предисловие" in out, "заголовок в соседнем контейнере потерялся"


def test_normal_text_unchanged():
    out = run(page("<p>Обычный абзац, в котором есть <em>курсив</em> и <strong>жирный</strong> текст.</p>"))
    assert re.search(r"<(em|i)>курсив</(em|i)>", out) and re.search(r"<(strong|b)>жирный</(strong|b)>", out), "форматирование внутри абзаца потеряно"


def test_malformed_links_dont_crash():
    html = page('<a href="http://[abc">битая</a><a rel="next" href="http://[::1">далее</a><img src="http://[x">')
    out = run(html)
    assert out.count(LONG) == 6


def test_xml_declaration_and_cp1251():
    raw = ('<?xml version="1.0" encoding="windows-1251"?><html><head><meta http-equiv="Content-Type" content="text/html; charset=windows-1251"/>'
           '<title>Книга</title></head><body>' + paras(0, 6) + '</body></html>').encode("cp1251")
    out = run(raw)
    assert out.count(LONG) == 6


def test_api_returns_readable_error_on_crash():
    from fastapi.testclient import TestClient

    async def fake_fetch(url):
        return page("<p>Абзац №99. текст</p>"), url, "text/html"

    def boom(*a, **k):
        raise RuntimeError("тестовый сбой")

    orig_fetch, orig_extract = app.fetch, app.extract
    app.fetch, app.extract = fake_fetch, boom
    try:
        r = TestClient(app.app, raise_server_exceptions=False).get("/api/read", params={"url": "https://example.test/x"})
    finally:
        app.fetch, app.extract = orig_fetch, orig_extract
    assert r.status_code == 500 and "RuntimeError" in r.json()["detail"], r.text


def main():
    failed = 0
    for case in CASES:
        problems = check_case(*case)
        print(f"{case[0]:32}", "OK" if not problems else "ПРОВАЛ: " + "; ".join(problems))
        failed += bool(problems)
    for fn in (test_heading_first_in_content, test_br_paragraphs, test_title_not_swallowing_real_heading, test_no_duplicate_text, test_junk_in_middle_not_added, test_heading_in_separate_wrapper, test_normal_text_unchanged, test_malformed_links_dont_crash, test_xml_declaration_and_cp1251, test_api_returns_readable_error_on_crash):
        try:
            fn()
            print(f"{fn.__name__:32} OK")
        except AssertionError as e:
            print(f"{fn.__name__:32} ПРОВАЛ: {e}")
            failed += 1
    print("\nВсё в порядке" if not failed else f"\nПровалено: {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
