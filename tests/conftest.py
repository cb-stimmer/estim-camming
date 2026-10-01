import pytest

from estim_camming.bus import EventBus
from estim_camming.devices.dummy import DummyDevice
from estim_camming.safety import SafetyConfig, SafetyGuard


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def device():
    return DummyDevice.from_config({"channels": ["A", "B"]})


@pytest.fixture
def make_guard(bus, device):
    def make(**overrides):
        config = SafetyConfig(**({"start_armed": True, "max_change_per_second": 1000} | overrides))
        return SafetyGuard(device, config, bus)

    return make
