"""
build_memory.py — Сборка памяти переводов из вычитанного файла

Разовая операция при заведении памяти для языка (или при переносе старых
переводов в систему). Читает уже ВЫЧИТАННЫЙ файл перевода и собирает
memory.csv — пары «английский текст → перевод».

Память версие-независима: хранит только текст, без id и file_key.
Ключ — английский текст (figma_text_en), значение — перевод (figma_text).

При расхождениях (один английский текст → несколько разных переводов внутри
файла) побеждает самый частый вариант; при равенстве — последний встреченный.
Такие расхождения показываются в отчёте, т.к. это признак недедуплицированного
старого перевода.

Вход  (по умолчанию): projects/<project>/translations/<lang>/hyperocket-<lang>.csv
       нужны колонки figma_text_en, figma_text
Выход: projects/<project>/translations/<lang>/memory.csv

Использование:
    python build_memory.py hyperocket portuguese
    python build_memory.py hyperocket portuguese --from путь/к/переводу.csv
"""

import argparse
import csv
import os
import sys
from collections import Counter, defaultdict

import pandas as pd


def normalize_en(text):
    """
    Нормализация английского ключа для памяти.

    Убираем только «шум»: пробелы/табы в самом конце текста и в конце
    каждой строки перед переносом. Значимые переносы \\n сохраняем —
    они часть смысла (намеренная разметка дизайнера).
    """
    if text is None:
        return ""
    lines = str(text).split("\n")
    lines = [ln.rstrip(" \t") for ln in lines]   # trailing whitespace каждой строки
    return "\n".join(lines).rstrip("\n ")         # хвостовые переносы/пробелы всего текста


def build_memory(df):
    """
    Возвращает (memory_dict, conflicts).

    memory_dict: {нормализованный_en: перевод}
    conflicts:   [(en, {перевод: количество}), ...] — где переводов было >1
    """
    # Для каждого английского ключа собираем счётчик переводов
    variants = defaultdict(Counter)
    for _, row in df.iterrows():
        en = normalize_en(row["figma_text_en"])
        tr = str(row["figma_text"]) if not pd.isna(row["figma_text"]) else ""
        if en == "":
            continue                       # пустой исходник в память не берём
        variants[en][tr] += 1

    memory = {}
    conflicts = []
    for en, counter in variants.items():
        if len(counter) > 1:
            conflicts.append((en, dict(counter)))
        # most_common упорядочивает по убыванию частоты; при равенстве
        # сохраняется порядок вставки → «последний встреченный» окажется
        # среди равных первым не гарантированно, поэтому берём победителя явно.
        winner = counter.most_common(1)[0][0]
        memory[en] = winner

    return memory, conflicts


def main():
    parser = argparse.ArgumentParser(description="Собрать память переводов из вычитанного файла.")
    parser.add_argument("project", help="Имя проекта (папка в projects/)")
    parser.add_argument("lang", help="Язык (папка в translations/)")
    parser.add_argument("--from", dest="src", help="Путь к файлу перевода (если не стандартный)")
    args = parser.parse_args()

    lang_dir = os.path.join("projects", args.project, "translations", args.lang)
    src = args.src or os.path.join(lang_dir, f"hyperocket-{args.lang}.csv")
    out = os.path.join(lang_dir, "memory.csv")

    if not os.path.exists(src):
        print(f"❌ Файл перевода не найден: {src}")
        print("   Положи вычитанный перевод по этому пути или укажи --from.")
        sys.exit(1)

    df = pd.read_csv(src, dtype=str).fillna("")
    for col in ("figma_text_en", "figma_text"):
        if col not in df.columns:
            print(f"❌ В файле нет колонки {col!r}. Колонки: {df.columns.tolist()}")
            sys.exit(1)

    print(f"Проект: {args.project} | Язык: {args.lang}")
    print(f"Источник: {src}")
    print(f"Строк во входе: {len(df)}")

    memory, conflicts = build_memory(df)

    print(f"\nУникальных английских текстов в памяти: {len(memory)}")
    if conflicts:
        print(f"\n⚠️  Расхождений (один текст → разные переводы): {len(conflicts)}")
        print("   Оставлен самый частый вариант. Проверь, если критично:")
        for en, counter in conflicts[:15]:
            print(f"\n   en: {en[:70]!r}")
            for tr, n in sorted(counter.items(), key=lambda x: -x[1]):
                mark = "  ← оставлен" if tr == memory[en] else ""
                print(f"      x{n}: {tr[:60]!r}{mark}")
        if len(conflicts) > 15:
            print(f"\n   … и ещё {len(conflicts) - 15}")
    else:
        print("✅ Расхождений нет — перевод единообразен.")

    os.makedirs(lang_dir, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow(["figma_text_en", "figma_text"])
        for en, tr in memory.items():
            writer.writerow([en, tr])

    print(f"\n💾 Память сохранена: {out}")
    print(f"   Пар: {len(memory)}")


if __name__ == "__main__":
    main()
