from abc import ABC, abstractmethod


class DeviceError(Exception):
    """Safe device error. Never includes credentials or raw protocol payloads."""


class Driver(ABC):
    @abstractmethod
    def get_attendance_records(self): ...

    @abstractmethod
    def get_users(self): ...

    @abstractmethod
    def get_device_info(self): ...

    @abstractmethod
    def close_connection(self): ...

    def test_connection(self):
        return self.get_device_info()

    def get_capabilities(self):
        return {"attendance": True, "users": True, "device_info": True, "clear_attendance": False}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close_connection()


_REGISTRY = {}


def register_driver(name, factory):
    if name in _REGISTRY:
        raise ValueError("Driver name is already registered")
    if not isinstance(factory, type) or not issubclass(factory, Driver):
        raise TypeError("A driver must implement the Driver interface")
    _REGISTRY[name] = factory


def get_driver(name, config):
    if "pyzk" not in _REGISTRY:
        from .pyzk import PyZKDriver

        register_driver("pyzk", PyZKDriver)
    factory = _REGISTRY.get(name)
    if factory is None:
        raise ValueError("Unsupported driver; only verified implementations may be registered")
    return factory(config)
