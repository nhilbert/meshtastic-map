import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "measure_logger.py"
spec = importlib.util.spec_from_file_location("measure_logger", SCRIPT)
measure_logger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(measure_logger)


def test_relayed_packet_without_hop_limit_counts_one_hop():
    # With hop_limit 1 a relayed packet arrives with hopLimit 0, which the API leaves out.
    assert measure_logger.hops_of({"hopStart": 1}) == "1"


def test_direct_packet():
    assert measure_logger.hops_of({"hopStart": 1, "hopLimit": 1}) == "0"


def test_old_firmware_without_hop_start():
    assert measure_logger.hops_of({"hopLimit": 3}) == ""
