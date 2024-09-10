"""(re-)generate the strawberry-graphql multi-output recipe based on `pyproject.toml`

Invoke this locally from the root of the feedstock, assuming `tomli` and `jinja2`:

    python recipe/test_recipe.py --update
    git commit -m "updated recipe with update script"
    conda smithy rerender

Without the optional `--update` parameter, it will fail if new `[extra]`s are
added, or dependencies change.

This tries to work with the conda-forge autotick bot by reading updates from
`meta.yml`:

- build_number
- version
- sha256sum

If running locally against a non-bot-requested version, you'll probably need
to update those fields in `meta.yaml`.

If some underlying project data changed e.g. the `path_to-the_tarball`, update
`TEMPLATE` below and re-run with `--update`.
"""
import os
import re
import sys
import tempfile
import tarfile
from pathlib import Path
from urllib.request import urlretrieve
import difflib

import jinja2
import tomli

DELIMIT = dict(
    # use alternate template delimiters to avoid conflicts
    block_start_string="<%",
    block_end_string="%>",
    variable_start_string="<<",
    variable_end_string=">>",
)

TEMPLATE = """
{% set version = "<< version >>" %}

# handle undefined PYTHON in `noarch: generic` outputs
{% if PYTHON is not defined %}{% set PYTHON = "$PYTHON" %}{% endif %}

package:
  name: strawberry-graphql
  version: {{ version }}

source:
  url: https://pypi.io/packages/source/s/strawberry-graphql/strawberry_graphql-{{ version }}.tar.gz
  # the SHA256 gets updated by the bot
  sha256: << sha256_sum >>

build:
  # the build number gets reset by the bot
  number: << build_number >>
  noarch: python
  script:
    - {{ PYTHON }} {{ RECIPE_DIR }}/test_recipe.py
    - {{ PYTHON }} -m pip install . -vv --no-deps --no-build-isolation
  entry_points:
    - strawberry = strawberry.cli:run

requirements:
  host:
    - jinja2
    - pip
    - poetry >=0.12
    - poetry-core
    - python << min_python >>
    - tomli
  run:
    - packaging
    - python << min_python >><% for dep in core_deps %>
    - << dep >>
    <%- endfor %>

test:
  imports:
    - strawberry
  commands:
    - pip check
  requires:
    - pip

outputs:
  - name: strawberry-graphql
<% for extra, extra_deps in extra_outputs.items() %>
  - name: strawberry-graphql-with-<< extra >>
    build:
      noarch: generic
    requirements:
      run:
        - {{ pin_subpackage("strawberry-graphql", max_pin="x.x.x") }}<% for dep in extra_deps %>
        - << dep >>
        <%- endfor %><% for dep in known_extra_deps.get(extra, []) %>
        - << dep >>
        <%- endfor %>
    test:
      imports:
        - strawberry<% if extra in extra_test_imports %>
        - << extra_test_imports[extra] >>
        <%- else %>
        # TODO: import test for << extra >>
        <%- endif %>
      <% if extra not in skip_pip_check or extra in extra_test_commands -%>
      commands:
        <% if extra not in skip_pip_check %>- pip check<% endif %><% if extra in extra_test_commands %>
        - << extra_test_commands[extra] >>
        <%- endif %>
      <% endif -%>
      requires:
        - pip
    about:
      home: https://strawberry.rocks
      summary: A library for creating GraphQL APIs (with << extra >>)
      license: MIT
      license_file: LICENSE
      dev_url: https://github.com/strawberry-graphql/strawberry
      doc_url: https://strawberry.rocks/docs
<% endfor %>

about:
  home: https://strawberry.rocks
  summary: A library for creating GraphQL APIs
  license: MIT
  license_file: LICENSE
  dev_url: https://github.com/strawberry-graphql/strawberry
  doc_url: https://strawberry.rocks/docs

extra:
  feedstock-name: strawberry-graphql
  recipe-maintainers:
    - cshaley
    - thewchan
    - bollwyvl
"""

DEV_URL = "https://github.com/strawberry-graphql/strawberry"

#: assume running locally
HERE = Path(__file__).parent
WORK_DIR = HERE
SRC_DIR = Path(os.environ["SRC_DIR"]) if "SRC_DIR" in os.environ else None

#: assume inside conda-build
if "RECIPE_DIR" in os.environ:
    WORK_DIR = Path(os.environ["RECIPE_DIR"])

TMPL = [*WORK_DIR.glob("*.j2.*")]
META = WORK_DIR / "meta.yaml"
CURRENT_META_TEXT = META.read_text(encoding="utf-8")
MIN_PYTHON = ">=3.8"

#: read the version from what the bot might have updated
try:
    VERSION = re.findall(r'set version = "([^"]*)"', CURRENT_META_TEXT)[0].strip()
    SHA256_SUM = re.findall(r"sha256: ([\S]*)", CURRENT_META_TEXT)[0].strip()
    BUILD_NUMBER = re.findall(r"number: ([\S]*)", CURRENT_META_TEXT)[0].strip()
except Exception as err:
    print(CURRENT_META_TEXT)
    print(f"failed to find version info in above {META}")
    print(err)
    sys.exit(1)

#: instead of cloning the whole repo, just download tarball
TARBALL_URL = f"{DEV_URL}/archive/refs/tags/{VERSION}.tar.gz"

#: the path to `pyproject.toml` in the tarball
PYPROJECT_TOML = f"strawberry-{VERSION}/pyproject.toml"

#: despite claiming optional, these end up as hard `Requires-Dist`
KNOWN_REQS = []

#: these are handled externally
KNOWN_SKIP = [
    "python",
]

SKIP_PIP_CHECK = ["chalice"]

#: known deps not handled by upstream
KNOWN_EXTRA_DEPS = {
    # "some-extra": ["some-dep >=1,<2  # comment explaining why"],
    "starlite": ["pydantic <2,!=1.10.12  # from pydantic-openapi-schema"],
}

REPLACE_DEPS = {"graphlib_backport": "graphlib-backport"}

EXTRA_TEST_IMPORTS = {
    "aiohttp": "strawberry.aiohttp",
    "asgi": "strawberry.asgi",
    "chalice": "strawberry.chalice",
    "channels": "strawberry.channels",
    "cli": "strawberry.cli",
    "django": "strawberry.django",
    "fastapi": "strawberry.fastapi",
    "flask": "strawberry.flask",
    "litestar": "strawberry.litestar",
    "opentelemetry": "strawberry.extensions.tracing",
    "pydantic": "strawberry.experimental.pydantic",
    "pyinstrument": "strawberry.extensions.pyinstrument",
    "quart": "strawberry.quart.views",
    "sanic": "strawberry.sanic",
    "starlite": "strawberry.starlite",
    # TODO: needs env var
    # "debug-server": "strawberry.cli.debug_server",
}

EXTRA_TEST_COMMANDS = {"cli": "strawberry --help"}

#: some extras may become temporarily broken: add them here to skip
SKIP_EXTRAS = []


def is_required(dep, dep_spec):
    required = True
    print(f"is {dep} required: {dep_spec}")

    if dep in KNOWN_SKIP:
        required = False
    elif dep in KNOWN_REQS:
        required = True
    elif isinstance(dep_spec, dict) and dep_spec.get("optional"):
        required = False

    print("...", required)
    return required


def reqtify(raw, deps):
    """split dependency into conda requirement"""
    dep = deps[raw]
    if not isinstance(dep, str):
        dep = dep["version"]

    if "~" in dep or "^" in dep:
        op = dep[0]
        bits = dep[1:].split(".")
        bits = bits[:1] if op == "^" else bits[:2]
        dep = ".".join([*bits, "*"])

    raw = REPLACE_DEPS.get(raw, raw)

    return f"{raw} {dep}".lower()


def preflight_recipe():
    """check the recipe first"""
    print("version:", VERSION)
    print("sha256: ", SHA256_SUM)
    print("number: ", BUILD_NUMBER)
    assert VERSION, "no meta.yaml#/package/version detected"
    assert SHA256_SUM, "no meta.yaml#/source/sha256 detected"
    assert BUILD_NUMBER, "no meta.yaml#/build/number detected"
    print("information from the recipe looks good!", flush=True)


def get_pyproject_data():
    """fetch the pyproject.toml data"""
    if SRC_DIR:
        print(f"reading pyprojec.toml from {TARBALL_URL}...")
        return tomli.loads((SRC_DIR / "pyproject.toml").read_text(encoding="utf-8"))

    print(f"reading pyproject.toml from {TARBALL_URL}...")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        tarpath = tdp / Path(TARBALL_URL).name
        urlretrieve(TARBALL_URL, tarpath)
        with tarfile.open(tarpath, "r:gz") as tf:
            return tomli.load(tf.extractfile(PYPROJECT_TOML))


def verify_recipe(update=False):
    """check (or update) a recipe based on the `pyproject.toml` data"""
    check = not update
    preflight_recipe()
    pyproject = get_pyproject_data()
    deps = pyproject["tool"]["poetry"]["dependencies"]
    core_deps = sorted(
        [reqtify(d, deps) for d, d_spec in deps.items() if is_required(d, d_spec)]
    )
    extras = pyproject["tool"]["poetry"]["extras"]
    extra_outputs = {
        extra: sorted([reqtify(d, deps) for d in extra_deps if d in deps])
        for extra, extra_deps in extras.items()
        if extra not in SKIP_EXTRAS
    }

    context = dict(
        version=VERSION,
        build_number=BUILD_NUMBER,
        sha256_sum=SHA256_SUM,
        extra_outputs=extra_outputs,
        core_deps=core_deps,
        known_extra_deps=KNOWN_EXTRA_DEPS,
        extra_test_imports=EXTRA_TEST_IMPORTS,
        extra_test_commands=EXTRA_TEST_COMMANDS,
        min_python=MIN_PYTHON,
        skip_pip_check=SKIP_PIP_CHECK,
    )

    old_text = META.read_text(encoding="utf-8")
    template = jinja2.Template(TEMPLATE.strip(), **DELIMIT)
    new_text = template.render(**context).strip() + "\n"

    if check:
        if new_text.strip() != old_text.strip():
            print(f"{META} is not up-to-date:")
            print(
                "\n".join(
                    difflib.unified_diff(
                        old_text.splitlines(),
                        new_text.splitlines(),
                        META.name,
                        f"{META} (expected)",
                    )
                )
            )
            print("either apply the above patch, or run locally:")
            print("\n\tpython recipe/test_recipe.py --update\n")
            return 1
    else:
        META.write_text(new_text, encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(verify_recipe(update="--update" in sys.argv))
