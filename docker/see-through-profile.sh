# Runtime values are generated from a small allowlist by PID 1. Public-key
# variables are intentionally not propagated into SSH sessions.
if [ -r /run/see-through/runtime.env ]; then
    . /run/see-through/runtime.env
fi
unset PUBLIC_KEY SSH_PUBLIC_KEY
