"""Unit tests for `write_document`, the generated-file serialisation built on
`xml.etree.ElementTree`.

Methodology: build small trees with ElementTree and compare the serialised
document with an exact expected string. Byte-exact layout is this module's
whole contract (the golden files, shared with the C++ implementation, depend
on it), so these tests also pin the ElementTree behaviour it relies on:
indentation, self-closed empty elements, and escaping.
"""

import xml.etree.ElementTree as ET

from topoc.xmldoc import write_document


def test_indents_nested_elements_and_closes_empty_ones() -> None:
    """The exact document layout: double-quoted declaration, banner comment,
    two-space indentation per level, text elements on one line, and empty
    elements (no children, no or empty text) self-closed as `<Name />`."""
    root = ET.Element("a")
    ET.SubElement(root, "b").text = "text"
    ET.SubElement(root, "c")
    ET.SubElement(ET.SubElement(root, "d"), "e").text = ""
    assert write_document(root, "generated") == (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!-- generated -->\n"
        "<a>\n"
        "  <b>text</b>\n"
        "  <c />\n"
        "  <d>\n"
        "    <e />\n"
        "  </d>\n"
        "</a>\n"
    )


def test_escapes_markup_characters_but_not_quotes() -> None:
    """`&`, `<` and `>` are escaped, as in the C++ implementation; quotes stay
    literal so AMPS filters read naturally in the output."""
    root = ET.Element("f")
    root.text = "'a' < \"b\" & c > d"
    assert write_document(root, "c").endswith("<f>'a' &lt; \"b\" &amp; c &gt; d</f>\n")


def test_leaves_the_tree_unmodified() -> None:
    """Serialising does not inject indentation whitespace into the caller's tree."""
    root = ET.Element("a")
    ET.SubElement(root, "b").text = "x"
    write_document(root, "c")
    assert ET.tostring(root, encoding="unicode") == "<a><b>x</b></a>"
