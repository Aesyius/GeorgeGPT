"""Generate static pdoc HTML; never start a documentation server."""

from pathlib import Path
from html import escape
import shutil
import sys


def main() -> None:
    """Render the guide and public Python APIs as portable, server-free HTML.

    Writes under project-root `docs/html`, normalizes machine-specific checkout
    paths in defaults, and makes the user guide the landing page. Requires pdoc
    plus runtime import dependencies; does not launch application services.
    """
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "docs"))
    try:
        import pdoc
    except ImportError:
        raise SystemExit(
            "Install documentation dependencies first: "
            r".\.venv\Scripts\python.exe -m pip install -r requirements-docs.txt"
        ) from None

    output = root / "docs" / "html"
    pdoc.render.configure(
        template_directory=root / "docs" / "templates",
        docformat="google",
        search=False, show_source=False,
        footer_text="GeorgeGPT — local repository assistant",
    )
    pdoc.pdoc(
        "user_guide", "common", "vectorizer", "Interface", "services", "evaluation",
        output_directory=output,
    )
    # Runtime Path defaults include the build machine's absolute checkout path.
    # Keep the distributable reference portable without changing application data.
    prefixes = {str(root), root.as_posix(), str(root).replace("\\", "\\\\")}
    for page in output.rglob("*.html"):
        text = page.read_text(encoding="utf-8")
        for prefix in sorted(prefixes, key=len, reverse=True):
            text = text.replace(escape(prefix), "{PROJECT_ROOT}")
        # pdoc templates emit whitespace-only lines; keep generated diffs clean.
        text = "\n".join(line.rstrip(" \t") for line in text.splitlines()).rstrip() + "\n"
        page.write_text(text, encoding="utf-8", newline="\n")
    # Both files are at the site root, so all pdoc relative links still work.
    shutil.copyfile(output / "user_guide.html", output / "index.html")
    print(f"Documentation: {output / 'index.html'}")


if __name__ == "__main__":
    main()
