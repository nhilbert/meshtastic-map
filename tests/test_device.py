import pytest

from meshplay import connect


@pytest.mark.hardware
def test_can_connect_and_read_node_info():
    with connect() as iface:
        assert iface.getMyNodeInfo()["user"]["id"].startswith("!")
