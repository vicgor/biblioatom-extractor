#!/usr/bin/env python3
import argparse
import json
import re
import html
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS_FB2 = "http://www.gribuser.ru/xml/fictionbook/2.0"
NS_XLINK = "http://www.w3.org/1999/xlink"

ET.register_namespace("", NS_FB2)
ET.register_namespace("l", NS_XLINK)

FRONT_MATTER_TITLES = {
    "ОБЛОЖКА",
    "ФРОНТИСПИС",
    "РОССИЙСКАЯ АКАДЕМИЯ НАУК",
    "ПЕТР ЛЕОНИДОВИЧ КАПИЦА",
    "ВОСПОМИНАНИЯ ПИСЬМА ДОКУМЕНТЫ",
    "МОСКВА “НАУКА” 1994",
    "МОСКВА \"НАУКА\" 1994",
    "СЕРИЯ “УЧЕНЫЕ РОССИИ. ОЧЕРКИ. ВОСПОМИНАНИЯ. МАТЕРИАЛЫ”",
    "СЕРИЯ \"УЧЕНЫЕ РОССИИ. ОЧЕРКИ. ВОСПОМИНАНИЯ. МАТЕРИАЛЫ\"",
    "РЕДАКЦИОННАЯ КОЛЛЕГИЯ СЕРИИ:",
}

STRICT_MIN_PAGE_FOR_CHAPTER = 5


def parse_args():
    parser = argparse.ArgumentParser(
        description="Convert BiblioAtom JSON export to chapter-based HTML / FB2 / EPUB"
    )
    parser.add_argument("--input", "-i", required=True, help="Path to source JSON file")
    parser.add_argument("--outdir", "-o", default="output_books", help="Output directory")
    parser.add_argument("--formats", "-f", default="html,fb2,epub", help="Comma-separated formats: html,fb2,epub")
    parser.add_argument("--prefix", default="", help="Optional output filename prefix")
    parser.add_argument(
        "--chapter-mode",
        choices=["strict", "normal"],
        default="strict",
        help="Chapter detection mode"
    )
    return parser.parse_args()


def read_source(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_embedded_content(raw):
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {"valid": False, "pagetext": str(raw), "pagehtml": ""}


def remove_leading_print_page_number(text):
    s = text or ""
    s = s.lstrip()
    s = re.sub(r"^\d+\s*\n+", "", s, count=1)
    return s.strip()


def normalize_text(text):
    s = text or ""
    s = s.replace("\r", "")
    s = s.replace("\u00A0", " ")
    s = remove_leading_print_page_number(s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def clean_pagehtml(pagehtml):
    s = pagehtml or ""
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = s.replace('class="page"', 'class="page-no"')
    s = s.replace("class='page'", "class='page-no'")
    return s.strip()


def strip_tags_preserve_text(s):
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</p\s*>", "\n", s, flags=re.I)
    s = re.sub(r"</div\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()


def extract_blocks_from_html(pagehtml, fallback_text=""):
    blocks = []

    if pagehtml:
        pattern = re.compile(
            r"<p(?P<attrs>[^>]*)>(?P<body>.*?)</p>|<div(?P<dattrs>[^>]*)>(?P<dbody>.*?)</div>",
            re.I | re.S
        )

        for m in pattern.finditer(pagehtml):
            attrs = m.group("attrs") or m.group("dattrs") or ""
            body = m.group("body") if m.group("body") is not None else m.group("dbody")
            body = body or ""

            class_match = re.search(r'class=["\']([^"\']+)["\']', attrs, re.I)
            classes = class_match.group(1).split() if class_match else []

            text = strip_tags_preserve_text(body)
            if not text:
                continue

            if "page-no" in classes:
                continue  # полностью выкидываем печатный номер страницы

            if "ftn" in classes:
                blocks.append({"type": "footnote", "text": text})
            elif "img" in classes:
                blocks.append({"type": "image-caption", "text": text})
            elif "text" in classes:
                blocks.append({"type": "p", "text": text})
            else:
                blocks.append({"type": "p", "text": text})

    if not blocks and fallback_text:
        cleaned = normalize_text(fallback_text)
        for part in re.split(r"\n\s*\n", cleaned):
            part = part.strip()
            if part:
                blocks.append({"type": "p", "text": part})

    return blocks


def page_to_model(item):
    embedded = parse_embedded_content(item.get("content", ""))
    page_num = item.get("page")
    pagetext = normalize_text(embedded.get("pagetext", ""))
    pagehtml = clean_pagehtml(embedded.get("pagehtml", ""))
    valid = embedded.get("valid", True)

    return {
        "page": page_num,
        "valid": valid,
        "pagetext": pagetext,
        "pagehtml": pagehtml,
        "blocks": extract_blocks_from_html(pagehtml, pagetext),
    }


def build_book_models(src):
    return [page_to_model(item) for item in src.get("items", [])]


def slugify(text):
    s = text.strip().lower()
    s = re.sub(r"[^\w\s.-]", "", s, flags=re.U)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"_+", "_", s)
    return s[:120] or "book"


def output_stem(src, input_path, prefix=""):
    title = src.get("title", "") or input_path.stem
    book_id = src.get("book_id", "")
    page_range = src.get("page_range", [])
    page_part = ""
    if isinstance(page_range, list) and len(page_range) == 2:
        page_part = f"{page_range[0]}-{page_range[1]}"
    parts = [prefix.strip(), slugify(title), slugify(book_id), page_part]
    parts = [p for p in parts if p]
    return "_".join(parts)


def strip_heading_marks(text):
    return re.sub(r"[*]+$", "", text.strip()).strip()


def normalized_heading_key(text):
    t = strip_heading_marks(text).upper()
    t = t.replace("Ё", "Е")
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def is_probable_heading(text):
    t = (text or "").strip()
    if not t:
        return False

    t = re.sub(r"\s+", " ", t)
    plain = t.strip("•*-—– ")
    if len(plain) < 4 or len(plain) > 120:
        return False

    if plain.endswith(".") and len(plain) > 40:
        return False

    letters = [ch for ch in plain if ch.isalpha()]
    if not letters:
        return False

    upper = sum(1 for ch in letters if ch.isupper())
    ratio = upper / len(letters)

    if ratio < 0.6:
        return False

    words = plain.split()
    if len(words) > 12:
        return False

    return True


def is_probable_author_line(text):
    t = (text or "").strip()
    if not t or len(t) > 80:
        return False

    if any(ch.isdigit() for ch in t):
        return False

    if " " not in t:
        return False

    words = t.split()
    if len(words) > 6:
        return False

    initials = sum(1 for w in words if re.match(r"^[А-ЯA-Z]\.?[А-ЯA-Z]?\.$", w))
    surname_like = any(re.match(r"^[А-ЯA-ZЁ][а-яa-zё-]+$", w) for w in words)

    return initials >= 1 and surname_like


def is_front_matter_heading(text):
    key = normalized_heading_key(text)
    return key in FRONT_MATTER_TITLES


def should_start_chapter(text, page_no, mode):
    if not is_probable_heading(text):
        return False

    if mode == "normal":
        return True

    key = normalized_heading_key(text)

    if page_no < STRICT_MIN_PAGE_FOR_CHAPTER:
        return False

    if is_front_matter_heading(key):
        return False

    if len(key.split()) <= 2 and not key.endswith(":"):
        return False

    return True


def split_into_chapters(pages, mode="strict"):
    chapters = []
    current = {
        "title": "Front Matter",
        "subtitle": "",
        "pages": [],
        "content": []
    }

    pending_author = ""

    for pg in pages:
        page_blocks = pg["blocks"]
        i = 0

        while i < len(page_blocks):
            block = page_blocks[i]
            btype = block["type"]
            btext = block["text"].strip()

            if not btext:
                i += 1
                continue

            if btype == "p" and is_probable_author_line(btext):
                if i + 1 < len(page_blocks):
                    next_block = page_blocks[i + 1]
                    if next_block["type"] == "p" and should_start_chapter(next_block["text"], pg["page"], mode):
                        pending_author = btext
                        i += 1
                        continue

            if btype == "p" and should_start_chapter(btext, pg["page"], mode):
                if current["content"] or current["pages"]:
                    chapters.append(current)

                current = {
                    "title": strip_heading_marks(btext),
                    "subtitle": pending_author,
                    "pages": [pg["page"]],
                    "content": []
                }
                pending_author = ""
                i += 1
                continue

            if pg["page"] not in current["pages"]:
                current["pages"].append(pg["page"])

            current["content"].append({
                "page": pg["page"],
                "type": btype,
                "text": btext
            })
            i += 1

    if current["content"] or current["pages"]:
        chapters.append(current)

    return merge_empty_front_matter(chapters)


def merge_empty_front_matter(chapters):
    cleaned = [ch for ch in chapters if ch["content"] or ch["pages"]]
    if not cleaned:
        return cleaned

    if len(cleaned) >= 2 and cleaned[0]["title"] == "Front Matter" and not cleaned[0]["content"]:
        nxt = cleaned[1]
        nxt["pages"] = sorted(set(cleaned[0]["pages"] + nxt["pages"]))
        return cleaned[1:]

    return cleaned


def build_html(src, chapters, out_path):
    title = src.get("title", "Untitled")
    book_id = src.get("book_id", "")
    source = src.get("source", "")
    page_range = src.get("page_range", [])

    sections = []
    for idx, ch in enumerate(chapters, start=1):
        body = []
        if ch["subtitle"]:
            body.append(f'<p class="chapter-subtitle">{html.escape(ch["subtitle"])}</p>')

        for block in ch["content"]:
            text = html.escape(block["text"])
            if block["type"] == "footnote":
                body.append(f'<p class="footnote">{text}</p>')
            elif block["type"] == "image-caption":
                body.append(f'<p class="image-caption">{text}</p>')
            else:
                body.append(f"<p>{text}</p>")

        page_span = ""
        if ch["pages"]:
            page_span = f"{min(ch['pages'])}–{max(ch['pages'])}"

        sections.append(f"""
<section class="chapter" id="chapter-{idx}">
  <h2>{html.escape(ch['title'])}</h2>
  <div class="chapter-meta">Pages: {html.escape(page_span)}</div>
  <div class="chapter-body">
    {''.join(body)}
  </div>
</section>
""")

    toc = []
    for idx, ch in enumerate(chapters, start=1):
        toc.append(f'<li><a href="#chapter-{idx}">{html.escape(ch["title"])}</a></li>')

    doc = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{
      margin: 0;
      padding: 24px;
      background: #f5f5f5;
      color: #111;
      font: 18px/1.65 Georgia, "Times New Roman", serif;
    }}
    main {{
      max-width: 900px;
      margin: 0 auto;
    }}
    header, nav.toc, section.chapter {{
      background: #fff;
      border: 1px solid #ddd;
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 20px;
    }}
    h1, h2 {{
      margin-top: 0;
    }}
    .meta, .chapter-meta {{
      color: #666;
      font-size: 14px;
    }}
    .toc ol {{
      margin: 0;
      padding-left: 20px;
    }}
    .chapter-subtitle {{
      font-style: italic;
      color: #444;
      margin-top: -0.2em;
    }}
    .footnote {{
      font-size: 0.92em;
      color: #444;
      border-top: 1px solid #e3e3e3;
      padding-top: 10px;
    }}
    .image-caption {{
      font-style: italic;
      color: #444;
    }}
    .chapter-body p {{
      white-space: pre-wrap;
      margin: 0 0 1em;
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>{html.escape(title)}</h1>
      <div class="meta">
        <div>book_id: {html.escape(book_id)}</div>
        <div>source: {html.escape(source)}</div>
        <div>page_range: {html.escape(str(page_range))}</div>
        <div>generated_at: {html.escape(src.get("generated_at", ""))}</div>
      </div>
    </header>

    <nav class="toc">
      <h2>Contents</h2>
      <ol>{''.join(toc)}</ol>
    </nav>

    {''.join(sections)}
  </main>
</body>
</html>
"""
    out_path.write_text(doc, encoding="utf-8")


def build_fb2(src, chapters, out_path):
    fb = ET.Element(f"{{{NS_FB2}}}FictionBook")
    desc = ET.SubElement(fb, f"{{{NS_FB2}}}description")
    title_info = ET.SubElement(desc, f"{{{NS_FB2}}}title-info")

    book_title = ET.SubElement(title_info, f"{{{NS_FB2}}}book-title")
    book_title.text = src.get("title", "Untitled")

    lang = ET.SubElement(title_info, f"{{{NS_FB2}}}lang")
    lang.text = "ru"

    doc_info = ET.SubElement(desc, f"{{{NS_FB2}}}document-info")
    program = ET.SubElement(doc_info, f"{{{NS_FB2}}}program-used")
    program.text = "custom json->chapter fb2 converter"
    date = ET.SubElement(doc_info, f"{{{NS_FB2}}}date")
    date.text = src.get("generated_at", "")

    body = ET.SubElement(fb, f"{{{NS_FB2}}}body")

    title_sec = ET.SubElement(body, f"{{{NS_FB2}}}title")
    p = ET.SubElement(title_sec, f"{{{NS_FB2}}}p")
    p.text = src.get("title", "Untitled")

    for idx, ch in enumerate(chapters, start=1):
        sec = ET.SubElement(body, f"{{{NS_FB2}}}section")
        sec.set("id", f"chapter_{idx}")

        sec_title = ET.SubElement(sec, f"{{{NS_FB2}}}title")
        sec_title_p = ET.SubElement(sec_title, f"{{{NS_FB2}}}p")
        sec_title_p.text = ch["title"]

        if ch["subtitle"]:
            subtitle = ET.SubElement(sec, f"{{{NS_FB2}}}subtitle")
            subtitle.text = ch["subtitle"]

        for block in ch["content"]:
            text = block["text"]
            if not text:
                continue

            if block["type"] == "image-caption":
                subtitle = ET.SubElement(sec, f"{{{NS_FB2}}}subtitle")
                subtitle.text = text
            else:
                p = ET.SubElement(sec, f"{{{NS_FB2}}}p")
                p.text = text

    tree = ET.ElementTree(fb)
    tree.write(out_path, encoding="utf-8", xml_declaration=True)


def make_epub_xhtml(title, body_html):
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="ru" xml:lang="ru">
<head>
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" type="text/css" href="../styles/style.css"/>
  <meta charset="utf-8"/>
</head>
<body>
{body_html}
</body>
</html>
"""


def build_epub(src, chapters, out_path):
    title = src.get("title", "Untitled")
    book_id = src.get("book_id", "book")
    language = "ru"

    style_css = """
body { font-family: serif; line-height: 1.5; }
h1, h2 { margin: 1em 0 0.5em; }
p { margin: 0 0 0.8em; white-space: pre-wrap; }
.footnote { font-size: 0.92em; }
.image-caption { font-style: italic; }
.chapter-subtitle { font-style: italic; color: #444; }
"""

    manifest_items = [
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="nav" href="text/nav.xhtml" media-type="application/xhtml+xml"/>',
        '<item id="css" href="styles/style.css" media-type="text/css"/>',
        '<item id="intro" href="text/intro.xhtml" media-type="application/xhtml+xml"/>',
    ]
    spine_items = ['<itemref idref="intro"/>']
    nav_items = ['<li><a href="intro.xhtml">Начало</a></li>']
    chapter_files = []

    for idx, ch in enumerate(chapters, start=1):
        body = [f"<h2>{html.escape(ch['title'])}</h2>"]
        if ch["subtitle"]:
            body.append(f'<p class="chapter-subtitle">{html.escape(ch["subtitle"])}</p>')

        for block in ch["content"]:
            text = html.escape(block["text"])
            if block["type"] == "footnote":
                body.append(f'<p class="footnote">{text}</p>')
            elif block["type"] == "image-caption":
                body.append(f'<p class="image-caption">{text}</p>')
            else:
                body.append(f"<p>{text}</p>")

        fname = f"text/chapter_{idx}.xhtml"
        xid = f"chapter_{idx}"
        chapter_files.append((fname, make_epub_xhtml(ch["title"], "\n".join(body))))
        manifest_items.append(f'<item id="{xid}" href="{fname}" media-type="application/xhtml+xml"/>')
        spine_items.append(f'<itemref idref="{xid}"/>')
        nav_items.append(f'<li><a href="chapter_{idx}.xhtml">{html.escape(ch["title"])}</a></li>')

    intro = make_epub_xhtml(
        title,
        f"<h1>{html.escape(title)}</h1>"
        f"<p><strong>Source:</strong> {html.escape(src.get('source', ''))}</p>"
        f"<p><strong>Generated at:</strong> {html.escape(src.get('generated_at', ''))}</p>"
    )

    nav_xhtml = make_epub_xhtml(
        "Contents",
        f'<h1>Contents</h1><ol>{"".join(nav_items)}</ol>'
    )

    toc_ncx_points = ['''
    <navPoint id="navPoint-0" playOrder="0">
      <navLabel><text>Начало</text></navLabel>
      <content src="text/intro.xhtml"/>
    </navPoint>''']

    for idx, ch in enumerate(chapters, start=1):
        toc_ncx_points.append(f"""
    <navPoint id="navPoint-{idx}" playOrder="{idx}">
      <navLabel><text>{html.escape(ch['title'])}</text></navLabel>
      <content src="text/chapter_{idx}.xhtml"/>
    </navPoint>""")

    content_opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{html.escape(title)}</dc:title>
    <dc:language>{language}</dc:language>
    <dc:identifier id="BookId">{html.escape(book_id)}</dc:identifier>
  </metadata>
  <manifest>
    {' '.join(manifest_items)}
  </manifest>
  <spine toc="ncx">
    {' '.join(spine_items)}
  </spine>
</package>
"""

    toc_ncx = f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="{html.escape(book_id)}"/>
  </head>
  <docTitle><text>{html.escape(title)}</text></docTitle>
  <navMap>
    {''.join(toc_ncx_points)}
  </navMap>
</ncx>
"""

    container_xml = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

    with zipfile.ZipFile(out_path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", content_opf)
        zf.writestr("OEBPS/toc.ncx", toc_ncx)
        zf.writestr("OEBPS/text/intro.xhtml", intro)
        zf.writestr("OEBPS/text/nav.xhtml", nav_xhtml)
        zf.writestr("OEBPS/styles/style.css", style_css)

        for fname, content in chapter_files:
            zf.writestr(f"OEBPS/{fname}", content)


def main():
    args = parse_args()

    input_path = Path(args.input)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    formats = {fmt.strip().lower() for fmt in args.formats.split(",") if fmt.strip()}
    allowed = {"html", "fb2", "epub"}
    unknown = formats - allowed
    if unknown:
        raise SystemExit(f"Unknown formats: {', '.join(sorted(unknown))}")

    src = read_source(input_path)
    pages = build_book_models(src)
    chapters = split_into_chapters(pages, mode=args.chapter_mode)
    stem = output_stem(src, input_path, args.prefix)

    print(f"Loaded: {input_path}")
    print(f"Title: {src.get('title', '')}")
    print(f"Pages parsed: {len(pages)}")
    print(f"Chapter mode: {args.chapter_mode}")
    print(f"Chapters built: {len(chapters)}")
    for i, ch in enumerate(chapters, start=1):
        start = min(ch["pages"]) if ch["pages"] else "?"
        end = max(ch["pages"]) if ch["pages"] else "?"
        print(f"  {i}. {ch['title']} [{start}-{end}]")

    if "html" in formats:
        out_html = outdir / f"{stem}.html"
        build_html(src, chapters, out_html)
        print(f"Written HTML: {out_html}")

    if "fb2" in formats:
        out_fb2 = outdir / f"{stem}.fb2"
        build_fb2(src, chapters, out_fb2)
        print(f"Written FB2: {out_fb2}")

    if "epub" in formats:
        out_epub = outdir / f"{stem}.epub"
        build_epub(src, chapters, out_epub)
        print(f"Written EPUB: {out_epub}")


if __name__ == "__main__":
    main()