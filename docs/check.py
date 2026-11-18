"""Check static HTML links and project CLI flag coverage without running services."""

import ast
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class Page(HTMLParser):
    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.links: list[str] = []
        self.navigation = False
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("aria-label") == "Documentation":
            self.navigation = True
        if "id" in values:
            self.ids.add(values["id"])
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])


def main() -> None:
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
    for package in ("Interface", "vectorizer", "services", "evaluation"):
        for path in (root / package).glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            arguments = [
                node for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"
            ]
            if not arguments:
                continue
            module = f"{package}.{path.stem}"
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
    print(f"PASS: {len(pages)} HTML pages, local links/anchors, {count} CLI flag declarations.")


if __name__ == "__main__":
    main()
