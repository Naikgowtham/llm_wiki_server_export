"""Advanced PDF processor: image extraction, noise filtering, and multimodal vision transcription."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, List, Optional, Set, Tuple

import pymupdf
import pymupdf4llm

from lib.utils import slugify

logger = logging.getLogger(__name__)


def filter_extracted_images(
    assets_dir: Path,
    min_dim: int = 120,
    min_pixels: int = 20000,
    max_aspect_ratio: float = 5.0,
) -> Set[str]:
    """Filter out noise graphics (icons, bullets, divider lines, logos) from extracted PDF assets.

    Returns:
        Set of filenames for valid, retained images.
    """
    valid_filenames: Set[str] = set()
    if not assets_dir.is_dir():
        return valid_filenames

    for img_file in list(assets_dir.iterdir()):
        if not img_file.is_file() or img_file.name.startswith("."):
            continue

        try:
            pix = pymupdf.Pixmap(str(img_file))
            w, h = pix.width, pix.height
            del pix

            # Noise heuristics
            is_too_small = w < min_dim or h < min_dim
            is_low_pixel = (w * h) < min_pixels
            aspect_ratio = max(w / max(1, h), h / max(1, w))
            is_extreme_ratio = aspect_ratio > max_aspect_ratio

            if is_too_small or is_low_pixel or is_extreme_ratio:
                logger.debug(
                    "Discarding noise image %s (%dx%d, ratio=%.1f)",
                    img_file.name,
                    w,
                    h,
                    aspect_ratio,
                )
                img_file.unlink(missing_ok=True)
            else:
                logger.debug(
                    "Retaining valid figure %s (%dx%d, ratio=%.1f)",
                    img_file.name,
                    w,
                    h,
                    aspect_ratio,
                )
                valid_filenames.add(img_file.name)
        except Exception as e:
            logger.warning("Could not inspect image %s: %s. Keeping by default.", img_file.name, e)
            valid_filenames.add(img_file.name)

    return valid_filenames


def cleanup_markdown_image_tags(
    markdown_text: str,
    valid_image_names: Set[str],
    doc_slug: str,
) -> str:
    """Normalize valid image links and remove links to discarded noise images.

    Ensures paths follow the Obsidian vault convention: assets/<doc_slug>/<filename>
    """
    # Regex matches standard markdown image syntax: ![alt](path)
    img_pattern = re.compile(r"!\[(.*?)\]\(([^\)]+)\)")

    def _replace_tag(match: re.Match) -> str:
        alt_text = match.group(1)
        raw_path = match.group(2)
        target_name = Path(raw_path).name

        if target_name in valid_image_names:
            clean_rel_path = f"assets/{doc_slug}/{target_name}"
            clean_alt = alt_text.strip() or f"Figure: {target_name}"
            return f"![{clean_alt}]({clean_rel_path})"
        # If image was filtered out as noise, strip the tag completely
        return ""

    cleaned = img_pattern.sub(_replace_tag, markdown_text)
    # Clean up redundant empty lines from stripped images
    return re.sub(r"\n{3,}", "\n\n", cleaned)


async def enrich_markdown_with_vision(
    markdown_text: str,
    assets_dir: Path,
    valid_image_names: Set[str],
    source_filename: str,
    doc_slug: str,
    provider: Optional[Any] = None,
    domain_name: str = "Knowledge Wiki",
) -> str:
    """Asynchronously transcribe valid figures into structured Markdown and inject into content."""
    if not valid_image_names or not provider or not hasattr(provider, "call_vision"):
        # No vision provider or no valid images; return cleaned markdown
        return cleanup_markdown_image_tags(markdown_text, valid_image_names, doc_slug)

    # Clean markdown image tags first
    cleaned_md = cleanup_markdown_image_tags(markdown_text, valid_image_names, doc_slug)

    # Load prompt template
    from lib.wiki_ops import get_template

    try:
        vision_template = get_template("ingest_vision.md")
    except Exception as e:
        logger.warning("Could not load ingest_vision.md template: %s. Skipping vision enrichment.", e)
        return cleaned_md

    semaphore = asyncio.Semaphore(2)

    async def _transcribe_image(img_name: str) -> Tuple[str, Optional[str]]:
        img_path = assets_dir / img_name
        if not img_path.is_file():
            return img_name, None

        prompt = vision_template.render(
            domain_name=domain_name,
            source_filename=source_filename,
            image_filename=img_name,
        )

        async with semaphore:
            await asyncio.sleep(0.3)  # Gentle pacing between vision calls
            loop = asyncio.get_running_loop()
            try:
                # Run synchronous call_vision in threadpool
                result = await loop.run_in_executor(
                    None,
                    lambda: provider.call_vision(
                        image_path=img_path,
                        prompt=prompt,
                        operation="vision",
                    ),
                )
                return img_name, result
            except Exception as ex:
                logger.warning("Vision transcription task failed for %s: %s", img_name, ex)
                return img_name, None

    # Run vision tasks concurrently across retained images
    tasks = [_transcribe_image(img_name) for img_name in sorted(valid_image_names)]
    results = await asyncio.gather(*tasks)

    # Map filename -> transcription
    transcriptions: dict[str, str] = {}
    for img_name, trans_text in results:
        if trans_text and trans_text.strip():
            transcriptions[img_name] = trans_text.strip()

    if not transcriptions:
        return cleaned_md

    # Splice transcriptions directly after their image tags
    for img_name, trans_text in transcriptions.items():
        rel_path = f"assets/{doc_slug}/{img_name}"
        # Target regex matching this specific figure
        fig_regex = re.compile(rf"(!\[[^\]]*\]\({re.escape(rel_path)}\))")

        def _insert_enrichment(m: re.Match) -> str:
            tag = m.group(1)
            formatted_transcription = f"\n\n> [!FIGURE: {img_name}]\n" + "\n".join(
                f"> {line}" for line in trans_text.splitlines()
            )
            return f"{tag}\n{formatted_transcription}\n"

        if fig_regex.search(cleaned_md):
            cleaned_md = fig_regex.sub(_insert_enrichment, cleaned_md, count=1)
        else:
            # If tag not matched, append figure analysis to the bottom
            cleaned_md += (
                f"\n\n![Figure: {img_name}]({rel_path})\n\n> [!FIGURE: {img_name}]\n"
                + "\n".join(f"> {line}" for line in trans_text.splitlines())
            )

    return re.sub(r"\n{3,}", "\n\n", cleaned_md)


async def ocr_scanned_pdf(
    pdf_path: Path,
    assets_dir: Path,
    provider: Any,
    domain_name: str = "Knowledge Wiki",
    source_filename: Optional[str] = None,
    max_pages: int = 50,
) -> str:
    """Fallback OCR pipeline for scanned/rasterized PDFs using multimodal vision.

    Renders each PDF page to a high-resolution PNG image and transcribes it
    verbatim into structured Markdown using the active vision model.
    """
    if not provider or not hasattr(provider, "call_vision"):
        return ""

    src_name = source_filename or pdf_path.name
    ocr_dir = assets_dir / "ocr_pages"
    ocr_dir.mkdir(parents=True, exist_ok=True)

    loop = asyncio.get_running_loop()

    def _render_all_pages() -> List[Tuple[int, Path, int]]:
        doc = pymupdf.open(str(pdf_path))
        total = len(doc)
        limit = min(total, max_pages)
        pages_to_ocr = []
        for i in range(limit):
            page = doc[i]
            # 150 DPI provides clear OCR rendering for text & tables without excessive payload size
            pix = page.get_pixmap(dpi=150)
            page_img_path = ocr_dir / f"page_{i + 1}.png"
            pix.save(str(page_img_path))
            pages_to_ocr.append((i + 1, page_img_path, total))
        doc.close()
        return pages_to_ocr

    rendered_pages = await loop.run_in_executor(None, _render_all_pages)
    if not rendered_pages:
        return ""

    # Load OCR prompt template
    from lib.wiki_ops import get_template
    try:
        ocr_template = get_template("ingest_ocr.md")
    except Exception as e:
        logger.warning("Could not load ingest_ocr.md template: %s. Using default OCR prompt.", e)
        ocr_template = None

    semaphore = asyncio.Semaphore(2)

    async def _ocr_single_page(page_num: int, img_path: Path, total: int) -> Tuple[int, Optional[str]]:
        if ocr_template:
            prompt = ocr_template.render(
                domain_name=domain_name,
                source_filename=src_name,
                page_number=page_num,
                total_pages=total,
            )
        else:
            prompt = (
                f"Transcribe all text, questions, lists, and tables visible on this scanned document page (Page {page_num} of {total}) "
                "into clean, exhaustive Markdown. Format tables as GFM pipe tables. Do not omit any content."
            )

        async with semaphore:
            await asyncio.sleep(0.3)
            try:
                result = await loop.run_in_executor(
                    None,
                    lambda: provider.call_vision(
                        image_path=img_path,
                        prompt=prompt,
                        operation="vision",
                    ),
                )
                return page_num, result
            except Exception as ex:
                logger.warning("Vision OCR failed for page %d of %s: %s", page_num, src_name, ex)
                return page_num, None

    tasks = [_ocr_single_page(p_num, path, tot) for p_num, path, tot in rendered_pages]
    results = await asyncio.gather(*tasks)

    page_blocks = []
    for p_num, page_md in sorted(results, key=lambda x: x[0]):
        if page_md and page_md.strip():
            page_blocks.append(f"<!-- Page {p_num} -->\n\n{page_md.strip()}")

    if not page_blocks:
        return ""

    logger.info("Successfully OCR'd %d page(s) from scanned PDF %s.", len(page_blocks), src_name)
    return "\n\n-----\n\n".join(page_blocks)


async def process_pdf_source(
    pdf_path: Path,
    wiki_dir: Optional[Path] = None,
    provider: Optional[Any] = None,
    domain_name: str = "Knowledge Wiki",
    min_dim: int = 120,
) -> str:
    """Master entrypoint to ingest a PDF file with image extraction, filtering, and vision transcription.

    Args:
        pdf_path: Path to the raw PDF document.
        wiki_dir: Path to the root of the wiki vault.
        provider: Active LLMProvider instance with vision capability.
        domain_name: Wiki domain name for prompt context.
        min_dim: Minimum pixel dimension for noise image filtering.

    Returns:
        Structured Markdown string enriched with table syntax and vision transcriptions.
    """
    pdf_path = Path(pdf_path).resolve()
    doc_slug = slugify(pdf_path.stem)

    if wiki_dir:
        wiki_dir = Path(wiki_dir).resolve()
        assets_dir = wiki_dir / "wiki" / "assets" / doc_slug
    else:
        assets_dir = pdf_path.parent / "assets" / doc_slug

    assets_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Extracting PDF layout and embedded graphics for %s...", pdf_path.name)

    # 1. Convert PDF pages to markdown and write extracted images to disk
    loop = asyncio.get_running_loop()
    raw_markdown = await loop.run_in_executor(
        None,
        lambda: pymupdf4llm.to_markdown(
            str(pdf_path),
            write_images=True,
            image_path=str(assets_dir),
        ),
    )

    # Check for scanned PDF (minimal selectable digital text)
    clean_text = re.sub(r"!\[.*?\]\(.*?\)", "", raw_markdown)
    clean_text = re.sub(r"[\s\-_=|#*`]+", " ", clean_text).strip()
    words = clean_text.split()
    if len(words) < 50 and provider and hasattr(provider, "call_vision"):
        logger.info(
            "PDF %s contains minimal selectable text (%d words). Triggering multimodal vision OCR fallback...",
            pdf_path.name,
            len(words),
        )
        ocr_markdown = await ocr_scanned_pdf(
            pdf_path=pdf_path,
            assets_dir=assets_dir,
            provider=provider,
            domain_name=domain_name,
            source_filename=pdf_path.name,
        )
        if ocr_markdown and ocr_markdown.strip():
            return ocr_markdown

    # 2. Filter out decorative noise, logos, and tiny icons
    valid_images = filter_extracted_images(assets_dir, min_dim=min_dim)
    logger.info("Retained %d valid figure(s) from %s after noise filtering.", len(valid_images), pdf_path.name)

    # 3. Enrich markdown with multimodal vision analysis
    enriched_markdown = await enrich_markdown_with_vision(
        markdown_text=raw_markdown,
        assets_dir=assets_dir,
        valid_image_names=valid_images,
        source_filename=pdf_path.name,
        doc_slug=doc_slug,
        provider=provider,
        domain_name=domain_name,
    )

    return enriched_markdown
