# Print live printer status (tape, errors)
status:
    uv run pt-status

# Print a text label on the loaded tape, e.g. just print "Hello"
print text *flags:
    uv run pt-print "{{text}}" {{flags}}

# Plugin autoload is off because the ROS 2 PYTHONPATH injects broken pytest plugins.

# Run the hermetic tests (no printer needed)
test:
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 flock -o /tmp/agent-tests.lock uv run pytest -q

# Install pt-print and pt-status on PATH as a uv tool
install:
    uv tool install --force --reinstall .

# The usbmisc trigger is needed because the lp node has no idVendor attribute.

# Install the udev rule so plugdev members can use the printer without root
install-udev:
    sudo install -m 0644 udev/99-pt-p750w.rules /etc/udev/rules.d/
    sudo udevadm control --reload
    sudo udevadm trigger --action=add --attr-match=idVendor=04f9
    sudo udevadm trigger --action=add --subsystem-match=usbmisc
