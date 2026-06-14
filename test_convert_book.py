#!/usr/bin/env python3
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from convert_book import (
    build_book_models,
    build_fb2,
    build_html,
    build_epub,
    clean_pagehtml,
    extract_blocks_from_html,
    is_front_matter_heading,
    is_probable_author_line,
    is_probable_heading,
    merge_empty_front_matter,
    normalize_text,
    output_stem,
    page_to_model,
    parse_embedded_content,
    read_source,
    remove_leading_print_page_number,
    should_start_chapter,
    slugify,
    split_into_chapters,
    strip_heading_marks,
    normalized_heading_key,
    strip_tags_preserve_text,
)


SAMPLE_JSON = {
    "title": "Тестовая книга",
    "book_id": "test_book",
    "source": "https://elib.biblioatom.ru/text/test_book/",
    "page_range": [0, 2],
    "mode": "json",
    "generated_at": "2024-01-01T00:00:00Z",
    "items": [
        {"page": 0, "content": json.dumps({"valid": True, "pagetext": "", "pagehtml": ""})},
        {"page": 1, "content": json.dumps({"valid": True, "pagetext": "Текст страницы 1", "pagehtml": "<p>Текст страницы 1</p>"})},
        {"page": 2, "content": json.dumps({"valid": True, "pagetext": "Текст страницы 2", "pagehtml": "<p>Текст страницы 2</p>"})},
    ],
}


class TestParseEmbeddedContent(unittest.TestCase):
    def test_dict_passthrough(self):
        d = {"valid": True, "pagetext": "hello"}
        self.assertEqual(parse_embedded_content(d), d)

    def test_json_string(self):
        s = json.dumps({"valid": True, "pagetext": "abc"})
        result = parse_embedded_content(s)
        self.assertEqual(result["pagetext"], "abc")

    def test_empty_string(self):
        self.assertEqual(parse_embedded_content(""), {})

    def test_none(self):
        self.assertEqual(parse_embedded_content(None), {})

    def test_invalid_json(self):
        result = parse_embedded_content("not json <<>>")
        self.assertFalse(result["valid"])
        self.assertEqual(result["pagetext"], "not json <<>>")

    def test_invalid_json_preserves_raw(self):
        raw = "some raw text"
        result = parse_embedded_content(raw)
        self.assertEqual(result["pagetext"], raw)


class TestNormalizeText(unittest.TestCase):
    def test_normal_text(self):
        self.assertEqual(normalize_text("Hello world"), "Hello world")

    def test_carriage_returns(self):
        self.assertEqual(normalize_text("a\r\nb\r\nc"), "a\nb\nc")

    def test_non_breaking_spaces(self):
        self.assertEqual(normalize_text("a\u00A0b"), "a b")

    def test_triple_newlines(self):
        self.assertEqual(normalize_text("a\n\n\n\nb"), "a\n\nb")

    def test_trailing_whitespace_on_lines(self):
        self.assertEqual(normalize_text("a   \nb   \n"), "a\nb")

    def test_double_spaces(self):
        self.assertEqual(normalize_text("a  b   c"), "a b c")

    def test_leading_page_number(self):
        self.assertEqual(normalize_text("5\nТекст страницы"), "Текст страницы")

    def test_leading_page_number_with_whitespace(self):
        self.assertEqual(normalize_text("  12  \n\nHello"), "Hello")

    def test_empty(self):
        self.assertEqual(normalize_text(""), "")

    def test_none(self):
        self.assertEqual(normalize_text(None), "")


class TestRemoveLeadingPrintPageNumber(unittest.TestCase):
    def test_number_at_start(self):
        self.assertEqual(remove_leading_print_page_number("5\nHello"), "Hello")

    def test_number_with_spaces(self):
        self.assertEqual(remove_leading_print_page_number("  12  \n\nHello"), "Hello")

    def test_no_number(self):
        self.assertEqual(remove_leading_print_page_number("Hello world"), "Hello world")

    def test_empty(self):
        self.assertEqual(remove_leading_print_page_number(""), "")


class TestCleanPagehtml(unittest.TestCase):
    def test_removes_comments(self):
        html_in = "<p>before</p><!-- comment --><p>after</p>"
        result = clean_pagehtml(html_in)
        self.assertNotIn("comment", result)
        self.assertIn("before", result)
        self.assertIn("after", result)

    def test_renames_page_class(self):
        html_in = '<p class="page">5</p>'
        result = clean_pagehtml(html_in)
        self.assertIn('class="page-no"', result)
        self.assertNotIn('class="page"', result)

    def test_single_quotes(self):
        html_in = "<p class='page'>5</p>"
        result = clean_pagehtml(html_in)
        self.assertIn("class='page-no'", result)

    def test_empty(self):
        self.assertEqual(clean_pagehtml(""), "")

    def test_none(self):
        self.assertEqual(clean_pagehtml(None), "")


class TestStripTagsPreserveText(unittest.TestCase):
    def test_br_to_newline(self):
        self.assertEqual(strip_tags_preserve_text("a<br>b"), "a\nb")

    def test_self_closing_br(self):
        self.assertEqual(strip_tags_preserve_text("a<br/>b"), "a\nb")

    def test_p_close_to_newline(self):
        self.assertEqual(strip_tags_preserve_text("<p>a</p><p>b</p>"), "a\nb")

    def test_div_close_to_newline(self):
        self.assertEqual(strip_tags_preserve_text("<div>a</div>"), "a")

    def test_html_entities(self):
        self.assertEqual(strip_tags_preserve_text("&amp; &lt;"), "& <")

    def test_nested_tags(self):
        self.assertEqual(strip_tags_preserve_text("<p><b>bold</b></p>"), "bold")

    def test_empty(self):
        self.assertEqual(strip_tags_preserve_text(""), "")


class TestExtractBlocksFromHtml(unittest.TestCase):
    def test_paragraphs(self):
        html_in = '<p class="text">Hello</p><p class="text">World</p>'
        blocks = extract_blocks_from_html(html_in)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["type"], "p")
        self.assertEqual(blocks[0]["text"], "Hello")

    def test_page_no_skipped(self):
        html_in = '<p class="page-no">5</p><p class="text">Content</p>'
        blocks = extract_blocks_from_html(html_in)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["text"], "Content")

    def test_footnote(self):
        html_in = '<p class="ftn">Сноска</p>'
        blocks = extract_blocks_from_html(html_in)
        self.assertEqual(blocks[0]["type"], "footnote")

    def test_image_caption(self):
        html_in = '<p class="img">Рисунок 1</p>'
        blocks = extract_blocks_from_html(html_in)
        self.assertEqual(blocks[0]["type"], "image-caption")

    def test_empty_html_fallback_text(self):
        blocks = extract_blocks_from_html("", "Paragraph one\n\nParagraph two")
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["type"], "p")
        self.assertEqual(blocks[0]["text"], "Paragraph one")

    def test_no_html_no_fallback(self):
        blocks = extract_blocks_from_html("", "")
        self.assertEqual(blocks, [])

    def test_div_blocks(self):
        html_in = '<div class="text">Content</div>'
        blocks = extract_blocks_from_html(html_in)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["text"], "Content")


class TestPageToModel(unittest.TestCase):
    def test_basic(self):
        item = {"page": 1, "content": json.dumps({"valid": True, "pagetext": "Hello", "pagehtml": "<p>Hello</p>"})}
        model = page_to_model(item)
        self.assertEqual(model["page"], 1)
        self.assertTrue(model["valid"])
        self.assertEqual(model["pagetext"], "Hello")
        self.assertIn("Hello", model["pagehtml"])
        self.assertGreater(len(model["blocks"]), 0)

    def test_missing_content(self):
        item = {"page": 0}
        model = page_to_model(item)
        self.assertEqual(model["page"], 0)
        self.assertEqual(model["blocks"], [])


class TestBuildBookModels(unittest.TestCase):
    def test_basic(self):
        src = {"items": [
            {"page": 0, "content": json.dumps({"valid": True, "pagetext": "", "pagehtml": ""})},
            {"page": 1, "content": json.dumps({"valid": True, "pagetext": "Text", "pagehtml": "<p>Text</p>"})},
        ]}
        models = build_book_models(src)
        self.assertEqual(len(models), 2)
        self.assertEqual(models[0]["page"], 0)
        self.assertEqual(models[1]["page"], 1)


class TestSlugify(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(slugify("Hello World"), "hello_world")

    def test_special_chars(self):
        self.assertEqual(slugify("Книга: тест! @#$"), "книга_тест")

    def test_multiple_spaces(self):
        self.assertEqual(slugify("a   b"), "a_b")

    def test_empty(self):
        self.assertEqual(slugify(""), "book")

    def test_long(self):
        result = slugify("a" * 200)
        self.assertLessEqual(len(result), 120)


class TestOutputStem(unittest.TestCase):
    def test_basic(self):
        src = {"title": "Test", "book_id": "id", "page_range": [0, 10]}
        path = Path("input.json")
        result = output_stem(src, path)
        self.assertIn("test", result)
        self.assertIn("id", result)
        self.assertIn("0-10", result)

    def test_with_prefix(self):
        src = {"title": "Test", "book_id": "id", "page_range": [0, 10]}
        path = Path("input.json")
        result = output_stem(src, path, prefix="prefix")
        self.assertTrue(result.startswith("prefix"))

    def test_fallback_to_stem(self):
        src = {}
        path = Path("my_book.json")
        result = output_stem(src, path)
        self.assertIn("my_book", result)


class TestStripHeadingMarks(unittest.TestCase):
    def test_stars(self):
        self.assertEqual(strip_heading_marks("ГЛАВА 1***"), "ГЛАВА 1")

    def test_no_stars(self):
        self.assertEqual(strip_heading_marks("ГЛАВА 1"), "ГЛАВА 1")

    def test_trailing_whitespace(self):
        self.assertEqual(strip_heading_marks("  ГЛАВА 1  "), "ГЛАВА 1")


class TestNormalizedHeadingKey(unittest.TestCase):
    def test_basic(self):
        result = normalized_heading_key("ГЛАВА 1***")
        self.assertEqual(result, "ГЛАВА 1")

    def test_yo_normalization(self):
        result = normalized_heading_key("ЁЖКИ")
        self.assertEqual(result, "ЕЖКИ")

    def test_multiple_spaces(self):
        result = normalized_heading_key("ГЛАВА   1")
        self.assertEqual(result, "ГЛАВА 1")


class TestIsProbableHeading(unittest.TestCase):
    def test_normal_heading(self):
        self.assertTrue(is_probable_heading("ГЛАВА ПЕРВАЯ"))

    def test_too_short(self):
        self.assertFalse(is_probable_heading("abc"))

    def test_too_long(self):
        self.assertFalse(is_probable_heading("A " * 70))

    def test_empty(self):
        self.assertFalse(is_probable_heading(""))

    def test_mostly_lowercase(self):
        self.assertFalse(is_probable_heading("это точно не заголовок а обычный текст"))

    def test_too_many_words(self):
        self.assertFalse(is_probable_heading("ОДИН ДВА ТРИ ЧЕТЫРЕ ПЯТЬ ШЕСТЬ СЕМЬ ВОСЕМЬ ДЕСЯТЬ ОДИНАДЦАТЬ ДВЕНАДЦАТЬ ТРИНАДЦАТЬ ЧЕТЫРНАДЦАТЬ"))

    def test_ends_with_dot_long(self):
        self.assertFalse(is_probable_heading("Это длинный текст который заканчивается точкой."))


class TestIsProbableAuthorLine(unittest.TestCase):
    def test_author_with_initials(self):
        self.assertTrue(is_probable_author_line("И.И. Иванов"))

    def test_author_short(self):
        self.assertTrue(is_probable_author_line("А. Блок"))

    def test_too_long(self):
        self.assertFalse(is_probable_author_line("Это очень длинная строка которая не может быть именем автора"))

    def test_has_digits(self):
        self.assertFalse(is_probable_author_line("Иванов 123"))

    def test_single_word(self):
        self.assertFalse(is_probable_author_line("Иванов"))

    def test_too_many_words(self):
        self.assertFalse(is_probable_author_line("А Б В Г Д Е Ж"))

    def test_empty(self):
        self.assertFalse(is_probable_author_line(""))


class TestIsFrontMatterHeading(unittest.TestCase):
    def test_known_title(self):
        self.assertTrue(is_front_matter_heading("ОБЛОЖКА"))

    def test_unknown_title(self):
        self.assertFalse(is_front_matter_heading("ГЛАВА 1"))


class TestShouldStartChapter(unittest.TestCase):
    def test_normal_mode_any_heading(self):
        self.assertTrue(should_start_chapter("ГЛАВА 1", 10, "normal"))

    def test_strict_mode_short_heading(self):
        self.assertFalse(should_start_chapter("АБ", 10, "strict"))

    def test_strict_mode_front_matter(self):
        self.assertFalse(should_start_chapter("ОБЛОЖКА", 10, "strict"))

    def test_strict_mode_early_page(self):
        self.assertFalse(should_start_chapter("ГЛАВА ПЕРВАЯ", 2, "strict"))

    def test_strict_mode_valid_heading(self):
        self.assertTrue(should_start_chapter("ГЛАВА ПЕРВАЯ ЧАСТЬ", 10, "strict"))

    def test_not_probable_heading(self):
        self.assertFalse(should_start_chapter("обычный текст", 10, "strict"))


class TestSplitIntoChapters(unittest.TestCase):
    def _make_page(self, page, text):
        return {
            "page": page,
            "blocks": [{"type": "p", "text": text}],
        }

    def test_single_chapter(self):
        pages = [
            self._make_page(5, "ГЛАВА ПЕРВАЯ ЧАСТЬ"),
            self._make_page(5, "Текст главы"),
        ]
        chapters = split_into_chapters(pages, mode="strict")
        self.assertGreaterEqual(len(chapters), 1)
        self.assertEqual(chapters[0]["title"], "ГЛАВА ПЕРВАЯ ЧАСТЬ")

    def test_two_chapters(self):
        pages = [
            self._make_page(10, "ГЛАВА ПЕРВАЯ ЧАСТЬ"),
            self._make_page(10, "Текст"),
            self._make_page(20, "ГЛАВА ВТОРАЯ ЧАСТЬ"),
            self._make_page(20, "Текст"),
        ]
        chapters = split_into_chapters(pages, mode="strict")
        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[0]["title"], "ГЛАВА ПЕРВАЯ ЧАСТЬ")
        self.assertEqual(chapters[1]["title"], "ГЛАВА ВТОРАЯ ЧАСТЬ")

    def test_empty_pages(self):
        chapters = split_into_chapters([], mode="strict")
        self.assertEqual(chapters, [])

    def test_front_matter_only(self):
        pages = [
            self._make_page(0, "ОБЛОЖКА"),
            self._make_page(1, "СОДЕРЖАНИЕ"),
        ]
        chapters = split_into_chapters(pages, mode="strict")
        self.assertGreaterEqual(len(chapters), 1)


class TestMergeEmptyFrontMatter(unittest.TestCase):
    def test_merge(self):
        chapters = [
            {"title": "Front Matter", "pages": [0, 1], "content": []},
            {"title": "Chapter 1", "pages": [2], "content": [{"page": 2, "type": "p", "text": "text"}]},
        ]
        result = merge_empty_front_matter(chapters)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Chapter 1")
        self.assertIn(0, result[0]["pages"])

    def test_no_merge_if_content(self):
        chapters = [
            {"title": "Front Matter", "pages": [0], "content": [{"page": 0, "type": "p", "text": "text"}]},
            {"title": "Chapter 1", "pages": [1], "content": [{"page": 1, "type": "p", "text": "text"}]},
        ]
        result = merge_empty_front_matter(chapters)
        self.assertEqual(len(result), 2)

    def test_empty_list(self):
        self.assertEqual(merge_empty_front_matter([]), [])


class TestBuildOutputs(unittest.TestCase):
    def setUp(self):
        self.src = SAMPLE_JSON.copy()
        self.chapters = [
            {
                "title": "Текст",
                "subtitle": "",
                "pages": [1, 2],
                "content": [
                    {"page": 1, "type": "p", "text": "Абзац 1"},
                    {"page": 2, "type": "p", "text": "Абзац 2"},
                ],
            }
        ]

    def test_build_html_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "test.html"
            build_html(self.src, self.chapters, out)
            self.assertTrue(out.exists())
            content = out.read_text(encoding="utf-8")
            self.assertIn("Тестовая книга", content)
            self.assertIn("Абзац 1", content)
            self.assertIn("Абзац 2", content)
            self.assertIn("Contents", content)

    def test_build_html_escapes_special_chars(self):
        src = self.src.copy()
        src["title"] = "<script>alert(1)</script>"
        chapters = [{"title": "<b>Title</b>", "subtitle": "", "pages": [1], "content": [{"page": 1, "type": "p", "text": "ok"}]}]
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "test.html"
            build_html(src, chapters, out)
            content = out.read_text(encoding="utf-8")
            self.assertNotIn("<script>", content)
            self.assertIn("&lt;script&gt;", content)

    def test_build_fb2_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "test.fb2"
            build_fb2(self.src, self.chapters, out)
            self.assertTrue(out.exists())
            content = out.read_text(encoding="utf-8")
            self.assertIn("Тестовая книга", content)
            self.assertIn("Абзац 1", content)

    def test_build_epub_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "test.epub"
            build_epub(self.src, self.chapters, out)
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 0)


class TestReadSource(unittest.TestCase):
    def test_read_valid(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(SAMPLE_JSON, f, ensure_ascii=False)
            f.flush()
            path = f.name
        try:
            src = read_source(path)
            self.assertEqual(src["title"], "Тестовая книга")
            self.assertEqual(len(src["items"]), 3)
        finally:
            os.unlink(path)


class TestEndToEnd(unittest.TestCase):
    def test_full_pipeline(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "input.json"
            json_path.write_text(json.dumps(SAMPLE_JSON, ensure_ascii=False), encoding="utf-8")

            src = read_source(json_path)
            pages = build_book_models(src)
            chapters = split_into_chapters(pages, mode="strict")

            self.assertGreaterEqual(len(chapters), 1)

            html_out = Path(tmpdir) / "out.html"
            build_html(src, chapters, html_out)
            self.assertTrue(html_out.exists())

            fb2_out = Path(tmpdir) / "out.fb2"
            build_fb2(src, chapters, fb2_out)
            self.assertTrue(fb2_out.exists())

            epub_out = Path(tmpdir) / "out.epub"
            build_epub(src, chapters, epub_out)
            self.assertTrue(epub_out.exists())


if __name__ == "__main__":
    unittest.main()
