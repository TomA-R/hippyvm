
import struct
from collections import OrderedDict
from hippy.consts import BYTECODE_STACK_EFFECTS, ARGVAL, BYTECODE_HAS_ARG,\
     BYTECODE_NAMES, ARGVAL1, ARGVAL2, _CHECKSTACK
from hippy.error import IllegalInstruction
from rpython.rlib import jit
from rpython.rlib.unroll import unrolling_iterable
from rpython.rlib.objectmodel import we_are_translated
from rpython.rlib.rstring import StringBuilder
from rpython.rlib.rstruct.runpack import runpack


class ByteCode(object):
    """ A representation of a single code block
    """
    _immutable_fields_ = ['code', 'consts[*]', 'varnames[*]',
                          'functions[*]', 'names[*]', 'stackdepth',
                          'var_to_pos', 'names_to_pos', 'user_functions[*]',
                          'method_of_class', 'superglobals[*]', 'this_var_num']
    _marker = None

    def __init__(self, code, consts, names, varnames, user_functions,
                 filename, sourcelines, method_of_class=None,
                 startlineno=0, bc_mapping=None, name='<main>',
                 superglobals=None, this_var_num=-1):
        self.code = code
        self.name = name      # not necessarily lowercase
        self.filename = filename
        self.startlineno = startlineno
        self.sourcelines = sourcelines
        self.consts = consts
        self.names = names
        self.varnames = varnames # named variables
        self.stackdepth = self.count_stack_depth()
        self.var_to_pos = {}
        self.names_to_pos = {}
        self.user_functions = user_functions[:]
        self.method_of_class = method_of_class
        self.bc_mapping = bc_mapping
        for i, v in enumerate(varnames):
            assert i >= 0
            self.var_to_pos[v] = i
        for i, v in enumerate(names):
            self.names_to_pos[v] = i
        self.superglobals = superglobals
        self.this_var_num = this_var_num

    def getline(self, no):
        return self.sourcelines[no - 1]

    @jit.elidable
    def lookup_pos(self, v):
        return self.names_to_pos[v]
    # XXX rename one or both of these two different functions!
    @jit.elidable
    def _lookup_pos(self, v):
        return self.var_to_pos[v]

    def next_arg(self, i):
        a = ord(self.code[i])
        i += 1
        if a >= 0x80:
            for k in unroll_k:
                b = ord(self.code[i])
                i += 1
                a ^= (b << k)
                if b < 0x80:
                    break
            else:
                raise IllegalInstruction("error")
        return i, a
    next_arg._always_inline_ = True

    def count_stack_depth(self):
        i = 0
        counter = 0
        max_eff = 0
        while i < len(self.code):
            c = ord(self.code[i])
            i += 1
            stack_eff = BYTECODE_STACK_EFFECTS[c]
            if c >= BYTECODE_HAS_ARG:
                i, arg = self.next_arg(i)
                if c == _CHECKSTACK:
                    assert counter == arg
            else:
                arg = -999999
            if stack_eff == ARGVAL:
                stack_eff = -arg
            elif stack_eff == ARGVAL1:
                stack_eff = -arg + 1
            elif stack_eff == ARGVAL2:
                stack_eff = -2*arg + 1
            counter += stack_eff
            assert counter >= 0
            max_eff = max(counter, max_eff)
        assert counter == 0
        return max_eff

    def dump(self):
        i = 0
        lines = []
        while i < len(self.code):
            if not we_are_translated() and i == self._marker:   # not translated
                line = ' ===> '
            else:
                num = str(i)
                line = " " * (4 - len(num)) + num + "  "
            c = ord(self.code[i])
            line += BYTECODE_NAMES[c]
            i += 1
            if c >= BYTECODE_HAS_ARG:
                i, arg = self.next_arg(i)
                line += " %s" % arg
            lines.append(line)
        return "\n".join(lines)

    def serialize(self):
        return Serializer().write_bytecode(self).finish()

    def show(self):
        print self.dump()

    def __repr__(self):
        return '<ByteCode %s (%s:%d)>' % (self.name, self.filename,
                                          self.startlineno)

    def _freeze_(self):
        raise Exception("bytecode should not be prebuilt")

unroll_k = unrolling_iterable([7, 14, 21, 28])

class Serializer(object):
    def __init__(self):
        self.builder = StringBuilder()

    def write_int(self, i):
        self.builder.append(struct.pack("l", i))

    def write_char(self, c):
        assert len(c) == 1
        self.builder.append(c)

    def write_str(self, s):
        self.write_int(len(s))
        self.builder.append(s)

    def write_wrapped_item(self, w_item):
        w_item.ll_serialize(self.builder)

    def write_wrapped_list(self, lst_w):
        self.write_int(len(lst_w))
        for w_item in lst_w:
            self.write_wrapped_item(w_item)

    def write_list_of_str(self, lst):
        self.write_int(len(lst))
        for item in lst:
            self.write_str(item)

    def write_list_of_int(self, lst):
        self.write_int(len(lst))
        for item in lst:
            self.write_int(item)

    def write_list_of_char(self, lst):
        self.write_int(len(lst))
        for item in lst:
            self.write_char(item)

    def write_list_of_functions(self, lst):
        from hippy.function import Function
        from hippy.klass import UserClass
        
        self.write_int(len(lst))
        for func in lst:
            if isinstance(func, Function):
                self.write_char("f")
                self.write_function(func)
            elif isinstance(func, UserClass):
                self.write_char("u")
                self.write_class(func)
            else:
                raise NotImplementedError

    def write_function(self, func):
        self.write_bytecode(func.bytecode)
        self.write_list_of_str(func.names)
        self.write_list_of_char(func.types)
        # closuredecls, defaults_w, typehints are missing

    def write_class(self, klass):
        # extends_name, property_decl, all_parents, access_flags,
        # const_decl, constructor_method, constants_w, initial_instance_dct_w,
        # base_interface_names, identifier, properties, methods
        self.write_str(klass.name)
        self.write_int(len(klass.methods))
        for k, v in klass.methods.iteritems():
            self.write_str(k)
            self.write_int(v.access_flags)
            self.write_function(v.method_func)
        # XXX

    def write_bytecode(self, bc):
        self.write_str(bc.code)
        self.write_wrapped_list(bc.consts)
        self.write_str(bc.name)
        self.write_str(bc.filename)
        self.write_int(bc.startlineno)
        self.write_list_of_str(bc.sourcelines[:])
        self.write_list_of_str(bc.names[:])
        self.write_list_of_str(bc.varnames[:])
        self.write_list_of_int(bc.superglobals[:])
        self.write_int(bc.this_var_num)
        self.write_list_of_functions(bc.user_functions[:])
        self.write_list_of_int(bc.bc_mapping[:])
        return self

    def finish(self):
        return self.builder.build()

LONG_SIZE = struct.calcsize('l')

class UnserializerException(Exception):
    pass

class Unserializer(object):
    def __init__(self, repr, space):
        self.repr = repr
        self.pos = 0
        self.lgt = len(repr)
        self.space = space

    def read_char(self):
        if self.pos + 1 > self.lgt:
            raise UnserializerException
        self.pos += 1
        return self.repr[self.pos - 1]
        
    def read_int(self):
        if self.pos + LONG_SIZE > self.lgt:
            raise UnserializerException
        stop = self.pos + LONG_SIZE
        assert stop >= 0
        res = runpack('l', self.repr[self.pos:stop])
        self.pos += LONG_SIZE
        return res

    def read_str(self):
        lgt = self.read_int()
        if self.pos + lgt > self.lgt:
            raise UnserializerException
        stop = self.pos + lgt
        assert stop >= 0
        res = self.repr[self.pos:stop]
        assert lgt >= 0
        self.pos += lgt
        return res

    def read_wrapped_item(self):
        type = self.read_char()
        if type == 'i':
            return self.space.wrap(self.read_int())
        else:
            raise UnserializerException("unknown type %s" % (type,))

    def read_wrapped_list(self):
        lgt = self.read_int()
        lst_w = [None] * lgt
        for i in range(lgt):
            lst_w[i] = self.read_wrapped_item()
        return lst_w

    def read_list_of_str(self):
        lgt = self.read_int()
        lst = [None] * lgt
        for i in range(lgt):
            lst[i] = self.read_str()
        return lst

    def read_list_of_chars(self):
        lgt = self.read_int()
        lst = ['\x00'] * lgt
        for i in range(lgt):
            lst[i] = self.read_char()
        return lst

    def read_list_of_int(self):
        lgt = self.read_int()
        lst = [0] * lgt
        for i in range(lgt):
            lst[i] = self.read_int()
        return lst

    def read_list_of_functions(self, interp):
        lgt = self.read_int()
        lst = [None] * lgt
        for i in range(lgt):
            lst[i] = self.read_callable(interp)
        return lst

    def read_class(self, interp):
        from hippy.klass import UserClass, Method
        
        name = self.read_str()
        cls = UserClass(name)
        no_of_methods = self.read_int()
        methods = OrderedDict()
        for i in range(no_of_methods):
            name = self.read_str()
            access_flags = self.read_int()
            func = self.read_function(interp)
            func.bytecode.method_of_class = cls
            meth = Method(func, access_flags, cls)
            methods[name] = meth
            if name == '__construct':
                cls.constructor_method = meth
        cls.methods = methods
        cls.class_declaration_now_encountered(interp)
        return cls

    def read_function(self, interp):
        from hippy.function import Function

        bytecode = self.unserialize(interp)
        names = self.read_list_of_str()
        types = self.read_list_of_chars()
        if len(names) != len(types):
            raise UnserializerException
        args = [(types[i], names[i], None) for i in range(len(names))]
        return Function(args, [], [], bytecode)

    def read_callable(self, interp):
        c = self.read_char()
        if c == 'f':
            return self.read_function(interp)
        elif c == 'u':
            return self.read_class(interp)
        else:
            raise UnserializerException
    
    def unserialize(self, interp):
        code = self.read_str()
        consts_w = self.read_wrapped_list()[:]
        name = self.read_str()
        filename = self.read_str()
        startlineno = self.read_int()
        sourcelines = self.read_list_of_str()[:]
        names = self.read_list_of_str()[:]
        varnames = self.read_list_of_str()[:]
        superglobals = self.read_list_of_int()[:]
        this_var_num = self.read_int()
        user_functions = self.read_list_of_functions(interp)[:]
        bc_mapping = self.read_list_of_int()[:]
        return ByteCode(code, consts_w, names, varnames, user_functions,
                        filename,
                        sourcelines, name=name, startlineno=startlineno,
                        superglobals=superglobals, this_var_num=this_var_num,
                        bc_mapping=bc_mapping)
    
def unserialize(bytecode_as_str, interp):
    space = interp.space
    return Unserializer(bytecode_as_str, space).unserialize(interp)
