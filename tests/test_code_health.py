from scripts.check_code_health import check_file
import glob
import os


def test_tldrgraph_code_health():
    """Ensures all source files in tldrgraph adhere to the <= 400 lines limit."""
    files = sorted(glob.glob("tldrgraph/**/*.py", recursive=True))
    all_file_errors = []
    for filepath in files:
        f_errs, _ = check_file(filepath, max_file_lines=400)
        all_file_errors.extend(f_errs)
    assert not all_file_errors, "\n".join(all_file_errors)
