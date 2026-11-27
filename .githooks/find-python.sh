# Sourced by the other hooks. Sets PYTHON_CMD to a Python 3.11 or newer.
# It tries python3, python and the Windows launcher in turn, and skips
# a candidate that cannot run, such as the Microsoft Store stub.
PYTHON_CMD=
for candidate in python3 python "py -3"
do
    if $candidate -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1
    then
        PYTHON_CMD=$candidate
        break
    fi
done
if [ -z "$PYTHON_CMD" ]
then
    echo "DecisionBound hook: Python 3.11 or newer was not found on PATH." >&2
    echo "Install it, or run git with --no-verify and rely on CI." >&2
    exit 1
fi
