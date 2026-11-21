"""Small HTML tree used to parse fragments from the live page."""

from __future__ import annotations

from html.parser import HTMLParser

VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Node:
    def __init__(self, tag: str, attrs: list[tuple[str, str | None]] | dict | None = None):
        raw = dict(attrs or [])
        self.tag = tag.lower()
        self.attrs = {key: value or "" for key, value in raw.items()}
        self.classes = [part for part in self.attrs.get("class", "").split() if part]
        self.children: list[Node] = []
        self.data: list[str] = []
        self.parent: Node | None = None

    def add(self, child: Node) -> None:
        child.parent = self
        self.children.append(child)

    def text(self) -> str:
        parts = list(self.data)
        for child in self.children:
            parts.append(child.text())
        return "".join(parts)

    def select(self, class_name: str) -> list[Node]:
        found: list[Node] = []

        def walk(node: Node) -> None:
            if class_name in node.classes:
                found.append(node)
            for child in node.children:
                walk(child)

        walk(self)
        return found

    def select_one(self, class_name: str) -> Node | None:
        matches = self.select(class_name)
        return matches[0] if matches else None

    def by_tag(self, tag: str) -> list[Node]:
        found: list[Node] = []

        def walk(node: Node) -> None:
            if node.tag == tag:
                found.append(node)
            for child in node.children:
                walk(child)

        for child in self.children:
            walk(child)
        return found


class _TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs) -> None:
        node = Node(tag, attrs)
        self.stack[-1].add(node)
        if tag.lower() not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.stack[-1].add(Node(tag, attrs))

    def handle_endtag(self, tag: str) -> None:
        wanted = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == wanted:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].data.append(data)


def parse_html(html: str) -> Node:
    parser = _TreeParser()
    parser.feed(html or "")
    parser.close()
    return parser.root
