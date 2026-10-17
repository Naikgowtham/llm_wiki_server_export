"""Multi-format document processor for Office documents, tabular spreadsheets, images, and HTML."""

from __future__ import annotations

import asyncio
import base64
import csv
import logging
import re
import shutil
from pathlib import Path
from typing import Any, List, Optional, Set, Tuple

from lib.utils import slugify

logger = logging.getLogger(__name__)


def _format_pipe_table(rows: List[List[str]], max_rows: int = 150) -> str:
    """Format a 2D list of strings as a GitHub Flavored Markdown (GFM) pipe table."""
    if not rows:
        return ""

    # Normalize row lengths
    max_cols = max(len(r) for r in rows)
    if max_cols == 0:
        return ""

    normalized_rows = []
    for r in rows:
        padded = [str(c or "").replace("|", "\\|").replace("\n", " ").strip() for c in r]
        while len(padded) < max_cols:
            padded.append("")
        normalized_rows.append(padded)

    # Use first row as header if possible
    header = normalized_rows[0]
    divider = [":---" for _ in range(max_cols)]

    table_lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(divider) + " |",
    ]

    data_rows = normalized_rows[1:max_rows]
    for r in data_rows:
        table_lines.append("| " + " | ".join(r) + " |")

    if len(normalized_rows) > max_rows:
        table_lines.append(f"\n*... [{len(normalized_rows) - max_rows} additional rows omitted for token limits] ...*")

    return "\n".join(table_lines)


def convert_docx_to_markdown(
    docx_path: Path,
    assets_dir: Optional[Path] = None,
    doc_slug: Optional[str] = None,
) -> str:
    """Extract paragraphs, headings, tables, and embedded images from a Word (.docx) document."""
    try:
        import docx
    except ImportError:
        raise ImportError("python-docx is required to parse .docx documents. Run `pip install python-docx`.")

    doc = docx.Document(str(docx_path))
    lines: List[str] = []

    # Title from first heading or filename
    title = docx_path.stem.replace("-", " ").replace("_", " ").title()
    lines.append(f"# {title}\n")

    for p in doc.paragraphs:
        text = p.text.strip()
        if not text:
            continue
        style_name = p.style.name.lower() if p.style else ""
        if "heading 1" in style_name:
            lines.append(f"## {text}\n")
        elif "heading 2" in style_name:
            lines.append(f"### {text}\n")
        elif "heading 3" in style_name or "heading 4" in style_name:
            lines.append(f"#### {text}\n")
        elif "bullet" in style_name or "list" in style_name:
            lines.append(f"- {text}")
        else:
            lines.append(f"{text}\n")

    for table in doc.tables:
        table_rows = []
        for row in table.rows:
            table_rows.append([cell.text.strip() for cell in row.cells])
        if table_rows:
            lines.append("\n" + _format_pipe_table(table_rows) + "\n")

    # Extract embedded graphics
    if assets_dir and doc_slug:
        assets_dir.mkdir(parents=True, exist_ok=True)
        img_idx = 1
        try:
            for rel in doc.part.related_parts.values():
                if hasattr(rel, "content_type") and "image" in rel.content_type:
                    ext = rel.content_type.split("/")[-1].replace("jpeg", "jpg")
                    img_name = f"docx_img_{img_idx}.{ext}"
                    dest = assets_dir / img_name
                    dest.write_bytes(rel.blob)
                    lines.append(f"\n![Embedded Image: {img_name}](assets/{doc_slug}/{img_name})\n")
                    img_idx += 1
        except Exception as e:
            logger.warning("Could not extract embedded images from %s: %s", docx_path.name, e)

    return "\n".join(lines).strip()


def convert_pptx_to_markdown(pptx_path: Path) -> str:
    """Extract slide titles, bullet points, and tables from a PowerPoint (.pptx) file."""
    try:
        import pptx
    except ImportError:
        raise ImportError("python-pptx is required to parse .pptx documents. Run `pip install python-pptx`.")

    prs = pptx.Presentation(str(pptx_path))
    slides_md: List[str] = []
    pres_title = pptx_path.stem.replace("-", " ").replace("_", " ").title()

    for i, slide in enumerate(prs.slides, 1):
        title = None
        if slide.shapes.title and slide.shapes.title.text:
            title = slide.shapes.title.text.strip()

        slide_lines = [f"## Slide {i}: {title or 'Presentation Slide'}\n"]

        for shape in slide.shapes:
            if shape == slide.shapes.title:
                continue
            if shape.has_text_frame:
                for p in shape.text_frame.paragraphs:
                    txt = p.text.strip()
                    if txt and txt != title:
                        indent = "  " * p.level
                        slide_lines.append(f"{indent}- {txt}")
            elif shape.has_table:
                table_rows = []
                for row in shape.table.rows:
                    table_rows.append([cell.text.strip() for cell in row.cells])
                if table_rows:
                    slide_lines.append("\n" + _format_pipe_table(table_rows) + "\n")

        slides_md.append("\n".join(slide_lines).strip())

    if not slides_md:
        return f"# Presentation: {pres_title}\n\n*(Empty Presentation)*"
    return f"# Presentation: {pres_title}\n\n" + "\n\n-----\n\n".join(slides_md)


def convert_csv_to_markdown(csv_path: Path) -> str:
    """Convert a CSV or TSV file into clean GitHub Flavored Markdown."""
    delimiter = "\t" if csv_path.suffix.lower() == ".tsv" else ","
    rows: List[List[str]] = []
    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f, delimiter=delimiter)
        for r in reader:
            if any(cell.strip() for cell in r):
                rows.append(r)

    title = csv_path.stem.replace("-", " ").replace("_", " ").title()
    if not rows:
        return f"# Data Table: {title}\n\n*(Empty CSV file)*"

    return f"# Data Table: {title}\n\n" + _format_pipe_table(rows)


def convert_xlsx_to_markdown(xlsx_path: Path) -> str:
    """Extract sheets and tabular rows from an Excel (.xlsx) file into GFM pipe tables."""
    try:
        import openpyxl
    except ImportError:
        raise ImportError("openpyxl is required to parse .xlsx documents. Run `pip install openpyxl`.")

    wb = openpyxl.load_workbook(str(xlsx_path), data_only=True)
    sheets_md: List[str] = []

    for name in wb.sheetnames:
        ws = wb[name]
        rows: List[List[str]] = []
        for r in ws.iter_rows(values_only=True):
            if any(cell is not None and str(cell).strip() for cell in r):
                rows.append([str(c).strip() if c is not None else "" for c in r])

        if rows:
            sheets_md.append(f"## Sheet: {name}\n\n" + _format_pipe_table(rows))

    wb.close()
    title = xlsx_path.stem.replace("-", " ").replace("_", " ").title()
    if not sheets_md:
        return f"# Spreadsheet: {title}\n\n*(Empty Workbook)*"

    return f"# Spreadsheet: {title}\n\n" + "\n\n-----\n\n".join(sheets_md)


async def process_image_source(
    image_path: Path,
    wiki_dir: Optional[Path] = None,
    provider: Optional[Any] = None,
    domain_name: str = "Knowledge Wiki",
) -> str:
    """Ingest a standalone image (.png, .jpg, .webp) using multimodal vision analysis."""
    image_path = Path(image_path).resolve()
    doc_slug = slugify(image_path.stem)

    if wiki_dir:
        assets_dir = Path(wiki_dir).resolve() / "wiki" / "assets" / doc_slug
    else:
        assets_dir = image_path.parent / "assets" / doc_slug

    assets_dir.mkdir(parents=True, exist_ok=True)
    target_img_path = assets_dir / image_path.name
    if image_path != target_img_path:
        shutil.copy2(image_path, target_img_path)

    rel_asset_path = f"assets/{doc_slug}/{image_path.name}"
    title = image_path.stem.replace("-", " ").replace("_", " ").title()

    vision_text = ""
    if provider and hasattr(provider, "call_vision"):
        from lib.wiki_ops import get_template
        try:
            template = get_template("ingest_vision.md")
            prompt = template.render(
                domain_name=domain_name,
                source_filename=image_path.name,
                image_filename=image_path.name,
            )
        except Exception:
            prompt = (
                f"Transcribe and analyze this technical image ({image_path.name}) for the {domain_name}. "
                "Describe diagrams, nodes, relationships, and convert any charts or tables into Markdown pipe tables."
            )

        loop = asyncio.get_running_loop()
        try:
            vision_text = await loop.run_in_executor(
                None,
                lambda: provider.call_vision(
                    image_path=target_img_path,
                    prompt=prompt,
                    operation="vision",
                ),
            )
        except Exception as e:
            logger.warning("Vision transcription failed for %s: %s", image_path.name, e)

    if vision_text and vision_text.strip():
        formatted_callout = "\n".join(f"> {line}" for line in vision_text.strip().splitlines())
        return (
            f"# {title}\n\n"
            f"![{title}]({rel_asset_path})\n\n"
            f"> [!FIGURE: {image_path.name}]\n"
            f"{formatted_callout}\n"
        )

    return f"# {title}\n\n![{title}]({rel_asset_path})\n\n*(Standalone image asset: {image_path.name})*\n"


async def process_html_source(
    html_path: Path,
    wiki_dir: Optional[Path] = None,
    provider: Optional[Any] = None,
    domain_name: str = "Knowledge Wiki",
) -> str:
    """Ingest an HTML file with base64 image extraction, local asset management, and vision analysis."""
    from bs4 import BeautifulSoup
    import html2text
    from lib.pdf_processor import filter_extracted_images, enrich_markdown_with_vision

    html_path = Path(html_path).resolve()
    doc_slug = slugify(html_path.stem)

    if wiki_dir:
        assets_dir = Path(wiki_dir).resolve() / "wiki" / "assets" / doc_slug
    else:
        assets_dir = html_path.parent / "assets" / doc_slug

    assets_dir.mkdir(parents=True, exist_ok=True)

    raw_html = html_path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(raw_html, "html.parser")

    # Strip noisy interactive / script tags
    for tag in soup(["script", "style", "noscript", "meta", "link", "nav", "footer", "aside"]):
        tag.decompose()

    extracted_images: Set[str] = set()

    # Extract embedded and local images
    for idx, img in enumerate(soup.find_all("img")):
        src = img.get("src", "")
        if not src:
            continue

        if src.startswith("data:image/"):
            # Embedded base64 data URI
            try:
                header, encoded = src.split(",", 1)
                mime = header.split(";")[0].split(":")[1]
                ext = mime.split("/")[-1].replace("jpeg", "jpg").replace("svg+xml", "svg")
                img_name = f"embedded_img_{idx + 1}.{ext}"
                target_file = assets_dir / img_name
                target_file.write_bytes(base64.b64decode(encoded))
                img["src"] = f"assets/{doc_slug}/{img_name}"
                extracted_images.add(img_name)
            except Exception as e:
                logger.warning("Failed decoding base64 image %d in %s: %s", idx, html_path.name, e)
        elif not src.startswith("http://") and not src.startswith("https://"):
            # Local referenced asset
            candidate = (html_path.parent / src).resolve()
            if candidate.is_file():
                dest = assets_dir / candidate.name
                shutil.copy2(candidate, dest)
                img["src"] = f"assets/{doc_slug}/{candidate.name}"
                extracted_images.add(candidate.name)

    # Convert cleaned DOM to markdown
    converter = html2text.HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = False
    converter.body_width = 0
    converter.protect_links = True
    converter.unicode_snob = True
    converter.mark_code = True

    rendered_md = converter.handle(str(soup)).strip()

    # Filter out noise graphics and enrich figures with multimodal vision
    valid_images = filter_extracted_images(assets_dir, min_dim=60, min_pixels=5000)
    if valid_images and provider and hasattr(provider, "call_vision"):
        rendered_md = await enrich_markdown_with_vision(
            markdown_text=rendered_md,
            assets_dir=assets_dir,
            valid_image_names=valid_images,
            source_filename=html_path.name,
            doc_slug=doc_slug,
            provider=provider,
            domain_name=domain_name,
        )

    return rendered_md
