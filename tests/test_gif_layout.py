import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


def bounds(element):
    return (element['x'], element['y'],
            element['x'] + element['width'], element['y'] + element['height'])


def absolute_points(element):
    return tuple((element['x'] + x, element['y'] + y) for x, y in element['points'])


def normalized(text):
    return ''.join(text.split())


class GifLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / 'modules/drawio/scripts/render_animated_diagram.py'
        module_spec = importlib.util.spec_from_file_location('gif_layout_renderer', path)
        cls.renderer = importlib.util.module_from_spec(module_spec)
        try:
            module_spec.loader.exec_module(cls.renderer)
        except ModuleNotFoundError as error:
            if error.name == 'PIL':
                raise unittest.SkipTest('Pillow is required for real render_static layout tests') from error
            raise
        cls.spec = json.loads((ROOT / 'modules/drawio/examples/05-animated-flow-spec.json').read_text(encoding='utf-8'))
        cls.themes = {'dark': cls.renderer.THEME, 'light': cls.renderer.LIGHT_THEME}
        cls.renders = {}
        for name, theme in cls.themes.items():
            with patch.object(cls.renderer, 'THEME', theme):
                ex, image = cls.renderer.render_static(copy.deepcopy(cls.spec))
            cls.renders[name] = (ex.elements, image)
            cls.addClassCleanup(image.close)

    def panel(self, elements, x, y):
        matches = [e for e in elements if e['type'] == 'rectangle' and (e['x'], e['y']) == (x, y)]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def layers(self, elements):
        cards = sorted((e for e in elements if e['type'] == 'rectangle' and e['y'] == 827),
                       key=lambda e: e['x'])
        self.assertEqual(len(cards), 4)
        return cards

    def connectors(self, elements):
        arrows = sorted((e for e in elements if e['type'] == 'arrow' and e['y'] == 890),
                        key=lambda e: e['x'])
        self.assertEqual(len(arrows), 4)
        return arrows

    def label(self, elements, text):
        matches = [e for e in elements if e['type'] == 'text' and normalized(e['text']) == normalized(text)]
        self.assertEqual(len(matches), 1, text)
        return matches[0]

    def assert_text_fits(self, element, image):
        draw = self.renderer.ImageDraw.Draw(image)
        font = self.renderer.load_font(element['fontSize'], cjk=self.renderer.has_cjk(element['text']))
        width, height = self.renderer.text_size(draw, element['text'], font)
        self.assertLessEqual(width, self.renderer.c(element['width']))
        self.assertLessEqual(height, self.renderer.c(element['height']))

    def test_four_layer_cards_stay_inside_parent(self):
        for theme, (elements, _) in self.renders.items():
            with self.subTest(theme=theme):
                parent = bounds(self.panel(elements, 333, 734))
                cards = self.layers(elements)
                self.assertEqual([card['x'] for card in cards], [346, 474, 602, 730])
                for card in cards:
                    left, top, right, bottom = bounds(card)
                    self.assertEqual((card['width'], card['height']), (112, 142))
                    self.assertGreaterEqual(left, parent[0])
                    self.assertGreaterEqual(top, parent[1])
                    self.assertLessEqual(right, parent[2])
                    self.assertLessEqual(bottom, parent[3])
                self.assertEqual(bounds(cards[-1])[2], 842)

    def test_three_connectors_meet_adjacent_card_edges(self):
        expected = [((458, 890), (474, 890)), ((586, 890), (602, 890)), ((714, 890), (730, 890))]
        for theme, (elements, _) in self.renders.items():
            with self.subTest(theme=theme):
                cards = self.layers(elements)
                arrows = self.connectors(elements)[:3]
                self.assertEqual([absolute_points(arrow) for arrow in arrows], expected)
                for arrow, left, right in zip(arrows, cards, cards[1:]):
                    self.assertEqual(absolute_points(arrow), ((bounds(left)[2], 890), (right['x'], 890)))

    def test_incoming_connection_and_label_fit_between_panels(self):
        for theme, (elements, image) in self.renders.items():
            with self.subTest(theme=theme):
                parent = self.panel(elements, 333, 734)
                right_panel = self.panel(elements, 904, 735)
                last_card = self.layers(elements)[-1]
                incoming = absolute_points(self.connectors(elements)[-1])
                self.assertEqual(incoming, ((842, 890), (904, 890)))
                self.assertEqual(incoming, ((bounds(last_card)[2], 890), (right_panel['x'], 890)))
                label = self.label(elements, self.spec['right_panel']['incoming_label'])
                self.assertEqual(bounds(label), (855, 868, 904, 888))
                self.assertGreaterEqual(label['x'], bounds(parent)[2])
                self.assertLessEqual(bounds(label)[2], right_panel['x'])
                self.assertGreaterEqual(label['fontSize'], 10)
                self.assertLessEqual(label['fontSize'], 12)
                self.assert_text_fits(label, image)

    def test_loop_labels_keep_text_and_clear_decision_diamond(self):
        expected = {'loop_label': (260, 493, 690, 543), 'retry_label': (430, 580, 690, 604)}
        for theme, (elements, image) in self.renders.items():
            with self.subTest(theme=theme):
                diamonds = [e for e in elements if e['type'] == 'diamond']
                self.assertEqual(len(diamonds), 1)
                diamond = bounds(diamonds[0])
                for key, box in expected.items():
                    with self.subTest(label=key):
                        label = self.label(elements, self.spec[key])
                        self.assertEqual(bounds(label), box)
                        self.assertEqual(label['fontSize'], 14)
                        left, top, right, bottom = bounds(label)
                        self.assertFalse(left < diamond[2] and right > diamond[0]
                                         and top < diamond[3] and bottom > diamond[1])
                        self.assertGreaterEqual(diamond[0] - right, 16)
                        self.assert_text_fits(label, image)

    def test_particle_paths_match_static_connectors(self):
        for theme, (elements, image) in self.renders.items():
            with self.subTest(theme=theme), patch.object(self.renderer, 'THEME', self.themes[theme]), \
                    patch.object(self.renderer, 'point_at_fraction', wraps=self.renderer.point_at_fraction) as point:
                frame = self.renderer.animate_frame(image, 0, self.renderer.DEFAULT_FRAMES)
                frame.close()
                paths = list(dict.fromkeys(tuple(call.args[0]) for call in point.call_args_list
                                           if all(y == 890 for _, y in call.args[0])))
                arrows = self.connectors(elements)
                self.assertEqual(paths, [tuple(p for arrow in arrows[:3] for p in absolute_points(arrow)),
                                         absolute_points(arrows[-1])])


if __name__ == '__main__':
    unittest.main()
