"""Serialises an `xml.etree.ElementTree` element as a generated-file document.

ElementTree handles the tree, escaping (`&`, `<`, `>`) and indentation. This
module adds only what `ElementTree.tostring` cannot express: a double-quoted
XML declaration and a banner comment before the root element.
"""

import copy
import xml.etree.ElementTree as ET

_DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>'
_INDENT = "  "


def write_document(root: ET.Element, comment: str) -> str:
    """`root` as a document: XML declaration, a `<!-- comment -->` banner, then
    the element tree indented by two spaces, ending with a newline. Empty
    elements are written as `<Name />`. `root` itself is left unmodified."""
    indented = copy.deepcopy(root)  # `ET.indent` works in place
    ET.indent(indented, space=_INDENT)
    body = ET.tostring(indented, encoding="unicode")
    return f"{_DECLARATION}\n<!-- {comment} -->\n{body}\n"
