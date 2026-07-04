
from setuptools import setup
from Cython.Build import cythonize

# Keep this list minimal and dependency-light. Each entry becomes a native .pyd.
SENSITIVE_MODULES = [
    "core/services/subscription_gate.py",
]

setup(
    name="quizmaster-native",
    ext_modules=cythonize(
        SENSITIVE_MODULES,
        compiler_directives={"language_level": "3"},
    ),
    # Build the extension(s) in place, beside the source, and stop there.
    script_args=["build_ext", "--inplace"],
)
