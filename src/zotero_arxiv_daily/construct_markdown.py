from datetime import datetime
from pathlib import Path

from omegaconf import DictConfig

from .protocol import Paper


def _markdown_escape(text: object) -> str:
    if text is None:
        return "Unknown"
    return str(text).replace("\\", "\\\\").replace("`", "\\`")


def _format_authors(authors: list[str]) -> str:
    if len(authors) <= 5:
        return ", ".join(authors)
    return ", ".join(authors[:3] + ["..."] + authors[-2:])


def _format_affiliations(affiliations: list[str] | None) -> str:
    if not affiliations:
        return "Unknown Affiliation"

    visible_affiliations = affiliations[:5]
    suffix = ", ..." if len(affiliations) > 5 else ""
    return ", ".join(visible_affiliations) + suffix


def render_markdown(papers: list[Paper], generated_at: datetime | None = None) -> str:
    generated_at = generated_at or datetime.now()
    today = generated_at.strftime("%Y-%m-%d")
    count_text = f"{len(papers)} papers" if len(papers) != 1 else "1 paper"

    lines = [
        f"# Zotero arXiv Daily - {today}",
        "",
        f"> Generated at {generated_at.strftime('%Y-%m-%d %H:%M:%S')} | {count_text}",
        "",
    ]

    if not papers:
        lines.extend(["## No Papers Today", "", "Take a rest.", ""])
        return "\n".join(lines)

    for index, paper in enumerate(papers, start=1):
        score = round(paper.score, 1) if paper.score is not None else "Unknown"
        authors = _markdown_escape(_format_authors(paper.authors))
        affiliations = _markdown_escape(_format_affiliations(paper.affiliations))
        tldr = _markdown_escape(paper.tldr or paper.abstract or "No summary available.")
        pdf_url = paper.pdf_url or paper.url

        lines.extend(
            [
                f"## {index}. {_markdown_escape(paper.title)}",
                "",
                f"- **Source:** {_markdown_escape(paper.source)}",
                f"- **Relevance:** {score}",
                f"- **Authors:** {authors}",
                f"- **Affiliations:** {affiliations}",
                f"- **Links:** [Paper]({paper.url}) | [PDF]({pdf_url})",
                "",
                "**TL;DR**  ",
                tldr,
                "",
                "---",
                "",
            ]
        )

    return "\n".join(lines)


def write_markdown_report(config: DictConfig, markdown: str, generated_at: datetime | None = None) -> Path:
    generated_at = generated_at or datetime.now()
    output_config = config.get("output", {})
    output_dir = Path(output_config.get("dir", "outputs"))
    filename_template = output_config.get("filename_template", "daily-arxiv-{date}.md")
    filename = filename_template.format(
        date=generated_at.strftime("%Y-%m-%d"),
        datetime=generated_at.strftime("%Y-%m-%d-%H%M%S"),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename
    output_path.write_text(markdown, encoding="utf-8")
    return output_path
