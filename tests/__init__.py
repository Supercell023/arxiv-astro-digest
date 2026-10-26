"""Regression tests are offline. Unexpected outbound connections fail immediately."""
import socket


def _no_network(*args, **kwargs):
    raise RuntimeError("Tests must not access the network")


socket.socket.connect = _no_network
socket.socket.connect_ex = _no_network
