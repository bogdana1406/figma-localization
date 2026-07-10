"""
make_design.py — Файл для дизайнера (сверка перевода)

Берёт финальный перевод, убирает строки где английский и перевод идентичны
(их проверять не нужно — бренды, числа и т.п.), сортирует по слайдам.
Оставляет колонку category, чтобы дизайнер мог отфильтровать только новое/
изменённое при обновлении презентации.

Вход:  projects/<project>/translations/<lang>/hyperocket-<lang>.csv
Выход: projects/<project>/translations/<lang>/hyperocket-<lang>-design-<дата>.csv

Использование:
    python make_design.py hyperocket portuguese
"""

import argparse
import csv
import os
import sys
from datetime import datetime

import pandas as pd


def slide_sort_key(frame):
    """Числовые слайды — по возрастанию, нечисловые — в конце по алфавиту."""
    s = str(frame).strip()
    if s.isdigit():
        return (0, int(s), "")
    return (1, 0, s)


def main():
    ap = argparse.ArgumentParser(description="Файл для дизайнера: перевод для сверки.")
    ap.add_argument("project")
    ap.add_argument("lang")
    args = ap.parse_args()

    lang_dir   = os.path.join("projects", args.project, "translations", args.lang)
    input_file = os.path.join(lang_dir, f"hyperocket-{args.lang}.csv")

    if not os.path.exists(input_file):
        print(f"❌ Файл не найден: {input_file}")
        print(f"   Сначала запусти: python finalize.py {args.project} {args.lang}")
        sys.exit(1)

    df = pd.read_csv(input_file, dtype=str).fillna("")

    # На случай старого файла без category — не падаем
    if "category" not in df.columns:
        df["category"] = ""

    # Убираем строки, где английский и перевод идентичны (проверять нечего)
    identical = df["figma_text_en"].str.strip() == df["figma_text"].str.strip()
    removed = int(identical.sum())
    df = df[~identical].copy()

    # Сортировка по слайду (числовая), сохраняя исходный порядок внутри слайда
    df["_order"] = range(len(df))
    df["_key"] = df["frame"].map(slide_sort_key)
    df = df.sort_values(["_key", "_order"]).drop(columns=["_key", "_order"]).reset_index(drop=True)

    out_cols = ["frame", "id", "group", "layer_name", "figma_text_en", "figma_text", "category"]
    out_df = df[out_cols].copy()

    stamp = datetime.now().strftime("%Y-%m-%d")
    output_file = os.path.join(lang_dir, f"hyperocket-{args.lang}-design-{stamp}.csv")
    out_df.to_csv(output_file, index=False, quoting=csv.QUOTE_ALL, encoding="utf-8")

    # Небольшая сводка по категориям — чтобы видеть, сколько нового/изменённого
    from collections import Counter
    cats = Counter(out_df["category"])

    print(f"✅ Файл для дизайнера:")
    print(f"   {output_file}")
    print(f"   Строк: {len(out_df)} (убрано идентичных: {removed})")
    if any(cats.values()):
        parts = []
        for c in ("new", "changed", "unchanged"):
            if cats.get(c):
                parts.append(f"{c}: {cats[c]}")
        if parts:
            print(f"   По категориям: {', '.join(parts)}")
    print(f"   Дизайнер может отфильтровать в таблице по колонке category.")


if __name__ == "__main__":
    main()
