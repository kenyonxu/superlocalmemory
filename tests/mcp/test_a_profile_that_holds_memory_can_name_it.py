# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Every memory operation is scoped to a profile, so a surface that can read or
write memory has to be able to say which profile it means.

A profile set that offers ``remember`` and ``recall`` but not ``switch_profile``
can store and retrieve for as long as you like and never leave the profile it
happened to start in. A second workspace is then unreachable from that surface,
and the only recovery is to edit the host's configuration and restart it.

A count assertion elsewhere pins the size of each set. This pins the reason.
"""

from __future__ import annotations

import pytest

from superlocalmemory.mcp.profiles import _PROFILE_DEFINITIONS

# Tools whose result depends on which profile is active — every one of them
# reads or writes rows that carry a profile_id.
MEMORY_TOOLS = frozenset({
    "remember", "recall", "search", "fetch", "list_recent",
    "update_memory", "delete_memory", "forget", "get_memory_summary",
})

SCOPE_SELECTOR = "switch_profile"


@pytest.mark.parametrize("name", sorted(_PROFILE_DEFINITIONS))
def test_a_profile_offering_memory_also_offers_the_selector(name: str) -> None:
    tools = _PROFILE_DEFINITIONS[name]
    offered = tools & MEMORY_TOOLS
    if not offered:
        pytest.skip(f"{name!r} exposes no profile-scoped memory tool")

    assert SCOPE_SELECTOR in tools, (
        f"profile {name!r} exposes {sorted(offered)} but not {SCOPE_SELECTOR!r}; "
        f"a client on this profile can read and write memory but can never "
        f"reach a second profile"
    )


def test_the_exemption_is_real_and_not_a_loophole() -> None:
    """Whichever profiles the test above skips must genuinely hold no memory.

    Without this, a profile could lose its memory tools by accident and the
    parametrised test would go quiet instead of failing.
    """
    exempt = {
        name: sorted(tools & MEMORY_TOOLS)
        for name, tools in _PROFILE_DEFINITIONS.items()
        if SCOPE_SELECTOR not in tools
    }
    for name, offered in exempt.items():
        assert not offered, (
            f"profile {name!r} was treated as exempt but offers {offered}"
        )

    assert exempt.keys() == {"mesh"}, (
        f"expected only the coordination-only profile to be exempt, got "
        f"{sorted(exempt)}; a new exemption needs a recorded decision"
    )
