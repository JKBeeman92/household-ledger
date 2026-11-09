"""Flash messages survive when another one is still waiting to be shown."""
from __future__ import annotations


def test_a_second_flash_is_kept_while_the_first_is_unread(client, reset_db):
    bad = {"body": "Item,CanonicalItem,Category,Flow,Note\nPipeweed,Pipeweed,Fun,nonsense,\n"}
    client.post("/settings/mapping", data=bad)          # first flash, not yet rendered
    client.post("/settings/household", data={"body": "[]"})   # second flash, added to the pending list
    page = client.get("/settings").text
    assert page.count('class="flash bad"') == 2
