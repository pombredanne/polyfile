import os
import unicodedata

from jinja2 import Environment, FileSystemLoader, Template

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'templates')
TEMPLATE = None


def assign_ids(matches):
    i = 0
    stack = list(matches)
    while stack:
        m = stack.pop()
        assert 'uid' not in m
        m['uid'] = i
        i += 1
        stack += list(m['children'])
    return matches


def generate(file_path, matches):
    global TEMPLATE
    if TEMPLATE is None:
        TEMPLATE = Environment(loader=FileSystemLoader(TEMPLATE_DIR)).get_template('template.html')

    matches = assign_ids(matches)

    input_bytes = os.stat(file_path).st_size
    with open(file_path, 'rb') as input_file:
        class ReadUnicode():
            def __init__(self):
                self.reset = False

            def tell(self):
                if not self.reset:
                    return 0
                else:
                    return input_file.tell()

            def translate(self, b, monospace=True):
                if b is None or len(b) == 0 or b == b' ':
                    return '&nbsp;'
                elif b == b'\n':
                    if monospace:
                        return '\u2424'
                    else:
                        return '\u2424</span><br /><span>'
                elif b == b'\t':
                    if monospace:
                        return b'\u2b7e'
                    else:
                        return '\t'
                elif b == b'\r':
                    return '\u240d'
                try:
                    u = b.decode('utf-8')
                    if unicodedata.category(u) == 'Cc':
                        # This is a control character
                        return '\ufffd'
                    else:
                        return u
                except UnicodeDecodeError:
                    return '\ufffd'

            def __iter__(self):
                input_file.seek(0)
                i = 0
                while True:
                    b = input_file.read(1)
                    if b is None or len(b) < 1:
                        break
                    yield i, self.translate(b, monospace=False)
                    i += 1

            def __call__(self):
                if not self.reset:
                    input_file.seek(0)
                    self.reset = True
                b = input_file.read(1)
                return self.translate(b)

        return TEMPLATE.render(
            file_path=file_path,
            matches=matches,
            input_file=input_file,
            input_bytes=input_bytes,
            read_unicode=ReadUnicode()
        )


if __name__ == '__main__':
    import json
    import sys

    with open(sys.argv[2], 'r') as f:
        print(generate(sys.argv[1], json.load(f)))