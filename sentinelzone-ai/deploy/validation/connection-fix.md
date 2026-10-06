# Setup connection correction — 2026-09-24

The screenshot's camera name contained a space, rejected by the identifier schema before any connection attempt. The old endpoint masked all validation failures as SSH/RTSP errors. Names now normalize whitespace to hyphens and validation responses identify the failing field without returning input values. The browser clears stale prerequisite lists after a failed save or field edits.

The new Check connection action uses the unsaved connection fields, independent of calibration/model readiness. It opens SSH when configured and reads one RTSP frame in a subprocess with a timeout; it saves neither configuration nor footage. Paths to the host's SSH private key and verified known-hosts file are now editable in setup, with existing environment configuration as fallback. Host verification remains strict. SSH and RTSP failures produce bounded, redacted diagnostics.

- 91 Python tests passed in 45.859 seconds, including name normalization, API error redaction, model-independent connection checks, timeout cleanup and camera authentication diagnostics.
- CPU candidate `sentinelzone-ai:connection-fix-candidate`, `sha256:e75d620461c68061230563fb111215119ab55de73745fb83a4d5ef0d060891cd`, passed real SSH host rejection, forwarding, server restart recovery and cleanup.
- Combined SSH/RTSP integration passed, including the new connection-check endpoint before activation, frame receipt, inference, synchronized snapshots/reviews, publisher interruption and recovery. The fixture's model was synthetic; this establishes pipeline behavior, not site accuracy.
- Browser confirmed blank connection/name/calibration fields and an available Check connection action that requests only connection prerequisites. The five-cycle/second and one-second freshness defaults remain.
- Local preview restarted with the changes. The candidate image precedes only a final explanatory-text wording adjustment. Production tags were not changed.

No connection to the user's actual SSH server was attempted: a usable private-key path and independently verified host-key file have not been supplied. Camera/SSH details and saved camera password were cleared at the user's request. No private-key files were removed.
