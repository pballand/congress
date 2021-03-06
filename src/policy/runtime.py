#! /usr/bin/python
#
# Copyright (c) 2013 VMware, Inc. All rights reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#

import collections
import logging
import copy

import compile
import unify

class Tracer(object):
    def __init__(self):
        self.expressions = []
    def trace(self, table):
        self.expressions.append(table)
    def is_traced(self, table):
        return table in self.expressions or '*' in self.expressions
    def log(self, table, msg, depth=0):
        if self.is_traced(table):
            logging.debug("{}{}".format(("| " * depth), msg))

class CongressRuntime (Exception):
    pass

class ExecutionLogger(object):
    def __init__(self):
        self.messages = []

    def debug(self, msg):
        self.messages.append(msg)
    def info(self, msg):
        self.messages.append(msg)
    def warn(self, msg):
        self.messages.append(msg)
    def error(self, msg):
        self.messages.append(msg)
    def critical(self, msg):
        self.messages.append(msg)

    def contents(self):
        return '\n'.join(self.messages)

    def empty(self):
        self.messages = []


##############################################################################
## Events
##############################################################################

class EventQueue(object):
    def __init__(self):
        self.queue = collections.deque()

    def enqueue(self, event):
        self.queue.append(event)

    def dequeue(self):
        return self.queue.popleft()

    def __len__(self):
        return len(self.queue)

    def __str__(self):
        return "[" + ",".join([str(x) for x in self.queue]) + "]"

class Event(object):
    def __init__(self, formula=None, insert=True, proofs=None):
        if proofs is None:
            proofs = []
        self.formula = formula
        self.proofs = proofs
        self.insert = insert
        # logging.debug("EV: created event {}".format(str(self)))

    def is_insert(self):
        return self.insert

    def tablename(self):
        return self.formula.tablename()

    def __str__(self):
        formula = self.formula.make_update(self.insert)
        return "{} with {}".format(str(formula),
            iterstr(self.proofs))

    def __hash__(self):
        return hash("Event(formula={}, proofs={}, insert={}".format(
            str(self.formula), str(self.proofs), str(self.insert)))

    def __eq__(self, other):
        return (self.atom == other.atom and
                self.proofs == other.proofs and
                self.insert == other.insert)

def iterstr(iter):
    return "[" + ";".join([str(x) for x in iter]) + "]"

def list_to_database(atoms):
    database = Database()
    for atom in atoms:
        if atom.is_atom():
            database.insert(atom)
    return database

def string_to_database(string):
    return list_to_database(compile.parse(string))

##############################################################################
## Logical Building Blocks
##############################################################################

class Proof(object):
    """ A single proof. Differs semantically from Database's
    Proof in that this verison represents a proof that spans rules,
    instead of just a proof for a single rule. """
    def __init__(self, root, children):
        self.root = root
        self.children = children

    def __str__(self):
        return self.str_tree(0)

    def str_tree(self, depth):
        s = " " * depth
        s += str(self.root)
        s += "\n"
        for child in self.children:
            s += child.str_tree(depth + 1)
        return s

    def leaves(self):
        if len(self.children) == 0:
            return [self.root]
        result = []
        for child in self.children:
            result.extend(child.leaves())
        return result

class DeltaRule(object):
    def __init__(self, trigger, head, body, original):
        self.trigger = trigger  # atom
        self.head = head  # atom
        self.body = body  # list of literals
        self.original = original # Rule from which SELF was derived

    def __str__(self):
        return "<trigger: {}, head: {}, body: {}>".format(
            str(self.trigger), str(self.head), [str(lit) for lit in self.body])

    def __eq__(self, other):
        return (self.trigger == other.trigger and
                self.head == other.head and
                len(self.body) == len(other.body) and
                all(self.body[i] == other.body[i]
                        for i in xrange(0, len(self.body))))

    def variables(self):
        """ Return the set of variables occurring in this delta rule. """
        vs = self.trigger.variables()
        vs |= self.head.variables()
        for atom in self.body:
            vs |= atom.variables()
        return vs

    def tables(self):
        """ Return the set of tablenames occurring in this delta rule. """
        tables = set()
        tables.add(self.head.table)
        tables.add(self.trigger.table)
        for atom in self.body:
            tables.add(atom.table)
        return tables


##############################################################################
## Abstract Theories
##############################################################################

class Theory(object):
    def __init__(self, name=None, abbr=None):
        self.tracer = Tracer()
        if name is None:
            self.name = repr(self)
        else:
            self.name = name
        if abbr is None:
            self.abbr = "th"
        else:
            self.abbr = abbr
        maxlength = 6
        if len(self.abbr) > maxlength:
            self.trace_prefix = self.abbr[0:maxlength]
        else:
            self.trace_prefix = self.abbr + " " * (maxlength - len(self.abbr))

    def set_tracer(self, tracer):
        self.tracer = tracer

    def log(self, table, msg, depth=0):
        self.tracer.log(table, self.trace_prefix + ": " + msg, depth)


class TopDownTheory(Theory):
    """ Class that holds the Top-Down evaluation routines.  Classes
    will inherit from this class if they want to import and specialize
    those routines. """
    class TopDownContext(object):
        """ Struct for storing the search state of top-down evaluation """
        def __init__(self, literals, literal_index, binding, context, depth):
            self.literals = literals
            self.literal_index = literal_index
            self.binding = binding
            self.previous = context
            self.depth = depth

        def __str__(self):
            return ("TopDownContext<literals={}, literal_index={}, binding={}, "
                    "previous={}, depth={}>").format(
                "[" + ",".join([str(x) for x in self.literals]) + "]",
                str(self.literal_index), str(self.binding),
                str(self.previous), str(self.depth))

    class TopDownResult(object):
        """ Stores a single result for top-down-evaluation """
        def __init__(self, binding, support):
            self.binding = binding
            self.support = support   # for abduction

        def __str__(self):
            return "TopDownResult(binding={}, support={})".format(
                unify.binding_str(self.binding), iterstr(self.support))

    class TopDownCaller(object):
        """ Struct for storing info about the original caller of top-down
        evaluation.
        VARIABLES is the list of variables (from the initial query)
            that we want bindings for.
        BINDING is the initially empty BiUnifier.
        FIND_ALL controls whether just the first or all answers are found.
        ANSWERS is populated by top-down evaluation: it is the list of
               VARIABLES instances that the search process proved true."""

        def __init__(self, variables, binding, theory,
                    find_all=True, save=None):
            # an iterable of variable objects
            self.variables = variables
            # a bi-unifier
            self.binding = binding
            # the top-level theory (for included theories)
            self.theory = theory
            # a boolean
            self.find_all = find_all
            # The results of top-down-eval: a list of TopDownResults
            self.results = []
            # a Function that takes a compile.Literal and a unifier and
            #   returns T iff that literal under the unifier should be
            #   saved as part of an abductive explanation
            self.save = save
            # A variable used to store explanations as they are constructed
            self.support = []

        def __str__(self):
            return ("TopDownCaller<variables={}, binding={}, find_all={}, "
                    "results={}, save={}, support={}>".format(
                iterstr(self.variables), str(self.binding), str(self.find_all),
                iterstr(self.results), repr(self.save), iterstr(self.support)))

    #########################################
    ## External interface

    def __init__(self, name=None, abbr=None):
        super(TopDownTheory, self).__init__(name=name, abbr=abbr)
        self.includes = []

    def select(self, query, find_all=True):
        """ Return list of instances of QUERY that are true.
            If FIND_ALL is False, the return list has at most 1 element."""
        assert (isinstance(query, compile.Atom) or
                isinstance(query, compile.Rule)), "Query must be atom/rule"
        if isinstance(query, compile.Atom):
            literals = [query]
        else:
            literals = query.body
        # Because our output is instances of QUERY, need all the variables
        #   in QUERY.
        bindings = self.top_down_evaluation(query.variables(), literals,
            find_all=find_all)
        # logging.debug("Top_down_evaluation returned: {}".format(
        #     str(bindings)))
        if len(bindings) > 0:
            self.log(query.tablename(), "Found answer {}".format(
                "[" + ",".join([str(query.plug(x))
                                for x in bindings]) + "]"))
        return [query.plug(x) for x in bindings]

    def explain(self, query, tablenames, find_all=True):
        """ Same as select except stores instances of TABLENAMES
        that participated in each proof. If QUERY is an atom,
        returns list of rules with QUERY in the head and
        the stored instances of TABLENAMES in the body; if QUERY is
        a rule, the rules returned have QUERY's head in the head
        and the stored instances of TABLENAMES in the body. """
        # This is different than abduction because instead of replacing
        #   a proof attempt with saving a literal, we want to save a literal
        #   after a successful proof attempt.
        assert False, "Not yet implemented"

    def abduce(self, query, tablenames, find_all=True):
        """ Computes additional literals that if true would make
            (some instance of) QUERY true.  Returns a list of rules
            where the head represents an instance of the QUERY and
            the body is the collection of literals that must be true
            in order to make that instance true.  If QUERY is a rule,
            each result is an instance of the head of that rule, and
            the computed literals if true make the body of that rule
            (and hence the head) true.  If FIND_ALL is true, the
            return list has at most one element.
            Limitation: every negative literal relevant to a proof of
            QUERY is unconditionally true, i.e. no literals are saved
            when proving a negative literal is true."""
        assert (isinstance(query, compile.Atom) or
                isinstance(query, compile.Rule)), \
             "Explain requires a formula"
        if isinstance(query, compile.Atom):
            literals = [query]
            output = query
        else:
            literals = query.body
            output = query.head
        # We need all the variables we will be using in the output, which
        #   here is just the head of QUERY (or QUERY itself if it is an atom)
        abductions = self.top_down_abduction(output.variables(), literals,
            find_all=find_all, save=lambda lit,binding: lit.table in tablenames)
        results = [compile.Rule(output.plug(abd.binding), abd.support)
                        for abd in abductions]
        self.log(query.tablename(), "abduction result:")
        self.log(query.tablename(), "\n".join([str(x) for x in results]))
        return results

    def consequences(self, filter=None, table_names=None):
        """ Return all the true instances of any table that is defined
            in this theory.  Default tablenames is DEFINED_TABLE_NAMES. """
        if table_names is None:
            table_names = self.defined_table_names()
        results = set()
        # create queries: need table names and arities
        for table in table_names:
            if filter is None or filter(table):
                arity = self.arity(table)
                vs = []
                for i in xrange(0, arity):
                    vs.append("x" + str(i))
                vs = [compile.Variable(var) for var in vs]
                query = compile.Atom(table, vs)
                results |= set(self.select(query))
        return results

    def top_down_evaluation(self, variables, literals,
            binding=None, find_all=True):
        """ Compute all bindings of VARIABLES that make LITERALS
            true according to the theory (after applying the unifier BINDING).
            If FIND_ALL is False, stops after finding one such binding.
            Returns a list of dictionary bindings. """
        # logging.debug("CALL: top_down_evaluation(vars={}, literals={}, "
        #               "binding={})".format(
        #         iterstr(variables), iterstr(literals),
        #         str(binding)))
        results = self.top_down_abduction(variables, literals,
            binding=binding, find_all=find_all, save=None)
        # logging.debug("EXIT: top_down_evaluation(vars={}, literals={}, "
        #               "binding={}) returned {}".format(
        #         iterstr(variables), iterstr(literals),
        #         str(binding), iterstr(results)))
        return [x.binding for x in results]

    def top_down_abduction(self, variables, literals, binding=None,
            find_all=True, save=None):
        """ Compute all bindings of VARIABLES that make LITERALS
            true according to the theory (after applying the
            unifier BINDING), if we add some number of additional
            literals.  Note: will not save any literals that are
            needed to prove a negated literal since the results
            would not make sense.  Returns a list of TopDownResults. """
        if binding is None:
            binding = self.new_bi_unifier()
        caller = self.TopDownCaller(variables, binding, self,
            find_all=find_all, save=save)
        if len(literals) == 0:
            self.top_down_finish(None, caller)
        else:
            # Note: must use same unifier in CALLER and CONTEXT
            context = self.TopDownContext(literals, 0, binding, None, 0)
            self.top_down_eval(context, caller)
        return list(set(caller.results))

    #########################################
    ## Internal implementation

    def top_down_eval(self, context, caller):
        """ Compute all instances of LITERALS (from LITERAL_INDEX and above)
            that are true according to the theory (after applying the
            unifier BINDING to LITERALS).  Returns False or an answer. """
        # no recursive rules, ever; this style of algorithm will not terminate
        lit = context.literals[context.literal_index]
        # logging.debug("CALL: top_down_eval({}, {})".format(str(context),
        #     str(caller)))

        # abduction
        if caller.save is not None and caller.save(lit, context.binding):
            self.print_call(lit, context.binding, context.depth)
            # save lit and binding--binding may not be fully flushed out
            #   when we save (or ever for that matter)
            caller.support.append((lit, context.binding))
            self.print_save(lit, context.binding, context.depth)
            success = self.top_down_finish(context, caller)
            caller.support.pop() # pop in either case
            if success:
                return True
            else:
                self.print_fail(lit, context.binding, context.depth)
                return False

        # regular processing
        if lit.is_negated():
            # logging.debug("{} is negated".format(str(lit)))
            # recurse on the negation of the literal
            assert lit.plug(context.binding).is_ground(), \
                "Negated literals must be ground when evaluated"
            self.print_call(lit, context.binding, context.depth)
            new_context = self.TopDownContext([lit.complement()],
                    0, context.binding, None, context.depth + 1)
            new_caller = self.TopDownCaller(caller.variables, caller.binding,
                caller.theory, find_all=False, save=None)
            # Make sure new_caller has find_all=False, so we stop as soon
            #    as we can.
            # Ensure save=None so that abduction does not save anything.
            #    Saving while performing NAF makes no sense.
            if self.top_down_includes(new_context, new_caller):
                self.print_fail(lit, context.binding, context.depth)
                return False
            else:
                # don't need bindings b/c LIT must be ground
                return self.top_down_finish(context, caller, redo=False)
        elif lit.tablename() == 'true':
            self.print_call(lit, context.binding, context.depth)
            return self.top_down_finish(context, caller, redo=False)
        elif lit.tablename() == 'false':
            self.print_fail(lit, context.binding, context.depth)
            return False
        else:
            return self.top_down_truth(context, caller)

    def top_down_truth(self, context, caller):
        """ Do top-down evaluation over the root theory at which
            the call was made and all the included theories. """
        return caller.theory.top_down_includes(context, caller)

    def top_down_includes(self, context, caller):
        """ Top-down evaluation of all the theories included in this theory. """
        is_true = self.top_down_th(context, caller)
        if is_true and not caller.find_all:
            return True
        for th in self.includes:
            is_true = th.top_down_includes(context, caller)
            if is_true and not caller.find_all:
                return True
        return False

    def top_down_th(self, context, caller):
        """ Top-down evaluation for the rules in SELF.CONTENTS. """
        # logging.debug("top_down_th({})".format(str(context)))
        lit = context.literals[context.literal_index]
        self.print_call(lit, context.binding, context.depth)
        for rule in self.head_index(lit.table):
            unifier = self.new_bi_unifier()
            # Prefer to bind vars in rule head
            undo = self.bi_unify(self.head(rule), unifier, lit, context.binding)
            # self.log(lit.table, "Rule: {}, Unifier: {}, Undo: {}".format(
            #     str(rule), str(unifier), str(undo)))
            if undo is None:  # no unifier
                continue
            if len(self.body(rule)) == 0:
                if self.top_down_finish(context, caller):
                    unify.undo_all(undo)
                    if not caller.find_all:
                        return True
                else:
                    unify.undo_all(undo)
            else:
                new_context = self.TopDownContext(rule.body, 0,
                    unifier, context, context.depth + 1)
                if self.top_down_eval(new_context, caller):
                    unify.undo_all(undo)
                    if not caller.find_all:
                        return True
                else:
                    unify.undo_all(undo)
        self.print_fail(lit, context.binding, context.depth)
        return False

    def top_down_finish(self, context, caller, redo=True):
        """ Helper that is called once top_down successfully completes
            a proof for a literal.  Handles (i) continuing search
            for those literals still requiring proofs within CONTEXT,
            (ii) adding solutions to CALLER once all needed proofs have
            been found, and (iii) printing out Redo/Exit during tracing.
            Returns True if the search is finished and False otherwise.
            Temporary, transparent modification of CONTEXT."""
        if context is None:
            # Found an answer; now store it
            if caller is not None:
                # flatten bindings and store before we undo
                # copy caller.support and store before we undo
                binding = {}
                for var in caller.variables:
                    binding[var] = caller.binding.apply(var)
                result = self.TopDownResult(binding,
                    [support[0].plug(support[1], caller=caller)
                        for support in caller.support])
                caller.results.append(result)
            return True
        else:
            self.print_exit(context.literals[context.literal_index],
                context.binding, context.depth)
            # continue the search
            if context.literal_index < len(context.literals) - 1:
                context.literal_index += 1
                finished = self.top_down_eval(context, caller)
                context.literal_index -= 1  # in case answer is False
            else:
                finished = self.top_down_finish(context.previous, caller)
            # return search result (after printing a Redo if failure)
            if redo and (not finished or caller.find_all):
                self.print_redo(context.literals[context.literal_index],
                    context.binding, context.depth)
            return finished

    def print_call(self, literal, binding, depth):
        self.log(literal.table, "{}Call: {}".format("| "*depth,
            literal.plug(binding)))

    def print_exit(self, literal, binding, depth):
        self.log(literal.table, "{}Exit: {}".format("| "*depth,
            literal.plug(binding)))

    def print_save(self, literal, binding, depth):
        self.log(literal.table, "{}Save: {}".format("| "*depth,
            literal.plug(binding)))

    def print_fail(self, literal, binding, depth):
        self.log(literal.table, "{}Fail: {}".format("| "*depth,
            literal.plug(binding)))
        return False

    def print_redo(self, literal, binding, depth):
        self.log(literal.table, "{}Redo: {}".format("| "*depth,
            literal.plug(binding)))
        return False

   #########################################
    ## Routines for specialization

    @classmethod
    def new_bi_unifier(cls, dictionary=None):
        """ Return a unifier compatible with unify.bi_unify """
        return unify.BiUnifier(dictionary=dictionary)
            # lambda (index):
            # compile.Variable("x" + str(index)), dictionary=dictionary)

    def arity(self, tablename):
        """ Return the number of arguments TABLENAME takes or None if
        unknown because TABLENAME is not defined here. """
        # assuming a fixed arity for all tables
        formulas = self.head_index(tablename)
        if len(formulas) == 0:
            return None
        first = formulas[0]
        # should probably have an overridable function for computing
        #   the arguments of a head.  Instead we assume heads have .arguments
        return len(self.head(first).arguments)

    def defined_table_names(self):
        """ This routine returns the list of all table names that are
        defined/written to in this theory. """
        return self.contents.keys()

    def head_index(self, table):
        """ This routine must return all the formulas pertinent for
        top-down evaluation when a literal with TABLE is at the top
        of the stack. """
        if table not in self.contents:
            return []
        return self.contents[table]

    def head(self, formula):
        """ Given a FORMULA, return the thing to unify against.
            Usually, FORMULA is a compile.Rule, but it could be anything
            returned by HEAD_INDEX."""
        return formula.head

    def body(self, formula):
        """ Given a FORMULA, return a list of things to push onto the
        top-down eval stack. """
        return formula.body

    def bi_unify(self, head, unifier1, body_element, unifier2):
        """ Given something returned by self.head HEAD and an element in
        the return of self.body BODY_ELEMENT, modify UNIFIER1 and UNIFIER2
        so that HEAD.plug(UNIFIER1) == BODY_ELEMENT.plug(UNIFIER2).
        Returns changes that can be undone via unify.undo-all. """
        return unify.bi_unify_atoms(head, unifier1, body_element, unifier2)

##############################################################################
## Concrete Theory: Database
##############################################################################

class Database(TopDownTheory):
    class Proof(object):
        def __init__(self, binding, rule):
            self.binding = binding
            self.rule = rule

        def __str__(self):
            return "apply({}, {})".format(str(self.binding), str(self.rule))

        def __eq__(self, other):
            result = (self.binding == other.binding and
                      self.rule == other.rule)
            # logging.debug("Pf: Comparing {} and {}: {}".format(
            #     str(self), str(other), result))
            # logging.debug("Pf: {} == {} is {}".format(
            #     str(self.binding), str(other.binding), self.binding == other.binding))
            # logging.debug("Pf: {} == {} is {}".format(
            #     str(self.rule), str(other.rule), self.rule == other.rule))
            return result

    class ProofCollection(object):
        def __init__(self, proofs):
            self.contents = list(proofs)

        def __str__(self):
            return '{' + ",".join(str(x) for x in self.contents) + '}'

        def __isub__(self, other):
            if other is None:
                return
            # logging.debug("PC: Subtracting {} and {}".format(str(self), str(other)))
            remaining = []
            for proof in self.contents:
                if proof not in other.contents:
                    remaining.append(proof)
            self.contents = remaining
            return self

        def __ior__(self, other):
            if other is None:
                return
            # logging.debug("PC: Unioning {} and {}".format(str(self), str(other)))
            for proof in other.contents:
                # logging.debug("PC: Considering {}".format(str(proof)))
                if proof not in self.contents:
                    self.contents.append(proof)
            return self

        def __getitem__(self, key):
            return self.contents[key]

        def __len__(self):
            return len(self.contents)

        def __ge__(self, iterable):
            for proof in iterable:
                if proof not in self.contents:
                    # logging.debug("Proof {} makes {} not >= {}".format(
                    #     str(proof), str(self), iterstr(iterable)))
                    return False
            return True

        def __le__(self, iterable):
            for proof in self.contents:
                if proof not in iterable:
                    # logging.debug("Proof {} makes {} not <= {}".format(
                    #     str(proof), str(self), iterstr(iterable)))
                    return False
            return True

        def __eq__(self, other):
            return self <= other and other <= self

    class DBTuple(object):
        def __init__(self, iterable, proofs=None):
            self.tuple = tuple(iterable)
            if proofs is None:
                proofs = []
            self.proofs = Database.ProofCollection(proofs)

        def __eq__(self, other):
            return self.tuple == other.tuple

        def __str__(self):
            return str(self.tuple) + str(self.proofs)

        def __len__(self):
            return len(self.tuple)

        def __getitem__(self, index):
            return self.tuple[index]

        def __setitem__(self, index, value):
            self.tuple[index] = value

        def match(self, atom, unifier):
            # logging.debug("DBTuple matching {} against atom {} in {}".format(
            #     str(self), iterstr(atom.arguments), str(unifier)))
            if len(self.tuple) != len(atom.arguments):
                return None
            changes = []
            for i in xrange(0, len(atom.arguments)):
                val, binding = unifier.apply_full(atom.arguments[i])
                # logging.debug("val({})={} at {}; comparing to object {}".format(
                #     str(atom.arguments[i]), str(val), str(binding),
                #     str(self.tuple[i])))
                if val.is_variable():
                    changes.append(binding.add(val,
                        compile.Term.create_from_python(self.tuple[i]),
                        None))
                else:
                    if val.name != self.tuple[i]:
                        unify.undo_all(changes)
                        return None
            return changes

    def __init__(self, name=None, abbr=None):
        super(Database, self).__init__(name=name, abbr=abbr)
        self.data = {}

    def __str__(self):
        def hash2str (h):
            s = "{"
            s += ", ".join(["{} : {}".format(str(key), str(h[key]))
                  for key in h])
            return s

        def hashlist2str (h):
            strings = []
            for key in h:
                s = "{} : ".format(key)
                s += '['
                s += ', '.join([str(val) for val in h[key]])
                s += ']'
                strings.append(s)
            return '{' + ", ".join(strings) + '}'

        return hashlist2str(self.data)

    def __eq__(self, other):
        return self.data == other.data

    def __sub__(self, other):
        def add_tuple(table, dbtuple):
            new = [table]
            new.extend(dbtuple.tuple)
            results.append(new)

        results = []
        for table in self.data:
            if table not in other.data:
                for dbtuple in self.data[table]:
                    add_tuple(table, dbtuple)
            else:
                for dbtuple in self.data[table]:
                    if dbtuple not in other.data[table]:
                        add_tuple(table, dbtuple)
        return results

    def __or__(self, other):
        def add_db(db):
            for table in db.data:
                for dbtuple in db.data[table]:
                    result.insert(compile.Atom.create_from_table_tuple(
                            table, dbtuple.tuple), proofs=dbtuple.proofs)
        result = Database()
        add_db(self)
        add_db(other)
        return result

    def __getitem__(self, key):
        # KEY must be a tablename
        return self.data[key]

    def contents(self):
        """ Return a sequence of Atoms representing all the table data. """
        results = []
        for table in self.data:
            for dbtuple in self.data[table]:
                results.append(compile.Atom.create_from_table_tuple(
                    table, dbtuple.tuple))
        return results

    def is_noop(self, event):
        """ Returns T if EVENT is a noop on the database. """
        # insert/delete same code but with flipped return values
        # Code below is written as insert, except noop initialization.
        if event.is_insert():
            noop = True
        else:
            noop = False
        if event.formula.table not in self.data:
            return not noop
        event_data = self.data[event.formula.table]
        raw_tuple = tuple(event.formula.argument_names())
        for dbtuple in event_data:
            if dbtuple.tuple == raw_tuple:
                if event.proofs <= dbtuple.proofs:
                    return noop
        return not noop

    def explain(self, atom):
        if atom.table not in self.data or not atom.is_ground():
            return self.ProofCollection([])
        args = tuple([x.name for x in atom.arguments])
        for dbtuple in self.data[atom.table]:
            if dbtuple.tuple == args:
                return dbtuple.proofs

    def table_names(self):
        """ Return all table names defined in this theory and all included
            theories. """
        tables = set()
        tables |= self.defined_table_names()
        for theory in self.includes:
            tables |= theory.defined_table_names()
        return tables

    # overloads for TopDownTheory so we can properly use the
    #    top_down_evaluation routines
    def defined_table_names(self):
        return self.data.keys()

    def head_index(self, table):
        if table not in self.data:
            return []
        return self.data[table]

    def head(self, thing):
        return thing

    def body(self, thing):
        return []

    def bi_unify(self, dbtuple, unifier1, atom, unifier2):
        """ THING1 is always a ground DBTuple and THING2 is always an ATOM. """
        return dbtuple.match(atom, unifier2)

    def atom_to_internal(self, atom, proofs=None):
        return atom.table, self.DBTuple(atom.argument_names(), proofs)

    def modify(self, atom, is_insert=True, proofs=None):
        """ Inserts/deletes ATOM and returns a list of changes that
        were caused. That list contains either 0 or 1 Event."""
        assert isinstance(atom, compile.Atom), "Modify requires compile.Atom"
        event = Event(formula=atom, insert=is_insert, proofs=proofs)
        self.log(atom.table, "Modify: {}".format(str(atom)))
        if self.is_noop(event):
            self.log(atom.table, "Event {} is a noop".format(str(event)))
            return []
        if is_insert:
            self.insert(atom, proofs=proofs)
        else:
            self.delete(atom, proofs=proofs)
        return [event]

    def insert(self, atom, proofs=None):
        assert isinstance(atom, compile.Atom), "Insert requires compile.Atom"
        table, dbtuple = self.atom_to_internal(atom, proofs)
        self.log(table, "Insert: {}".format(str(atom)))
        if table not in self.data:
            self.data[table] = [dbtuple]
            self.log(atom.table, "First tuple in table {}".format(table))
            return
        else:
            self.log(table, "Not first tuple in table {}".format(table))
            for existingtuple in self.data[table]:
                assert(existingtuple.proofs is not None)
                if existingtuple.tuple == dbtuple.tuple:
                    # self.log(table, "Found existing tuple: {}".format(
                    #     str(existingtuple)))
                    assert(existingtuple.proofs is not None)
                    existingtuple.proofs |= dbtuple.proofs
                    # self.log(table, "Updated tuple: {}".format(str(existingtuple)))
                    assert(existingtuple.proofs is not None)
                    return
            self.data[table].append(dbtuple)
            self.log(table, "current contents of {}: {}".format(table,
                iterstr(self.data[table])))


    def delete(self, atom, proofs=None):
        assert isinstance(atom, compile.Atom), "Delete requires compile.Atom"
        self.log(atom.table, "Delete: {}".format(str(atom)))
        table, dbtuple = self.atom_to_internal(atom, proofs)
        if table not in self.data:
            return
        for i in xrange(0, len(self.data[table])):
            existingtuple = self.data[table][i]
            #self.log(table, "Checking tuple {}".format(str(existingtuple)))
            if existingtuple.tuple == dbtuple.tuple:
                existingtuple.proofs -= dbtuple.proofs
                if len(existingtuple.proofs) == 0:
                    del self.data[table][i]
                return

##############################################################################
## Concrete Theories: other
##############################################################################

class NonrecursiveRuleTheory(TopDownTheory):
    """ A non-recursive collection of Rules. """

    def __init__(self, rules=None, name=None, abbr=None):
        super(NonrecursiveRuleTheory, self).__init__(name=name, abbr=abbr)
        # dictionary from table name to list of rules with that table in head
        self.contents = {}
        if rules is not None:
            for rule in rules:
                self.insert(rule)

    def __str__(self):
        return str(self.contents)

    def insert(self, rule):
        """ Insert RULE and return list of changes (either 0 or 1
            rules). """
        if isinstance(rule, compile.Atom):
            rule = compile.Rule(rule, [], rule.location)
        self.log(rule.head.table,
            "Insert: {}".format(str(rule)))
        table = rule.head.table
        if table in self.contents:
            if rule not in self.contents[table]:  # eliminate dups
                self.contents[table].append(rule)
                return [rule]
            return []
        else:
            self.contents[table] = [rule]
            return [rule]

    def delete(self, rule):
        """ Delete RULE and return list of changes (either 0 or 1
            rules). """
        if isinstance(rule, compile.Atom):
            rule = compile.Rule(rule, [], rule.location)
        self.log(rule.head.table, "Delete: {}".format(str(rule)))
        table = rule.head.table
        if table in self.contents:
            try:
                self.contents[table].remove(rule)
                return [rule]
            except ValueError:
                return []
        return []

    def define(self, rules):
        """ Empties and then inserts RULES. """
        self.empty()
        for rule in rules:
            self.insert(rule)

    def empty(self):
        """ Deletes contents of theory. """
        self.contents = {}

    def content(self):
        results = []
        for table in self.contents:
            results.extend(self.contents[table])
        return results

class DeltaRuleTheory (Theory):
    """ A collection of DeltaRules. """
    def __init__(self, name=None, abbr=None):
        super(DeltaRuleTheory, self).__init__(name=name, abbr=abbr)
        # dictionary from table name to list of rules with that table as trigger
        self.contents = {}
        # dictionary from delta_rule to the rule from which it was derived
        self.originals = set()
        # dictionary from table name to number of rules with that table in head
        self.views = {}
        # all tables
        self.all_tables = {}

    def modify(self, rule, is_insert):
        """ Insert/delete the compile.Rule RULE into the theory.
            Return list of changes (either the empty list or
            a list including just RULE). """
        self.log(None, "DeltaRuleTheory.modify")
        if is_insert is True:
            if self.insert(rule):
                return [rule]
        else:
            if self.delete(rule):
                return [rule]
        return []

    def insert(self, rule):
        """ Insert a compile.Rule into the theory.
            Return True iff the theory changed. """
        assert isinstance(rule, compile.Rule), \
            "DeltaRuleTheory only takes rules"
        self.log(rule.tablename(), "Insert: {}".format(str(rule)))
        if rule in self.originals:
            return False
        for delta in self.compute_delta_rules([rule]):
            self.insert_delta(delta)
        self.originals.add(rule)
        return True

    def insert_delta(self, delta):
        """ Insert a delta rule. """
        # views (tables occurring in head)
        if delta.head.table in self.views:
            self.views[delta.head.table] += 1
        else:
            self.views[delta.head.table] = 1

        # tables
        for table in delta.tables():
            if table in self.all_tables:
                self.all_tables[table] += 1
            else:
                self.all_tables[table] = 1

        # contents
        if delta.trigger.table not in self.contents:
            self.contents[delta.trigger.table] = [delta]
        else:
            self.contents[delta.trigger.table].append(delta)

    def delete(self, rule):
        """ Delete a compile.Rule from theory.
            Assumes that COMPUTE_DELTA_RULES is deterministic.
            Returns True iff the theory changed. """
        self.log(rule.tablename(), "Delete: {}".format(str(rule)))
        if rule not in self.originals:
            return False
        for delta in self.compute_delta_rules([rule]):
            self.delete_delta(delta)
        self.originals.remove(rule)
        return True

    def delete_delta(self, delta):
        # views
        if delta.head.table in self.views:
            self.views[delta.head.table] -= 1
            if self.views[delta.head.table] == 0:
                del self.views[delta.head.table]

        # tables
        for table in delta.tables():
            if table in self.all_tables:
                self.all_tables[table] -= 1
                if self.all_tables[table] == 0:
                    del self.all_tables[table]

        # contents
        if delta.trigger.table not in self.contents:
            return
        self.contents[delta.trigger.table].remove(delta)

    def __str__(self):
        return str(self.contents)

    def rules_with_trigger(self, table):
        if table not in self.contents:
            return []
        else:
            return self.contents[table]

    def is_view(self, x):
        return x in self.views

    def is_known(self, x):
        return x in self.all_tables

    def base_tables(self):
        base = []
        for table in self.all_tables:
            if table not in self.views:
                base.append(table)
        return base

    @classmethod
    def eliminate_self_joins(cls, formulas):
        """ Return new list of formulas that is equivalent to
            the list of formulas FORMULAS except that there
            are no self-joins. """
        def new_table_name(name, arity, index):
            return "___{}_{}_{}".format(name, arity, index)
        def n_variables(n):
            vars = []
            for i in xrange(0, n):
                vars.append("x" + str(i))
            return vars
        # dict from (table name, arity) tuple to
        #      max num of occurrences of self-joins in any rule
        global_self_joins = {}
        # dict from (table name, arity) to # of args for
        arities = {}
        # remove self-joins from rules
        results = []
        for rule in formulas:
            if rule.is_atom():
                results.append(rule)
                continue
            logging.debug("eliminating self joins from {}".format(rule))
            occurrences = {}  # for just this rule
            for atom in rule.body:
                table = atom.table
                arity = len(atom.arguments)
                tablearity = (table, arity)
                if tablearity not in occurrences:
                    occurrences[tablearity] = 1
                else:
                    # change name of atom
                    atom.table = new_table_name(table, arity,
                        occurrences[tablearity])
                    # update our counters
                    occurrences[tablearity] += 1
                    if tablearity not in global_self_joins:
                        global_self_joins[tablearity] = 1
                    else:
                        global_self_joins[tablearity] = \
                            max(occurrences[tablearity] - 1,
                                global_self_joins[tablearity])
            results.append(rule)
            logging.debug("final rule: {}".format(str(rule)))
        # add definitions for new tables
        for tablearity in global_self_joins:
            table = tablearity[0]
            arity = tablearity[1]
            for i in xrange(1, global_self_joins[tablearity] + 1):
                newtable = new_table_name(table, arity, i)
                args = [compile.Variable(var) for var in n_variables(arity)]
                head = compile.Atom(newtable, args)
                body = [compile.Atom(table, args)]
                results.append(compile.Rule(head, body))
                logging.debug("Adding rule {}".format(results[-1]))
        return results

    @classmethod
    def compute_delta_rules(cls, formulas):
        """ Assuming FORMULAS has no self-joins, return a list of DeltaRules
        derived from those FORMULAS. """
        formulas = cls.eliminate_self_joins(formulas)
        delta_rules = []
        for rule in formulas:
            if rule.is_atom():
                continue
            for literal in rule.body:
                newbody = [lit for lit in rule.body if lit is not literal]
                delta_rules.append(
                    DeltaRule(literal, rule.head, newbody, rule))
        return delta_rules


class MaterializedViewTheory(TopDownTheory):
    """ A theory that stores the table contents of views explicitly.
        Relies on included theories to define the contents of those
        tables not defined by the rules of the theory.
        Recursive rules are allowed. """

    def __init__(self, name=None, abbr=None):
        super(MaterializedViewTheory, self).__init__(name=name, abbr=abbr)
        # queue of events left to process
        self.queue = EventQueue()
        # data storage
        db_name = None
        db_abbr = None
        delta_name = None
        delta_abbr = None
        if name is not None:
            db_name = name + "Database"
            delta_name = name + "Delta"
        if abbr is not None:
            db_abbr = abbr + "DB"
            delta_abbr = abbr + "Dlta"
        self.database = Database(name=db_name, abbr=db_abbr)
        # rules that dictate how database changes in response to events
        self.delta_rules = DeltaRuleTheory(name=delta_name, abbr=delta_abbr)

    def set_tracer(self, tracer):
        self.tracer = tracer
        self.database.tracer = tracer
        self.delta_rules.tracer = tracer

    ############### External Interface ###############

    # SELECT is handled by TopDownTheory
    # def select(self, query):
    #     """ Returns list of instances of QUERY true in the theory. """
    #     assert (isinstance(query, compile.Atom) or
    #             isinstance(query, compile.Rule)), \
    #          "Select requires a formula"
    #     return self.database.select(query)

    def insert(self, formula):
        """ Insert FORMULA.  Returns True iff the theory changed. """
        assert (isinstance(formula, compile.Atom) or
                isinstance(formula, compile.Rule)), \
             "Insert requires a formula"
        return self.modify(formula, is_insert=True)

    def delete(self, formula):
        """ Delete FORMULA.  Returns True iff the theory changed. """
        assert (isinstance(formula, compile.Atom) or
                isinstance(formula, compile.Rule)), \
             "Delete requires a formula"
        return self.modify(formula, is_insert=False)

    def explain(self, query, tablenames, find_all):
        """ Returns None if QUERY is False in theory.  Otherwise returns
            a list of proofs that QUERY is true. """
        assert isinstance(query, compile.Atom), \
            "Explain requires an atom"
        # ignoring TABLENAMES and FIND_ALL
        #    except that we return the proper type.
        proof = self.explain_aux(query, 0)
        if proof is None:
            return None
        else:
            return [proof]

    ############### Interface implementation ###############

    def explain_aux(self, query, depth):
        self.log(query.table, "Explaining {}".format(str(query)), depth)
        # Bail out on negated literals.  Need different
        #   algorithm b/c we need to introduce quantifiers.
        if query.is_negated():
            return Proof(query, [])
        # grab first local proof, since they're all equally good
        localproofs = self.database.explain(query)
        if localproofs is None:
            return None
        if len(localproofs) == 0:   # base fact
            return Proof(query, [])
        localproof = localproofs[0]
        rule_instance = localproof.rule.plug(localproof.binding)
        subproofs = []
        for lit in rule_instance.body:
            subproof = self.explain_aux(lit, depth + 1)
            if subproof is None:
                return None
            subproofs.append(subproof)
        return Proof(query, subproofs)

    def modify(self, formula, is_insert=True):
        """ Modifies contents of theory to insert/delete FORMULA.
            Returns True iff the theory changed. """
        self.log(None, "Materialized.modify")
        self.enqueue_with_included(formula, is_insert=is_insert)
        changes = self.process_queue()
        self.log(formula.tablename(),
            "modify returns {}".format(iterstr(changes)))
        return changes

    def enqueue_with_included(self, formula, is_insert=True):
        """ Insertion/deletion of FORMULA can require communication
            with included theories.  Also, rules are a bit different
            in that they generate additional events that we want
            to process either before the rule is deleted or after
            it is inserted.  PROCESS_QUEUE is similar but assumes
            that only the data will cause propagations and ignores
            included theories.  """
        # Note: all included theories must define MODIFY
        if is_insert:
            text = "Insert"
        else:
            text = "Delete"
        if formula.is_atom():
            self.log(formula.tablename(), "compute/enq: atom {}".format(str(formula)))
            assert not self.is_view(formula.table), \
                "Cannot directly modify tables computed from other tables"
            self.log(formula.table, "{}: {}".format(text, str(formula)))
            for theory in self.includes:
                changes = theory.modify(formula, is_insert=is_insert)
                self.log(formula.table, "Includee {} returned {} ".format(
                    theory.abbr, iterstr(changes)))
                # an atomic change can only produce atomic changes
                for change in changes:
                    self.enqueue(change)
            return []
        else:
            # rules do not need to talk to included theories because they
            #   only generate events for views
            # need to eliminate self-joins here so that we fill all
            #   the tables introduced by self-join elimination.
            for rule in DeltaRuleTheory.eliminate_self_joins([formula]):
                bindings = self.top_down_evaluation(
                    rule.variables(), rule.body)
                self.log(rule.tablename(),
                    "new bindings after top-down: " + iterstr(bindings))
                event = Event(formula=rule, insert=is_insert)
                if is_insert:
                    # insert rule and then process data so that
                    #   we know that data is for a view
                    self.enqueue(event)
                    self.process_new_bindings(bindings, rule.head,
                        is_insert, rule)
                else:
                    # process data and then delete the rule so
                    #   that we know that data is for a view
                    self.process_new_bindings(bindings, rule.head,
                        is_insert, rule)
                    self.enqueue(event)
            return []

    def enqueue(self, event):
        if event.is_insert():
            text = "Adding Insert to queue"
        else:
            text = "Adding Delete to queue"
        self.log(event.tablename(), "{}: {}".format(text, str(event)))
        self.queue.enqueue(event)

    def process_queue(self):
        """ Data and rule propagation routine.
            Returns list of events that were not noops """
        self.log(None, "Processing queue")
        history = []
        while len(self.queue) > 0:
            event = self.queue.dequeue()
            self.log(event.tablename(), "Dequeued " + str(event))
            if isinstance(event.formula, compile.Rule):
                history.extend(self.delta_rules.modify(event.formula,
                    is_insert=event.is_insert()))
            else:
                self.propagate(event)
                # if self.is_view(event.formula.table):
                history.extend(self.database.modify(event.formula,
                    is_insert=event.is_insert(), proofs=event.proofs))
            self.log(event.tablename(), "History: " + iterstr(history))
        return history

    def propagate(self, event):
        """ Computes events generated by EVENT and the DELTA_RULES,
            and enqueues them. """
        self.log(event.formula.table, "Processing event: {}".format(str(event)))
        applicable_rules = self.delta_rules.rules_with_trigger(event.formula.table)
        if len(applicable_rules) == 0:
            self.log(event.formula.table, "No applicable delta rule")
        for delta_rule in applicable_rules:
            self.propagate_rule(event, delta_rule)

    def propagate_rule(self, event, delta_rule):
        """ Compute and enqueue new events generated by EVENT and DELTA_RULE. """
        self.log(event.formula.table, "Processing event {} with rule {}".format(
            str(event), str(delta_rule)))

        # compute tuples generated by event (either for insert or delete)
        # print "event: {}, event.tuple: {}, event.tuple.rawtuple(): {}".format(
        #     str(event), str(event.tuple), str(event.tuple.raw_tuple()))
        # binding_list is dictionary

        # Save binding for delta_rule.trigger; throw away binding for event
        #   since event is ground.
        binding = self.new_bi_unifier()
        assert isinstance(delta_rule.trigger, compile.Atom)
        assert isinstance(event.formula, compile.Atom)
        undo = self.bi_unify(delta_rule.trigger, binding,
                             event.formula, self.new_bi_unifier())
        if undo is None:
            return
        self.log(event.formula.table,
            "binding list for event and delta-rule trigger: {}".format(
                str(binding)))
        bindings = self.top_down_evaluation(
            delta_rule.variables(), delta_rule.body, binding)
        self.log(event.formula.table, "new bindings after top-down: {}".format(
            ",".join([str(x) for x in bindings])))

        if delta_rule.trigger.is_negated():
            insert_delete = not event.insert
        else:
            insert_delete = event.insert
        self.process_new_bindings(bindings, delta_rule.head,
            insert_delete, delta_rule.original)

    def process_new_bindings(self, bindings, atom, insert, original_rule):
        """ For each of BINDINGS, apply to ATOM, and enqueue it as an insert if
            INSERT is True and as a delete otherwise. """
        # for each binding, compute generated tuple and group bindings
        #    by the tuple they generated
        new_atoms = {}
        for binding in bindings:
            new_atom = atom.plug(binding)
            if new_atom not in new_atoms:
                new_atoms[new_atom] = []
            new_atoms[new_atom].append(Database.Proof(
                binding, original_rule))
        self.log(atom.table, "new tuples generated: " + iterstr(new_atoms))

        # enqueue each distinct generated tuple, recording appropriate bindings
        for new_atom in new_atoms:
            # self.log(event.table,
            #     "new_tuple {}: {}".format(str(new_tuple), str(new_tuples[new_tuple])))
            # Only enqueue if new data.
            # Putting the check here is necessary to support recursion.
            self.enqueue(Event(formula=new_atom,
                                proofs=new_atoms[new_atom],
                                insert=insert))

    def is_view(self, x):
        return self.delta_rules.is_view(x)

    def is_known(self, x):
        return self.delta_rules.is_known(x)

    def base_tables(self):
        return self.delta_rules.base_tables()

    def top_down_th(self, context, caller):
        return self.database.top_down_th(context, caller)

    def content(self):
        return self.database.content()

# class MaterializedViewTheory(MaterializedRuleTheory):
#     """ A MaterializedRuleTheory where all tables are views
#        of its included theories. """
#     # Not sure this theory and MaterializedRuleTheory
#     #    should be related via inheritance.
#     #    MaterializedRuleTheory ignores included
#     #    theories on insert/delete.  This theory relies on other theories
#     #    to compute its base tables.  No way for recursive rules to span
#     #    the two theories.
#     # Internally, views/base_tables are defined as usual so that we can
#     #    ignore events other than those for base_tables.
#     # Can only 'include'
#     #   MaterializedViewTheory and MaterializedRuleTheory (for now).
#     def insert(self, formula):
#         """ Insert FORMULA.  Returns True iff the theory changed. """
#         assert (isinstance(formula, compile.Atom) or
#                 isinstance(formula, compile.Rule)), \
#              "Insert requires a formula"
#         return self.modify(formula, is_insert=True)

#     def delete(self, formula):
#         """ Delete FORMULA.  Returns True iff the theory changed. """
#         assert (isinstance(formula, compile.Atom) or
#                 isinstance(formula, compile.Rule)), \
#              "Delete requires a formula"
#         return self.modify(formula, is_insert=False)

#     def modify(self, formula, is_insert=True):
#         """ Modifies contents of theory to insert/delete FORMULA.
#             Returns list of changes to this theory and all included theories. """
#         # send modification down to other theories and get events back
#         changed_rules = set()
#         events = set()
#         for theory in self.includes:
#             changed_rules |= theory.enqueue_events(formula, is_insert=is_insert)
#             events |= theory.process_queue()  # doesn't include noops
#         # enqueue events on my base tables, process them, and return
#         #   the results
#         base_tables = self.base_tables()
#         for event in events:
#             if event.formula.table in base_tables:
#                 self.queue.enqueue(event)
#         local_events = self.process_queue()
#         return changed_rules + (events | local_events)


##############################################################################
## Runtime
##############################################################################

class Runtime (object):
    """ Runtime for the Congress policy language.  Only have one instantiation
        in practice, but using a class is natural and useful for testing. """
    # Names of theories
    CLASSIFY_THEORY = "classification"
    SERVICE_THEORY = "service"
    ACTION_THEORY = "action"
    ENFORCEMENT_THEORY = "enforcement"
    DATABASE = "database"

    def __init__(self):

        # tracer object
        self.tracer = Tracer()
        # record execution
        self.logger = ExecutionLogger()
        # collection of theories
        self.theory = {}
        # Representation of external data
        self.theory[self.DATABASE] = Database(abbr="DB")
        # CLASSIFY_THEORY: the policy
        #  Allow negation for sure.  Currently supports recursion.
        self.theory[self.CLASSIFY_THEORY] = MaterializedViewTheory(abbr='Clas')
        self.theory[self.CLASSIFY_THEORY].includes.append(
            self.theory[self.DATABASE])
        # ENFORCEMENT_THEORY: describes what actions to take and when.
        #  An extension of the classification theory.
        self.theory[self.ENFORCEMENT_THEORY] = MaterializedViewTheory(
            abbr='Enfor')
        self.theory[self.ENFORCEMENT_THEORY].includes.append(
            self.theory[self.CLASSIFY_THEORY])

        # ACTION_THEORY: describes how actions affect tables.
        #  non-recursive, with semi-positive negation over base tables
        #    of CLASSIFY_THEORY, i.e. no negation over views of
        #    either ACTION_THEORY or CLASSIFY_THEORY.
        #    (Why?: Using top-down eval, hence no recursion, and abduction
        #    saves no literal under a negated literal.)
        #  The +/- atoms (a) should only appear in the head and (b)
        #    should only be for the basetables of CLASSIFY_THEORY, i.e.
        #    no action should change a table that exists only in the policy.
        #  Can reference tables defined in CLASSIFY_THEORY in the body
        #    of rules or any other tables defined in ACTION_THEORY.
        #    Should throw warning if referencing table not appearing
        #    in either and provide special table False.
        self.theory[self.ACTION_THEORY] = NonrecursiveRuleTheory(abbr='Act')
        self.theory[self.ACTION_THEORY].includes.append(
            self.theory[self.CLASSIFY_THEORY])
        # SERVICE_THEORY: describes bindings for tables to real-world
        #    software.
        #  Need bindings for every table in DATABASE
        #   and every action in ACTION_THEORY.
        self.theory[self.SERVICE_THEORY] = NonrecursiveRuleTheory(abbr='Serv')

    def get_target(self, name):
        if name is None:
            name = self.CLASSIFY_THEORY
        assert name in self.theory, "Unknown target {}".format(name)
        return self.theory[name]

    def get_action_names(self):
        """ Return a list of the names of action tables. """
        actionth = self.theory[self.ACTION_THEORY]
        actions = actionth.select(compile.parse1('action(x)'))
        return [action.arguments[0].name for action in actions]

    def log(self, table, msg, depth=0):
        self.tracer.log(table, "  RT: " + msg, depth)

    def set_tracer(self, tracer):
        self.tracer = tracer
        for th in self.theory:
            self.theory[th].set_tracer(tracer)

    def debug_mode(self):
        tracer = Tracer()
        tracer.trace('*')
        self.set_tracer(tracer)

    def production_mode(self):
        tracer = Tracer()
        self.set_tracer(tracer)

    ############### External interface ###############
    def load_file(self, filename, target=None):
        """ Compile the given FILENAME and insert each of the statements
            into the runtime. """
        for formula in compile.parse_file(filename):
            self.insert(formula, target=target)

    def select(self, query, target=None):
        """ Event handler for arbitrary queries. Returns the set of
            all instantiated QUERY that are true. """
        if isinstance(query, basestring):
            return self.select_string(query, self.get_target(target))
        elif isinstance(query, tuple):
            return self.select_tuple(query, self.get_target(target))
        else:
            return self.select_obj(query, self.get_target(target))

    def explain(self, query, tablenames=None, find_all=False, target=None):
        """ Event handler for explanations.  Given a ground query and
            a collection of tablenames that we want the explanation in
            terms of, return proof(s) that the query is true. If
            FIND_ALL is True, returns list; otherwise, returns single proof."""
        if isinstance(query, basestring):
            return self.explain_string(
                query, tablenames, find_all, self.get_target(target))
        elif isinstance(query, tuple):
            return self.explain_tuple(
                query, tablenames, find_all, self.get_target(target))
        else:
            return self.explain_obj(
                query, tablenames, find_all, self.get_target(target))

    def insert(self, formula, target=None):
        """ Event handler for arbitrary insertion (rules and facts). """
        if isinstance(formula, basestring):
            return self.insert_string(formula, self.get_target(target))
        elif isinstance(formula, tuple):
            return self.insert_tuple(formula, self.get_target(target))
        else:
            return self.insert_obj(formula, self.get_target(target))

    def delete(self, formula, target=None):
        """ Event handler for arbitrary deletion (rules and facts). """
        if isinstance(formula, basestring):
            return self.delete_string(formula, self.get_target(target))
        elif isinstance(formula, tuple):
            return self.delete_tuple(formula, self.get_target(target))
        else:
            return self.delete_obj(formula, self.get_target(target))

    def remediate(self, formula):
        """ Event handler for remediation. """
        if isinstance(formula, basestring):
            return self.remediate_string(formula)
        elif isinstance(formula, tuple):
            return self.remediate_tuple(formula)
        else:
            return self.remediate_obj(formula)

    def simulate(self, query, sequence):
        """ Event handler for simulation: the computation of a query given an
            action sequence.  That sequence can include updates to atoms,
            updates to rules, and action invocations.
            Example atom update: q+(1) or q-(1)
            Example rule update: p+(x) :- q(x) or p-(x) :- q(x)
            Example action invocation:
               create_network(17), options:value(17, "name", "net1") :- true
        """
        if isinstance(query, basestring) and isinstance(sequence, basestring):
            return self.simulate_string(query, sequence)
        else:
            return self.simulate_obj(query, sequence)

    def execute(self, action_sequence):
        """ Event handler for execute: execute a sequence of ground actions
            in the real world. """
        if isinstance(action_sequence, basestring):
            return self.execute_string(action_sequence)
        else:
            return self.execute_obj(action_sequence)

    ############### Internal interface ###############
    ## Translate different representations of formulas into
    ##   the compiler's internal representation and then invoke
    ##   appropriate theory's version of the API.
    ## Arguments that are strings are suffixed with _string.
    ## All other arguments are instances of Theory, Atom, etc.

    ###################################
    # Update (internal or external) state

    # insert
    def insert_string(self, policy_string, theory):
        policy = compile.parse(policy_string)
        # TODO: send entire parsed theory so that e.g. self-join elim
        #    is more efficient.
        for formula in policy:
            #logging.debug("Parsed {}".format(str(formula)))
            self.insert_obj(formula, theory)

    def insert_tuple(self, tuple, theory):
        self.insert_obj(compile.Atom.create_from_iter(tuple), theory)

    def insert_obj(self, formula, theory):
        # reroute a data insert into classify theory as
        #   a data insert into enforcement theory.
        # Enforcement theory passes that insert into classify_theory.
        theory = self.compute_route(formula, theory, "insert")
        changes = theory.insert(formula)
        self.react_to_changes(changes)
        return changes

    # delete
    def delete_string(self, policy_string, theory):
        policy = compile.parse(policy_string)
        for formula in policy:
            self.delete_obj(formula, theory)

    def delete_tuple(self, tuple, theory):
        self.delete_obj(compile.Atom.create_from_iter(tuple), theory)

    def delete_obj(self, formula, theory):
        theory = self.compute_route(formula, theory, "delete")
        changes = theory.delete(formula)
        self.react_to_changes(changes)
        return changes

    # execute
    def execute_string(self, actions_string):
        self.execute_obj(compile.parse(actions_string))

    def execute_obj(self, actions):
        """ Executes the list of ACTION instances one at a time.
            For now, our execution is just logging. """
        logging.debug("Executing: " + iterstr(actions))
        assert all(isinstance(action, compile.Atom) and action.is_ground()
                    for action in actions)
        action_names = self.get_action_names()
        assert all(action.table in action_names for action in actions)
        for action in actions:
            if not action.is_ground():
                if self.logger is not None:
                    self.logger.warn("Unground action to execute: {}".format(
                        str(action)))
                continue
            if self.logger is not None:
                self.logger.info(str(action))

    ##########################
    # Analyze (internal) state

    # select
    def select_string(self, policy_string, theory):
        policy = compile.parse(policy_string)
        assert len(policy) == 1, \
                "Queries can have only 1 statement: {}".format(
                    [str(x) for x in policy])
        results = self.select_obj(policy[0], theory)
        return compile.formulas_to_string(results)

    def select_tuple(self, tuple, theory):
        return self.select_obj(compile.Atom.create_from_iter(tuple), theory)

    def select_obj(self, query, theory):
        return theory.select(query)

    # explain
    def explain_string(self, query_string, tablenames, find_all, theory):
        policy = compile.parse(query_string)
        assert len(policy) == 1, "Queries can have only 1 statement"
        results = self.explain_obj(policy[0], tablenames, find_all, theory)
        return compile.formulas_to_string(results)

    def explain_tuple(self, tuple, tablenames, find_all, theory):
        self.explain_obj(compile.Atom.create_from_iter(tuple),
            tablenames, find_all, theory)

    def explain_obj(self, query, tablenames, find_all, theory):
        return theory.explain(query, tablenames, find_all)

    # remediate
    def remediate_string(self, policy_string):
        policy = compile.parse(policy_string)
        assert len(policy) == 1, "Queries can have only 1 statement"
        return compile.formulas_to_string(self.remediate_obj(policy[0]))

    def remediate_tuple(self, tuple, theory):
        self.remediate_obj(compile.Atom.create_from_iter(tuple))

    def remediate_obj(self, formula):
        """ Find a collection of action invocations that if executed
        result in FORMULA becoming false. """
        actionth = self.theory[self.ACTION_THEORY]
        classifyth = self.theory[self.CLASSIFY_THEORY]
        # look at FORMULA
        if isinstance(formula, compile.Atom):
            output = formula
        elif isinstance(formula, compile.Rule):
            output = formula.head
        else:
            assert False, "Must be a formula"
        # grab a single proof of FORMULA in terms of the base tables
        base_tables = classifyth.base_tables()
        proofs = classifyth.explain(formula, base_tables, False)
        if proofs is None:  # FORMULA already false; nothing to be done
            return []
        # Extract base table literals that make that proof true.
        #   For remediation, we assume it suffices to make any of those false.
        #   (Leaves of proof may not be literals or may not be written in
        #    terms of base tables, despite us asking for base tables--
        #    because of negation.)
        leaves = [leaf for leaf in proofs[0].leaves()
                    if (isinstance(leaf, compile.Atom) and
                        leaf.table in base_tables)]
        self.log(None, "Leaves: {}".format(iterstr(leaves)))
        # Query action theory for abductions of negated base tables
        actions = self.get_action_names()
        results = []
        for lit in leaves:
            goal = lit.make_positive()
            if lit.is_negated():
                goal.table = goal.table + "+"
            else:
                goal.table = goal.table + "-"
            # return is a list of goal :- act1, act2, ...
            # This is more informative than query :- act1, act2, ...
            for abduction in actionth.abduce(goal, actions, False):
                results.append(abduction)
        return results

    # simulate
    def simulate_string(self, query, sequence):
        query = compile.parse1(query)
        sequence = compile.parse(sequence)
        result = self.simulate_obj(query, sequence)
        return compile.formulas_to_string(result)


    def simulate_obj(self, query, sequence):
        assert (isinstance(query, compile.Rule) or
                isinstance(query, compile.Atom)), "Query must be formula"
        # Each action is represented as a rule with the actual action
        #    in the head and its supporting data (e.g. options) in the body
        assert all(isinstance(x, compile.Rule) or isinstance(x, compile.Atom)
                    for x in sequence), "Sequence must be an iterable of Rules"
        # apply SEQUENCE
        self.log(query.tablename(), "** Simulate: Applying sequence {}".format(
            iterstr(sequence)))
        undo = self.project(sequence)

        # query the resulting state
        self.log(query.tablename(), "** Simulate: Querying {}".format(
            str(query)))
        result = self.theory[self.CLASSIFY_THEORY].select(query)
        self.log(query.tablename(), "Result of {} is {}".format(
            str(query), iterstr(result)))
        # rollback the changes
        self.log(query.tablename(), "** Simulate: Rolling back")
        self.project(undo)
        return result

    ############### Helpers ###############

    def react_to_changes(self, changes):
        """ Filters changes and executes actions contained therein. """
        # logging.debug("react to: " + iterstr(changes))
        actions = self.get_action_names()
        formulas = [change.formula for change in changes
                        if (isinstance(change, Event)
                            and change.is_insert()
                            and change.formula.is_atom()
                            and change.tablename() in actions)]
        # logging.debug("going to execute: " + iterstr(formulas))
        self.execute(formulas)

    def compute_route(self, formula, theory, operation):
        """ When a formula is inserted/deleted (in OPERATION) into a THEORY,
            it may need to be rerouted to another theory.  This function
            computes that rerouting.  Returns a Theory object. """
        # Since Enforcement includes Classify and Classify includes Database,
        #   any operation on data needs to be funneled into Enforcement.
        #   Enforcement pushes it down to the others and then
        #   reacts to the results.  That is, we really have one big theory
        #   Enforcement + Classify + Database as far as the data is concerned
        #   but formulas can be inserted/deleted into each policy individually.
        if isinstance(formula, compile.Atom):
            if (theory is self.theory[self.CLASSIFY_THEORY] or
                theory is self.theory[self.DATABASE]):
                return self.theory[self.ENFORCEMENT_THEORY]
        return theory

    def project(self, sequence):
        """ Apply the list of updates SEQUENCE to the classification theory.
            Return an update sequence that will undo the projection.

            SEQUENCE can include atom insert/deletes, rule insert/deletes,
            and action invocations.  Projecting an action only
            simulates that action's invocation using the action's description;
            the results are therefore only an approximation of executing
            actions directly.

            SEQUENCE is really a program in a mini-programming
            language--enabling results of one action to be passed to another.
            Hence, even ignoring actions, this functionality cannot be achieved
            by simply inserting/deleting. """
        actth = self.theory[self.ACTION_THEORY]
        # apply changes to the state
        newth = NonrecursiveRuleTheory(abbr="Temp")
        newth.tracer.trace('*')
        actth.includes.append(newth)
        actions = self.get_action_names()
        self.log(None, "Actions: " + iterstr(actions))
        undos = []         # a list of updates that will undo SEQUENCE
        self.log(None, "Project: " + iterstr(sequence))
        last_results = []
        for formula in sequence:
            self.log(None, "** Updating with {}".format(str(formula)))
            self.log(None, "Actions: " + iterstr(actions))
            self.log(None, "Last_results: " + iterstr(last_results))
            tablename = formula.tablename()
            if tablename not in actions:
                updates = [formula]
            else:
                self.log(tablename, "Projecting " + str(formula))
                # define extension of current Actions theory
                if formula.is_atom():
                    assert formula.is_ground(), \
                        "Projection atomic updates must be ground"
                    assert not formula.is_negated(), \
                        "Projection atomic updates must be positive"
                    newth.define([formula])
                else:
                    # instantiate action using prior results
                    newth.define(last_results)
                    self.log(tablename, "newth (with prior results) {} ".format(
                        iterstr(newth.content())))
                    bindings = actth.top_down_evaluation(formula.variables(),
                        formula.body, find_all=False)
                    if len(bindings) == 0:
                        continue
                    grounds = formula.plug_heads(bindings[0])
                    grounds = [act for act in grounds if
                                act.is_ground()]
                    assert all(not lit.is_negated() for lit in grounds)
                    newth.define(grounds)
                self.log(tablename, "newth contents (after action insertion): {}".format(
                    iterstr(newth.content())))
                # self.log(tablename, "action contents: {}".format(
                #     iterstr(actth.content())))
                # self.log(tablename, "action.includes[1] contents: {}".format(
                #     iterstr(actth.includes[1].content())))
                # self.log(tablename, "newth contents: {}".format(
                #     iterstr(newth.content())))
                # compute updates caused by action
                updates = actth.consequences(compile.is_update)
                updates = self.resolve_conflicts(updates)
                updates = unify.skolemize(updates)
                self.log(tablename, "Computed updates: " + iterstr(updates))
                # compute results for next time
                for update in updates:
                    newth.insert(update)
                last_results = actth.consequences(compile.is_result)
                last_results = set([atom for atom in last_results
                                         if atom.is_ground()])
            # apply updates
            for update in updates:
                undo = self.update_classifier(update)
                if undo is not None:
                    undos.append(undo)
        undos.reverse()
        actth.includes.remove(newth)
        return undos

    def update_classifier(self, delta):
        """ Takes an atom/rule DELTA with update head table
            (i.e. ending in + or -) and inserts/deletes, respectively,
            that atom/rule into CLASSIFY_THEORY after stripping
            the +/-. Returns None if DELTA had no effect on the
            current state or an atom/rule that when given to
            UPDATE_CLASSIFIER will produce the original state. """
        self.log(None, "Applying update {}".format(str(delta)))
        clsth = self.theory[self.CLASSIFY_THEORY]
        isinsert = delta.tablename().endswith('+')
        newdelta = delta.drop_update()
        if isinsert:
            changed = clsth.insert(newdelta)
        else:
            changed = clsth.delete(newdelta)
        if changed:
            return delta.invert_update()
        else:
            return None

    def resolve_conflicts(self, atoms):
        """ If p+(args) and p-(args) are present, removes the p-(args). """
        neg = set()
        result = set()
        # split atoms into NEG and RESULT
        for atom in atoms:
            if atom.table.endswith('+'):
                result.add(atom)
            elif atom.table.endswith('-'):
                neg.add(atom)
            else:
                result.add(atom)
        # add elems from NEG only if their inverted version not in RESULT
        for atom in neg:
            if atom.invert_update() not in result:  # slow: copying ATOM here
                result.add(atom)
        return result

