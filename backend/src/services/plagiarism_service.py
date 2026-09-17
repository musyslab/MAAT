"""Install as backend/src/services/plagiarism_service.py (Python 3.10+).

Static screening only: student code is NEVER executed or imported. Scores are
heuristic similarity percentages, not probabilities of academic misconduct.
Token 40%, AST 40%, approximate dependency/control structure 20%. Missing AST
layers are excluded and remaining weights renormalized. Common cohort features
are downweighted; optional instructor starter code is excluded from scoring.
The dependency layer is conservative and intraprocedural, not semantic proof.
No new third-party dependencies or database migrations are required.
"""
from __future__ import annotations

import ast
import builtins
import hashlib
import io
import keyword
import math
import os
import time
import tokenize
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

MAX_SUBMISSIONS = 150
MAX_FILES = 12
MAX_BYTES = 128_000
MAX_TOKENS = 8_000
MAX_NODES = 8_000
MAX_DEPTH = 100
MAX_TOTAL_BYTES = 4_000_000
MAX_SECONDS = 25
GRAM = 7
WEIGHTS = {'tokens': .4, 'ast': .4, 'dependency': .2}
BUILTINS = frozenset(dir(builtins))
NOTICE = ('Screening evidence only. Similarity is not proof of plagiarism or a '
          'probability of misconduct. Review assignment constraints, starter code, '
          'short solutions, and each student’s explanation before deciding.')


class AnalysisLimit(ValueError):
    pass


def digest(value):
    return hashlib.blake2b(repr(value).encode('utf-8'), digest_size=16).hexdigest()


def read_python_files(code_path, allowed_root):
    """Bounded reads, no symlinks; paths originate in authorized database rows."""
    raw = Path(code_path or '')
    root = Path(allowed_root).resolve()
    if not code_path or raw.is_symlink():
        raise ValueError('Missing or unsafe source path.')
    path = raw.resolve()
    if not path.is_relative_to(root):
        raise ValueError('Source path is outside the configured student directory.')
    candidates = []
    if path.is_file():
        if path.suffix.lower() == '.py':
            candidates = [path]
    elif path.is_dir():
        visited = 0
        for directory, dirs, names in os.walk(path, followlinks=False):
            visited += 1
            if visited > 1000:
                raise AnalysisLimit('Too many directories in submission.')
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and
                             d != '__pycache__' and not (Path(directory) / d).is_symlink())
            for name in sorted(names):
                visited += 1
                if visited > 1000:
                    raise AnalysisLimit('Too many files in submission.')
                entry = Path(directory) / name
                if entry.suffix.lower() == '.py':
                    if entry.is_symlink() or not entry.is_file() or not entry.resolve().is_relative_to(root):
                        raise ValueError('Unsafe Python source path.')
                    candidates.append(entry)
                    if len(candidates) > MAX_FILES:
                        raise AnalysisLimit(f'More than {MAX_FILES} Python files.')
    if not candidates:
        raise ValueError('No Python source files found.')
    result, used = [], 0
    for entry in sorted(candidates):
        with entry.open('rb') as stream:
            data = stream.read(MAX_BYTES - used + 1)
        used += len(data)
        if used > MAX_BYTES:
            raise AnalysisLimit(f'Submission exceeds {MAX_BYTES} source bytes.')
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        text = data.decode(encoding)
        result.append({'name': entry.name if path.is_file() else entry.relative_to(path).as_posix(),
                       'content': text})
    return result


class Bindings(ast.NodeVisitor):
    """Collect one lexical scope, including bindings that precede their use."""
    def __init__(self):
        self.names = []
        self.external = set()

    def add(self, name):
        if name and name not in self.names:
            self.names.append(name)

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.add(node.id)

    def visit_arg(self, node):
        self.add(node.arg)

    def visit_FunctionDef(self, node):
        self.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Lambda(self, node):
        pass

    def visit_Global(self, node):
        self.external.update(node.names)

    visit_Nonlocal = visit_Global

    def visit_Import(self, node):
        for item in node.names:
            self.add(item.asname or item.name.split('.')[0])

    visit_ImportFrom = visit_Import

    def visit_ExceptHandler(self, node):
        self.add(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node):
        self.add(node.name)
        self.generic_visit(node)

    visit_MatchStar = visit_MatchAs

    def visit_MatchMapping(self, node):
        self.add(node.rest)
        self.generic_visit(node)


class Canonical(ast.NodeTransformer):
    """Lexical binding normalization; keeps builtins, attributes and constants.

    Comprehension bindings are conservatively included in their enclosing
    analysis scope. This approximation is reported in the methodology.
    """
    def __init__(self, rename):
        self.rename = rename
        self.scopes = []

    def name(self, value):
        if not self.rename:
            return value
        for depth, scope in enumerate(reversed(self.scopes)):
            if value in scope:
                return f'v{depth}_{scope[value]}'
        return value

    def scope(self, node, body, arguments=None):
        bindings = Bindings()
        if arguments:
            bindings.visit(arguments)
        for item in body:
            bindings.visit(item)
        names = [n for n in bindings.names if n not in bindings.external]
        self.scopes.append({n: i for i, n in enumerate(names)})
        result = self.generic_visit(node)
        self.scopes.pop()
        return result

    def visit_Module(self, node):
        return self.scope(node, node.body)

    def visit_FunctionDef(self, node):
        node.name = self.name(node.name)
        # Defaults and decorators execute in the enclosing scope.
        node.decorator_list = [self.visit(x) for x in node.decorator_list]
        node.args.defaults = [self.visit(x) for x in node.args.defaults]
        node.args.kw_defaults = [self.visit(x) if x else None for x in node.args.kw_defaults]
        defaults, kwdefaults, decorators = node.args.defaults, node.args.kw_defaults, node.decorator_list
        node.args.defaults, node.args.kw_defaults, node.decorator_list = [], [], []
        result = self.scope(node, node.body, node.args)
        result.args.defaults, result.args.kw_defaults, result.decorator_list = defaults, kwdefaults, decorators
        return result

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        node.name = self.name(node.name)
        node.bases = [self.visit(x) for x in node.bases]
        bases = node.bases
        node.bases = []
        result = self.scope(node, node.body)
        result.bases = bases
        return result

    def visit_Lambda(self, node):
        return self.scope(node, [node.body], node.args)

    def visit_Name(self, node):
        node.id = self.name(node.id)
        return node

    def visit_arg(self, node):
        node.arg = self.name(node.arg)
        return self.generic_visit(node)

    def visit_alias(self, node):
        node.asname = self.name(node.asname or node.name.split('.')[0])
        return node

    def visit_Global(self, node):
        node.names = [self.name(n) for n in node.names]
        return node

    visit_Nonlocal = visit_Global

    def visit_ExceptHandler(self, node):
        node.name = self.name(node.name) if node.name else None
        return self.generic_visit(node)

    def visit_MatchAs(self, node):
        node.name = self.name(node.name) if node.name else None
        return self.generic_visit(node)

    visit_MatchStar = visit_MatchAs

    def visit_MatchMapping(self, node):
        node.rest = self.name(node.rest) if node.rest else None
        return self.generic_visit(node)

    def visit_Expr(self, node):
        # Ignore standalone string literals/docstrings, but retain prompt strings.
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return None
        return self.generic_visit(node)


def checked_tree(source):
    tree = ast.parse(source)
    stack, count = [(tree, 0)], 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise AnalysisLimit('Python syntax tree exceeds analysis limits.')
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return tree


def ast_features(tree):
    features, regions = Counter(), defaultdict(list)

    def visit(node):
        parts, size = [type(node).__name__], 1
        for key, value in ast.iter_fields(node):
            if isinstance(value, ast.AST):
                value, n = visit(value)
                size += n
            elif isinstance(value, list):
                items = []
                for child in value:
                    if isinstance(child, ast.AST):
                        child, n = visit(child)
                        size += n
                    items.append(child)
                value = items
                if key in ('body', 'orelse', 'finalbody'):
                    for a, b in zip(items, items[1:]):
                        features[digest(('sequence', a, b))] += 1
            parts.append((key, value))
        signature = digest(parts)
        if size >= 4:
            features[signature] += 1
            if isinstance(node, ast.stmt) and size >= 8:
                regions[signature].append((node.lineno, node.end_lineno))
        return signature, size
    visit(tree)
    return features, regions


def dependency_features(tree):
    """Statement shapes + control containment + conservative reaching-def edges.

    Branches/loops merge possible definitions; no interprocedural analysis,
    alias analysis or equivalence claim. Independent statement reorderings
    preserve the graph multiset. Constants are abstracted only in this layer.
    """
    features = Counter()

    def shape(node):
        if isinstance(node, ast.Name):
            return ('Name', node.id if node.id in BUILTINS else 'id')
        if isinstance(node, ast.Constant):
            return ('Constant', type(node.value).__name__)
        if isinstance(node, ast.AST):
            return (type(node).__name__, tuple(
                (key, shape(value)) for key, value in ast.iter_fields(node)
                if key not in ('body', 'orelse', 'finalbody', 'handlers', 'name', 'arg')))
        if isinstance(node, list):
            return tuple(shape(x) for x in node)
        return node

    def names(node):
        loads, stores = set(), set()
        stack = [node]
        while stack:
            item = stack.pop()
            if isinstance(item, ast.Name):
                (loads if isinstance(item.ctx, ast.Load) else stores).add(item.id)
            for key, value in ast.iter_fields(item):
                if key in ('body', 'orelse', 'finalbody', 'handlers'):
                    continue
                if isinstance(value, ast.AST):
                    stack.append(value)
                elif isinstance(value, list):
                    stack.extend(x for x in value if isinstance(x, ast.AST))
        return loads, stores

    def block(statements, definitions, parent=None, branch='body'):
        definitions = {key: set(value) for key, value in definitions.items()}
        for node in statements:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue
            label = digest(shape(node))
            features[('node', label)] += 1
            if parent:
                features[('control', parent, branch, label)] += 1
            loads, stores = names(node)
            for name in loads:
                for origin in definitions.get(name, ()):
                    features[('data', origin, label)] += 1
            for name in stores:
                definitions[name] = {label}
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                block(node.body, {}, label)
                continue
            # Union over alternatives, retaining incoming defs conservatively.
            for key in ('body', 'orelse', 'finalbody'):
                children = getattr(node, key, [])
                if children:
                    outgoing = block(children, definitions, label, key)
                    for name, origins in outgoing.items():
                        definitions.setdefault(name, set()).update(origins)
            for handler in getattr(node, 'handlers', []):
                outgoing = block(handler.body, definitions, label, 'except')
                for name, origins in outgoing.items():
                    definitions.setdefault(name, set()).update(origins)
            for case in getattr(node, 'cases', []):
                outgoing = block(case.body, definitions, label, 'case')
                for name, origins in outgoing.items():
                    definitions.setdefault(name, set()).update(origins)
        return definitions
    block(tree.body, {})
    return features


@dataclass
class Prepared:
    public: dict
    bags: dict = field(default_factory=lambda: {key: Counter() for key in WEIGHTS})
    locations: dict = field(default_factory=lambda: {key: defaultdict(list) for key in ('tokens', 'ast')})
    token_count: int = 0
    ast_ok: bool = True


def prepare_submission(public, rename=True):
    item = Prepared(public)
    warnings = public.setdefault('warnings', [])
    for file_index, file in enumerate(public['files']):
        source = file['content']
        tokens, positions, tree = [], [], None
        line_count = max(1, len(source.splitlines()))
        ignored_lines = set()
        try:
            tree = checked_tree(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    ignored_lines.update(range(node.lineno, node.end_lineno + 1))
        except (SyntaxError, ValueError, RecursionError) as exc:
            if isinstance(exc, AnalysisLimit):
                raise
            item.ast_ok = False
            warnings.append(f"{file['name']}: AST/dependency unavailable ({type(exc).__name__}); token evidence only for this submission.")
        try:
            previous = ''
            for token in tokenize.generate_tokens(io.StringIO(source).readline):
                kind, value, start, end, _ = token
                if kind in (tokenize.COMMENT, tokenize.NL, tokenize.ENCODING, tokenize.ENDMARKER):
                    continue
                if kind in (tokenize.STRING, tokenize.NEWLINE) and start[0] in ignored_lines:
                    continue
                if kind == tokenize.NAME and rename and not keyword.iskeyword(value) and value not in BUILTINS and previous != '.':
                    value = 'IDENTIFIER'
                if kind in (tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE):
                    value = tokenize.tok_name[kind]
                tokens.append((kind, value))
                positions.append((min(start[0], line_count), min(end[0], line_count)))
                previous = token.string
                if item.token_count + len(tokens) > MAX_TOKENS:
                    raise AnalysisLimit(f'More than {MAX_TOKENS} tokens.')
        except (tokenize.TokenError, IndentationError, SyntaxError):
            warnings.append(f"{file['name']}: incomplete tokenization; evidence covers only the readable prefix.")
        item.token_count += len(tokens)
        for index in range(max(0, len(tokens) - GRAM + 1)):
            key = digest(tokens[index:index + GRAM])
            item.bags['tokens'][key] += 1
            item.locations['tokens'][key].append((file_index, positions[index][0], positions[index + GRAM - 1][1]))
        if tree is not None:
            # Use original identifiers for data dependencies, canonical tree for AST.
            item.bags['dependency'].update(dependency_features(tree))
            normalized = Canonical(rename).visit(tree)
            features, regions = ast_features(normalized)
            item.bags['ast'].update(features)
            for key, ranges in regions.items():
                item.locations['ast'][key].extend((file_index, a, b) for a, b in ranges)
    if not item.ast_ok:
        item.bags['ast'].clear()
        item.bags['dependency'].clear()
        item.locations['ast'].clear()
    if item.token_count < 50:
        warnings.append('Very short program: common syntax can produce high similarity with little evidence.')
    public['token_count'] = item.token_count
    public['ast_available'] = item.ast_ok
    return item


def weighted_dice(left, right, rarity):
    denominator = sum(n * rarity.get(k, 1) for k, n in left.items()) + sum(n * rarity.get(k, 1) for k, n in right.items())
    if not denominator:
        return None
    intersection = sum(min(n, right.get(k, 0)) * rarity.get(k, 1) for k, n in left.items())
    return 100 * 2 * intersection / denominator


def matched_regions(left, right, limit=120):
    matches, seen = [], set()
    for layer in ('ast', 'tokens'):
        shared = left.bags[layer].keys() & right.bags[layer].keys()
        candidates = []
        for key in sorted(shared):
            # Pair occurrences one-to-one; never create a quadratic cross product.
            for a, b in zip(left.locations[layer][key], right.locations[layer][key]):
                candidates.append((-(a[2] - a[1] + b[2] - b[1]), a, b))
        for _, a, b in sorted(candidates):
            pair = (a, b)
            if pair in seen:
                continue
            seen.add(pair)
            if any(m['left']['file'] == a[0] and m['right']['file'] == b[0]
                   and m['left']['start'] <= a[1] <= a[2] <= m['left']['end']
                   and m['right']['start'] <= b[1] <= b[2] <= m['right']['end'] for m in matches):
                continue
            matches.append({'kind': layer, 'left': {'file': a[0], 'start': a[1], 'end': a[2]},
                            'right': {'file': b[0], 'start': b[1], 'end': b[2]}})
            if len(matches) == limit:
                return matches, True
    return matches, False


def analyze_submissions(submissions, *, rename=True, starter_code='', top_limit=100):
    """Input: authorized snapshots {id, user_id, name, files:[{name,content}]}.

    Returns source once per submission plus top pairs and line-based evidence.
    Excludes same-user pairs even if the caller supplies multiple versions.
    All limits fail explicitly; reports never silently truncate the cohort.
    """
    started = time.monotonic()
    if len(submissions) > MAX_SUBMISSIONS:
        raise AnalysisLimit(f'At most {MAX_SUBMISSIONS} submitted students per run.')
    if sum(len(f['content'].encode('utf-8')) for s in submissions for f in s['files']) > MAX_TOTAL_BYTES:
        raise AnalysisLimit('Cohort exceeds 4 MB of Python source.')
    if len(starter_code.encode('utf-8')) > MAX_BYTES:
        raise AnalysisLimit('Starter code is too large.')
    def deadline():
        if time.monotonic() - started > MAX_SECONDS:
            raise AnalysisLimit('Analysis exceeded its 25-second budget; use a smaller cohort.')
    prepared, skipped = [], []
    for submission in submissions:
        deadline()
        try:
            item = prepare_submission(submission, rename)
            if not item.bags['tokens'] and not item.bags['ast']:
                raise ValueError('No analyzable Python content.')
            prepared.append(item)
        except (ValueError, RecursionError) as exc:
            skipped.append({'id': submission['id'], 'name': submission['name'], 'reason': str(exc)})
    baseline = prepare_submission({'files': [{'name': 'starter.py', 'content': starter_code}]}, rename) if starter_code.strip() else None
    if baseline and not baseline.ast_ok:
        raise ValueError('Starter code must be valid Python.')
    rarity = {}
    for layer in WEIGHTS:
        frequency = Counter()
        for item in prepared:
            if baseline:
                # Exclude all occurrences of provided boilerplate features.
                for key in baseline.bags[layer]:
                    item.bags[layer].pop(key, None)
            frequency.update(item.bags[layer].keys())
        rarity[layer] = {key: .2 + math.log((len(prepared) + 1) / (count + 1))
                         for key, count in frequency.items()}
    results = []
    for left, right in combinations(prepared, 2):
        deadline()
        if left.public['user_id'] == right.public['user_id']:
            continue
        scores = {}
        for layer in WEIGHTS:
            value = None
            if layer == 'tokens' or (left.ast_ok and right.ast_ok):
                value = weighted_dice(left.bags[layer], right.bags[layer], rarity[layer])
            scores[layer] = round(value, 1) if value is not None else None
        available = {k: w for k, w in WEIGHTS.items() if scores[k] is not None}
        overall = sum(scores[k] * w for k, w in available.items()) / sum(available.values()) if available else None
        results.append({'left_id': left.public['id'], 'right_id': right.public['id'],
                        'scores': scores, 'overall': round(overall, 1) if overall is not None else None,
                        'evidence': 'limited' if min(left.token_count, right.token_count) < 50 or not (left.ast_ok and right.ast_ok) else 'standard'})
    results.sort(key=lambda p: (-(p['overall'] if p['overall'] is not None else -1), p['left_id'], p['right_id']))
    count = len(results)
    results = results[:max(1, min(top_limit, 500))]
    by_id = {item.public['id']: item for item in prepared}
    for pair in results:
        deadline()
        pair['matches'], pair['matches_truncated'] = matched_regions(by_id[pair['left_id']], by_id[pair['right_id']])
    return {'version': 'python-screening-1', 'notice': NOTICE, 'weights': WEIGHTS,
            'methodology': 'Normalized token 7-grams, lexical AST subtrees/statement sequences, and approximate intraprocedural dependency/control features. Comprehension bindings are approximated in the enclosing scope; dynamic Python behavior is not modeled. Common cohort features are downweighted. Unavailable layers are omitted from the weighted score.',
            'rename_identifiers': rename, 'starter_excluded': bool(baseline),
            'submitted_count': len(submissions), 'analyzed_count': len(prepared),
            'pair_count': count, 'returned_count': len(results),
            'submissions': [item.public for item in prepared], 'pairs': results,
            'skipped': skipped, 'elapsed_seconds': round(time.monotonic() - started, 2)}
