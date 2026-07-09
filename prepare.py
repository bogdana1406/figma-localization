"""
prepare.py — Подготовка к переводу: дифф, память, дедупликация

Сердце инкрементального режима. Сравнивает свежий экспорт с предыдущим,
отсеивает то, что уже есть в памяти переводов, схлопывает дубли — и выдаёт
компактный файл уникальных текстов на перевод плюс карту раскладки для finalize.

Работает и «с нуля» (первый перевод): если предыдущего экспорта и памяти нет,
все узлы считаются новыми и просто дедуплицируются.

Режимы выбора пары экспортов:
  • по умолчанию берёт два самых свежих экспорта по имени-дате и показывает выбор;
  • --old / --new — указать файлы явно.

Вход:
  projects/<project>/exports/*.csv          — снимки английского макета
  projects/<project>/translations/<lang>/memory.csv   — память (может отсутствовать)

Выход:
  projects/<project>/translations/<lang>/to_translate.csv  — уникальные тексты на перевод
  projects/<project>/translations/<lang>/layout.csv        — карта всех узлов нового экспорта

Использование:
  python prepare.py hyperocket portuguese
  python prepare.py hyperocket portuguese --old .../1154_export.csv --new .../1302_export.csv
"""

import argparse
import csv
import os
import sys
import glob

import pandas as pd


# ─── Нормализация текста для сравнения и ключей памяти ────────────────────────

def normalize(text):
    """
    Убираем только «шум»: хвостовые пробелы/табы в конце каждой строки и в
    конце всего текста. Значимые переносы сохраняем — и \\n, и \\u2028
    (Figma использует \\u2028 как мягкий перенос внутри абзаца).
    """
    if text is None or (isinstance(text, float)):
        return ""
    s = str(text)
    # обе разновидности переноса приводим к общему виду ТОЛЬКО для разбиения,
    # но при склейке возвращаем \n — сравнение идёт по нормализованному виду.
    # Важно: \u2028 остаётся значимым (меняется перенос → меняется текст),
    # поэтому не схлопываем \u2028 и \n в одно, а различаем их.
    parts = s.split("\n")
    parts = [p.rstrip(" \t") for p in parts]
    return "\n".join(parts).rstrip("\n ")


# ─── Загрузка экспортов ───────────────────────────────────────────────────────

def find_two_latest(export_dir):
    """Возвращает (old_path, new_path) — два свежайших экспорта по имени-дате."""
    files = sorted(glob.glob(os.path.join(export_dir, "*_export.csv")))
    return files


def load_export(path):
    df = pd.read_csv(path, dtype=str).fillna("")
    df["id"] = df["id"].str.strip()
    return df


# ─── Память ───────────────────────────────────────────────────────────────────

def load_memory(path):
    """Возвращает dict {нормализованный_en: перевод}. Нет файла → пустая память."""
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path, dtype=str).fillna("")
    return {normalize(en): tr for en, tr in zip(df["figma_text_en"], df["figma_text"])}


# ─── Дифф ─────────────────────────────────────────────────────────────────────

def classify(new_df, old_df):
    """
    Размечает каждый узел нового экспорта категорией:
      unchanged — тот же id, тот же текст (по normalize)
      changed   — тот же id, текст изменился
      new       — id не было в старом экспорте
    Возвращает список dict-строк с добавленным полем 'category'.
    Плюс отдельно множество 'deleted' id (были в старом, нет в новом) — для отчёта.
    """
    old_text = {}
    if old_df is not None:
        old_text = {row["id"]: normalize(row["figma_text_en"]) for _, row in old_df.iterrows()}

    rows = []
    for _, r in new_df.iterrows():
        nid = r["id"]
        cur = normalize(r["figma_text_en"])
        if nid not in old_text:
            cat = "new"
        elif old_text[nid] != cur:
            cat = "changed"
        else:
            cat = "unchanged"
        rows.append({**r.to_dict(), "category": cat})

    deleted = set(old_text) - set(new_df["id"]) if old_df is not None else set()
    return rows, deleted


# ─── Основная логика ──────────────────────────────────────────────────────────

def build(rows, memory):
    """
    По размеченным узлам и памяти формирует:
      layout      — карта всех узлов: + колонки category, source, figma_text
      to_translate— уникальные нормализованные тексты, которых нет в памяти

    source (откуда взят перевод):
      memory  — найден в памяти
      keep    — unchanged, перевод будет взят из прошлого перевода на finalize
      pending — нужно перевести (уйдёт в to_translate)
    """
    layout = []
    pending_texts = {}   # нормализованный en → исходный en (для файла на перевод)

    for row in rows:
        en_norm = normalize(row["figma_text_en"])
        cat = row["category"]

        if cat == "unchanged":
            source, translated = "keep", ""          # finalize возьмёт из старого перевода
        elif en_norm in memory:
            source, translated = "memory", memory[en_norm]
        else:
            source, translated = "pending", ""
            if en_norm != "":
                pending_texts.setdefault(en_norm, row["figma_text_en"])

        layout.append({
            "id":            row["id"],
            "page":          row.get("page", ""),
            "frame":         row.get("frame", ""),
            "group":         row.get("group", ""),
            "layer_name":    row.get("layer_name", ""),
            "figma_text_en": row["figma_text_en"],
            "file_key":      row.get("file_key", ""),
            "category":      cat,
            "source":        source,
            "figma_text":    translated,
        })

    to_translate = [{"figma_text_en": en, "figma_text": ""} for en in pending_texts.values()]
    return layout, to_translate


# ─── Запись ───────────────────────────────────────────────────────────────────

LAYOUT_COLS = ["id", "page", "frame", "group", "layer_name",
               "figma_text_en", "file_key", "category", "source", "figma_text"]


def write_csv(path, rows, cols):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(rows)


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Дифф экспортов + отсев по памяти + дедуп.")
    ap.add_argument("project")
    ap.add_argument("lang")
    ap.add_argument("--old", help="Путь к предыдущему экспорту (иначе — авто по дате)")
    ap.add_argument("--new", help="Путь к новому экспорту (иначе — авто по дате)")
    ap.add_argument("--yes", action="store_true", help="Не спрашивать подтверждения выбора пары")
    args = ap.parse_args()

    export_dir = os.path.join("projects", args.project, "exports")
    lang_dir   = os.path.join("projects", args.project, "translations", args.lang)
    memory_path = os.path.join(lang_dir, "memory.csv")

    # --- выбор экспортов ---
    if args.new:
        new_path = args.new
        old_path = args.old   # может быть None → первый перевод
    else:
        exports = find_two_latest(export_dir)
        if not exports:
            print(f"❌ В {export_dir} нет файлов *_export.csv"); sys.exit(1)
        new_path = exports[-1]
        old_path = exports[-2] if len(exports) >= 2 else None

    new_df = load_export(new_path)
    old_df = load_export(old_path) if old_path and os.path.exists(old_path) else None

    # --- показать выбор ---
    print(f"Проект: {args.project} | Язык: {args.lang}\n")
    if old_df is not None:
        print(f"Предыдущий: {old_path}")
        print(f"            {len(old_df)} узлов | file_key {old_df['file_key'].iloc[0]}")
    else:
        print("Предыдущий: НЕТ — режим первого перевода (все узлы новые)")
    print(f"Новый:      {new_path}")
    print(f"            {len(new_df)} узлов | file_key {new_df['file_key'].iloc[0]}")

    memory = load_memory(memory_path)
    print(f"\nПамять: {len(memory)} пар" + ("" if memory else " (пусто/нет файла)"))

    if not args.yes:
        ans = input("\nПродолжить с этой парой? [y/N] ").strip().lower()
        if ans != "y":
            print("Отменено."); sys.exit(0)

    # --- дифф и сборка ---
    rows, deleted = classify(new_df, old_df)
    layout, to_translate = build(rows, memory)

    # --- отчёт по категориям ---
    from collections import Counter
    cats = Counter(r["category"] for r in layout)
    srcs = Counter(r["source"] for r in layout)
    print("\n─ Категории узлов нового экспорта ─")
    print(f"  без изменений: {cats.get('unchanged',0)}")
    print(f"  изменённые:    {cats.get('changed',0)}")
    print(f"  новые:         {cats.get('new',0)}")
    if deleted:
        print(f"  удалены (были в старом, нет в новом): {len(deleted)}")
    print("\n─ Источник перевода ─")
    print(f"  из памяти:        {srcs.get('memory',0)}")
    print(f"  перенос старого:  {srcs.get('keep',0)}")
    print(f"  на перевод:       {srcs.get('pending',0)}")
    print(f"\n  → уникальных текстов на перевод (после дедупа): {len(to_translate)}")

    # --- запись ---
    layout_path = os.path.join(lang_dir, "layout.csv")
    tt_path     = os.path.join(lang_dir, "to_translate.csv")
    write_csv(layout_path, layout, LAYOUT_COLS)
    write_csv(tt_path, to_translate, ["figma_text_en", "figma_text"])

    print(f"\n💾 Карта раскладки: {layout_path}  ({len(layout)} строк)")
    print(f"💾 На перевод:      {tt_path}  ({len(to_translate)} строк)")
    print("\n👉 Дальше:")
    if to_translate:
        print(f"   python translate.py {args.project} {args.lang}   (переведёт to_translate.csv)")
        print(f"   затем вычитка, затем: python finalize.py {args.project} {args.lang}")
    else:
        print("   Переводить нечего — всё покрыто памятью. Сразу:")
        print(f"   python finalize.py {args.project} {args.lang}")


if __name__ == "__main__":
    main()
