"""
translate.py — Перевод компактного файла через Gemini API

Переводит to_translate.csv (создан prepare.py) — только те уникальные тексты,
которых не было в памяти. Пишет перевод в тот же файл, в колонку figma_text.
Повторный запуск продолжает с места обрыва (уже заполненные строки пропускаются).

Логика перевода (модель, температура, пропуск чисел, защита от «думалок»)
перенесена из прежнего translate.py без изменений.

Промпт берётся из projects/<project>/prompts/<lang>.txt

После перевода — ВЫЧИТКА файла to_translate.csv вручную, затем:
    python finalize.py <project> <lang>

Использование:
    python translate.py hyperocket portuguese
"""

import argparse
import os
import sys
import time

import pandas as pd
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("GCP_API_KEY")

# Модель Gemini. По умолчанию — актуальная стабильная gemini-3.5-flash.
# Google периодически отключает старые модели (2.0 → 2.5 → 3.5), причём иногда
# раньше объявленной даты. Чтобы не переписывать код при следующей смене:
#   • можно переопределить через .env:  GEMINI_MODEL=gemini-3.5-flash
#   • или поставить алиас 'gemini-flash-latest' — Google сам держит его на
#     новейшей Flash-модели (но поведение может слегка меняться между версиями).
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")


# ─── Ядро перевода (перенесено без изменений) ─────────────────────────────────

def translate_text(text, prompt, client):
    if pd.isna(text) or not str(text).strip():
        return ""
    # Чистое целое число переводить не нужно. Более широкое условие (без букв
    # вообще) нельзя — оно блокирует конвертацию числовых нод вроде '+122.79%'
    # которые модель должна обработать (точка → запятая).
    if str(text).strip().isdigit():
        return str(text)
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=str(text),
            config=types.GenerateContentConfig(
                system_instruction=prompt,
                temperature=0.1,
            )
        )
        result = response.text.strip()
        if result.lower() == 'nan':
            return ''
        return result
    except Exception as e:
        print(f"  ⚠️  Ошибка: {e}")
        time.sleep(3)
        return ""


THOUGHT_PATTERNS = [
    'THOUGHT', 'Translation:', 'Note:', 'I need to', 'Let me',
    'The user', 'should be translated', "Let's ", 'I will ',
    'Here is', "Here's", 'In this case',
]


def check_thought_leaks(df, output_file):
    problems = []
    for idx, row in df.iterrows():
        en   = str(row.get('figma_text_en', ''))
        text = str(row['figma_text'])

        thought_hit = False
        for p in THOUGHT_PATTERNS:
            if p in text:
                problems.append({'row': idx, 'issue': f'thought_leak: {p!r}',
                                 'figma_text_en': en, 'figma_text': text[:300]})
                thought_hit = True
                break
        if thought_hit:
            continue

        if text.strip() in ('', 'nan') and en.strip() and any(c.isalpha() for c in en):
            problems.append({'row': idx, 'issue': 'empty_translation',
                             'figma_text_en': en, 'figma_text': text[:300]})
            continue

        if (text.strip() == en.strip()
                and len(en.strip()) > 25
                and ' ' in en.strip()
                and any(c.isalpha() for c in en)):
            problems.append({'row': idx, 'issue': 'not_translated (en==tr)',
                             'figma_text_en': en, 'figma_text': text[:300]})

    if not problems:
        print("\n✅ Проверка пройдена — пустых переводов и утечек не найдено.")
        return

    by_issue = {}
    for p in problems:
        by_issue.setdefault(p['issue'], []).append(p)

    print(f"\n⚠️  Найдено {len(problems)} проблемных строк:")
    for issue, items in by_issue.items():
        print(f"\n  [{issue}] — {len(items)} строк:")
        for p in items:
            print(f"    [{p['row']}] en: {p['figma_text_en'][:80]}")
            if p['figma_text'].strip() not in ('', 'nan'):
                print(f"           tr: {p['figma_text'][:80]}")

    report_file = output_file.replace('.csv', '-problems.csv')
    pd.DataFrame(problems).to_csv(report_file, index=False, quoting=1)
    print(f"\n📋 Отчёт: {report_file}")
    print(f"   Пустые переводы: запусти translate.py снова — дополнит пропущенные.")
    print(f"   Утечки думалок: исправь вручную в файле перевода.")


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Перевод to_translate.csv через Gemini.")
    ap.add_argument("project")
    ap.add_argument("lang")
    args = ap.parse_args()

    lang_dir    = os.path.join("projects", args.project, "translations", args.lang)
    target      = os.path.join(lang_dir, "to_translate.csv")
    prompt_file = os.path.join("projects", args.project, "prompts", f"{args.lang}.txt")

    if not os.path.exists(target):
        print(f"❌ Не найден {target}. Сначала: python prepare.py {args.project} {args.lang}")
        sys.exit(1)
    if not os.path.exists(prompt_file):
        print(f"❌ Не найден промпт: {prompt_file}")
        sys.exit(1)
    if not API_KEY:
        print("❌ Не задан GCP_API_KEY в .env")
        sys.exit(1)

    with open(prompt_file, encoding="utf-8") as f:
        prompt = f.read().strip()

    df = pd.read_csv(target, dtype=str).fillna("")
    if "figma_text" not in df.columns:
        df["figma_text"] = ""

    total   = len(df)
    already = (df["figma_text"].str.strip() != "").sum()
    print(f"Проект: {args.project} | Язык: {args.lang}")
    print(f"Модель: {MODEL}")
    print(f"Промпт: {prompt_file}")
    print(f"Всего: {total} | Переведено: {already} | Осталось: {total - already}\n")

    if total - already == 0:
        print("Всё уже переведено. Переходи к вычитке, затем finalize.py")
        return

    client = genai.Client(api_key=API_KEY)

    for index, row in df.iterrows():
        if str(row["figma_text"]).strip():
            continue
        en = row["figma_text_en"]
        print(f"[{index + 1}/{total}] {str(en)[:60]}...")
        df.at[index, "figma_text"] = translate_text(en, prompt, client)
        if index % 5 == 0:
            df.to_csv(target, index=False, quoting=1)
        time.sleep(2)

    df.to_csv(target, index=False, quoting=1)
    print(f"\n✅ Перевод завершён: {target}")

    print("\n🔍 Проверка на артефакты модели...")
    check_thought_leaks(df, target)

    print("\n👉 Дальше:")
    print(f"   1. Вычитай {target} вручную (правки прямо в колонке figma_text)")
    print(f"   2. python finalize.py {args.project} {args.lang}")


if __name__ == "__main__":
    main()
