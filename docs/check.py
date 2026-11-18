"""Check HTML navigation, public API docstrings, and CLI flags without running services."""

import ast
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class Page(HTMLParser):
    """Collect local navigation links and anchor IDs from one generated HTML page."""

    def __init__(self, text: str):
        """Parse a page immediately, retaining links, IDs, and navigation presence."""
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.links: list[str] = []
        self.navigation = False
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        """Record link destinations, named IDs, and the shared navigation marker."""
        values = dict(attrs)
        if values.get("aria-label") == "Documentation":
            self.navigation = True
        if "id" in values:
            self.ids.add(values["id"])
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])


def main() -> None:
    """Validate the built site and source documentation, exiting nonzero on gaps.

    Checks links/anchors, portable paths, shared navigation, public Python API
    docstrings, and per-command flag coverage. Uses AST inspection rather than
    importing services or executing CLI entry points.
    """
    root = Path(__file__).resolve().parents[1]
    output = root / "docs" / "html"
    pages = {
        path.resolve(): Page(path.read_text(encoding="utf-8"))
        for path in output.rglob("*.html")
    }
    errors = []
    if not (output / "index.html").is_file():
        errors.append("Missing docs/html/index.html; run docs/build.py first.")
    for path, page in pages.items():
        if not page.navigation:
            errors.append(f"{path.name}: missing documentation navigation")
        text = path.read_text(encoding="utf-8")
        if root.as_posix() in text or str(root) in text:
            errors.append(f"{path.name}: build-machine checkout path leaked into HTML")
        for href in page.links:
            url = urlsplit(href)
            if url.scheme or url.netloc:
                continue
            target = (path.parent / unquote(url.path)).resolve() if url.path else path
            if not target.is_relative_to(output.resolve()):
                errors.append(f"{path.name}: link escapes HTML output: {href}")
            elif not target.is_file():
                errors.append(f"{path.name}: missing link target: {href}")
            elif target in pages and url.fragment and unquote(url.fragment) not in pages[target].ids:
                errors.append(f"{path.name}: missing anchor: {href}")

    # Inspect parser declarations as data; never invoke startup/indexing.
    reference = (root / "docs" / "user_guide" / "cli.py").read_text(encoding="utf-8")
    count = 0
    documented = 0
    for package in ("common", "Interface", "vectorizer", "services", "evaluation"):
        for path in (root / package).glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            module = f"{package}.{path.stem}"
            for node in tree.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) or node.name.startswith("_"):
                    continue
                definitions = [(node.name, node)]
                if isinstance(node, ast.ClassDef):
                    definitions.extend(
                        (f"{node.name}.{member.name}", member)
                        for member in node.body
                        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and (not member.name.startswith("_") or member.name == "__init__")
                    )
                for name, definition in definitions:
                    documented += 1
                    if not ast.get_docstring(definition):
                        errors.append(f"{module}.{name}: missing public API docstring")
            arguments = [
                node for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"
            ]
            if not arguments:
                continue
            if module not in reference:
                errors.append(f"Undocumented CLI module: {module}")
            # Scope flags to the module section so another CLI cannot mask a gap.
            section = reference.split(f"## {module}", 1)[-1]
            if f"## {module}" not in reference:
                section = reference.split(f"### {module}", 1)[-1].split("\n### ", 1)[0]
            else:
                section = section.split("\n## ", 1)[0]
            for call in arguments:
                for arg in call.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith("-"):
                        count += 1
                        shared = arg.value in {
                            "--db-path", "--collection", "--embedding-model", "--ollama-url"
                        } and "Shared flags" in section
                        if arg.value not in section and not shared:
                            errors.append(f"{module}: missing flag {arg.value}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(
        f"PASS: {len(pages)} HTML pages, local links/anchors, "
        f"{count} CLI flag declarations, {documented} documented public definitions."
    )


if __name__ == "__main__":
    main()
