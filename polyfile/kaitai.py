from enum import Enum
import glob
import os

import yaml

from kaitaistruct import KaitaiStruct, KaitaiStream, BytesIO

from . import expressions
from . import logger

log = logger.getStatusLogger("Kaitai")

KSY_DIR = os.path.join(os.path.realpath(os.path.dirname(__file__)), 'kaitai_defs')

DEFS = {}


class Endianness(Enum):
    BIG = 'be'
    LITTLE = 'le'
    NONE = ''


PRIMITIVE_TYPES_BY_NAME = {}


class AST:
    def __init__(self, obj, parent=None):
        self.obj = obj
        self.parent = parent
        self._children = []
        self._offset = None
        self._length = None

    @property
    def offset(self):
        if self._offset is None:
            if self._children:
                self._offset = self._children[0].offset
            elif self.parent is not None:
                self._offset = self.parent.offset + self.parent.length
            else:
                self._offset = 0
        return self._offset

    @property
    def length(self):
        if self._length is None:
            if self._children:
                self._length = self._children[-1].offset + self._children[-1].length - self.offset
            else:
                self._length = 0
        return self._length

    def add_child(self, child):
        self._children.append(child)
        if child.parent is not None:
            breakpoint()
        child.parent = self
        self._offset = None
        self._length = None

    @property
    def children(self):
        return self._children

    def __len__(self):
        return len(self._children)


class RawBytes(AST):
    def __init__(self, raw_bytes, offset, parent: AST=None):
        super().__init__(raw_bytes, parent)
        self._offset = offset
        self._length = len(self.obj)


class Integer(AST):
    def __init__(self, value:int, offset:int, length:int, parent: AST=None):
        super().__init__(value, parent)
        self._offset = offset
        self._length = length


class IntegerTypes(Enum):
    U1 = ('u1', 8, False, Endianness.NONE, 0, 255, KaitaiStream.read_u1)
    U2LE = ('u2le', 16, False, Endianness.LITTLE, 0, 65535, KaitaiStream.read_u2le)
    U2BE = ('u2be', 16, False, Endianness.BIG, 0, 65535, KaitaiStream.read_u2be)
    U4LE = ('u4le', 32, False, Endianness.LITTLE, 0, 4294967295, KaitaiStream.read_u4le)
    U4BE = ('u4be', 32, False, Endianness.BIG, 0, 4294967295, KaitaiStream.read_u4be)
    U8LE = ('u8le', 64, False, Endianness.LITTLE, 0, 18446744073709551615, KaitaiStream.read_u8le)
    U8BE = ('u8be', 64, False, Endianness.BIG, 0, 18446744073709551615, KaitaiStream.read_u8be)
    S1 = ('s1', 8, True, Endianness.NONE, -128, 127, KaitaiStream.read_s1)
    S2LE = ('s2le', 16, False, Endianness.LITTLE, -32768, 32767, KaitaiStream.read_s2le)
    S2BE = ('s2be', 16, False, Endianness.BIG, -32768, 32767, KaitaiStream.read_s2be)
    S4LE = ('s4le', 32, False, Endianness.LITTLE, -2147483648, 2147483647, KaitaiStream.read_s4le)
    S4BE = ('s4be', 32, False, Endianness.BIG, -2147483648, 2147483647, KaitaiStream.read_s4be)
    S8LE = ('s8le', 64, False, Endianness.LITTLE, -9223372036854775808, 9223372036854775807, KaitaiStream.read_s8le)
    S8BE = ('s8be', 64, False, Endianness.BIG, -9223372036854775808, 9223372036854775807, KaitaiStream.read_s8be)

    def parse(self, stream: KaitaiStream):
        offset = stream.pos()
        b = self.reader(stream)
        return Integer(b, offset, self.bitwidth//8)

    def __init__(self, typename, bitwidth, signed, endianness, min_value, max_value, reader):
        self.typename = typename
        self.bitwidth = bitwidth
        self.signed = signed
        self.endianness = endianness
        self.min_value = min_value
        self.max_value = max_value
        self.reader = reader

        PRIMITIVE_TYPES_BY_NAME[self.typename] = self


class FloatTypes(Enum):
    F4LE = ('f4le', 32, Endianness.LITTLE, 24, 8)
    F4BE = ('f4be', 32, Endianness.BIG, 24, 8)
    F8LE = ('f8le', 32, Endianness.LITTLE, 53, 11)
    F8BE = ('f8be', 32, Endianness.BIG, 53, 11)

    def __init__(self, typename, bitwidth, endianness, mantissa_bits, exponent_bits):
        self.typename = typename
        self.bitwidth = bitwidth
        self.endianness = endianness
        self.mantissa_bits = mantissa_bits
        self.exponent_bits = exponent_bits

        PRIMITIVE_TYPES_BY_NAME[self.typename] = self


class Repeat(Enum):
    NONE = 'norepeat'
    EXPR = 'expr'
    EOS = 'eos'
    UNTIL = 'until'


def get_primitive_type(type_name: str, endianness: Endianness=None):
    if type_name in PRIMITIVE_TYPES_BY_NAME:
        return PRIMITIVE_TYPES_BY_NAME[type_name]
    elif endianness is not None and type_name + endianness.value in PRIMITIVE_TYPES_BY_NAME:
        return PRIMITIVE_TYPES_BY_NAME[type_name + endianness.value]
    raise KeyError(f'Unknown type "{type_name}"')


class Expression:
    def __init__(self, expr):
        self.expr = expressions.parse(expr)

    def interpret(self):
        return self.expr.interpret()


class ByteMatch:
    def __init__(self, contents):
        if isinstance(contents, bytes):
            self.contents = contents
        elif isinstance(contents, bytearray):
            self.contents = bytes(contents)
        elif isinstance(contents, list):
            self.contents = bytes(contents)
        else:
            raise RuntimeError(f"TODO: Implement support for `contents` of type {type(contents)}")

    def parse(self, stream: KaitaiStream):
        offset = stream.pos()
        c = stream.read_bytes(len(self.contents))
        assert c == self.contents
        return RawBytes(c, offset)


class Switch:
    def __init__(self, raw_yaml, parent):
        self.parent = parent
        self.switch_on = raw_yaml['switch-on']
        self.cases = {}
        for k, v in raw_yaml['cases'].items():
            if isinstance(k, int):
                self.cases[Expression(str(k))] = v
            else:
                self.cases[Expression(k)] = v
            # TODO: Test for duplicate cases

    def parse(self, stream: KaitaiStream) -> AST:
        # TODO: Implement
        return


class Attribute:
    def __init__(self, raw_yaml, parent):
        self.parent = parent
        self.uid = raw_yaml.get('id', None)
        self.contents = raw_yaml.get('contents', None)
        if self.contents is not None:
            self.contents = ByteMatch(self.contents)
        self._type_name = raw_yaml.get('type', None)
        self._type = None
        if isinstance(self._type_name, dict):
            if 'switch-on' in self._type_name:
                self._type = Switch(self._type_name, parent=self)
            else:
                raise ValueError(f"Unknown type: {self._type_name!r}")
        self.repeat = raw_yaml.get('repeat', 'norepeat')
        for r in Repeat:
            if r.value == self.repeat:
                self.repeat = r
                break
        else:
            self.repeat = Repeat.NONE
        self.repeat_expr = raw_yaml.get('repeat-expr', None)
        if self.repeat_expr is not None:
            self.repeat_expr = Expression(self.repeat_expr)
        self.repeat_until = raw_yaml.get('repeat-until', None)
        if self.repeat_until is not None:
            self.repeat_until = Expression(self.repeat_until)
        self.if_expr = raw_yaml.get('if', None)
        if self.if_expr is not None:
            self.if_expr = Expression(self.if_expr)

    @property
    def type(self):
        if self._type is None:
            if self.contents is not None:
                self._type = self.contents
            else:
                self._type = self.parent.get_type(self._type_name)
        return self._type

    def parse(self, stream: KaitaiStream) -> AST:
        ast = AST(self)
        if self.repeat == Repeat.EOS:
            while not stream.is_eof():
                ast.add_child(self.type.parse(stream))
        elif self.repeat == Repeat.EXPR:
            while self.repeat_expr.interpret():
                ast.add_child(self.type.parse(stream))
        elif self.repeat == Repeat.UNTIL:
            while not self.repeat_until.interpret():
                ast.add_child(self.type.parse(stream))
        else:
            ast.add_child(self.type.parse(stream))
        return ast

    def __repr__(self):
        raw_yaml = {
            'id': self.uid,
            'contents': self.contents,
            'type': self._type_name
        }
        return f"{self.__class__.__name__}(raw_yaml={raw_yaml!r}, parent={self.parent!r})"


class Type:
    def __init__(self, raw_yaml, uid=None, parent=None):
        self.parent = parent
        self.meta = raw_yaml.get('meta', {})
        if uid is None:
            uid = self.meta['id']
        self.uid = uid
        self._endianness = self.meta.get('endian', None)
        if self._endianness == 'be':
            self._endianness = Endianness.BIG
        elif self._endianness == 'le':
            self._endianness = Endianness.LITTLE
        else:
            self._endianness = None
        self._encoding = self.meta.get('encoding', None)
        if 'imports' in self.meta:
            self._imports = self.meta['imports']
        else:
            self._imports = []
        self.seq = [Attribute(s, self) for s in raw_yaml.get('seq', ())]
        self.types = {
            typename: Type(raw_type, uid=typename, parent=self)
            for typename, raw_type in raw_yaml.get('types', {}).items()
        }

    @property
    def endianness(self):
        if self._endianness is not None:
            return self._endianness
        elif self.parent is not None:
            return self.parent.endianness
        else:
            return None

    @property
    def encoding(self):
        if self._encoding is not None:
            return self._encoding
        elif self.parent is not None:
            return self.parent.encoding
        else:
            return None

    @property
    def imports(self):
        return [DEFS[i] for i in self._imports]

    def get_type(self, type_name):
        if type_name in self.types:
            return self.types[type_name]
        # see if it is defined in an import
        for t in self.imports:
            try:
                return t.get_type(type_name)
            except KeyError:
                # this import did not have the type
                pass
        if self.parent is not None:
            return self.parent.get_type(type_name)
        else:
            return get_primitive_type(type_name, self.endianness)

    def parse(self, stream: KaitaiStream) -> AST:
        ast = AST(self)
        for attr in self.seq:
            ast.add_child(attr.parse(stream))
        return ast


def parse(typename, bytes_like) -> AST:
    if typename not in DEFS:
        load()
    return DEFS[typename].parse(KaitaiStream(BytesIO(bytes_like)))


def load():
    for ksy_path in glob.glob(os.path.join(KSY_DIR, '*.ksy')):
        log.status(f'Loading KSY file definitions... {os.path.split(ksy_path)[-1]}')
        with open(ksy_path, 'r') as f:
            ksy = Type(yaml.safe_load(f))
            DEFS[ksy.uid] = ksy

    #for t in DEFS.values():
    #    for attr in t.seq:
    #        print(attr, attr.type)


if __name__ == '__main__':
    load()
