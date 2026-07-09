# Шпаргалка по .venv

Виртуальное окружение — это отдельная папка `.venv` внутри проекта со своим
Python и своими пакетами. Нужна, чтобы ставить зависимости проекта, не трогая
системный Python (macOS/Homebrew это запрещает — отсюда ошибка
`externally-managed-environment`).

Все команды выполняются из папки проекта (`figma-localization`).

## Создать окружение — один раз на проект

```bash
python3 -m venv .venv
```

Создаёт папку `.venv`. Она в `.gitignore`, в git не попадает. Повторять не нужно.

## Войти в окружение — каждую новую сессию терминала

```bash
source .venv/bin/activate
```

После этого в начале строки терминала появляется `(.venv)` — признак,
что вы внутри. Теперь работают просто `python` и `pip` (без тройки) и без
флагов вроде `--break-system-packages`.

## Поставить зависимости

```bash
pip install -r requirements.txt
```

Делается после активации. Повторять при добавлении новых пакетов.

## Запустить скрипт

```bash
python export_figma.py hyperocket
```

## Выйти из окружения

```bash
deactivate
```

## Как понять, где я

- Есть префикс `(.venv)` в строке терминала → вы внутри, можно работать.
- Префикса нет → вы снаружи, сначала `source .venv/bin/activate`.
- Проверить точно: `which python` — путь должен вести в
  `.../figma-localization/.venv/bin/python`.

## Если что-то пошло не так

- `command not found: pip` вне окружения — на macOS системного `pip` нет,
  вне venv используйте `pip3`. Внутри venv — просто `pip`.
- `externally-managed-environment` — вы вне venv. Активируйте окружение
  (`source .venv/bin/activate`) и повторите.
- Забыли и закрыли терминал — окружение никуда не делось, просто снова
  выполните `source .venv/bin/activate` из папки проекта.

## Коротко: типичный старт работы

```bash
cd путь/к/figma-localization
source .venv/bin/activate
python export_figma.py hyperocket
```
