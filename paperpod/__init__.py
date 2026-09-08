"""paperpod — the physical half of an e-paper Mopidy player.

Buttons, volume and transport, driving two APIs that already exist:
mopidy-epaper's input API for anything to do with the panel, and Mopidy's
JSON-RPC for anything to do with playback. Nothing here is a Mopidy extension;
it is an ordinary process that talks HTTP.
"""

__version__ = "0.1.0"
