# BiblioAtom CP Extractor

Набор инструментов для извлечения полного текста книг с читалки
[elib.biblioatom.ru](https://elib.biblioatom.ru) через внутренний RPC-эндпоинт
`/rpc/bookviewer/cp/`.

## Состав

- `biblioatom-extractor.user.js` — Tampermonkey-userscript с панелью управления:
  обход страниц книги через RPC, выбор диапазона, режимы вывода и сохранение/копирование.
- `convert_book.py` — постобработка сохранённого Raw JSON в чистый текст.

## Как это работает

Читалка отдаёт текст каждой страницы по адресу:
https://elib.biblioatom.ru/rpc/bookviewer/cp/?url=<BOOK_ID>&page=<N>


Ответ — JSON:

```json
{
  "valid": true,
  "pagetext": "чистый текст страницы",
  "pagehtml": "<p class=\"page\">5</p><div class=\"comp-draft\">...</div>"
}
```

Нумерация страниц начинается с `0` (обложка). `BOOK_ID` берётся из
`data-settings` корневого элемента `.bookviewer-root` (например, `kapitsa_1994`).

## Установка userscript

1. Установите расширение [Tampermonkey](https://www.tampermonkey.net/).
2. Создайте новый скрипт и вставьте содержимое `biblioatom-extractor.user.js`.
3. Откройте страницу книги, например
   `https://elib.biblioatom.ru/text/kapitsa_1994/p0/`.
4. В правом верхнем углу появится панель **BiblioAtom CP Extractor**.

## Использование

| Поле     | Назначение                                   |
|----------|----------------------------------------------|
| Book ID  | Идентификатор книги (`kapitsa_1994`)         |
| From / To| Диапазон страниц (с 0)                        |
| Mode     | `Text` / `HTML` / `Raw JSON`                 |
| Delay ms | Пауза между запросами (вежливость к серверу) |

Кнопки: **Старт**, **Стоп**, **Сохранить файл**, **Копировать**.

### Режимы вывода

- **Text** — только `pagetext`, чистый текст книги.
- **HTML** — `pagehtml` с разметкой (`<p class="page">`, `<div class="comp-draft">`).
- **Raw JSON** — полный ответ RPC, для отладки и постобработки.

## Конвертер

```bash
python convert_book.py <input.json> [output.txt]
```

Извлекает `pagetext` из сохранённого Raw JSON и склеивает страницы в один файл.

## Дисклеймер

Инструмент предназначен для личного использования (чтение, офлайн-доступ,
обработка). Соблюдайте условия использования источника и авторские права
на публикуемые материалы.

