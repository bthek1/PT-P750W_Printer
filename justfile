# Print live printer status (tape, errors)
status:
    uv run pt_status.py

# The usbmisc trigger is needed because the lp node has no idVendor attribute.

# Install the udev rule so plugdev members can use the printer without root
install-udev:
    sudo install -m 0644 udev/99-pt-p750w.rules /etc/udev/rules.d/
    sudo udevadm control --reload
    sudo udevadm trigger --action=add --attr-match=idVendor=04f9
    sudo udevadm trigger --action=add --subsystem-match=usbmisc
