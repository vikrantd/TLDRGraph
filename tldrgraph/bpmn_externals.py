"""
Where a workflow's work leaves the process.

BPMN puts third-party work in its own pool, so the reader can see the boundary
between what the tool does and what it hands to something else. This table is
what decides that: it maps the calls that cross the boundary to the system on
the other side.

Matching is on the qualified call - ``requests.get`` rather than ``get`` - so an
ordinary dictionary lookup is never mistaken for a network hop.
"""

from __future__ import annotations

from typing import Dict, List, Optional

# Calls that leave the process. Matching is on the qualified call - "requests.get"
# rather than "get" - so a dictionary lookup is never mistaken for a network hop.
EXTERNAL_CALLS: Dict[str, str] = {
    "subprocess.run": "Coding agent",
    "subprocess.Popen": "Coding agent",
    "subprocess.check_output": "Coding agent",
    "requests.get": "Network service",
    "requests.post": "Network service",
    "httpx.get": "Network service",
    "httpx.post": "Network service",
    "urllib.request.urlopen": "Network service",
    "sqlite3.connect": "SQLite database",
    "conn.execute": "SQLite database",
    "conn.executemany": "SQLite database",
    "conn.commit": "SQLite database",
    "cursor.execute": "SQLite database",
    "os.makedirs": "File system",
    "os.remove": "File system",
    "os.rmdir": "File system",
    "shutil.rmtree": "File system",
    "shutil.copy": "File system",
    "path.write_text": "File system",
    "path.read_text": "File system",
    "path.mkdir": "File system",
    "json.dump": "File system",
    "yaml.safe_dump": "File system",
    "webbrowser.open": "Web browser",
    "webbrowser.open_new": "Web browser",
    "TextEmbedding": "Embedding model",
    "model.embed": "Embedding model",
}

# Bare names that can only mean one thing.
EXTERNAL_BARE: Dict[str, str] = {
    "open": "File system",
    "urlopen": "Network service",
    "TextEmbedding": "Embedding model",
}


def external_for(qualified: List[str]) -> Optional[str]:
    for name in qualified:
        bare = name.split(".")[-1]
        for pattern, system in EXTERNAL_CALLS.items():
            if name == pattern or name.endswith("." + pattern) or name.endswith("." + bare) and pattern.endswith("." + bare) and pattern.split(".")[0] in name:
                return system
        if bare in EXTERNAL_BARE and "." not in name:
            return EXTERNAL_BARE[bare]
    return None


