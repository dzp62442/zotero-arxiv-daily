from datetime import datetime

from omegaconf import OmegaConf

from tests.canned_responses import make_sample_paper
from zotero_arxiv_daily.construct_markdown import render_markdown, write_markdown_report


def test_render_markdown_with_papers():
    papers = [make_sample_paper(score=7.5, tldr="A great paper.", affiliations=["MIT"])]
    markdown = render_markdown(
        papers,
        generated_at=datetime(2026, 6, 8, 10, 0, 0),
        download_stats={
            "arxiv": {
                "source_tar_bytes": 1024,
                "pdf_bytes": 2048,
                "html_bytes": 512,
                "total_bytes": 3584,
            }
        },
    )

    assert "# Zotero arXiv Daily - 2026-06-08" in markdown
    assert "> Download traffic: arxiv 3.5 KiB" in markdown
    assert "## Download Traffic" not in markdown
    assert "## 每日 arxiv 推送" in markdown
    assert "### 1. Sample Paper Title" in markdown
    assert "3.5 KiB" in markdown
    assert "Sample Paper Title" in markdown
    assert "**TL;DR:** A great paper." in markdown
    assert "A great paper." in markdown
    assert "MIT" in markdown
    assert "[PDF](https://arxiv.org/pdf/2026.00001)" in markdown


def test_render_markdown_empty_list():
    markdown = render_markdown([], generated_at=datetime(2026, 6, 8, 10, 0, 0))

    assert "No Papers Today" in markdown
    assert "0 papers" in markdown


def test_render_markdown_truncates_authors_and_affiliations():
    paper = make_sample_paper(
        authors=[f"Author {i}" for i in range(10)],
        affiliations=[f"Uni {i}" for i in range(8)],
        score=7.0,
        tldr="ok",
    )
    markdown = render_markdown([paper], generated_at=datetime(2026, 6, 8, 10, 0, 0))

    assert "Author 0" in markdown
    assert "Author 5" not in markdown
    assert "Author 9" in markdown
    assert "Uni 0" in markdown
    assert "Uni 7" not in markdown


def test_write_markdown_report(tmp_path):
    config = OmegaConf.create(
        {
            "output": {
                "dir": str(tmp_path),
                "filename_template": "report-{date}.md",
            }
        }
    )
    output_path = write_markdown_report(
        config,
        "hello",
        generated_at=datetime(2026, 6, 8, 10, 0, 0),
    )

    assert output_path == tmp_path / "report-2026-06-08.md"
    assert output_path.read_text(encoding="utf-8") == "hello"
