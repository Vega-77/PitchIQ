"""Baseline code-clarity metrics for PitchIQ. Run from the repo root: python tools/audit_metrics.py"""

import ast, collections, hashlib, re, subprocess
from pathlib import Path

FILES = subprocess.check_output(['git', 'ls-files', '*.py']).decode().split()
CORE = [f for f in FILES if re.fullmatch(r'cv/[a-z_0-9]+\.py', f)]
BALLTRACK = [f for f in FILES if f.startswith('cv/balltrack/')]
KEEP_ALIASES = {'np', 'pd', 'mp', 'plt', 'cv2'}


def parse(path):
    return ast.parse(Path(path).read_text(encoding='utf-8', errors='ignore'))


def doc_lines(tree):
    nodes = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    return sum(
        len(d.split('\n'))
        for n in ast.walk(tree)
        if isinstance(n, nodes) and (d := ast.get_docstring(n, clean=False))
    )


def non_code_share(path):
    text = Path(path).read_text(encoding='utf-8', errors='ignore').split('\n')
    comments = sum(1 for line in text if line.strip().startswith('#'))
    return (doc_lines(parse(path)) + comments) / max(len(text), 1)


def functions(paths):
    for path in paths:
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.FunctionDef):
                yield path, node


def short_aliases(paths):
    found = collections.Counter()
    for path in paths:
        for node in ast.walk(parse(path)):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.asname and len(alias.asname) < 3 and alias.asname not in KEEP_ALIASES:
                        found[path] += 1
    return found


def duplicate_bodies(paths, min_lines=5):
    seen = collections.defaultdict(list)
    for path, node in functions(paths):
        if node.end_lineno - node.lineno + 1 < min_lines or len(node.body) < 3:
            continue
        body = ast.Module(body=node.body, type_ignores=[])
        if ast.get_docstring(node):
            body.body = node.body[1:]
        names = {n.id: '_' for n in ast.walk(body) if isinstance(n, ast.Name)}
        key = re.sub(
            r'\b(%s)\b' % '|'.join(map(re.escape, names)) if names else r'$^',
            '_',
            ast.unparse(body),
        )
        seen[hashlib.md5(key.encode()).hexdigest()].append(f'{path}:{node.lineno}:{node.name}')
    return [group for group in seen.values() if len(group) > 1]


def main():
    core_funcs = list(functions(CORE))
    public = [(p, n) for p, n in core_funcs if not n.name.startswith('_')]
    print(
        'cv core: files',
        len(CORE),
        '| non-code share (docstrings+comments):',
        f'{sum(non_code_share(f) for f in CORE) / len(CORE):.0%} mean, worst:',
        sorted(((round(non_code_share(f), 2), f) for f in CORE), reverse=True)[:3],
    )
    print(
        'cv core: public functions/methods without docstring:',
        sum(1 for _, n in public if not ast.get_docstring(n)),
        'of',
        len(public),
    )
    print(
        'cv core: functions without return annotation:',
        sum(1 for _, n in core_funcs if n.returns is None and n.name != '__init__'),
    )
    print(
        'cv core: functions > 80 lines:',
        sorted(
            (
                (n.end_lineno - n.lineno + 1, f'{p}:{n.name}')
                for p, n in core_funcs
                if n.end_lineno - n.lineno + 1 > 80
            ),
            reverse=True,
        )[:5],
    )
    print(
        'cv core: functions with >= 12 params:',
        [f'{p}:{n.name}' for p, n in core_funcs if len(n.args.args) + len(n.args.kwonlyargs) >= 12],
    )
    print(
        'balltrack: functions without docstring:',
        sum(1 for _, n in functions(BALLTRACK) if not ast.get_docstring(n)),
        'of',
        len(list(functions(BALLTRACK))),
    )
    print(
        'short import aliases (<3 chars), files:',
        len(short_aliases(FILES)),
        'total:',
        sum(short_aliases(FILES).values()),
    )
    print('duplicate function bodies in cv/*.py:', duplicate_bodies(CORE))
    print('duplicate function bodies in cv/balltrack:', len(duplicate_bodies(BALLTRACK)))
    print(
        'sys.path.insert hacks in cv/balltrack:',
        sum(
            Path(f).read_text(encoding='utf-8', errors='ignore').count('sys.path.insert')
            for f in BALLTRACK
        ),
    )
    text = '\n'.join(Path(f).read_text(encoding='utf-8', errors='ignore') for f in CORE + BALLTRACK)
    print(
        'personal names / local paths in cv/:', len(re.findall(r'\bAlex\b|C:/Users/|alexv', text))
    )


if __name__ == '__main__':
    main()
