#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Sourced by tinydm-run-session (env-wayland.d) BEFORE it execs the cage compositor, so the
# renderer default reaches cage itself. /etc/cage-ui/*.sh is only sourced by cage's child
# (cage-ui-session.sh), after the compositor has already chosen its wlroots renderer.
export WLR_RENDERER="${WLR_RENDERER:-pixman}"
