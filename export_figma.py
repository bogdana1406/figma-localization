"""
export_figma.py — Выгрузка текстовых узлов из Figma через REST API

Заменяет ручной экспорт CopyDoc. Читает файл Figma, обходит выбранные
страницы и собирает все текстовые узлы (TEXT) в CSV, пригодный для
дальнейшего перевода.

Проект (файл Figma + какие страницы брать) описывается в projects.json.

Использование:
    python export_figma.py hyperocket
    python export_figma.py hyperocket --pages Presentation "Logo Guidelines"
    python export_figma.py hyperocket --all-pages

Требуется переменная окружения FIGMA_TOKEN (в .env).
Токену достаточно прав «File content: read».

Результат:
    projects/<project>/exports/<YYYY-MM-DD_HHMM>_export.csv   — снимок с датой
    projects/<project>/exports/latest.csv                     — тот же снимок под стабильным именем

Колонки CSV:
    page, frame, group, layer_name, id, figma_text_en, file_key

    id            — идентификатор текстового узла (главный ключ для импорта)
    figma_text_en — исходный текст узла (то, что будем переводить)
    file_key      — ключ файла-источника (нужен плагину для проверки «тот ли файл»)
"""

import argparse
import csv
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

FIGMA_TOKEN   = os.getenv("FIGMA_TOKEN")
PROJECTS_FILE = "projects.json"

# Значения, при которых список страниц трактуется как «все страницы».
ALL_PAGES_MARKERS = {"*", "all", "все"}


# ─── Работа с Figma API ──────────────────────────────────────────────────────

def figma_get_file(file_key):
    """Загружает полное дерево файла Figma. Возвращает распарсенный JSON."""
    url = f"https://api.figma.com/v1/files/{file_key}"
    req = urllib.request.Request(url, headers={"X-Figma-Token": FIGMA_TOKEN})
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        _explain_http_error(e, file_key)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"❌ Не удалось подключиться к Figma API: {e.reason}")
        sys.exit(1)


def _explain_http_error(e, file_key):
    """Понятное объяснение кодов ответа Figma."""
    if e.code == 403:
        print("❌ 403 Forbidden — токен неверный или у него нет доступа к файлу.")
        print("   Проверь FIGMA_TOKEN в .env и что файл открыт этому аккаунту.")
    elif e.code == 404:
        print(f"❌ 404 Not Found — файл с ключом '{file_key}' не найден.")
        print("   Проверь file_key в projects.json (часть URL между /design/ и названием).")
    elif e.code == 429:
        print("❌ 429 Too Many Requests — превышен лимит запросов Figma. Подожди минуту.")
    else:
        print(f"❌ HTTP {e.code}: {e.reason}")


# ─── Обход дерева ─────────────────────────────────────────────────────────────

def unwrap_sections(node):
    """
    Возвращает «уровень слайдов» страницы.

    Прямые дети страницы — это слайды/фреймы. Но иногда слайды сгруппированы
    в SECTION. Тогда слайды — это дети секции. Разворачиваем секции (в т.ч.
    вложенные), чтобы за «слайд» принимался реальный фрейм, а не секция-обёртка.
    """
    if node.get("type") == "SECTION":
        for child in node.get("children", []):
            yield from unwrap_sections(child)
    else:
        yield node


def collect_text_nodes(page):
    """Собирает все TEXT-узлы страницы с контекстом (frame, group)."""
    results = []
    page_name = page.get("name", "")

    # Прямые дети страницы = слайды/фреймы (секции разворачиваем до реальных фреймов)
    frame_level = []
    for child in page.get("children", []):
        frame_level.extend(unwrap_sections(child))

    for frame_node in frame_level:
        # frame_node — слайд/фрейм верхнего уровня (или текст прямо на холсте)
        if frame_node.get("type") == "TEXT":
            frame_name = ""            # текст лежит вне слайда
        else:
            frame_name = frame_node.get("name", "")
        _recurse(frame_node, frame_name=frame_name, group_name="",
                 page_name=page_name, results=results)

    return results


def _recurse(node, frame_name, group_name, page_name, results):
    node_type = node.get("type", "")

    if node_type == "TEXT":
        results.append({
            "page":          page_name,
            "frame":         frame_name,
            "group":         group_name,
            "layer_name":    node.get("name", ""),
            "id":            node.get("id", ""),
            "figma_text_en": node.get("characters", ""),
        })
        return  # у TEXT нет интересных нам детей

    # Запоминаем имя ближайшей группы-предка (как делал CopyDoc в колонке group)
    if node_type == "GROUP":
        group_name = node.get("name", "")

    for child in node.get("children", []):
        _recurse(child, frame_name, group_name, page_name, results)


# ─── Выбор страниц ────────────────────────────────────────────────────────────

def resolve_pages(document, requested):
    """
    Возвращает список страниц (узлов CANVAS) для обхода.

    requested — None или список имён. None/маркеры → все страницы.
    Если запрошенное имя не найдено — сообщаем и выходим (защита от опечаток).
    """
    all_pages = document.get("children", [])
    by_name = {p.get("name", ""): p for p in all_pages}

    take_all = (
        requested is None
        or any(str(r).strip().lower() in ALL_PAGES_MARKERS for r in requested)
    )
    if take_all:
        return all_pages

    selected = []
    missing = []
    for name in requested:
        if name in by_name:
            selected.append(by_name[name])
        else:
            missing.append(name)

    if missing:
        print(f"❌ Страницы не найдены в файле: {missing}")
        print(f"   Доступные страницы: {list(by_name.keys())}")
        sys.exit(1)

    return selected


# ─── Конфиг проекта ───────────────────────────────────────────────────────────

def load_project(project_name):
    if not os.path.exists(PROJECTS_FILE):
        print(f"❌ Не найден {PROJECTS_FILE}")
        sys.exit(1)

    with open(PROJECTS_FILE, encoding="utf-8") as f:
        projects = json.load(f)

    if project_name not in projects:
        print(f"❌ Проект '{project_name}' не найден в {PROJECTS_FILE}.")
        print(f"   Доступные проекты: {list(projects.keys())}")
        sys.exit(1)

    return projects[project_name]


# ─── Запись результата ────────────────────────────────────────────────────────

COLUMNS = ["page", "frame", "group", "layer_name", "id", "figma_text_en", "file_key"]


def write_csv(path, rows, file_key):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for r in rows:
            r = dict(r)
            r["file_key"] = file_key
            writer.writerow(r)


# ─── Отчёт ────────────────────────────────────────────────────────────────────

def print_summary(rows, pages):
    print(f"\nВсего текстовых узлов: {len(rows)}")

    per_page = {}
    empty_frame = 0
    for r in rows:
        per_page[r["page"]] = per_page.get(r["page"], 0) + 1
        if r["frame"] == "":
            empty_frame += 1

    print("По страницам:")
    for name in [p.get("name", "") for p in pages]:
        print(f"  {per_page.get(name, 0):5d}  {name!r}")

    if empty_frame:
        print(f"\n⚠️  {empty_frame} узлов вне слайдов (frame пустой) — "
              f"текст лежит прямо на холсте страницы. На импорт по id это не влияет.")


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Экспорт текстовых узлов из Figma в CSV.")
    parser.add_argument("project", help="Имя проекта из projects.json")
    parser.add_argument("--pages", nargs="+", metavar="NAME",
                        help="Переопределить список страниц (через пробел, имена в кавычках)")
    parser.add_argument("--all-pages", action="store_true",
                        help="Взять все страницы файла")
    args = parser.parse_args()

    if not FIGMA_TOKEN:
        print("❌ Не задан FIGMA_TOKEN. Добавь его в .env:")
        print("   FIGMA_TOKEN=figd_...")
        sys.exit(1)

    project = load_project(args.project)
    file_key = project["file_key"]

    # Приоритет выбора страниц: --all-pages > --pages > projects.json
    if args.all_pages:
        requested = None
    elif args.pages:
        requested = args.pages
    else:
        requested = project.get("pages")  # None → все страницы

    print(f"Проект:   {args.project}")
    print(f"file_key: {file_key}")
    print("Подключаемся к Figma API...")

    data = figma_get_file(file_key)
    document = data["document"]
    print(f"Файл: {data.get('name')!r}")

    pages = resolve_pages(document, requested)
    print(f"Страниц к обходу: {len(pages)} — {[p.get('name', '') for p in pages]}")

    rows = []
    for page in pages:
        rows.extend(collect_text_nodes(page))

    print_summary(rows, pages)

    timestamp   = datetime.now().strftime("%Y-%m-%d_%H%M")
    export_dir  = os.path.join("projects", args.project, "exports")
    stamped     = os.path.join(export_dir, f"{timestamp}_export.csv")
    latest      = os.path.join(export_dir, "latest.csv")

    write_csv(stamped, rows, file_key)
    write_csv(latest,  rows, file_key)

    print(f"\n💾 Снимок:  {stamped}")
    print(f"💾 Latest:  {latest}")
    print("\n👉 Дальше (когда соберём второй батч):")
    print(f"   python dedup.py {args.project}")


if __name__ == "__main__":
    main()
