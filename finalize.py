"""
finalize.py — Финальная сборка перевода

Собирает полный переведённый файл под НОВЫЙ макет из трёх источников:
  • layout.csv        — карта всех узлов нового экспорта (id, file_key, категория)
  • to_translate.csv  — свежевычитанные переводы новых/изменённых текстов
  • memory.csv        — утверждённые переводы прошлых версий

Правило наполнения (единый источник истины — память + свежевычитанное):
  • перевод для каждого узла берётся из памяти по нормализованному тексту;
  • свежевычитанное (to_translate) вливается в память ПЕРЕД наполнением,
    по правилу «последний утверждённый побеждает» — поэтому новое и
    изменённое подхватывается автоматически.

Перед сборкой — проверка целостности: все ли pending-узлы получили перевод,
совпадает ли английский в to_translate с layout, нет ли пустых.

Выход:
  projects/<project>/translations/<lang>/hyperocket-<lang>.csv   — полный файл под новый макет
  memory.csv обновляется; прежний финал сохраняется в бэкап.

Использование:
    python finalize.py hyperocket portuguese
"""

import argparse
import csv
import os
import shutil
import sys
from datetime import datetime

import pandas as pd


# ─── Нормализация (та же, что в prepare/build_memory) ─────────────────────────

def normalize(text):
    if text is None or isinstance(text, float):
        return ""
    parts = str(text).split("\n")
    parts = [p.rstrip(" \t") for p in parts]
    return "\n".join(parts).rstrip("\n ")


def is_number(text):
    """Чистое целое — перевод = сам текст (как в translate.py)."""
    return str(text).strip().isdigit()


# ─── Загрузки ─────────────────────────────────────────────────────────────────

def load_memory(path):
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path, dtype=str).fillna("")
    return {normalize(en): tr for en, tr in zip(df["figma_text_en"], df["figma_text"])}


def save_memory(path, memory):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(["figma_text_en", "figma_text"])
        for en, tr in memory.items():
            w.writerow([en, tr])


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Финальная сборка перевода под новый макет.")
    ap.add_argument("project")
    ap.add_argument("lang")
    ap.add_argument("--yes", action="store_true", help="Не спрашивать подтверждения при предупреждениях")
    args = ap.parse_args()

    lang_dir    = os.path.join("projects", args.project, "translations", args.lang)
    layout_path = os.path.join(lang_dir, "layout.csv")
    tt_path     = os.path.join(lang_dir, "to_translate.csv")
    memory_path = os.path.join(lang_dir, "memory.csv")
    final_path  = os.path.join(lang_dir, f"hyperocket-{args.lang}.csv")

    if not os.path.exists(layout_path):
        print(f"❌ Нет {layout_path}. Сначала: python prepare.py {args.project} {args.lang}")
        sys.exit(1)

    layout = pd.read_csv(layout_path, dtype=str).fillna("")
    memory = load_memory(memory_path)

    # to_translate может отсутствовать, если всё покрыла память
    if os.path.exists(tt_path):
        tt = pd.read_csv(tt_path, dtype=str).fillna("")
    else:
        tt = pd.DataFrame(columns=["figma_text_en", "figma_text"])

    print(f"Проект: {args.project} | Язык: {args.lang}")
    print(f"Узлов в макете: {len(layout)} | Память: {len(memory)} пар | Вычитано: {len(tt)}\n")

    # ── 1. Влить свежевычитанное в память (последний побеждает) ──
    added = 0
    for _, r in tt.iterrows():
        en = normalize(r["figma_text_en"])
        tr = str(r["figma_text"])
        if en == "":
            continue
        memory[en] = tr        # перезапись = «последний утверждённый побеждает»
        added += 1
    print(f"Влито в память из вычитки: {added}")

    # ── 2. Проверка целостности ──
    problems = []
    for _, row in layout.iterrows():
        en_raw  = row["figma_text_en"]
        en_norm = normalize(en_raw)

        if en_norm == "":
            continue                       # пустой исходник — пустой перевод, это норма
        if is_number(en_raw):
            continue                       # число не требует перевода

        if en_norm not in memory or memory[en_norm].strip() == "":
            problems.append({
                "id": row["id"], "frame": row.get("frame", ""),
                "category": row.get("category", ""),
                "figma_text_en": en_raw[:80],
            })

    if problems:
        print(f"\n⚠️  {len(problems)} узлов без перевода (нет в памяти/пусто):")
        for p in problems[:15]:
            print(f"   [{p['category']}] id={p['id']} frame={p['frame']}: {p['figma_text_en']!r}")
        if len(problems) > 15:
            print(f"   … и ещё {len(problems) - 15}")
        print("\n   Причина обычно: строку не вычитали, или текст испортился при правке")
        print("   (Google Sheets мог съесть перенос — сравни с layout.csv).")
        if not args.yes:
            ans = input("\nВсё равно собрать файл (пустые останутся пустыми)? [y/N] ").strip().lower()
            if ans != "y":
                print("Отменено. Поправь to_translate.csv и запусти снова.")
                sys.exit(0)
    else:
        print("✅ Все узлы обеспечены переводом.")

    # ── 3. Сборка финального файла ──
    out_rows = []
    for _, row in layout.iterrows():
        en_raw  = row["figma_text_en"]
        en_norm = normalize(en_raw)

        if en_norm == "":
            translated = ""
        elif is_number(en_raw):
            translated = str(en_raw)                # число = само себя
        else:
            translated = memory.get(en_norm, "")

        out_rows.append({
            "frame":         row.get("frame", ""),
            "id":            row["id"],
            "group":         row.get("group", ""),
            "layer_name":    row.get("layer_name", ""),
            "figma_text_en": en_raw,
            "figma_text":    translated,
            "file_key":      row.get("file_key", ""),
        })

    # ── 4. Бэкап прежнего финала ──
    if os.path.exists(final_path):
        ts = datetime.now().strftime("%Y-%m-%d_%H%M")
        backup = os.path.join(lang_dir, f"hyperocket-{args.lang}.backup-{ts}.csv")
        shutil.copy2(final_path, backup)
        print(f"\n🗄  Прежний финал сохранён: {backup}")

    # ── 5. Запись ──
    cols = ["frame", "id", "group", "layer_name", "figma_text_en", "figma_text", "file_key"]
    with open(final_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(out_rows)

    save_memory(memory_path, memory)

    print(f"\n💾 Финал: {final_path}  ({len(out_rows)} узлов)")
    print(f"💾 Память обновлена: {memory_path}  ({len(memory)} пар)")
    print("\n👉 Дальше: импорт в Figma через плагин (третий батч).")


if __name__ == "__main__":
    main()
