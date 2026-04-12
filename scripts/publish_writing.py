#!/usr/bin/env python3
"""Publish a markdown file as a writing on the static site."""

from __future__ import annotations

import argparse
import html
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

try:
    import markdown as markdown_lib  # type: ignore
except ImportError:
    markdown_lib = None


ROOT = Path(__file__).resolve().parent.parent
ESSAY_DIR = ROOT / "Essay_Folder"
WRITINGS_PATH = ROOT / "writings.html"

FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
META_TAG_RE = re.compile(
    r'<meta\s+name="(?P<name>post:[^"]+)"\s+content="(?P<content>[^"]*)"\s*/?>',
    re.IGNORECASE,
)
H1_RE = re.compile(r"<h1>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
ARTICLE_META_RE = re.compile(
    r'<p class="article-meta">Published on (.*?)</p>', re.IGNORECASE | re.DOTALL
)
FIRST_PARAGRAPH_RE = re.compile(r"<p(?:\s+class=\"[^\"]+\")?>(.*?)</p>", re.IGNORECASE | re.DOTALL)
LIST_ITEM_RE = re.compile(r"^(?P<indent>\s*)(?P<marker>[-+*]|\d+\.)\s+(?P<content>.+)$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
CODE_RE = re.compile(r"`([^`]+)`")
BOLD_RE = re.compile(r"(\*\*|__)(.+?)\1")
ITALIC_RE = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)|(?<!_)_(?!\s)(.+?)(?<!\s)_(?!_)")


@dataclass
class Post:
    title: str
    slug: str
    iso_date: str
    display_date: str
    excerpt: str
    output_path: Path
    body_html: str

    @property
    def relative_output_path(self) -> str:
        return self.output_path.relative_to(ROOT).as_posix()


@dataclass
class ListToken:
    indent: int
    ordered: bool
    content: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert markdown into a blog post and update writings.html."
    )
    parser.add_argument("markdown_path", help="Path to the markdown file to publish.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Prompt for metadata and print the inferred output without writing files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    markdown_path = Path(args.markdown_path).expanduser().resolve()

    if not markdown_path.exists():
        print(f"Markdown file not found: {markdown_path}", file=sys.stderr)
        return 1

    raw_text = markdown_path.read_text(encoding="utf-8")
    front_matter, body_text = parse_front_matter(raw_text)
    fallback_title = title_from_filename(markdown_path.stem)
    inferred_title, body_text = resolve_title(front_matter.get("title"), body_text, fallback_title)
    inferred_date = normalize_date(front_matter.get("date"))
    inferred_excerpt = normalize_excerpt(
        front_matter.get("excerpt") or infer_excerpt(body_text, inferred_title)
    )

    try:
        title = prompt_with_default("Title", inferred_title)
        iso_date = prompt_date(inferred_date)
        excerpt = prompt_with_default("Excerpt", inferred_excerpt)
        push_now = prompt_yes_no("Push right now? [y/N]", default=False)
    except UserAbort:
        print("Aborted. No files were created.")
        return 1

    slug = slugify(title)
    display_date = format_display_date(iso_date)
    excerpt = normalize_excerpt(excerpt)
    output_path = ESSAY_DIR / f"{slug}.html"
    body_html = render_markdown(body_text)

    post = Post(
        title=title,
        slug=slug,
        iso_date=iso_date,
        display_date=display_date,
        excerpt=excerpt,
        output_path=output_path,
        body_html=body_html,
    )

    if args.dry_run:
        print(f"title: {post.title}")
        print(f"slug: {post.slug}")
        print(f"date: {post.iso_date}")
        print(f"excerpt: {post.excerpt}")
        print(f"output: {post.output_path}")
        print(f"push: {'yes' if push_now else 'no'}")
        return 0

    ESSAY_DIR.mkdir(parents=True, exist_ok=True)
    post.output_path.write_text(build_article_html(post), encoding="utf-8")

    posts = discover_posts()
    posts = [existing for existing in posts if existing.relative_output_path != post.relative_output_path]
    posts.append(post)
    posts.sort(key=lambda item: (item.iso_date, item.title.lower()), reverse=True)

    update_writings_index(posts)

    if push_now:
        try:
            commit_and_push(post, markdown_path)
        except subprocess.CalledProcessError as error:
            print(f"Publish files created, but git failed: {error}", file=sys.stderr)
            return error.returncode or 1

    print(f"Published {post.title}")
    print(f"Article: {post.output_path}")
    print(f"Index: {WRITINGS_PATH}")
    return 0


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    match = FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text

    metadata: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip().lower()] = value.strip()
    return metadata, text[match.end() :]


class UserAbort(Exception):
    """Raised when the user aborts an interactive prompt."""


def resolve_title(
    front_matter_title: str | None,
    body_text: str,
    fallback_title: str,
) -> tuple[str, str]:
    if front_matter_title:
        return front_matter_title.strip(), body_text

    lines = body_text.splitlines()
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line.strip())
        if match and len(match.group(1)) == 1:
            title = match.group(2).strip()
            remaining = lines[:index] + lines[index + 1 :]
            return title, "\n".join(remaining).lstrip()
    return fallback_title, body_text


def title_from_filename(stem: str) -> str:
    cleaned = stem.replace("_", " ").replace("-", " ").strip()
    words = [word.capitalize() for word in cleaned.split()]
    return " ".join(words) or "Untitled Post"


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "untitled-post"


def normalize_date(value: str | None) -> str:
    if not value:
        return date.today().isoformat()

    value = value.strip()
    for fmt in ("%Y-%m-%d", "%B %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    raise SystemExit(f"Unsupported date format: {value}. Use YYYY-MM-DD.")


def prompt_with_default(label: str, default: str) -> str:
    prompt = f"{label} [{default}]: "
    while True:
        response = prompt_input(prompt).strip()
        if response:
            return response
        if default:
            return default
        print(f"{label} cannot be empty.")


def prompt_date(default_iso_date: str) -> str:
    prompt = f"Date [{default_iso_date}] (YYYY-MM-DD): "
    while True:
        response = prompt_input(prompt).strip()
        if not response:
            return default_iso_date
        try:
            return normalize_date(response)
        except SystemExit as error:
            print(error)


def prompt_yes_no(prompt: str, default: bool) -> bool:
    while True:
        response = prompt_input(f"{prompt} ").strip().lower()
        if not response:
            return default
        if response in {"y", "yes"}:
            return True
        if response in {"n", "no"}:
            return False
        print("Enter y or n.")


def prompt_input(prompt: str) -> str:
    try:
        response = input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        raise UserAbort()

    if response.strip().lower() in {"abort", "quit", "exit"}:
        raise UserAbort()
    return response


def format_display_date(iso_value: str) -> str:
    return datetime.strptime(iso_value, "%Y-%m-%d").strftime("%B %d, %Y")


def normalize_excerpt(text: str, limit: int = 160) -> str:
    collapsed = re.sub(r"\s+", " ", strip_markdown(text)).strip()
    if len(collapsed) <= limit:
        return collapsed
    trimmed = collapsed[:limit].rsplit(" ", 1)[0].strip()
    return f"{trimmed}..."


def infer_excerpt(body_text: str, title: str) -> str:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", body_text) if block.strip()]
    for block in blocks:
        if HEADING_RE.match(block.splitlines()[0].strip()):
            continue
        if LIST_ITEM_RE.match(block.splitlines()[0]):
            continue
        excerpt = strip_markdown(" ".join(line.strip() for line in block.splitlines()))
        if excerpt and excerpt != title:
            return excerpt
    return title


def strip_markdown(text: str) -> str:
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"\1", text)
    text = LINK_RE.sub(r"\1", text)
    text = re.sub(r"(\*\*|__|\*|_)(.+?)\1", r"\2", text)
    text = CODE_RE.sub(r"\1", text)
    text = text.replace("\\", "")
    return text


def render_markdown(text: str) -> str:
    if markdown_lib is not None:
        return markdown_lib.markdown(text, extensions=["extra", "sane_lists"])
    return render_markdown_fallback(text)


def render_markdown_fallback(text: str) -> str:
    blocks = [block for block in re.split(r"\n\s*\n", text.strip()) if block.strip()]
    rendered: list[str] = []

    for block in blocks:
        lines = block.splitlines()
        heading_match = HEADING_RE.match(lines[0].strip())
        if heading_match:
            level = len(heading_match.group(1))
            rendered.append(f"<h{level}>{render_inline(heading_match.group(2).strip())}</h{level}>")
            continue
        if all(LIST_ITEM_RE.match(line) or not line.strip() for line in lines):
            rendered.append(render_list_block(lines))
            continue
        rendered.append(render_paragraph(block))

    return "\n\n".join(rendered)


def render_paragraph(block: str) -> str:
    lines = [line.strip() for line in block.splitlines()]
    text = " ".join(line for line in lines if line)
    return f"<p>{render_inline(text)}</p>"


def render_list_block(lines: Iterable[str]) -> str:
    tokens = tokenize_list_block(lines)
    if not tokens:
        return ""

    parts: list[str] = []
    index = 0
    while index < len(tokens):
        html_block, index = render_list(tokens, index, tokens[index].indent, tokens[index].ordered)
        parts.append(html_block)
    return "\n".join(parts)


def tokenize_list_block(lines: Iterable[str]) -> list[ListToken]:
    tokens: list[ListToken] = []
    for line in lines:
        if not line.strip():
            continue
        match = LIST_ITEM_RE.match(line)
        if match:
            indent = len(match.group("indent").replace("\t", "    "))
            ordered = match.group("marker").endswith(".")
            content = match.group("content").strip()
            tokens.append(ListToken(indent=indent, ordered=ordered, content=content))
            continue
        if tokens:
            tokens[-1].content = f"{tokens[-1].content} {line.strip()}"
    return tokens


def render_list(tokens: list[ListToken], start: int, indent: int, ordered: bool) -> tuple[str, int]:
    tag = "ol" if ordered else "ul"
    parts = [f"<{tag}>"]
    index = start

    while index < len(tokens):
        token = tokens[index]
        if token.indent < indent:
            break
        if token.indent > indent or token.ordered != ordered:
            break

        parts.append(f"<li>{render_inline(token.content)}")
        index += 1

        while index < len(tokens) and tokens[index].indent > indent:
            nested_html, index = render_list(
                tokens, index, tokens[index].indent, tokens[index].ordered
            )
            parts.append(nested_html)

        parts.append("</li>")

    parts.append(f"</{tag}>")
    return "".join(parts), index


def render_inline(text: str) -> str:
    placeholders: dict[str, str] = {}

    def store(value: str) -> str:
        key = f"@@PLACEHOLDER{len(placeholders)}@@"
        placeholders[key] = value
        return key

    text = CODE_RE.sub(lambda match: store(f"<code>{html.escape(match.group(1))}</code>"), text)
    escaped = html.escape(text)
    escaped = escaped.replace("\\*", "*").replace("\\_", "_").replace("\\[", "[").replace("\\]", "]")
    escaped = escaped.replace("\\!", "!").replace("\\`", "`")
    escaped = LINK_RE.sub(
        lambda match: f'<a href="{html.escape(match.group(2), quote=True)}">{match.group(1)}</a>',
        escaped,
    )
    escaped = BOLD_RE.sub(lambda match: f"<strong>{match.group(2)}</strong>", escaped)
    escaped = ITALIC_RE.sub(
        lambda match: f"<em>{next(group for group in match.groups() if group)}</em>",
        escaped,
    )

    for key, value in placeholders.items():
        escaped = escaped.replace(key, value)
    return escaped


def build_article_html(post: Post) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(post.title)} - Daniel Li</title>
    <meta name="post:title" content="{html.escape(post.title, quote=True)}">
    <meta name="post:date" content="{post.iso_date}">
    <meta name="post:display-date" content="{html.escape(post.display_date, quote=True)}">
    <meta name="post:excerpt" content="{html.escape(post.excerpt, quote=True)}">
    <link rel="icon" type="image/webp" href="../favicon.webp">
    <link rel="stylesheet" href="../styles.css">
</head>
<body>
    <header>
        <div class="breadcrumb">
            <a href="../index.html">Daniel Li</a> &gt; <a href="../writings.html">Writings</a> &gt; <span>{html.escape(post.title)}</span>
        </div>
    </header>

    <main>
        <article>
            <h1>{html.escape(post.title)}</h1>
            <p class="article-meta">Published on {html.escape(post.display_date)}</p>

            {post.body_html}
        </article>
    </main>
</body>
</html>
"""


def commit_and_push(post: Post, markdown_path: Path) -> None:
    add_paths = [post.output_path, WRITINGS_PATH]
    try:
        markdown_relative = markdown_path.resolve().relative_to(ROOT)
        add_paths.append(ROOT / markdown_relative)
    except ValueError:
        pass

    add_command = ["git", "add", "--", *[str(path) for path in add_paths]]
    commit_command = ["git", "commit", "-m", f"add writing {post.title}"]
    push_command = ["git", "push", "origin", "main"]

    subprocess.run(add_command, cwd=ROOT, check=True)
    subprocess.run(commit_command, cwd=ROOT, check=True)
    subprocess.run(push_command, cwd=ROOT, check=True)


def discover_posts() -> list[Post]:
    posts: list[Post] = []
    for path in sorted(ESSAY_DIR.glob("*.html")):
        if path.name == "TEMPLATE.html":
            continue
        post = load_existing_post(path)
        if post is not None:
            posts.append(post)
    return posts


def load_existing_post(path: Path) -> Post | None:
    content = path.read_text(encoding="utf-8")
    meta = {match.group("name"): html.unescape(match.group("content")) for match in META_TAG_RE.finditer(content)}

    title = meta.get("post:title") or extract_with_regex(H1_RE, content)
    raw_date = meta.get("post:date")
    display_date = meta.get("post:display-date")
    excerpt = meta.get("post:excerpt")

    if not title:
        return None

    if raw_date:
        iso_date = normalize_date(raw_date)
    else:
        article_date = extract_with_regex(ARTICLE_META_RE, content)
        if not article_date:
            return None
        iso_date = normalize_date(html.unescape(strip_tags(article_date)))
        display_date = display_date or format_display_date(iso_date)

    if not display_date:
        display_date = format_display_date(iso_date)

    if not excerpt:
        excerpt = extract_first_content_paragraph(content) or title

    return Post(
        title=html.unescape(strip_tags(title)),
        slug=path.stem,
        iso_date=iso_date,
        display_date=display_date,
        excerpt=normalize_excerpt(excerpt),
        output_path=path,
        body_html="",
    )


def extract_with_regex(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def extract_first_content_paragraph(content: str) -> str | None:
    for match in FIRST_PARAGRAPH_RE.finditer(content):
        paragraph = match.group(1)
        if 'class="article-meta"' in match.group(0):
            continue
        cleaned = html.unescape(strip_tags(paragraph)).strip()
        if cleaned:
            return cleaned
    return None


def strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value)


def update_writings_index(posts: list[Post]) -> None:
    section = build_writings_section(posts)
    if WRITINGS_PATH.exists():
        content = WRITINGS_PATH.read_text(encoding="utf-8")
    else:
        content = build_writings_page(section)
        WRITINGS_PATH.write_text(content, encoding="utf-8")
        return

    pattern = re.compile(r'<section class="writings-list">.*?</section>', re.DOTALL)
    if pattern.search(content):
        updated = pattern.sub(section, content, count=1)
    else:
        updated = build_writings_page(section)
    WRITINGS_PATH.write_text(updated, encoding="utf-8")


def build_writings_section(posts: list[Post]) -> str:
    grouped: dict[str, list[Post]] = {}
    for post in posts:
        grouped.setdefault(post.iso_date[:4], []).append(post)

    lines = ["        <section class=\"writings-list\">"]
    for year in sorted(grouped.keys(), reverse=True):
        lines.append("            <div class=\"year-section\">")
        lines.append(f"                <h2>{year}</h2>")
        for post in grouped[year]:
            lines.append("                <div class=\"article-item\">")
            lines.append(
                f"                    <h3><a href=\"{html.escape(post.relative_output_path)}\">{html.escape(post.title)}</a></h3>"
            )
            lines.append(
                f"                    <p class=\"writing-excerpt\">Published on {html.escape(post.display_date)}.</p>"
            )
            lines.append(
                f"                    <p class=\"writing-excerpt\">{html.escape(post.excerpt)}</p>"
            )
            lines.append("                </div>")
        lines.append("            </div>")
    lines.append("        </section>")
    return "\n".join(lines)


def build_writings_page(section: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Daniel Li Writings</title>
    <link rel="icon" type="image/webp" href="favicon.webp">
    <link rel="stylesheet" href="styles.css">
</head>
<body>
    <header>
        <div class="breadcrumb">
            <a href="index.html">Daniel Li</a> &gt; <span>Writings</span>
        </div>
    </header>

    <main>
        <section class="page-header">
            <p>My thoughts, articles, and reflections on various topics</p>
        </section>

{section}
    </main>

</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
