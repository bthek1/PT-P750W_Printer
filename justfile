# Print live printer status (tape, errors)
status:
    uv run pt-status

# Print a text label on the loaded tape, e.g. just print "Hello"
print text *flags:
    uv run pt-print "{{text}}" {{flags}}

# Run the hermetic tests (no printer needed)
test:
    flock -o /tmp/agent-tests.lock uv run pytest -q

# Run the web UI tests (mocked API, no server or printer needed)
web-test:
    cd web && flock -o /tmp/agent-tests.lock npm test

# Coverage for Python and web tests; each fails under 90%
coverage:
    flock -o /tmp/agent-tests.lock uv run pytest -q -p pytest_cov --cov
    cd web && flock -o /tmp/agent-tests.lock npm run coverage

# Build the web UI, then serve it and the API on http://127.0.0.1:8750
serve *flags: web-build
    uv run pt-server {{flags}}

# Build the web UI into web/dist
web-build:
    cd web && npm ci --silent && npm run build

# Vite dev server with hot reload on :5173; run `uv run pt-server` alongside it
web-dev:
    cd web && npm run dev

# Install pt-print, pt-status and pt-server on PATH as a uv tool
install:
    uv tool install --force --reinstall .

# The usbmisc trigger is needed because the lp node has no idVendor attribute.

# Install the udev rule so plugdev members can use the printer without root
install-udev:
    sudo install -m 0644 udev/99-pt-p750w.rules /etc/udev/rules.d/
    sudo udevadm control --reload
    sudo udevadm trigger --action=add --attr-match=idVendor=04f9
    sudo udevadm trigger --action=add --subsystem-match=usbmisc
