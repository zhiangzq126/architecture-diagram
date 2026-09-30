import base64
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from drawio_adapter import attach_icons, parse_diagram


class DrawioSafetyTests(unittest.TestCase):
    def setUp(self):
        self.raw = (ROOT / 'examples/service.drawio').read_bytes()

    def cell_xml(self, node_id='gateway', **attributes):
        tree = ET.fromstring(self.raw)
        cell = tree.find(f'.//mxCell[@id="{node_id}"]')
        cell.attrib.update(attributes)
        return ET.tostring(tree, encoding='utf-8')

    def test_encoded_javascript_links(self):
        for label in (
            '<a href="&#106;avascript:alert(1)">link</a>',
            '<a href="jav&#x61;script&colon;alert(1)">link</a>',
            '<a href="java&Tab;script&colon;alert(1)">link</a>',
            '<span href="jav&#x61;script:alert(1)">link</span>',
            '<a href="https://example.invalid/">link</a>',
        ):
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, 'HTML content in gateway'):
                parse_diagram(self.cell_xml(value=label))

    def test_external_fetch_and_active_tags(self):
        for label in (
            '<image src="https://example.invalid/image.png">',
            '<img src="https://example.invalid/image.png">',
            '<link rel="stylesheet" href="https://example.invalid/style.css">',
            '<video poster="https://example.invalid/image.png"></video>',
            '<source src="https://example.invalid/media">',
            '<svg><image href="https://example.invalid/image.svg"/></svg>',
            '<IFRAME src="https://example.invalid/"></IFRAME>',
            '<object data="https://example.invalid/"></object>',
            '<embed src="https://example.invalid/">',
            '<script>alert(1)</script>',
            '<style>@import "https://example.invalid/style.css";</style>',
            '<audio src="https://example.invalid/media"></audio>',
        ):
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, 'Unsupported HTML label tag'):
                parse_diagram(self.cell_xml(value=label))

    def test_only_formatting_attributes(self):
        for attribute in (
            'onmouseover="alert(1)"',
            'ONCLICK="alert(1)"',
            'src="https://example.invalid/"',
            'background="https://example.invalid/"',
            'class="external-style"',
            'style="color: red" style="color: blue"',
            'style',
        ):
            with self.subTest(attribute=attribute), self.assertRaisesRegex(ValueError, 'HTML content in gateway'):
                parse_diagram(self.cell_xml(value=f'<span {attribute}>label</span>'))

    def test_css_url_escapes_and_unsupported_values(self):
        for style in (
            r'background-image: \75rl(https://example.invalid/image.png)',
            r'background: u\72l(https://example.invalid/image.png)',
            r'color: \75\72\6c(https://example.invalid/image.png)',
            r'font-family: u\72l(https://example.invalid/font)',
            r'c\6flor: red',
            'color: url(https://example.invalid/image.png)',
            'color: &#117;rl(https://example.invalid/image.png)',
            'color: expression(alert(1))',
            'color: red/**/',
            'font-family: Arial; behavior: url(https://example.invalid/)',
            '@import "https://example.invalid/style.css"',
            'position: fixed',
        ):
            with self.subTest(style=style), self.assertRaisesRegex(ValueError, 'HTML label CSS'):
                parse_diagram(self.cell_xml(value=f'<span style="{style}">label</span>'))

    def test_comments_and_declarations(self):
        for label in (
            '<!--[if IE]><img src="https://example.invalid/"><![endif]-->',
            '<!DOCTYPE html>',
            '<?instruction data?>',
            '<![CDATA[hidden]]>',
            '<![unknown[hidden]]>',
        ):
            with self.subTest(label=label), self.assertRaises(ValueError):
                parse_diagram(self.cell_xml(value=label))

    def test_normal_html_formatting_is_preserved(self):
        labels = (
            '<div align="center"><font face="Arial, Helvetica, sans-serif" color="#172B4D" size="4">'
            '<b>网关</b><br><strong>Gateway</strong></font></div>',
            '<span style="font-family: &quot;Times New Roman&quot;, serif; font-size: 18px; '
            'color: rgb(23, 43, 77); background-color: rgba(255, 255, 255, 0.5); '
            'font-weight: 700; font-style: italic; text-decoration: underline line-through; '
            'text-align: center; vertical-align: middle; white-space: pre-wrap; '
            'line-height: 1.5; letter-spacing: -0.5px">label &amp; text</span>',
            '<center><p dir="ltr"><i>italic</i> <em>emphasis</em> <u>underline</u> '
            '<s>strike</s> x<sup>2</sup> H<sub>2</sub>O<br/></p></center>',
            '<FONT COLOR="blue" FACE="宋体" SIZE="+1" STYLE="FONT-SIZE: 12pt">文本</FONT>',
            'a < b &amp; c &lt;image&gt; https://example.invalid/',
        )
        for label in labels:
            with self.subTest(label=label):
                raw = self.cell_xml(value=label)
                tree, pages = parse_diagram(raw)
                self.assertEqual(pages[0][2]['gateway'].get('value'), label)
                self.assertEqual(ET.tostring(tree), ET.tostring(ET.fromstring(raw)))

    def test_unknown_root_wrappers_are_rejected(self):
        for tag in ('object', 'UserObject', 'wrapper', 'mxcell', '{urn:unknown}mxCell'):
            with self.subTest(tag=tag):
                tree = ET.fromstring(self.raw)
                root = tree.find('.//root')
                wrapper = ET.SubElement(root, tag, {'id': 'hidden-business', 'label': 'Hidden business'})
                cell = ET.SubElement(wrapper, 'mxCell', {'vertex': '1', 'parent': '1'})
                ET.SubElement(cell, 'mxGeometry', {'width': '120', 'height': '60', 'as': 'geometry'})
                with self.assertRaisesRegex(ValueError, 'Unsupported DrawIO root element'):
                    parse_diagram(ET.tostring(tree))

    def test_malformed_xml_has_readable_value_error(self):
        for raw in (b'<mxfile><diagram></mxfile>', b'<mxfile', b'', b'<mxfile>&unknown;</mxfile>'):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, 'Invalid DrawIO XML: .*line') as caught:
                parse_diagram(raw)
            self.assertIsInstance(caught.exception.__cause__, ET.ParseError)

    def test_utf16_entity_input_is_rejected_before_xml_parsing(self):
        text = (
            '<?xml version="1.0" encoding="UTF-16"?>'
            '<!DOCTYPE mxfile [<!ENTITY label "expanded">]>'
            '<mxfile><diagram><mxGraphModel><root><mxCell id="0"/>'
            '<mxCell id="1" parent="0"/>'
            '<mxCell id="node" value="&label;" vertex="1" parent="1">'
            '<mxGeometry width="120" height="60"/></mxCell>'
            '</root></mxGraphModel></diagram></mxfile>'
        )
        for encoding in ('utf-16', 'utf-16-le', 'utf-16-be'):
            with self.subTest(encoding=encoding), self.assertRaisesRegex(ValueError, 'UTF-8'):
                parse_diagram(text.encode(encoding))

    def test_utf8_declarations_and_invalid_encoding(self):
        for declaration in (b'<!DOCTYPE mxfile>', b'<!DOCTYPE mxfile [<!ENTITY x "expanded">]>', b'<!ENTITY x "expanded">'):
            with self.subTest(declaration=declaration), self.assertRaisesRegex(ValueError, 'DTD/entities'):
                parse_diagram(declaration + self.raw)
        with self.assertRaisesRegex(ValueError, 'UTF-8'):
            parse_diagram(self.raw + b'\xff')
        self.assertEqual(len(parse_diagram(b'\xef\xbb\xbf' + self.raw)[1]), 1)

    def test_image_style_keys_and_values(self):
        for key in ('image', ' Image ', '\tImAgE\n', 'backgroundImage', ' BACKGROUNDIMAGE '):
            for value in ('https://example.invalid/icon.svg', 'data:image/svg+xml,%3Csvg/%3E',
                          'data:image/png;base64,aW1hZ2U=', 'file:///tmp/icon.png', '', 'none.svg'):
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, 'Unverified image in gateway'):
                    parse_diagram(self.cell_xml(style=f'rounded=1;{key} = {value};html=1'))
            for value in ('none', ' NONE '):
                with self.subTest(key=key, value=value):
                    style = f'rounded=1;{key} = {value};html=1'
                    _, pages = parse_diagram(self.cell_xml(style=style))
                    self.assertEqual(pages[0][2]['gateway'].get('style'), style)
        with self.assertRaisesRegex(ValueError, 'Unverified image'):
            parse_diagram(self.cell_xml(style='image=none; IMAGE =https://example.invalid/'))

    def test_layer_content_is_also_validated(self):
        for node_id in ('0', '1'):
            for attributes in ({'value': '<link href="https://example.invalid/">'},
                               {'style': ' backgroundImage =https://example.invalid/image.png'}):
                with self.subTest(node_id=node_id, attributes=attributes), self.assertRaises(ValueError):
                    parse_diagram(self.cell_xml(node_id, **attributes))

    def test_service_fixture_and_attach_icons_preserve_business_content(self):
        original = ET.fromstring(self.raw)
        tree, pages = parse_diagram(self.raw)
        self.assertEqual(len(pages), 1)
        self.assertEqual(ET.tostring(tree), ET.tostring(original))
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M0 0h24v24H0z"/></svg>'
        prepared = {'gateway': {'svg_data_uri': 'data:image/svg+xml;base64,' + base64.b64encode(svg).decode(),
                                'theme': {'dark_backdrop': True}}}
        output, count = attach_icons(self.raw, prepared, [{'node_id': 'gateway'}, {'node_id': 'orders'}])
        self.assertEqual(count, 1)
        cells = {cell.get('id'): cell for cell in ET.fromstring(output).findall('.//mxCell')}

        def snapshot(cell):
            return [(element.tag, {key: value for key, value in element.attrib.items() if key != 'style'},
                     sorted(element.get('style', '').split(';'))) for element in cell.iter()]

        for cell in original.findall('.//mxCell'):
            self.assertEqual(snapshot(cells[cell.get('id')]), snapshot(cell))
        self.assertEqual(set(cells), set(pages[0][2]) | {'gateway__ad_icon'})
        icon = cells['gateway__ad_icon']
        self.assertEqual(icon.get('parent'), 'gateway')
        self.assertEqual(icon.get('connectable'), '0')
        self.assertIn('image=data:image/svg+xml,%3Csvg', icon.get('style'))
        self.assertIn('fillColor=#FFFFFF', icon.get('style'))
        self.assertNotIn(b' />', output)


if __name__ == '__main__':
    unittest.main()
