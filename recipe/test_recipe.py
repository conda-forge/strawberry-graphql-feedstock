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

import difflib
import os
import re
import sys
import tarfile
import tempfile
from pathlib import Path
from urllib.request import urlretrieve

import jinja2
import tomli
from packaging.requirements import Requirement

DELIMIT = dict(
    # use alternate template delimiters to avoid conflicts
    block_start_string="<%",
    block_end_string="%>",
    variable_start_string="<<",
    variable_end_string=">>",
)
#: this appears in the global pinnings, but not predictably reachable during build
MIN_PYTHON = "3.9"

TEMPLATE = """
# yaml-language-server: $schema=https://raw.githubusercontent.com/prefix-dev/recipe-format/main/schema.json
schema_version: 1

context:
  version: "<< version >>"

recipe:
  name: strawberry-graphql
  version: ${{ version }}

source:
  url: https://pypi.org/packages/source/s/strawberry-graphql/strawberry_graphql-${{ version }}.tar.gz
  # the SHA256 gets updated by the bot
  sha256: << sha256_sum >>

build:
  # the build number gets reset by the bot
  number: << build_number >>

outputs:
  - package:
      name: strawberry-graphql
    build:
      noarch: python
      script:
        - ${{ PYTHON }} ${{ RECIPE_DIR }}/test_recipe.py
        - ${{ PYTHON }} -m pip install . -vv --no-deps --no-build-isolation --disable-pip-version-check
      python:
        entry_points:
          - strawberry = strawberry.cli:run
    requirements:
      host:
        - jinja2
        - pip
        - poetry >=0.12
        - poetry-core
        - python ${{ python_min }}.*
        - tomli
      run:
        - python >=${{ python_min }}<% for dep in core_deps %>
        - << dep >>
        <%- endfor %>
        # fix after https://github.com/conda-forge/astunparse-feedstock/pull/15
        - wheel
    tests:
      - python:
          pip_check: true
          python_version: ${{ python_min }}.*
          imports: strawberry
<% for extra, extra_deps in extra_outputs.items() %>
  - package:
      name: strawberry-graphql-with-<< extra >>
    build:
      noarch: generic
    requirements:
      run:
        - ${{ pin_subpackage("strawberry-graphql", upper_bound="x.x.x") }}<% for dep in extra_deps %>
        - << dep >>
        <%- endfor %><% for dep in known_extra_deps.get(extra, []) %>
        - << dep >>
        <%- endfor %>
    tests:
      - python:
          pip_check: << "false" if extra in skip_pip_check else "true" >>
          python_version: ${{ python_min }}.*
          imports:
            - strawberry<% if extra in extra_test_imports %>
            - << extra_test_imports[extra] >>
            <%- else %>
            # TODO: import test for << extra >>
            <%- endif %>
      <%- if extra in extra_test_commands %>
      - requirements:
          run:
            - python ${{ python_min }}.*
        script:
          - << extra_test_commands[extra] >>
      <%- endif %>
    about:
      homepage: https://strawberry.rocks
      summary: A library for creating GraphQL APIs (with << extra >>)
      license: MIT
      license_file: LICENSE
      repository: https://github.com/strawberry-graphql/strawberry
      documentation: https://strawberry.rocks/docs
<% endfor %>

about:
  homepage: https://strawberry.rocks
  summary: A library for creating GraphQL APIs
  license: MIT
  license_file: LICENSE
  repository: https://github.com/strawberry-graphql/strawberry
  documentation: https://strawberry.rocks/docs

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
PYPROJECT_PATH = (
    Path(os.environ["PYPROJECT_PATH"]) if "PYPROJECT_PATH" in os.environ else None
)

#: assume inside conda-build
if "RECIPE_DIR" in os.environ:
    WORK_DIR = Path(os.environ["RECIPE_DIR"])

RECIPE = WORK_DIR / "recipe.yaml"
CURRENT_RECIPE_TEXT = RECIPE.read_text(encoding="utf-8")

#: read the version from what the bot might have updated
try:
    VERSION = re.findall(r' version: "([^"]*)"', CURRENT_RECIPE_TEXT)[0].strip()
    SHA256_SUM = re.findall(r" sha256: ([\S]*)", CURRENT_RECIPE_TEXT)[0].strip()
    BUILD_NUMBER = re.findall(r" number: ([\S]*)", CURRENT_RECIPE_TEXT)[0].strip()
except Exception as err:
    print(CURRENT_RECIPE_TEXT)
    print(f"failed to find version info in above {RECIPE}")
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
    print("-", dep, "required:", end=" ")

    if dep in KNOWN_SKIP:
        required = False
    elif dep in KNOWN_REQS:
        required = True
    elif isinstance(dep_spec, dict):
        optional = dep_spec.get("optional")
        python = dep_spec.get("python")
        if optional:
            required = False
        elif python:
            required = required_python(python)

    print("YES" if required else "no", "\n  - ", dep_spec)
    return required


def required_python(python_spec: str):
    python_req = Requirement(reqtify(f"python {python_spec}"))
    return python_req.specifier.contains(MIN_PYTHON)


def reqtify(raw: str):
    """Split dependency into conda requirement"""
    dep = Requirement(raw)
    name = f"{dep.name}"
    spec = f"{dep.specifier}"

    has_tilde = "~" in spec
    has_caret = "^=" in spec

    if has_tilde or has_caret:
        strip_bits = 2 if has_tilde else 3
        bits = spec[strip_bits:].split(".")
        min_ = ".".join(bits)
        max_base, max_last = bits[:-strip_bits], str(int(bits[-strip_bits]) + 1)
        max_ = ".".join([*max_base, max_last])
        spec = f">={min_},<{max_}"

    if "," in spec:
        spec = ",".join(reversed(sorted(spec.split(","))))

    name = REPLACE_DEPS.get(name, name)

    final = f"{name} {spec}".lower()
    if final.replace(" ", "") != raw.replace(" ", ""):
        print("... normalizing\n\t", raw, "\n\t", final)
    return final


def preflight_recipe():
    """Check the recipe first"""
    print("version:", VERSION)
    print("sha256: ", SHA256_SUM)
    print("number: ", BUILD_NUMBER)
    assert VERSION, "no meta.yaml#/package/version detected"
    assert SHA256_SUM, "no meta.yaml#/source/sha256 detected"
    assert BUILD_NUMBER, "no meta.yaml#/build/number detected"
    print("information from the recipe looks good!", flush=True)


def get_pyproject_data():
    """Fetch the pyproject.toml data"""
    if PYPROJECT_PATH:
        print(f"reading pyproject.toml from {PYPROJECT_PATH}...")
        return tomli.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))

    if SRC_DIR:
        print(f"reading pyprojec.toml from {TARBALL_URL}...")
        return tomli.loads((SRC_DIR / "pyproject.toml").read_text(encoding="utf-8"))

    print(f"reading pyproject.toml from {TARBALL_URL}...")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        tarpath = tdp / Path(TARBALL_URL).name
        urlretrieve(TARBALL_URL, tarpath)
        with tarfile.open(tarpath, "r:gz") as tf:
            fp = tf.extractfile(PYPROJECT_TOML)
            assert fp, f"failed to extract {PYPROJECT_TOML}"
            return tomli.load(fp)


def verify_recipe(update=False):
    """Check (or update) a recipe based on the `pyproject.toml` data"""
    check = not update
    preflight_recipe()
    pyproject = get_pyproject_data()
    deps = pyproject["project"]["dependencies"]
    core_deps = sorted([reqtify(d) for d in deps])
    extras = pyproject["project"]["optional-dependencies"]
    extra_outputs = {
        extra: sorted([reqtify(ed) for ed in extra_deps])
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
        skip_pip_check=SKIP_PIP_CHECK,
        min_python=MIN_PYTHON,
    )

    old_text = RECIPE.read_text(encoding="utf-8")
    template = jinja2.Template(TEMPLATE.strip(), **DELIMIT)
    new_text = template.render(**context).strip() + "\n"

    if check:
        if new_text.strip() != old_text.strip():
            print(f"{RECIPE} is not up-to-date:")
            print(
                "\n".join(
                    difflib.unified_diff(
                        old_text.splitlines(),
                        new_text.splitlines(),
                        RECIPE.name,
                        f"{RECIPE} (expected)",
                    )
                )
            )
            print("\neither apply the above patch, or run locally:")
            print("\n\tpython recipe/test_recipe.py --update\n")
            return 1
    else:
        RECIPE.write_text(new_text, encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(verify_recipe(update="--update" in sys.argv))
