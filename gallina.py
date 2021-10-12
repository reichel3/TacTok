# Utilities for reconstructing Gallina terms from their serialized S-expressions in CoqGym
from io import StringIO
from vernac_types import Constr__constr
from lark import Lark, Transformer, Visitor, Discard
from lark.lexer import Token
from lark.tree import Tree
from lark.tree import pydot__tree_to_png
from serapi import SerAPI
import logging
logging.basicConfig(level=logging.DEBUG)
from collections import defaultdict
import re
import pdb


def traverse_postorder(node, callback, parent_info=None, get_parent_info=None):
    old_parent_info = parent_info
    if get_parent_info is not None:
        parent_info = get_parent_info(node, parent_info)
    for c in node.children:
        if isinstance(c, Tree):
            traverse_postorder(c, callback, parent_info, get_parent_info)
    if get_parent_info is not None:
        callback(node, old_parent_info)
    else:
        callback(node)

SERAPI = SerAPI(600)

CONSTRUCTOR_NONTERMINALS = {
    'constructor_construct': '(Construct ({0} {1}))',
    'constructor_ulevel':  '(ULevel {})',
    'names__label__t': '{}',
    'names__constructor': '({0} {1})',
    'names__inductive': '({0} {1})',
    'constructor_mutind': '(Mutind {0} {1} {2})',
    'constructor_mpfile': '(MPfile {})',
    'constructor_mpbound': '(MPbound {})',
    'constructor_mpdot': '(MPdot {0} {1})',
    'constructor_mbid': '(Mbid {0} {1})'
}

# Takes a tree and converts it back to a string rep of an s-expression
def unparse(node):
    if node.data == 'int':
        return node.children[0].value
    elif node.data == 'names__id__t':
        return '(Id {})'.format(node.children[0].data)
    elif node.data == 'constructor_dirpath':
        return '(DirPath ({}))'.format(' '.join(map(unparse, node.children)))
    elif node.data == 'constructor_instance':
        return '(Instance ({}))'.format(' '.join(map(unparse, node.children)))
    else:
        return CONSTRUCTOR_NONTERMINALS[node.data].format(*map(unparse, node.children))

class GallinaTermParser:

    def __init__(self, caching=True):
        self.caching = caching
        t = Constr__constr()
        self.grammar = t.to_ebnf(recursive=True) + '''
        %import common.STRING_INNER
        %import common.ESCAPED_STRING
        %import common.SIGNED_INT
        %import common.WS
        %ignore WS
        '''
        self.parser = Lark(StringIO(self.grammar), start='constr__constr', parser='lalr')
        if caching:
            self.cache = {}


    def parse_no_cache(self, term_str):
        ast = self.parser.parse(term_str)

        ast.quantified_idents = set()

        def get_quantified_idents(node):
            if node.data == 'constructor_prod' and node.children != [] and node.children[0].data == 'constructor_name':
                ident = node.children[0].children[0].value
                if ident.startswith('"') and ident.endswith('"'):
                    ident = ident[1:-1]
                ast.quantified_idents.add(ident)

        traverse_postorder(ast, get_quantified_idents)
        ast.quantified_idents = list(ast.quantified_idents)

        def make_ident(value):
            # Just make everything a nonterminal for compatibility
            ident_value = Tree(value, [])
            ident_wrapper = Tree('names__id__t', [ident_value])
            ident_value.height = 0
            ident_wrapper.height = 1
            return ident_wrapper

        # Postprocess: compute height, remove some tokens (variable names), make identifiers explicit
        def postprocess(node, is_construct_child):
            # Recover the constructor name
            if node.data == 'constructor_construct':
                unparsed = unparse(node)
                # TODO: fix not working for non-builtins due to wrong path
                constructor_name = SERAPI.print_constr(unparsed)[1:]
                node.children.append(make_ident(constructor_name))

            children = []
            node.height = 0
            for c in node.children:
                if isinstance(c, Tree):
                    node.height = max(node.height, c.height + 1)
                    children.append(c)
                # Don't erase fully-qualified names
                elif node.data == 'names__label__t' or node.data == 'constructor_dirpath':
                    node.height = 2
                    children.append(make_ident(c.value))
                # Don't erase the node if it is part of a constructor
                elif is_construct_child:
                    children.append(c)
            node.children = children

        def get_is_construct_child(node, is_construct_child):
            return is_construct_child or node.data == 'constructor_construct'

        traverse_postorder(ast, postprocess, False, get_is_construct_child)
        return ast


    def parse(self, term_str):
        if self.caching:
            if term_str not in self.cache:
                self.cache[term_str] = self.parse_no_cache(term_str)
            return self.cache[term_str]
        else:
            return self.parse_no_cache(term_str)


    def print_grammar(self):
        print(self.grammar)


class Counter(Visitor):

    def __init__(self):
        super().__init__()
        self.counts_nonterminal = defaultdict(int)
        self.counts_terminal = defaultdict(int)

    def __default__(self, tree):
         self.counts_nonterminal[tree.data] += 1
         for c in tree.children:
             if isinstance(c, Token):
                 self.counts_terminal[c.value] += 1


class TreeHeight(Transformer):

    def __default__(self, symbol, children, meta):
        return 1 + max([0 if isinstance(c, Token) else c for c in children] + [-1])


class TreeNumTokens(Transformer):

    def __default__(self, symbol, children, meta):
        return sum([1 if isinstance(c, Token) else c for c in children])
