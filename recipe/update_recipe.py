"""(re-)generate the strawberry-graphql multi-output recipe based on `pyproject.toml`

Invoke this locally from the root of the feedstock, assuming `tomli` and `jinja2`:

    python recipe/update_recipe.py
    git commit -m "updated recipe with update script"
    conda smithy rerender

The optional `--check` parameter will fail if new `[extra]`s are added, or
dependencies change.

This tries to work with the conda-forge autotick bot by reading updates from
`meta.yml`:

- build_number
- version
- sha256sum

If running locally against a non-bot-requested version, you'll probably need
to update those fields in `meta.yaml`.

If some underlying project data changed e.g. the `path_to-the_tarball`, update
`meta.j2.yaml` and re-run.
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
MIN_PYTHON = ">=3.7"

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

EXTRA_TEST_IMPORTS = {
    "aiohttp": "strawberry.aiohttp",
    "asgi": "strawberry.asgi",
    "chalice": "strawberry.chalice",
    "channels": "strawberry.channels",
    # TODO: needs env var
    # "debug-server": "strawberry.cli.debug_server",
    "django": "strawberry.django",
    "fastapi": "strawberry.fastapi",
    "flask": "strawberry.flask",
    "opentelemetry": "strawberry.extensions.tracing",
    "pydantic": "strawberry.experimental.pydantic",
    "sanic": "strawberry.sanic",
    "cli": "strawberry.cli",
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


def update_recipe(check=False):
    """update or check a recipe based on the `pyproject.toml` data"""
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
        extra_test_imports=EXTRA_TEST_IMPORTS,
        extra_test_commands=EXTRA_TEST_COMMANDS,
        min_python=MIN_PYTHON
    )

    for tmpl_path in TMPL:
        dest_path = tmpl_path.parent / tmpl_path.name.replace(".j2", "")
        old_text = dest_path.read_text(encoding="utf-8")
        template = jinja2.Template(
            tmpl_path.read_text(encoding="utf-8").strip(), **DELIMIT
        )
        new_text = template.render(**context).strip() + "\n"

        if check:
            if new_text.strip() != old_text.strip():
                print(f"{dest_path} is not up-to-date:")
                print(
                    "\n".join(
                        difflib.unified_diff(
                            old_text.splitlines(),
                            new_text.splitlines(),
                            dest_path.name,
                            f"{dest_path.name} (updated)",
                        )
                    )
                )
                print("either apply the above patch, or run locally:")
                print("\n\tpython recipe/update_recipe.py\n")
                return 1
        else:
            dest_path.write_text(new_text, encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(update_recipe(check="--check" in sys.argv))
